"""Read-only access to courses and saved notes, always confined to courses_dir."""
from __future__ import annotations

from pathlib import Path
import re

ATTACHMENTS = ("transcript", "live", "review")
# 2026-10-07_143000-课堂笔记-a1b2c3, as written by cli.session().
NOTE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})-(.+)-[0-9a-f]{6}$")


class Forbidden(ValueError):
    """A path outside courses_dir, including through a symbolic link."""


class NotFound(LookupError):
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


def note_entry(path: Path) -> dict:
    match = NOTE_NAME.match(path.stem)
    started = f"{match[1]}T{match[2]}:{match[3]}:{match[4]}" if match else None
    return {"name": path.name, "path": str(path), "started": started, "kind": match[5] if match else None,
            "attachments": {kind: path.with_suffix(f".{kind}.md").is_file() for kind in ATTACHMENTS}}


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
        attachment = path.with_suffix(f".{kind}.md")
        if attachment.is_file() and not attachment.is_symlink():
            result[kind] = attachment.read_text(errors="replace")
    return result
