"""PLAN-GUI-5 R3: every relative link and anchor in README.md (and docs/reference.md, which it points to) exists."""
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "reference.md"]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def prose(text):
    """The text outside fenced code blocks, where links are links."""
    return re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)


def anchors(path):
    """GitHub's heading ids: lowercase, punctuation dropped (letters, digits, _, - and spaces stay), spaces to
    hyphens, repeats numbered -1, -2, ..."""
    seen, result = {}, set()
    for line in prose(path.read_text(encoding="utf-8")).splitlines():
        match = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not match:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", match.group(1))
        slug = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


def links(path):
    for target in LINK.findall(prose(path.read_text(encoding="utf-8"))):
        if not re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto: are not checked
            yield target


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: path.name)
def test_relative_links_and_anchors_exist(doc):
    checked = 0
    for target in links(doc):
        file, _, anchor = target.partition("#")
        destination = (doc.parent / unquote(file)).resolve() if file else doc
        assert destination.is_file(), f"{doc.name}: {target} points to a missing file"
        if anchor:
            assert unquote(anchor) in anchors(destination), f"{doc.name}: {target} has no such heading"
        checked += 1
    assert checked, f"{doc.name} has no relative links to check"


def test_the_slugs_follow_github():
    """The rule above on headings like the ones the README links to."""
    sample = ROOT / "docs" / "reference.md"
    found = anchors(sample)
    for slug in ("qwen-语音识别", "录不到声音怎么查", "云端-api-转录无-gpu--弱-cpu", "实时-whisper--课后离线-qwen"):
        assert slug in found


def test_readme_stays_short_and_has_no_personal_paths():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 120
    assert "Umass_CS_Class" not in text and "/home/" not in text
