"""The saved-note structure the GUI reader parses (frontend/src/lib/note.ts and markdown.ts).

The reader takes the title from the learning path, the header notices from the quoted lines, hides
the attachment link lines, follows [live-L…](….transcript.md#live-L…) references into the transcript
panel and highlights anchored segments there. If storage.Journal.render() changes these shapes, the
reader must change with it."""
import json
import re

from lecture_cli.storage import REVIEW_MARKER, Journal, write_json


def render(tmp_path, finished=True, details=True):
    directory = tmp_path / "session"
    directory.mkdir()
    output = tmp_path / "LectureNotes" / "2026-10-07_090200-课堂笔记-k7m2q9.md"
    output.parent.mkdir()
    write_json(directory / "session.json", {"course": "MATH421", "output": str(output),
                                             "started": "2026-10-07T09:02:00-04:00", "notes_model": "m"})
    records = [{"id": i, "start": f"00:00:{i:02d}.00", "end": f"00:00:{i:02d}.50", "text": f"line {i}"} for i in range(1, 7)]
    (directory / "transcript.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    journal = Journal(directory)
    journal.save(records, "- 随堂 [L1–L2]\n- 待核对：板书 [L3]")
    if details:
        journal.set_info("outline", json.dumps([{"title": "链接数", "question": "怎么定义？", "first": 1, "last": 6}]))
        journal.save_detail(records, f"## 链接数\n\n定义 $x$ [L2–L4]。\n{REVIEW_MARKER}\n- 疑点 [L5]")
        journal.set_info("detail_status", "complete")
    journal.add_warning("离线校正未完成")
    journal.render(finished=finished)
    journal.close()
    return output


def test_main_file_has_the_parts_the_reader_splits(tmp_path):
    output = render(tmp_path)
    main = output.read_text()
    lines = main.splitlines()
    # note.ts parseNote: H1 "课程 · started", quoted header lines, the first "## " starts the body.
    assert re.fullmatch(r"# MATH421 · \d{4}-\d{2}-\d{2}T\d{2}:\d{2}\S*", lines[0])
    head = lines[:next(i for i, line in enumerate(lines) if line.startswith("## "))]
    quoted = [line[2:] for line in head if line.startswith("> ")]
    assert quoted[0].startswith("已结束") and "记录提示：离线校正未完成" in quoted
    # The attachment links stand on lines of their own, which the reader hides.
    attachment = re.compile(r"^\[[^\]]+\]\([^)]*\.(?:transcript|live|review)\.md\)(?:\s*·\s*\[[^\]]+\]\([^)]*\.md\))*\s*$")
    assert sum(bool(attachment.match(line)) for line in lines) == 2
    assert lines[-1].endswith(".review.md)") and attachment.match(lines[-1])
    # Title from the learning path's first topic.
    assert re.search(r"^## 学习路线\n\n- \*\*链接数\*\*：", main, re.M)
    # References are links to the transcript anchor, labelled version-L….
    assert re.search(r"\[live-L2–L4\]\([^)]*\.transcript\.md#live-L2\)", main)
    # 待核对 lines start the paragraph or list item.
    review = output.with_suffix(".review.md").read_text()
    assert review.startswith("# ") and re.search(r"\[live-L5\]\([^)]*\.transcript\.md#live-L5\)", review)


def test_transcript_segments_are_anchored_as_the_panel_expects(tmp_path):
    transcript = render(tmp_path).with_suffix(".transcript.md").read_text()
    # note.ts parseTranscript: "## live" groups, an anchor line, then "### live-L1 · start–end", then text.
    assert re.search(r"^> 正文来源版本：(live|refined)", transcript, re.M)
    segment = re.compile(r'^## live\n\n<a id="live-L1"></a>\n\n### live-L1 · 00:00:01\.00–00:00:01\.50\n\nline 1$', re.M)
    assert segment.search(transcript)
    assert len(re.findall(r'^<a id="live-L\d+"></a>$', transcript, re.M)) == 6


def test_a_note_still_recording_says_so(tmp_path):
    main = render(tmp_path, finished=False, details=False).read_text()
    assert re.search(r"^> 记录中", main, re.M) and "\n## 随堂预览\n" in main
