"""Plan GUI-3 item 4: review points as a checklist, ticks kept beside the .review.md."""
import json

from lecture_cli import cli
from lecture_cli.gui import library, review
from lecture_cli.storage import ATTACHMENT_DIR, attachment_path, review_state_path
from tests.test_gui_actions import FakeGio, note_with_attachments, trash_with
from tests.test_gui_n2 import REVIEW
from tests.test_gui_server import home, save_config, serve  # noqa: F401 (fixtures)


def test_item_id_is_sha256_prefix_of_normalised_text():
    import hashlib
    assert review.item_id("  a \n\t b  ") == hashlib.sha256("a b".encode()).hexdigest()[:16]
    assert review.item_id("a b") == review.item_id("a  b\n") and len(review.item_id("x")) == 16


def test_parse_lists_paragraphs_and_unsorted_ranges_but_not_headings_or_notices():
    points, notices = review.parse(REVIEW)
    assert points == [
        "讲义 R2 示意图方向有误 [live-L16](x.transcript.md#live-L16)。",
        "右手法则的具体方向未完整出现 [live-L19](x.transcript.md#live-L19)。\n- 子项不单独计数",
        "未确认的转录 [live-L21](x.transcript.md#live-L21)",
        "待整理原文 · [live-L1–L2](x.transcript.md#live-L1)\n\n"
        "> [live-L1](x.transcript.md#live-L1) An eigenvector is nonzero.\n"
        "> [live-L2](x.transcript.md#live-L2) These are the final words.",
        "模型写成段落的疑点也算一处 [L30]。",
    ]
    assert len(notices) == 2 and notices[0].startswith("## 处理提示") and "原因：Qwen 环境未就绪" in notices[1]
    assert library.review_count(REVIEW) == 5
    # Numbered lists, one block of several items, paragraphs split on blank lines, headings skipped.
    text = "# 待核对与处理记录\n\n## 小节\n\n1. 第一处\n2) 第二处\n   续行\n\n第一段\n第一段续\n\n第二段\n"
    assert review.parse(text) == (["第一处", "第二处\n续行", "第一段\n第一段续", "第二段"], [])
    # Lines before the first marker are a point of their own; a quote outside a range is not.
    assert review.parse("说明\n- 甲\n- 乙\n\n> 引文\n")[0] == ["说明", "甲", "乙"]
    assert review.parse("") == ([], []) and review.parse("# 待核对与处理记录\n\n## 处理提示\n\n已跳过。\n")[0] == []


def test_sources_keep_order_version_and_ranges():
    text = "甲 [refined-L18–L19](a.transcript.md#refined-L18) 乙 [L30] 丙 [live-L16](a.transcript.md#live-L16) [L30]"
    assert review.sources_of(text) == [
        {"version": "refined", "first": 18, "last": 19, "label": "L18–L19"},
        {"version": None, "first": 30, "last": 30, "label": "L30"},
        {"version": "live", "first": 16, "last": 16, "label": "L16"}]


def setup(home, serve, layout="new"):
    app = serve()
    app.login()
    save_config(home)
    note = note_with_attachments(home / "MATH421", layout=layout)
    target = attachment_path(note, "review") if layout == "new" else note.with_suffix(".review.md")
    target.write_text("# 待核对与处理记录\n\n- 第一处 [live-L16](x.transcript.md#live-L16)\n- 第二处\n\n第三处是段落。\n")
    return app, note, target


def get(app, note):
    return app.client.get("/api/notes/review", params={"path": str(note)})


def put(app, note, checked, **extra):
    return app.client.put("/api/notes/review", params={"path": str(note)}, json={"checked": checked, **extra},
                          headers=app.origin)


def test_ticks_persist_in_a_state_file_and_never_touch_the_review(home, serve):
    app, note, target = setup(home, serve)
    before = target.read_bytes()
    listed = get(app, note).json()
    assert [item["text"] for item in listed["items"]] == [
        "第一处 [live-L16](x.transcript.md#live-L16)", "第二处", "第三处是段落。"]
    assert listed["items"][0]["sources"] == [{"version": "live", "first": 16, "last": 16, "label": "L16"}]
    assert not any(item["checked"] for item in listed["items"]) and listed["notices"] == []
    ids = [item["id"] for item in listed["items"]]
    response = put(app, note, [ids[2], ids[0]])
    assert response.status_code == 200, response.text
    assert [item["checked"] for item in response.json()["items"]] == [True, False, True]
    state = review_state_path(target)
    assert state == target.parent / f"{note.stem}.review-state.json"
    assert json.loads(state.read_text()) == {"version": 1, "checked": [ids[0], ids[2]]}
    assert target.read_bytes() == before
    assert [item["checked"] for item in get(app, note).json()["items"]] == [True, False, True]
    # The notes list counts what is still unticked and the total.
    entry = app.client.get("/api/notes", params={"course": "MATH421"}).json()[0]
    assert (entry["review_count"], entry["review_total"]) == (1, 3)
    assert put(app, note, ids).status_code == 200
    entry = app.client.get("/api/notes", params={"course": "MATH421"}).json()[0]
    assert (entry["review_count"], entry["review_total"]) == (0, 3)
    assert put(app, note, []).status_code == 200 and json.loads(state.read_text())["checked"] == []


def test_a_rewritten_review_keeps_unchanged_ticks_and_drops_vanished_ones(home, serve):
    app, note, target = setup(home, serve)
    ids = [item["id"] for item in get(app, note).json()["items"]]
    put(app, note, ids[:2])
    target.write_text("# 待核对与处理记录\n\n- 第二处\n- 新的一处\n\n第三处是段落。\n")
    items = get(app, note).json()["items"]
    assert [(item["text"], item["checked"]) for item in items] == [("第二处", True), ("新的一处", False),
                                                                   ("第三处是段落。", False)]
    # The vanished id can no longer be ticked.
    assert put(app, note, [ids[0]]).status_code == 422
    entry = app.client.get("/api/notes", params={"course": "MATH421"}).json()[0]
    assert (entry["review_count"], entry["review_total"]) == (2, 3)


def test_invalid_ids_bodies_and_paths_are_refused(home, serve, tmp_path):
    app, note, target = setup(home, serve)
    ids = [item["id"] for item in get(app, note).json()["items"]]
    for checked in (["0123456789abcdef"], [ids[0], "x"], "abc", [1], None):
        response = put(app, note, checked)
        assert response.status_code == 422, checked
    assert not review_state_path(target).exists()
    outside = tmp_path / "outside" / "LectureNotes"
    outside.mkdir(parents=True)
    (outside / "x.md").write_text("# x")
    (outside / "x.review.md").write_text("- secret")
    for path in (str(outside / "x.md"), "MATH421/../../outside/LectureNotes/x.md"):
        assert app.client.get("/api/notes/review", params={"path": path}).status_code == 403
        assert app.client.put("/api/notes/review", params={"path": path}, json={"checked": []},
                              headers=app.origin).status_code == 403
    assert not (outside / "x.review-state.json").exists()
    # Attachments are not notes; a note without review points has nothing to tick.
    assert app.client.get("/api/notes/review", params={"path": str(target)}).status_code == 404
    bare = note.with_name("2026-10-08_090000-课堂笔记-0f0f0f.md")
    bare.write_text("# bare")
    assert get(app, bare).status_code == 404
    # Writing needs the page's Origin, as every other change does.
    assert app.client.put("/api/notes/review", params={"path": str(note)}, json={"checked": []}).status_code == 403
    # A linked state file is never followed.
    secret = tmp_path / "elsewhere.json"
    secret.write_text(json.dumps({"version": 1, "checked": ids}))
    review_state_path(target).symlink_to(secret)
    assert not any(item["checked"] for item in get(app, note).json()["items"])
    assert put(app, note, ids[:1]).status_code == 403 and json.loads(secret.read_text())["checked"] == ids


def test_legacy_flat_layout_keeps_the_state_beside_its_review(home, serve):
    app, note, target = setup(home, serve, layout="old")
    ids = [item["id"] for item in get(app, note).json()["items"]]
    assert put(app, note, ids[1:2]).status_code == 200
    assert json.loads((note.parent / f"{note.stem}.review-state.json").read_text())["checked"] == ids[1:2]
    assert not (note.parent / ATTACHMENT_DIR / f"{note.stem}.review-state.json").exists()
    names = [entry["name"] for entry in app.client.get("/api/notes", params={"course": "MATH421"}).json()]
    assert names == [note.name]  # The state file is not listed as a note.


def test_deleting_a_note_trashes_its_state_file_in_either_layout(home, serve):
    gio = FakeGio()
    app = serve(trash=trash_with(gio))
    app.login()
    save_config(home)
    for layout, stem in (("new", "2026-10-07_143000-课堂笔记-a1b2c3"), ("old", "2026-09-01_090000-课堂笔记-eeeeee")):
        note = note_with_attachments(home / "MATH421", stem, layout=layout)
        review_file = attachment_path(note, "review") if layout == "new" else note.with_suffix(".review.md")
        state = review_state_path(review_file)
        state.write_text('{"version": 1, "checked": []}')
        response = app.client.delete("/api/notes", params={"path": str(note)}, headers=app.origin)
        assert response.status_code == 200, response.text
        assert gio.calls[-1][-2:] == [str(state), str(note)] and not state.exists()


def test_discard_removes_the_state_file_of_this_run_only(tmp_path):
    folder = tmp_path / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True)
    output = folder / "2026-10-07_100000-课堂笔记-abc123.md"
    other = folder / "2026-10-07_100000-课堂笔记-abc124.md"
    mine = [output, attachment_path(output, "review"), review_state_path(attachment_path(output, "review"))]
    kept = [review_state_path(attachment_path(other, "review")), folder / f"{output.stem}.review-state.json"]
    for path in mine + kept:
        path.write_text("x")
    assert cli.discard_outputs(output) == []
    assert not any(path.exists() for path in mine) and all(path.exists() for path in kept)
