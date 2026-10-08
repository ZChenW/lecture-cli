import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import time

import numpy as np
import pytest

from lecture_cli import refinement, final_notes
from lecture_cli.worker import APIError
from lecture_cli.storage import ATTACHMENT_DIR, Journal, attachment_path, events, final_events, read_json, write_json


def session(root):
    write_json(root / "session.json", dict(course="MATH421", started="today", notes_model="deepseek-flash",
               output=str(root / "notes.md"), refine=True, language="en"))
    (root / "transcript.jsonl").write_text(json.dumps(dict(id=1, start="00:00:00", end="00:01:01",
                                                         text="24 AC live error")) + "\n")


def test_offline_segments_cover_every_sample_without_overlap_and_include_tail(tmp_path):
    pcm = np.tile(np.arange(100, 500, dtype="<i2"), 2440).tobytes()  # 61 seconds
    path = tmp_path / "audio.pcm"
    path.write_bytes(pcm)
    chunks = list(refinement.segments(path))
    assert b"".join(part[2] for part in chunks) == pcm
    assert chunks[0][0] == 0
    assert chunks[-1][1] == 61
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))
    assert all(0 < end - start <= 30 for start, end, _ in chunks)


def test_archive_limit_falls_back_without_breaking_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(refinement, "MAX_ARCHIVE_BYTES", 8)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(b"a" * 8)
    archive.append(b"b" * 8)
    archive.close()
    assert read_json(tmp_path / "archive.json")["error"]
    assert (tmp_path / "refinement.pcm").read_bytes() == b"a" * 8


def test_archive_manifest_failure_does_not_interrupt_capture_cleanup(tmp_path, monkeypatch):
    archive = refinement.AudioArchive(tmp_path)
    def no_space(*args):
        raise OSError("disk full")
    monkeypatch.setattr(refinement, "write_json", no_space)
    archive.close()
    assert archive.error
    assert archive.file.closed


@pytest.mark.parametrize("records", [[], [{"id": 1}], [{"id": 2, "text": "bad"}]])
def test_invalid_offline_source_falls_back_without_mislabeling(tmp_path, records):
    session(tmp_path)
    write_json(tmp_path / "refinement-state.json", {"complete": True, "count": len(records)})
    (tmp_path / "refined.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    assert final_events(tmp_path) == events(tmp_path)
    journal = Journal(tmp_path)
    journal.render()
    journal.close()
    assert "依据 Qwen" not in (tmp_path / "notes.md").read_text()


def test_corrected_source_reaches_final_notes_and_controller_recovery(tmp_path):
    session(tmp_path)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(np.full(16000 * 31, 100, dtype="<i2").tobytes())
    archive.close()
    assert refinement.refine(tmp_path, lambda pcm: "minus four ac corrected") == 0
    assert "24 AC" in events(tmp_path)[0]["text"]
    assert len(final_events(tmp_path)) == 2
    journal = Journal(tmp_path)
    calls = []
    def api(messages, model, tokens):
        calls.append(messages[-1]["content"])
        raise APIError('offline')
    final_notes.generate(journal, final_events(tmp_path), api)
    journal.preserve_detail_tail()
    journal.render(finished=True)
    assert "24 AC" not in calls[0]
    assert "minus four ac corrected" in calls[0]
    assert "minus four ac corrected" in (tmp_path / ATTACHMENT_DIR / "notes.transcript.md").read_text()
    assert "编号独立" in (tmp_path / "notes.md").read_text()
    journal.close()


def test_failed_late_segment_cannot_publish_partial_replacement(tmp_path):
    session(tmp_path)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(np.full(31 * 16000, 100, dtype="<i2").tobytes())
    archive.close()
    count = 0
    def transcribe(pcm):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("failure")
        return "good first part"
    assert refinement.refine(tmp_path, transcribe) == 1
    assert final_events(tmp_path) == events(tmp_path)
    assert not (tmp_path / "refined.jsonl").exists()


def test_non_silent_empty_result_is_failure(tmp_path):
    session(tmp_path)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(np.full(16000, 1000, dtype="<i2").tobytes())
    archive.close()
    assert refinement.refine(tmp_path, lambda pcm: "") == 1
    assert "24 AC" in final_events(tmp_path)[0]["text"]


@pytest.mark.parametrize("text", [".", "。", " … ", "?!"])
def test_non_silent_punctuation_only_result_is_failure(tmp_path, text):
    # Plan GUI-4 Q1.1: a result without a letter, digit or CJK character counts as no text recognised.
    session(tmp_path)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(np.full(16000, 1000, dtype="<i2").tobytes())
    archive.close()
    assert refinement.refine(tmp_path, lambda pcm: text) == 1
    assert "24 AC" in final_events(tmp_path)[0]["text"]
    assert "有声片段" in read_json(tmp_path / "refinement-state.json")["reason"]


def test_near_silent_punctuation_only_result_is_the_near_silent_placeholder(tmp_path):
    session(tmp_path)
    archive = refinement.AudioArchive(tmp_path)
    archive.append(np.full(16000, 10, dtype="<i2").tobytes())
    archive.close()
    assert refinement.refine(tmp_path, lambda pcm: "。") == 0
    assert [r["text"] for r in final_events(tmp_path)] == ["[近静音片段，未识别到文字]"]


@pytest.mark.parametrize("mode", ["success", "failure", "cancel", "skip", "unobserved"])
def test_real_controller_waits_for_refine_and_cleans_audio(tmp_path, mode, isolated_run_registry):
    fail = mode in ("failure", "cancel", "skip")
    root = Path(__file__).resolve().parents[1]
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text('''
import sys, json, os
from pathlib import Path
from lecture_cli import cli
cli.capture_python = lambda model, *args: sys.executable
if os.environ.get("REFINE_TEST_NO_STATE") and not {"_worker", "_capture", "_refine"} & set(sys.argv):
    original_write_json = cli.write_json
    def write_json(path, value):
        if path.name == "controller-state.json":
            with open(os.environ["REFINE_TEST_NO_STATE"], "a") as log:
                log.write(value["phase"] + "\\n")
            raise OSError(28, "No space left on device")
        original_write_json(path, value)
    cli.write_json = write_json
if "_capture" in sys.argv:
    from lecture_cli import capture
    from lecture_cli.storage import write_json
    def capture_run(directory):
        from lecture_cli.refinement import AudioArchive
        a = AudioArchive(directory)
        a.append(b"\\0" * 32000)
        a.close()
        capture.Transcript(directory).append("LIVE_SOURCE", 0, 1)
        write_json(directory / "asr-state.json", {"status": "转录完成"})
        return 0
    capture.run = capture_run
if "_refine" in sys.argv:
    from lecture_cli import refinement
    def refine(directory):
        assert "DEEPSEEK_API_KEY" not in os.environ
        assert (directory / "refinement.pcm").exists()
        if os.environ.get("REFINE_TEST_CANCEL"):
            import time
            Path(os.environ["REFINE_TEST_CANCEL"]).touch()
            while True:
                time.sleep(0.1)
        if os.environ.get("REFINE_TEST_FAIL"):
            return 1
        from lecture_cli.storage import write_json
        records = [{"id": 1, "start": "00:00:00", "end": "00:00:01", "text": "CORRECTED_SOURCE"}]
        (directory / "refined.jsonl").write_text(json.dumps(records[0]) + "\\n")
        write_json(directory / "refinement-state.json", {"complete": True, "count": 1})
        return 0
    refinement.refine = refine
if "_worker" in sys.argv:
    from lecture_cli import worker
    def complete(messages, model, max_tokens=2000):
        source = "LIVE_SOURCE" if max_tokens == 2000 or os.environ.get("REFINE_TEST_FAIL") else "CORRECTED_SOURCE"
        assert source in messages[-1]["content"]
        if max_tokens == 4000:
            return json.dumps({"continues_previous": False, "topics": [
                {"title": "测试主题", "question": "结论是什么？", "first": 1, "last": 1}]})
        body = source + " [L1]"
        return json.dumps({"body": body, "review": ""}) if max_tokens == 8000 else body
    worker.complete = complete
''')
    courses = tmp_path / "courses"
    (courses / "MATH421").mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=f"{shim}:{root}", XDG_CONFIG_HOME=str(tmp_path / "config"),
               DEEPSEEK_API_KEY="test-not-real")
    if fail:
        env["REFINE_TEST_FAIL"] = "1"
    marker = tmp_path / "refine.started"
    attempts = tmp_path / "phases.txt"
    if mode == "unobserved":
        env["REFINE_TEST_NO_STATE"] = str(attempts)
    if mode in ("cancel", "skip"):
        env["REFINE_TEST_CANCEL"] = str(marker)
    process = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(courses),
                                "start", "MATH421", "--refine"] + (["--headless"] if mode == "skip" else []),
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if mode in ("cancel", "skip"):
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert marker.exists()
        if mode == "cancel":
            process.send_signal(signal.SIGINT)
        elif mode == "skip":
            # An outside observer sees a skippable phase and asks to skip it.
            directory = next(path for path in Path("/tmp").glob(f"lecture-{os.getuid()}-*")
                             if str(courses) in read_json(path / "session.json").get("output", ""))
            state = read_json(directory / "controller-state.json")
            assert state["phase"] == "refining" and state["can_skip"] is True
            assert [stage["name"] for stage in state["stages"]] == ["录制与转录", "离线校正"]
            (directory / "skip-refine").touch()
        stdout, stderr = process.communicate(timeout=25)
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    note_path = next(p for p in (courses / "MATH421" / "LectureNotes").glob("*.md")
                     if not p.stem.endswith((".live", ".review", ".transcript")))
    note = note_path.read_text()
    assert "LIVE_SOURCE" in attachment_path(note_path, 'transcript').read_text()
    skipped = mode in ("cancel", "skip")
    # A user skip is reported as skipped, never as a failure; the two flags exclude each other.
    failed = fail and not skipped
    assert ("CORRECTED_SOURCE" in note) is not fail
    assert (refinement.WARNING in note) is failed and (refinement.SKIPPED in note) is skipped
    if skipped:
        assert "用户跳过" in attachment_path(note_path, 'review').read_text()
    record = json.loads(next(isolated_run_registry.glob("*.json")).read_text())
    assert record["status"] == "done" and record["flags"]["refinement_failed"] is failed
    assert record["flags"]["refinement_skipped"] is skipped
    assert [stage["name"] for stage in record["stages"]] == ["录制与转录", "离线校正", "课后笔记"]
    assert (refinement.WARNING in record["warnings"]) is failed
    assert (refinement.SKIPPED in record["warnings"]) is skipped
    if mode == "unobserved":
        # Every phase, including the refinement wait, tried to publish and failed harmlessly.
        assert {"starting", "recording", "draining", "refining", "finalizing", "saving"} <= \
            set(attempts.read_text().split())
    for directory in Path("/tmp").glob(f"lecture-{os.getuid()}-*"):
        assert str(courses) not in str(read_json(directory / "session.json"))
