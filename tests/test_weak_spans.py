"""Plan GUI-4 Q3: weak stretches found after class are marked for review, not silently written into the notes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from lecture_cli import api_capture, cli, final_notes, weak_spans, worker
from lecture_cli.gui import review as checklist
from lecture_cli.gui import sessions
from lecture_cli.input_level import WeakInput
from lecture_cli.storage import ATTACHMENT_DIR, Journal, attachment_path, events, final_events, read_json, write_json
from lecture_cli.worker import APIError
from test_lifecycle import own_session, stub_runtime, wait_until  # noqa: F401 (fixture)
from test_weak_input import tone, wav

PREFIX = weak_spans.PREFIX


def stamp(seconds):
    return f"{int(seconds) // 3600:02}:{int(seconds) // 60 % 60:02}:{seconds % 60:05.2f}"


def seg(i, start, end, text):
    return {"id": i, "start": stamp(start), "end": stamp(end), "text": text}


def windows(*parts):
    """parts: (count, dBFS) runs of 10 s windows, back to back from 0."""
    levels, t = [], 0.0
    for count, level in parts:
        for _ in range(count):
            levels.append({"start": t, "end": t + 10, "rms_dbfs": level})
            t += 10
    return levels


def every(start, end, text, step=10, first_id=1):
    return [seg(first_id + i, t, t + step - 1, f"{text}{i + 1}。")
            for i, t in enumerate(range(int(start), int(end), step))]


def workspace(tmp_path, levels, live, refined=None, captured=None):
    write_json(tmp_path / "session.json", dict(course="MATH421", started="2026-10-08T10:00:00-04:00",
               notes_model="m", interval=1, output=str(tmp_path / "notes.md"), refine=refined is not None, language="zh"))
    (tmp_path / "levels.jsonl").write_text("".join(json.dumps(w) + "\n" for w in levels))
    (tmp_path / "transcript.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in live))
    if refined is not None:
        (tmp_path / "refined.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in refined))
        write_json(tmp_path / "refinement-state.json", {"complete": True, "count": len(refined)})
    if captured is not None:
        write_json(tmp_path / "asr-state.json", {"captured": captured})
    return tmp_path


# --- Q3.3 the rules --------------------------------------------------------------------------------

def test_reference_is_the_80th_percentile_of_windows_with_live_text():
    levels = windows((4, -20), (5, -30), (3, -60))
    live = every(0, 90, "讲")  # text in the first nine windows only
    reference, spans = weak_spans.find_spans(levels, live, None)
    assert reference == -20.0  # the median of four at -20 and five at -30 would be -30
    assert spans == []  # the -60 windows have no text in them
    assert weak_spans.percentile([1, 2, 3, 4, 5], 80) == pytest.approx(4.2)
    assert weak_spans.percentile([7], 80) == 7


def test_absolute_floor_marks_windows_with_text_below_minus_40_dbfs():
    # A quiet lecture: reference -40.1, so the relative rule would need -55.1 and marks nothing.
    levels = windows((6, -45), (2, -40.0), (2, -40.1), (2, -45))
    live = every(0, 120, "讲")
    reference, spans = weak_spans.find_spans(levels, live, None)
    assert reference == -40.1
    # -40.0 is not below the floor; every window from 80 s on is, and each holds text.
    assert spans == [{"start": 0.0, "end": 60.0}, {"start": 80.0, "end": 120.0}]
    # Without text in a window the floor does not apply to it.
    assert weak_spans.find_spans(windows((6, -20), (3, -45)), every(0, 60, "讲"), None)[1] == []
    # Fewer than six reference windows: no relative rule, but the floor still marks text.
    short = windows((3, -20), (3, -50))
    live = every(0, 20, "讲") + every(30, 60, "弱", first_id=3)  # five windows with text
    assert weak_spans.find_spans(short, live, None) == (None, [{"start": 30.0, "end": 60.0}])


def test_fewer_than_six_reference_windows_give_no_judgement():
    levels = windows((5, -20), (4, -60))
    live = every(0, 50, "讲") + every(50, 90, "弱", first_id=6)
    live = [r for r in live if weak_spans.interval(r)[0] < 50]  # text only in the five normal windows
    assert weak_spans.find_spans(levels, live + every(50, 90, "弱", first_id=6), None) != (None, [])
    assert weak_spans.find_spans(levels, live, None) == (None, [])


def test_weak_is_15_db_or_more_below_the_reference():
    levels = windows((6, -20), (2, -34.9), (1, -20), (2, -35))
    live = every(0, 110, "讲")
    _, spans = weak_spans.find_spans(levels, live, None)
    assert spans == [{"start": 90.0, "end": 110.0}]


def test_neighbouring_weak_windows_merge_and_short_spans_are_dropped():
    levels = windows((6, -20), (1, -50), (1, -20), (3, -50), (1, -20), (2, -45), (1, -20))
    live = every(0, 150, "讲")
    _, spans = weak_spans.find_spans(levels, live, None)
    # 60–70 is one window (10 s, dropped); 80–110 merges three; 120–140 is exactly 20 s.
    assert spans == [{"start": 80.0, "end": 110.0}, {"start": 120.0, "end": 140.0}]


def test_only_spans_with_text_in_either_version_are_kept():
    levels = windows((6, -20), (3, -55), (1, -20), (3, -55), (1, -20), (3, -55))
    live = every(0, 60, "讲") + every(90, 100, "讲", first_id=7) + every(130, 140, "讲", first_id=8)
    refined = [seg(1, 0, 60, "正常。"), seg(2, 60, 85, "校正写出的内容。"), seg(3, 90, 99, "正常。")]
    _, spans = weak_spans.find_spans(levels, live, refined)
    # 60–90: only the refined version has text there; 100–130: nothing at all; 140–170: nothing.
    assert spans == [{"start": 60.0, "end": 90.0}]


def test_punctuation_and_markers_alone_are_not_text():
    levels = windows((6, -20), (3, -55))
    live = every(0, 60, "讲") + [seg(7, 60, 70, "."), seg(8, 70, 80, "[近静音片段，未识别到文字]")]
    assert weak_spans.find_spans(levels, live, None)[1] == []


def test_levels_file_tolerates_a_cut_line_and_bad_values(tmp_path):
    (tmp_path / "levels.jsonl").write_text('{"start": 0, "end": 10, "rms_dbfs": -20}\n'
                                           '{"start": 10, "end": 20, "rms_dbfs": null}\n'
                                           '{"start": 20, "end": 20, "rms_dbfs": -20}\n'
                                           '{"start": 20, "end": 30, "rms_d')
    assert weak_spans.load_levels(tmp_path) == [{"start": 0, "end": 10, "rms_dbfs": -20}]
    assert weak_spans.load_levels(tmp_path / "missing") == []


# --- Q3.4 marking, review, hints, prompt -----------------------------------------------------------

def weak_middle(tmp_path, refined=True):
    """Two normal minutes, a weak minute in which both versions wrote something, a normal minute."""
    levels = windows((12, -22), (6, -52), (6, -22))
    live = every(0, 120, "正常") + [seg(13, 125, 135, "在这严重监督下"), seg(14, 150, 160, "羼水也很为难")] \
        + every(180, 240, "后来", first_id=15)
    ref = [seg(1, 0, 120, "前两分钟。"), seg(2, 120, 180, "在这一条件下，产值很低。"), seg(3, 180, 240, "最后一分钟。")]
    return workspace(tmp_path, levels, live, ref if refined else None, captured=240)


def test_marked_segments_are_prefixed_wherever_the_final_version_is_used(tmp_path):
    directory = weak_middle(tmp_path)
    assert weak_spans.analyse(directory) == ["2:00–3:00 收音很弱，相关内容已标为待核对。"]
    assert read_json(directory / "weak-spans.json")["spans"] == [{"start": 120.0, "end": 180.0}]
    texts = [r["text"] for r in final_events(directory)]
    assert texts == ["前两分钟。", PREFIX + "在这一条件下，产值很低。", "最后一分钟。"]
    # The transcript files themselves are never changed; marking twice adds nothing.
    assert "收音很弱" not in (directory / "refined.jsonl").read_text() + (directory / "transcript.jsonl").read_text()
    assert weak_spans.mark(final_events(directory), weak_spans.load(directory)) == final_events(directory)


def test_review_entry_gives_both_versions_when_the_refined_one_is_used(tmp_path):
    directory = weak_middle(tmp_path)
    journal = Journal(directory)
    for hint in weak_spans.analyse(directory):
        journal.add_warning(hint)
    journal.render(finished=True)
    journal.close()
    review = attachment_path(Path(directory / "notes.md"), "review").read_text()
    assert ("- 这一段收音很弱，待核对 [refined-L2](notes.transcript.md#refined-L2)："
            "校正版本：在这一条件下，产值很低。／实时版本：在这严重监督下 羼水也很为难") in review
    points, notices = checklist.parse(review)
    assert len(points) == 1 and "校正版本" in points[0]
    assert notices == ["## 处理提示\n\n2:00–3:00 收音很弱，相关内容已标为待核对。"]
    assert "> 记录提示：2:00–3:00 收音很弱，相关内容已标为待核对。" in (directory / "notes.md").read_text()
    # The transcript attachment keeps both versions unmarked.
    transcript = (directory / ATTACHMENT_DIR / "notes.transcript.md").read_text()
    assert "在这一条件下，产值很低。" in transcript and "在这严重监督下" in transcript and "收音很弱" not in transcript


def test_review_entry_with_the_live_version_only(tmp_path):
    directory = weak_middle(tmp_path, refined=False)
    weak_spans.analyse(directory)
    journal = Journal(directory)
    journal.render(finished=True)
    journal.close()
    review = attachment_path(Path(directory / "notes.md"), "review").read_text()
    assert "- 这一段收音很弱，待核对 [live-L13](notes.transcript.md#live-L13)：实时版本：在这严重监督下" in review
    assert "- 这一段收音很弱，待核对 [live-L14](notes.transcript.md#live-L14)：实时版本：羼水也很为难" in review
    assert "校正版本" not in review and review.count("收音很弱，待核对") == 2


@pytest.mark.parametrize("live_text,expected", [("在这一条件下，产值很低。", "校正版本与实时版本相同：在这一条件下，产值很低。"),
                                                (None, "校正版本：在这一条件下，产值很低。")])
def test_one_text_when_the_versions_agree_or_live_has_none(live_text, expected):
    record = seg(2, 120, 180, PREFIX + "在这一条件下，产值很低。")
    live = [seg(1, 125, 135, live_text)] if live_text else []
    assert weak_spans.review_line(record, "refined", live) == f"- 这一段收音很弱，待核对 [L2]：{expected}"


def test_final_notes_receive_the_prefix_and_the_prompt_says_what_to_do(tmp_path):
    directory = weak_middle(tmp_path)
    weak_spans.analyse(directory)
    assert "标有“这一段收音很弱”的片段只转述能确定的部分，不展开细节，并在正文里注明这一段收音很弱。" in final_notes.FINAL_SYSTEM
    journal = Journal(directory)
    calls = []
    def api(messages, model, tokens):
        calls.append(messages)
        raise APIError("offline")
    final_notes.generate(journal, final_events(directory), api)
    journal.close()
    assert PREFIX + "在这一条件下，产值很低。" in calls[0][-1]["content"]


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (754.9, "12:34"), (3754, "1:02:34")])
def test_clock_text(seconds, text):
    assert weak_spans.clock_text(seconds) == text


def test_hints_list_every_span():
    spans = [{"start": 750, "end": 850}, {"start": 1860, "end": 1900}]
    assert weak_spans.spans_hint(spans) == "12:30–14:10、31:00–31:40 收音很弱，相关内容已标为待核对。"
    assert weak_spans.spans_hint([]) == weak_spans.gaps_hint([]) == ""


# --- Q3.5 long stretches without text --------------------------------------------------------------

def test_three_minutes_without_content_are_listed_but_not_points_to_check(tmp_path):
    live = every(0, 1080, "讲") + every(1470, 1800, "讲", first_id=200)
    directory = workspace(tmp_path, windows((180, -22)), live, captured=1800)
    hints = weak_spans.analyse(directory)
    # The last segment before the gap ends at 17:59 (1079 s).
    assert hints == ["17:59–24:30 没有识别出内容（可能是课间，也可能没有收到声音）。"]
    journal = Journal(directory)
    for hint in hints:
        journal.add_warning(hint)
    journal.render(finished=True)
    journal.close()
    review = attachment_path(Path(directory / "notes.md"), "review").read_text()
    points, notices = checklist.parse(review)
    assert points == [] and "17:59–24:30 没有识别出内容" in notices[0]
    assert all(PREFIX not in r["text"] for r in final_events(directory))


def test_gap_rule_uses_both_versions_and_the_edges_of_the_recording():
    live = every(200, 260, "讲")
    refined = [seg(1, 400, 420, "校正版本这里有字。")]
    gaps = weak_spans.find_gaps(live, refined, 800)
    # 0–200 is 200 s; 259–400 is under 3 minutes; 420–800 is 380 s.
    assert gaps == [{"start": 0.0, "end": 200.0}, {"start": 420.0, "end": 800}]
    assert weak_spans.find_gaps(live, refined, 590) == [{"start": 0.0, "end": 200.0}]


# --- incident 2 (plan section 1), as fixtures --------------------------------------------------------

def incident(tmp_path, variant):
    """The read-aloud of incident 2 at lecture scale: weak, normal, weak, a minute each (the plan's
    six-window minimum cannot be met by the original 20 s parts; see the Q3 report).

    -40 dB: live recognised nothing in the weak parts; refinement rewrote the last part into text
    that is not in the original. -30 dB: live caught fragments in the first weak part; refinement
    dropped that part entirely. "-30 whisper": the reviewer's run (GUI-4 fix) — live Whisper
    transcribed both weak parts throughout, so weak windows with text are two thirds of the
    reference windows and their median is the weak level itself."""
    weak = {"-40": -58.0, "-30": -49.0, "-30 whisper": -53.0}[variant]
    levels = windows((6, weak), (6, -23), (6, weak))
    normal = every(60, 120, "正常朗读", step=5, first_id=1)
    if variant == "-30 whisper":
        live = every(0, 180, "朗读", step=10)
        refined = [seg(1, 0, 58, "开头一段校正。"), seg(2, 60, 118, "中间正常朗读的校正。"), seg(3, 120, 178, "最后一段校正。")]
    elif variant == "-40":
        live = normal
        refined = [seg(1, 2, 58, "开头一段校正。"), seg(2, 60, 120, "中间正常朗读的校正。"),
                   seg(3, 121, 178, "在这一条件下，产值很低……便改与专管工钱的李寡妇了。")]
    else:
        live = [seg(100 + i, 3 + 4 * i, 5 + 4 * i, f"片段{i}") for i in range(12)] + normal
        live = sorted(live, key=lambda r: weak_spans.clock(r["start"]))
        live = [{**r, "id": i} for i, r in enumerate(live, 1)]
        refined = [seg(1, 60, 120, "中间正常朗读的校正。"), seg(2, 125, 175, "最后一段的校正。")]
    return workspace(tmp_path, levels, live, refined, captured=180)


@pytest.mark.parametrize("variant", ["-40", "-30", "-30 whisper"])
def test_incident_two_marks_both_weak_parts_and_not_the_middle(tmp_path, variant):
    directory = incident(tmp_path, variant)
    hints = weak_spans.analyse(directory)
    assert read_json(directory / "weak-spans.json")["spans"] == [{"start": 0.0, "end": 60.0}, {"start": 120.0, "end": 180.0}]
    assert hints == ["0:00–1:00、2:00–3:00 收音很弱，相关内容已标为待核对。"]
    final = final_events(directory)
    middle = [r for r in final if weak_spans.interval(r)[0] >= 60 and weak_spans.interval(r)[1] <= 120]
    assert middle and all(PREFIX not in r["text"] for r in middle)
    assert all(r["text"].startswith(PREFIX) for r in final if r not in middle)
    # The rewritten ending of the -40 dB run is marked and listed for review.
    journal = Journal(directory)
    journal.render(finished=True)
    journal.close()
    review = attachment_path(Path(directory / "notes.md"), "review").read_text()
    assert review.count("这一段收音很弱，待核对") == len(final) - len(middle)


def test_the_reviewers_case_needs_the_new_rules(tmp_path):
    """3 min, weak/normal/weak at -30 dB, all transcribed live: the old median rule marked nothing.
    Each new rule marks both weak parts on its own."""
    directory = incident(tmp_path, "-30 whisper")
    levels = weak_spans.load_levels(directory)
    live = events(directory)
    reference = [w["rms_dbfs"] for w in levels]  # every window holds live text here
    assert sum(r == -53.0 for r in reference) == 12 and len(reference) == 18
    assert weak_spans.percentile(reference, 50) == -53.0  # the old reference: nothing is 15 dB below it
    assert weak_spans.find_spans(levels, live, None)[0] == -23.0
    expected = [{"start": 0.0, "end": 60.0}, {"start": 120.0, "end": 180.0}]
    louder = [{**w, "rms_dbfs": w["rms_dbfs"] + 20} for w in levels]  # -33 / -3: above the floor
    assert weak_spans.find_spans(louder, live, None) == (-3.0, expected)  # the percentile alone
    quieter = [{**w, "rms_dbfs": -45.0 if w["rms_dbfs"] == -23.0 else -53.0} for w in levels]
    assert weak_spans.find_spans(quieter, live, None) == (-45.0, [{"start": 0.0, "end": 180.0}])  # the floor alone


def test_weak_parts_beyond_a_fifth_of_the_reference_are_left_to_the_floor():
    """The percentile keeps the normal level only while normal windows are at least a fifth of the
    windows with live text; past that, only the -40 dBFS floor can still mark the weak parts."""
    live = every(0, 360, "讲")
    above_floor = windows((30, -38), (6, -8))  # five weak minutes at -38, one normal at -8
    assert weak_spans.find_spans(above_floor, live, None) == (-38.0, [])
    below_floor = windows((30, -48), (6, -18))
    assert weak_spans.find_spans(below_floor, live, None)[1] == [{"start": 0.0, "end": 300.0}]


def test_incident_two_at_its_original_length_is_marked_by_the_floor_only(tmp_path):
    """60 s: two normal windows only, below the minimum of six, so there is no reference. The floor
    still marks the weak part that holds text (the rewritten ending); the opening weak part has no
    text in either version and is not marked."""
    levels = windows((2, -58), (2, -23), (2, -58))
    directory = workspace(tmp_path, levels, every(20, 40, "正常", step=5),
                          [seg(1, 41, 59, "便改与专管工钱的李寡妇了。")], captured=60)
    assert weak_spans.analyse(directory) == ["0:40–1:00 收音很弱，相关内容已标为待核对。"]
    assert read_json(directory / "weak-spans.json") == {"reference_dbfs": None,
                                                         "spans": [{"start": 40.0, "end": 60.0}], "gaps": []}


# --- worker and controller hand-off -----------------------------------------------------------------

def test_worker_analyses_before_the_final_request(tmp_path, monkeypatch):
    directory = weak_middle(tmp_path)
    (directory / "capture.done").touch()
    seen = []
    def generate(journal, records, workers):
        seen.append([r["text"] for r in records])
        assert "2:00–3:00 收音很弱" in dict(journal.db.execute("SELECT key, value FROM info"))["warning"]
    monkeypatch.setattr(final_notes, "generate", generate)
    monkeypatch.setattr(worker, "process_batch", lambda journal, records, call=None, *, batch=None: journal.save(batch, "- 笔记"))
    assert worker.run(directory) == 0
    assert seen == [["前两分钟。", PREFIX + "在这一条件下，产值很低。", "最后一分钟。"]]


def test_empty_recording_is_not_analysed(tmp_path, monkeypatch):
    directory = workspace(tmp_path, windows((6, -60)), [seg(1, 0, 1, ".")], captured=60)
    (directory / "capture.done").touch()
    monkeypatch.setattr(worker, "process_batch", lambda journal, records, call=None, *, batch=None: journal.save(batch, "- 笔记"))
    assert worker.run(directory) == 0
    assert not (directory / "weak-spans.json").exists()


SHIM = '''
if "_capture" in sys.argv and os.environ.get("LECTURE_TEST_WEAK_SPANS"):
    from lecture_cli import capture
    from lecture_cli.storage import write_json
    def run(directory):
        transcript = capture.Transcript(directory)
        transcript.append("An eigenvector is nonzero.", 0, 1)
        write_json(directory / "asr-state.json", {"status": "test-ready", "count": 1})
        (directory / "test-ready").touch()
        while not (directory / "stop").exists():
            time.sleep(0.05)
        for i in range(2, 13):
            transcript.append(f"Normal words {i}.", (i - 1) * 10, (i - 1) * 10 + 9)
        transcript.append("Weak words that came out.", 130, 139)
        transcript.append("Last normal words.", 185, 189)
        with open(directory / "levels.jsonl", "w") as f:
            for i in range(19):
                f.write(json.dumps({"start": i * 10, "end": i * 10 + 10, "rms_dbfs": -52 if 12 <= i < 18 else -22}) + "\\n")
        write_json(directory / "asr-state.json", {"status": "转录完成", "count": 14, "captured": 190,
                                                  "weak_input": "收到的声音很弱", "weak_input_seconds": 300})
        return 0
    capture.run = run
'''


def test_controller_lists_the_spans_instead_of_the_total(stub_runtime, isolated_run_registry):
    root, env = stub_runtime
    shim = Path(env["PYTHONPATH"].split(":")[0]) / "sitecustomize.py"
    shim.write_text(shim.read_text() + SHIM)
    env["LECTURE_TEST_WEAK_SPANS"] = "1"
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421", "--headless", "--interval", "1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        directory = wait_until(lambda: own_session(root))
        (directory / "stop").touch()
        output = proc.communicate(timeout=30)[0]
        assert proc.returncode == 0, output
        record = next(json.loads(p.read_text()) for p in isolated_run_registry.glob("*.json"))
        note = Path(record["output"])
        review = attachment_path(note, "review").read_text()
        assert "> 记录提示：2:00–3:00 收音很弱，相关内容已标为待核对。" in note.read_text()
        assert "累计约" not in note.read_text() + review
        assert "- 这一段收音很弱，待核对 [live-L13]" in review
        assert not directory.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_recovery_keeps_the_spans_instead_of_the_total(tmp_path, isolated_run_registry):
    import tempfile
    import uuid
    from lecture_cli import runs
    root = tmp_path / "courses"
    folder = root / "MATH421" / "LectureNotes"
    folder.mkdir(parents=True)
    directory = Path(tempfile.mkdtemp(prefix=f"lecture-{os.getuid()}-", dir="/tmp"))
    try:
        output = folder / f"2026-10-08_100000-课堂笔记-{uuid.uuid4().hex[:6]}.md"
        weak_middle(directory, refined=False)
        meta = {**read_json(directory / "session.json"), "app": "lecture-cli-v1", "output": str(output),
                "children": [], "refine": False}
        write_json(directory / "session.json", meta)
        write_json(directory / "controller-state.json", {"phase": "finalizing"})
        write_json(directory / "asr-state.json", {"captured": 240, "weak_input_seconds": 300})
        weak_spans.analyse(directory)
        runs.begin({**meta, "controller_pid": 999999}, directory)
        cli.reap_stale_sessions()
        text = output.read_text()
        assert "异常中断" in text and "2:00–3:00 收音很弱，相关内容已标为待核对。" in text and "累计约" not in text
    finally:
        if directory.exists():
            import shutil
            shutil.rmtree(directory)


# --- Q3.2 levels.jsonl and Q3.1 last text -----------------------------------------------------------

def test_levels_are_written_per_window_on_the_transcript_clock(tmp_path):
    level = WeakInput()
    level.levels_path = tmp_path / "levels.jsonl"
    level.add(tone(25, -30), 0)
    level.pause()  # drops 5 s; that audio was fed to recognition, so the clock keeps it
    level.add(tone(10, -50), 0)
    lines = [json.loads(line) for line in (tmp_path / "levels.jsonl").read_text().splitlines()]
    assert [(l["start"], l["end"]) for l in lines] == [(0, 10), (10, 20), (25, 35)]
    assert lines[0]["rms_dbfs"] == pytest.approx(-30, abs=0.1) and lines[2]["rms_dbfs"] == pytest.approx(-50, abs=0.1)
    level.add(tone(10, None), 0)
    assert json.loads((tmp_path / "levels.jsonl").read_text().splitlines()[-1])["rms_dbfs"] == -120.0


def test_a_failed_levels_write_never_stops_the_recording(tmp_path):
    level = WeakInput()
    level.levels_path = tmp_path / "missing-dir" / "levels.jsonl"
    level.add(tone(30, -50), 3)  # no exception
    assert not level.levels_path.exists()


def test_cloud_backend_writes_levels_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    path = wav(tmp_path, tone(41, -30))
    write_json(tmp_path / "session.json", dict(asr_backend="api", asr_api_base="https://example.invalid/v1",
               asr_api_model="test", language="zh", fast=True, audio_file=str(path)))
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"segments": [dict(start=0, end=1, text="老师在讲话。")]})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    lines = [json.loads(line) for line in (tmp_path / "levels.jsonl").read_text().splitlines()]
    assert [(l["start"], l["end"]) for l in lines] == [(0, 10), (10, 20), (20, 30), (30, 40)]


def test_snapshot_last_text_seconds_skips_segments_without_text(tmp_path):
    write_json(tmp_path / "session.json", {"course": "MATH421"})
    cache = sessions.SnapshotCache()
    assert sessions.snapshot(tmp_path, cache)["transcript"]["last_text_seconds"] is None
    with open(tmp_path / "transcript.jsonl", "w") as f:
        for record in (seg(1, 0, 4.5, "讲了一句。"), seg(2, 10, 12, "。"), seg(3, 20, 25, "[近静音片段，未识别到文字]")):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    assert sessions.snapshot(tmp_path, cache)["transcript"]["last_text_seconds"] == 4.5
    with open(tmp_path / "transcript.jsonl", "a") as f:
        f.write(json.dumps(seg(4, 30, 42.2, "[疑似重复，待核对] 又一句。"), ensure_ascii=False) + "\n")
    assert sessions.snapshot(tmp_path, cache)["transcript"]["last_text_seconds"] == 42.2
