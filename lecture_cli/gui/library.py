"""Read-only access to courses and saved notes, always confined to courses_dir."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

from ..storage import ATTACHMENTS, attachment_path, legacy_attachment_path

TRASH_TIMEOUT = 30
# 2026-10-07_143000-课堂笔记-a1b2c3, as written by cli.session().
NOTE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})-(.+)-[0-9a-f]{6}$")


class Forbidden(ValueError):
    """A path outside courses_dir, including through a symbolic link."""


class NotFound(LookupError):
    pass


class TrashUnavailable(RuntimeError):
    """gio is missing: notes are never deleted permanently instead."""


class TrashFailed(RuntimeError):
    pass


def inside(root, candidate) -> Path:
    root = Path(root).expanduser().resolve()
    path = Path(candidate).expanduser()
    real = (path if path.is_absolute() else root / path).resolve()
    # resolve() follows every link, so a link pointing outside lands outside here.
    if real != root and root not in real.parents:
        raise Forbidden("路径不在课程目录内")
    return real


def course_dir(root, name) -> Path:
    if not isinstance(name, str) or not name:
        raise NotFound("课程不存在")
    path = Path(root).expanduser().resolve() / name
    real = inside(root, path)
    # Course listings skip hidden and linked folders, so the API does too.
    if "/" in name or name.startswith(".") or path.is_symlink() or not real.is_dir():
        raise NotFound("课程不存在")
    return real


def is_attachment(path: Path) -> bool:
    return path.stem.endswith(tuple(f".{kind}" for kind in ATTACHMENTS))


def attachment_files(path: Path, kind: str) -> list[Path]:
    """This note's attachment of one kind: the 原文与记录 folder first, then the older flat layout."""
    found = []
    for candidate in (attachment_path(path, kind), legacy_attachment_path(path, kind)):
        if not candidate.parent.is_symlink() and not candidate.is_symlink() and candidate.is_file():
            found.append(candidate)
    return found


def attachment_file(path: Path, kind: str) -> Path | None:
    return next(iter(attachment_files(path, kind)), None)


# Sections of the review file that record how the note was made, not points to check.
PROCESS_SECTIONS = ("## 处理提示", "## 离线校正")
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s")


def review_count(text: str) -> int:
    """Points to check in a .review.md: each top-level list item, each unsorted transcript range
    (## 待整理原文) and each other plain paragraph. Processing notices and quoted source lines
    are not counted."""
    count, process = 0, False
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith("# "):
            continue
        if block.startswith("#"):
            process = block.startswith(PROCESS_SECTIONS)
            count += block.startswith("## 待整理原文")
            continue
        items = sum(1 for line in block.splitlines() if LIST_ITEM.match(line))
        if items:
            process = False  # Model review items follow the notices without a heading of their own.
            count += items
        elif not process and not block.startswith(">"):
            count += 1
    return count


def note_entry(path: Path) -> dict:
    match = NOTE_NAME.match(path.stem)
    started = f"{match[1]}T{match[2]}:{match[3]}:{match[4]}" if match else None
    review = attachment_file(path, "review")
    try:
        reviews = review_count(review.read_text(encoding="utf-8", errors="replace")) if review else 0
    except OSError:
        reviews = 0
    return {"name": path.name, "path": str(path), "started": started, "kind": match[5] if match else None,
            "attachments": {kind: attachment_file(path, kind) is not None for kind in ATTACHMENTS},
            "review_count": reviews}


def notes(root, course: Path) -> list[dict]:
    folder = course / "LectureNotes"
    if folder.is_symlink() or not folder.is_dir():
        return []
    entries = []
    for path in folder.glob("*.md"):
        if path.is_symlink() or not path.is_file() or is_attachment(path):
            continue
        inside(root, path)
        entries.append(note_entry(path))
    return sorted(entries, key=lambda entry: (entry["started"] or "", entry["name"]), reverse=True)


def courses(root) -> list[dict]:
    from ..cli import course_paths
    result = []
    for course in course_paths(Path(root).expanduser().resolve()):
        listed = notes(root, course)
        result.append({"name": course.name, "path": str(course), "notes_count": len(listed),
                       "last_note": next((entry["started"] for entry in listed if entry["started"]), None),
                       "has_glossary": (course / "glossary.json").is_file()})
    return result


def create_course(root, name) -> dict:
    name = name.strip() if isinstance(name, str) else ""
    if not name or "/" in name or name.startswith(".") or any(ord(c) < 32 for c in name):
        raise ValueError("课程名不能为空，不能包含 / 或控制字符，也不能以 . 开头")
    path = inside(root, Path(root).expanduser().resolve() / name)
    path.mkdir()  # FileExistsError: the caller reports a conflict.
    return {"name": name, "path": str(path), "notes_count": 0, "last_note": None, "has_glossary": False}


def note_path(root, candidate) -> Path:
    path = inside(root, candidate)
    # Only notes are readable here, not arbitrary course files.
    if path.parent.name != "LectureNotes" or path.suffix != ".md" or is_attachment(path) or not path.is_file():
        raise NotFound("笔记不存在")
    return path


def content(root, candidate) -> dict:
    path = note_path(root, candidate)
    result = {"main": path.read_text(errors="replace")}
    for kind in ATTACHMENTS:
        attachment = attachment_file(path, kind)
        if attachment is not None:
            result[kind] = inside(root, attachment).read_text(errors="replace")
    return result


def trash_note(root, candidate, *, which=shutil.which, run=subprocess.run) -> list[str]:
    """Move a note and its attachments (both layouts) to the desktop trash; never delete outright."""
    unresolved = Path(candidate).expanduser() if isinstance(candidate, str) else Path()
    if isinstance(candidate, str) and not unresolved.is_absolute():
        unresolved = Path(root).expanduser().resolve() / unresolved
    path = note_path(root, candidate)
    if unresolved.is_symlink():
        raise Forbidden("不能删除指向其他文件的链接")
    if not which("gio"):
        raise TrashUnavailable("未找到 gio，无法移到回收站；为免永久删除，未做任何改动。")
    # Attachments first: the main note never outlives the files it links to.
    targets = [inside(root, item) for kind in ATTACHMENTS for item in attachment_files(path, kind)] + [path]
    try:
        result = run(["gio", "trash", "--", *map(str, targets)], stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, timeout=TRASH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrashFailed(f"移到回收站失败：{exc}") from exc
    if result.returncode != 0:
        remaining = [str(item) for item in targets if item.exists()]
        raise TrashFailed("移到回收站失败：" + ((result.stderr or "").strip() or f"gio 退出码 {result.returncode}")
                          + ("；仍在原处：" + "、".join(remaining) if remaining else ""))
    return [str(item) for item in targets]
