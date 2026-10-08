"""Plan GUI-4 Q1.1–Q1.3: punctuation is not text, and a recording that recognised nothing says so."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, capture, worker
from lecture_cli.capture import Transcript
from lecture_cli.gui import library
from lecture_cli.input_level import NO_SPEECH
from lecture_cli.storage import (EMPTY_CAUSES, EMPTY_TITLE, Journal, events, has_content, no_content, read_json,
                                 write_json)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, expected", [
    (".", False), ("。", False), ("…", False), ("?!", False), ("  ", False), ("-—", False), ("（）", False),
    ("a", True), ("3", True), ("好", True), ("x.", True), ("Ω", True), ("３", True), ("。好", True)])
def test_content_text_needs_a_letter_digit_or_cjk_character(text, expected):
    assert has_content(text) is expected


def test_live_segments_without_content_are_dropped_and_take_no_number(tmp_path):
    transcript = Transcript(tmp_path)
    for index, text in enumerate([".", "Hello.", "。", " … ", "World"]):
        transcript.append(text, index, index + 1)
    assert [(r["id"], r["text"]) for r in events(tmp_path)] == [(1, "Hello."), (2, "World")]
    assert transcript.count == 2 and transcript.last == "World"


def test_cloud_live_drops_punctuation_and_keeps_the_repetition_marker(tmp_path):
    transcript = Transcript(tmp_path)
    api_capture.consume_response(transcript, {"segments": [
        dict(start=0, end=1, text="."),
        dict(start=1, end=2, text="。。。", compression_ratio=3.0),  # never "[疑似重复，待核对] 。。。"
        dict(start=2, end=3, text="repeat repeat", compression_ratio=2.5),
        dict(start=3, end=4, text="end."),
    ]}, 0, 4)
    api_capture.consume_response(transcript, {"text": "."}, 4, 30)
    assert [r["text"] for r in events(tmp_path)] == ["[疑似重复，待核对] repeat repeat end."]
    assert events(tmp_path)[0]["start"] == "00:00:02.00"


def tone(seconds, dbfs):
    t = np.arange(int(seconds * 16000)) / 16000
    return (10 ** (dbfs / 20) * np.sqrt(2) * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()


def wav(tmp_path, pcm):
    path = tmp_path / "input.wav"
    path.write_bytes(api_capture.wav_audio(pcm))
    return path


@pytest.mark.parametrize("buffer, kept", [("。", []), ("…?", []), ("还没说完", ["[未确认尾部，待核对] 还没说完"])])
def test_local_unconfirmed_tail_needs_content(tmp_path, monkeypatch, buffer, kept):
    path = wav(tmp_path, tone(2, -20))
    write_json(tmp_path / "session.json", dict(asr_model="base", language="zh", fast=True, audio_file=str(path)))
    fronts = asyncio.Queue()

    class Processor:
        def __init__(self, **kwargs):
            self.sent = False
        async def create_tasks(self):
            async def results():
                while (front := await fronts.get()) is not None:
                    yield front
            return results()
        async def process_audio(self, pcm):
            if not self.sent:
                self.sent = True
                await fronts.put(SimpleNamespace(status="ok", lines=[], buffer_transcription=buffer,
                                                 remaining_time_transcription_processing=0))
            if not pcm:
                await fronts.put(None)
        async def cleanup(self): pass

    monkeypatch.setitem(sys.modules, "whisperlivekit", SimpleNamespace(AudioProcessor=Processor))
    monkeypatch.setattr(capture, "build_engine", lambda meta: (None, "cpu", ""))
    asyncio.run(asyncio.wait_for(capture.record(tmp_path), 20))
    assert [r["text"] for r in events(tmp_path)] == kept


@pytest.mark.parametrize("reply", [{"text": "."}, {"segments": [dict(start=0, end=1, text=".")]}])
def test_cloud_returning_only_dots_for_the_first_30_seconds_still_raises_the_start_notice(tmp_path, monkeypatch, reply):
    # Plan GUI-4 Q1.3 (incident 1): "." used to count as text and cleared the start notice.
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    path = wav(tmp_path, tone(41, -50))
    write_json(tmp_path / "session.json", dict(asr_backend="api", asr_api_base="https://example.invalid/v1",
               asr_api_model="test", language="zh", fast=True, audio_file=str(path)))
    uploads = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        uploads.append(request)
        return httpx.Response(200, json=reply)
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    state = read_json(tmp_path / "asr-state.json")
    assert uploads and events(tmp_path) == []
    assert state["weak_input"] == NO_SPEECH


# --- Plan GUI-4 Q1.2: an empty transcript ----------------------------------------------------------

def workspace(tmp_path, records=(), refined=None):
    directory = tmp_path / "work"
    directory.mkdir()
    output = tmp_path / "MATH421" / "LectureNotes" / "2026-10-08_10-00-00_lecture.md"
    output.parent.mkdir(parents=True)
    write_json(directory / "session.json", dict(course="MATH421", started="2026-10-08 10:00", output=str(output),
               interval=0, notes_model="m", context="", refine=refined is not None))
    (directory / "transcript.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    if refined is not None:
        (directory / "refined.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in refined))
        write_json(directory / "refinement-state.json", {"complete": True, "count": len(refined)})
    (directory / "capture.done").write_text("")
    return directory, output


def record(i, text):
    return dict(id=i, start=f"00:00:{i:02d}.00", end=f"00:00:{i + 1:02d}.00", text=text)


@pytest.mark.parametrize("records, refined", [
    ([], None),
    ([], [record(1, "[近静音片段，未识别到文字]"), record(2, "[近静音片段，未识别到文字]")]),
])
def test_notes_service_is_never_asked_when_nothing_was_recognised(tmp_path, monkeypatch, records, refined):
    directory, output = workspace(tmp_path, records, refined)
    assert no_content(directory)
    calls = []
    monkeypatch.setattr(worker, "complete", lambda *args: calls.append(args) or "")
    monkeypatch.setattr(worker, "configure", lambda meta: None)
    assert worker.run(directory) == 0
    assert calls == []
    note = output.read_text()
    assert f"**{EMPTY_TITLE}。**" in note
    assert all(cause in note for cause in EMPTY_CAUSES) and "`lecture doctor --mic-test`" in note
    assert library.note_entry(output)["empty"] is True


def test_a_recording_with_text_is_not_empty(tmp_path):
    directory, output = workspace(tmp_path, [record(1, "[未确认尾部，待核对] 特征值")])
    assert not no_content(directory)
    journal = Journal(directory)
    journal.render(finished=True)
    journal.close()
    assert EMPTY_TITLE not in output.read_text()
    assert library.note_entry(output)["empty"] is False


SHIM = '''
import sys, time, os
from pathlib import Path
if "_capture" in sys.argv:
    from lecture_cli import capture
    from lecture_cli.storage import write_json
    def run(directory):
        transcript = capture.Transcript(directory)
        transcript.append(".", 0, 1)  # incident 1: only punctuation came back
        write_json(directory / "asr-state.json", {"status": "录制中", "count": 0, "captured": 12.0})
        (directory / "test-ready").touch()
        while not (directory / "stop").exists():
            time.sleep(0.05)
        transcript.append("。", 1, 2)
        write_json(directory / "asr-state.json", {"status": "转录完成", "count": 0, "captured": 12.0})
        return 0
    capture.run = run
if "_worker" in sys.argv:
    from lecture_cli import worker
    def complete(*args):
        with open(os.environ["LECTURE_TEST_CALLS"], "a") as log:
            log.write("called\\n")
        raise worker.APIError("unexpected")
    worker.complete = complete
'''


def test_controller_reports_an_empty_recording_and_exits_0(tmp_path, isolated_run_registry):
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM)
    root = tmp_path / "courses"
    (root / "MATH421").mkdir(parents=True)
    calls = tmp_path / "calls.log"
    env = dict(os.environ, DEEPSEEK_API_KEY="synthetic-test-key", PYTHONPATH=f"{shim}:{ROOT}",
               XDG_CONFIG_HOME=str(tmp_path / "config"), LECTURE_TEST_CALLS=str(calls))
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "MATH421", "--headless", "--interval", "1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 15
        directory = None
        while directory is None and time.monotonic() < deadline:
            for path in Path("/tmp").glob(f"lecture-{os.getuid()}-*"):
                meta = read_json(path / "session.json")
                if meta.get("output", "").startswith(str(root)) and (path / "test-ready").exists():
                    directory = path
            time.sleep(0.05)
        assert directory is not None
        (directory / "stop").touch()
        output = proc.communicate(timeout=20)[0]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, output
    flat = output.replace("\n", "")  # The terminal wraps long lines.
    assert EMPTY_TITLE in flat and all(cause in flat for cause in EMPTY_CAUSES)
    assert "lecture doctor --mic-test" in flat
    assert not calls.exists()
    [path] = isolated_run_registry.glob("*.json")
    entry = json.loads(path.read_text())
    assert entry["status"] == "done" and entry["exit_code"] == 0 and entry["flags"]["empty"] is True
    note = Path(entry["output"])
    assert f"**{EMPTY_TITLE}。**" in note.read_text()
