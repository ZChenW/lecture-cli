"""Points to check in a .review.md, and which of them the reader has ticked (plan GUI-3 item 4).

The .review.md itself is never modified; ticks live in <stem>.review-state.json beside it.
"""
from __future__ import annotations

import hashlib
import json
import re
import textwrap
from pathlib import Path

from ..storage import CITATION, atomic_text, review_state_path

STATE_VERSION = 1
# Sections of the review file that record how the note was made, not points to check.
PROCESS_SECTIONS = ("## 处理提示", "## 离线校正")
UNSORTED = "## 待整理原文"
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s")
# Links written by storage.linked_sources: [live-L12–L15](….transcript.md#live-L12).
LINKED = re.compile(r"\[(live|refined)-L(\d+)(?:[–—-]L?(\d+))?[^\]]*\]\([^)\s]*\.transcript\.md#(?:live|refined)-L\d+\)")


def item_id(text: str) -> str:
    """The first 16 hex digits of SHA-256 over the text, trimmed and with whitespace runs collapsed."""
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip()).encode("utf-8")).hexdigest()[:16]


def sources_of(text: str) -> list[dict]:
    """Transcript references in reading order: linked ones carry their version, bare [L30] ones do not."""
    found = [(m.start(), m[1], int(m[2]), int(m[3] or m[2])) for m in LINKED.finditer(text)]
    found += [(m.start(), None, int(m[1]), int(m[2] or m[1])) for m in CITATION.finditer(text)]
    result, seen = [], set()
    for _, version, first, last in sorted(found):
        last = max(first, last)
        if (version, first, last) in seen:
            continue
        seen.add((version, first, last))
        result.append({"version": version, "first": first, "last": last,
                       "label": f"L{first}–L{last}" if last > first else f"L{first}"})
    return result


def list_items(block: str) -> list[str]:
    """Each top-level list item with its continuation lines, without the marker; lines before
    the first marker form a paragraph of their own."""
    items: list[list[str]] = []
    lead: list[str] = []
    for line in block.splitlines():
        if LIST_ITEM.match(line):
            items.append([LIST_ITEM.sub("", line, count=1)])
        elif items:
            items[-1].append(line)
        else:
            lead.append(line)
    texts = ["\n".join(lead).strip()] if any(line.strip() for line in lead) else []
    return texts + [(item[0] + "\n" + textwrap.dedent("\n".join(item[1:]))).strip() for item in items]


def parse(text: str) -> tuple[list[str], list[str]]:
    """(points to check, processing notices) in file order, both as Markdown.

    Points: each top-level list item; each unsorted transcript range (## 待整理原文, with the
    quoted lines under it); each other plain paragraph. Headings are not points; quoted lines
    outside an unsorted range are not either. The processing sections (## 处理提示, ## 离线校正)
    are returned as notices until the model's own points begin.
    """
    points: list[str] = []
    notices: list[str] = []
    process = unsorted = False
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith("# "):
            continue
        if block.startswith("#"):
            process = block.startswith(PROCESS_SECTIONS)
            unsorted = block.startswith(UNSORTED)
            if process:
                notices.append(block)
            elif unsorted:
                points.append(block.lstrip("#").strip())
            continue
        if block.startswith(">"):
            if unsorted and points:
                points[-1] += "\n\n" + block
            elif process and notices:
                notices[-1] += "\n\n" + block
            continue
        unsorted = False
        if any(LIST_ITEM.match(line) for line in block.splitlines()):
            process = False  # Model review items follow the notices without a heading of their own.
            points.extend(list_items(block))
        elif process and notices:
            notices[-1] += "\n\n" + block
        else:
            points.append(block)
    return points, notices


def read_checked(review: Path) -> list[str]:
    """Ticked ids as stored; a missing, linked or unreadable state file means nothing is ticked."""
    path = review_state_path(review)
    if path.is_symlink() or not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    checked = value.get("checked") if isinstance(value, dict) and value.get("version") == STATE_VERSION else None
    return [item for item in checked if isinstance(item, str)] if isinstance(checked, list) else []


def items(review: Path) -> dict:
    """The points of one review file with their ticks; ids that no longer exist are dropped."""
    points, notices = parse(review.read_text(encoding="utf-8", errors="replace"))
    checked = set(read_checked(review))
    return {"items": [{"id": item_id(text), "text": text, "sources": sources_of(text),
                       "checked": item_id(text) in checked} for text in points],
            "notices": notices}


def counts(review: Path | None) -> tuple[int, int]:
    """(unchecked, total) for the notes list."""
    if review is None:
        return 0, 0
    listed = items(review)["items"]
    return sum(not item["checked"] for item in listed), len(listed)


def save_checked(review: Path, checked: list[str]) -> dict:
    """Stores exactly these ticks; each id must belong to a point of the current file."""
    current = [item["id"] for item in items(review)["items"]]
    unknown = [item for item in checked if item not in current]
    if unknown:
        raise ValueError(f"未知的条目：{unknown[0]}")
    path = review_state_path(review)
    if path.is_symlink():
        raise PermissionError("核对记录是一个链接，未写入")
    wanted = set(checked)
    atomic_text(path, json.dumps({"version": STATE_VERSION,
                                  "checked": list(dict.fromkeys(i for i in current if i in wanted))},
                                 ensure_ascii=False) + "\n")
    return items(review)
