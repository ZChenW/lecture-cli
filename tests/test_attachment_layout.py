"""N1.2: attachments live in LectureNotes/原文与记录/, and every link in the main note resolves."""
import json
import re
from pathlib import Path
from urllib.parse import unquote

from lecture_cli.gui import library
from lecture_cli.storage import ATTACHMENT_DIR, REVIEW_MARKER, Journal, attachment_path, write_json

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def broken_links(note: Path) -> list[str]:
    """Every relative link must name an existing file and, with a fragment, an anchor in it."""
    broken = []
    for target in LINK.findall(note.read_text()):
        if re.match(r"^[a-z]+://", target):
            continue
        name, _, anchor = target.partition("#")
        path = note.parent / unquote(name)
        if not path.is_file():
            broken.append(target)
        elif anchor and f'<a id="{unquote(anchor)}"></a>' not in path.read_text():
            broken.append(target)
    return broken


def render(tmp_path, refined=False):
    directory = tmp_path / "session"
    directory.mkdir(parents=True)
    output = tmp_path / "MATH421" / "LectureNotes" / "2026-10-07_090200-课堂笔记-k7m2q9.md"
    output.parent.mkdir(parents=True)
    write_json(directory / "session.json", {"course": "MATH421", "output": str(output),
                                             "started": "2026-10-07T09:02:00-04:00", "refine": refined})
    records = [{"id": i, "start": f"00:00:{i:02d}.00", "end": f"00:00:{i:02d}.50", "text": f"line {i}"}
               for i in range(1, 7)]
    (directory / "transcript.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    if refined:
        (directory / "refined.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records[:3]))
        write_json(directory / "refinement-state.json", {"complete": True, "count": 3})
    journal = Journal(directory)
    journal.save(records, "- 随堂 [L1–L2]\n- 待核对：板书 [L3]")
    journal.set_info("outline", json.dumps([{"title": "链接数", "question": "怎么定义？", "first": 1, "last": 3}]))
    journal.save_detail(records[:3], f"## 链接数\n\n定义 $x$ [L2–L3]。\n{REVIEW_MARKER}\n- 疑点 [L1]")
    journal.set_info("detail_status", "complete")
    journal.render(finished=True)
    journal.close()
    return output


def test_attachments_are_written_to_the_visible_subfolder(tmp_path):
    output = render(tmp_path)
    folder = output.parent / ATTACHMENT_DIR
    assert not ATTACHMENT_DIR.startswith(".")
    assert sorted(path.name for path in output.parent.iterdir()) == sorted([output.name, ATTACHMENT_DIR])
    assert sorted(path.name for path in folder.iterdir()) == sorted(
        f"{output.stem}.{kind}.md" for kind in ("transcript", "live", "review"))
    main = output.read_text()
    encoded = "%E5%8E%9F%E6%96%87%E4%B8%8E%E8%AE%B0%E5%BD%95/"  # quote("原文与记录/")
    assert f"[原始转录]({encoded}" in main and f"[待核对与处理记录]({encoded}" in main


def test_every_link_in_the_main_note_and_attachments_resolves(tmp_path):
    for refined in (False, True):
        output = render(tmp_path / str(refined), refined)
        main_links = LINK.findall(output.read_text())
        assert any("#live-L" in link or "#refined-L" in link for link in main_links)
        assert broken_links(output) == []
        # Attachments link to their siblings in the same folder.
        for kind in ("live", "review"):
            assert broken_links(attachment_path(output, kind)) == []


def test_library_reads_new_layout_first_then_the_old_flat_one(tmp_path):
    root = tmp_path
    output = render(tmp_path)
    legacy = output.parent / "2026-09-01_090000-课堂笔记-aaaaaa.md"
    legacy.write_text("# old")
    legacy.with_suffix(".transcript.md").write_text("old transcript")
    legacy.with_suffix(".review.md").write_text("old review")
    entries = {entry["name"]: entry for entry in library.notes(root, root / "MATH421")}
    assert set(entries) == {output.name, legacy.name}
    assert entries[output.name]["attachments"] == {"transcript": True, "live": True, "review": True}
    assert entries[legacy.name]["attachments"] == {"transcript": True, "live": False, "review": True}
    assert library.content(root, str(legacy)) == {"main": "# old", "transcript": "old transcript", "review": "old review"}
    content = library.content(root, str(output))
    assert set(content) == {"main", "transcript", "live", "review"} and "line 1" in content["transcript"]
    # When both exist, the new location wins.
    output.with_suffix(".transcript.md").write_text("stale flat copy")
    assert library.content(root, str(output))["transcript"] == attachment_path(output, "transcript").read_text()


def test_linked_attachment_folder_is_never_followed(tmp_path):
    root = tmp_path
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = root / "MATH421" / "LectureNotes"
    folder.mkdir(parents=True)
    (folder / ATTACHMENT_DIR).symlink_to(outside)
    note = folder / "2026-10-07_090200-课堂笔记-bbbbbb.md"
    note.write_text("# note")
    (outside / f"{note.stem}.transcript.md").write_text("SECRET")
    assert library.content(root, str(note)) == {"main": "# note"}
