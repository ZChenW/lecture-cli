"""N3.4: a GUI-only notice after 30 s of weak input while someone is talking."""
import asyncio
import sys
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, capture
from lecture_cli.gui import sessions
from lecture_cli.input_level import NOTICE, WeakInput, summary
from lecture_cli.storage import read_json, write_json


def tone(seconds, dbfs):
    """A 440 Hz tone whose RMS is the given level in dBFS (0 means digital silence)."""
    if dbfs is None:
        return np.zeros(int(seconds * 16000), dtype="<i2").tobytes()
    t = np.arange(int(seconds * 16000)) / 16000
    amplitude = 10 ** (dbfs / 20) * np.sqrt(2) * 32767
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()


def feed(level, seconds, dbfs, texts_every=None, start_count=0):
    """Feed one-second blocks; a confirmed segment appears every texts_every seconds."""
    count, changes = start_count, []
    for second in range(seconds):
        if texts_every and second % texts_every == texts_every - 1:
            count += 1
        if level.add(tone(1, dbfs), count):
            changes.append((second + 1, level.notice))
    return count, changes


def test_thirty_seconds_of_weak_speech_raise_the_notice_and_recovery_clears_it():
    level = WeakInput()
    count, changes = feed(level, 29, -50, texts_every=4)
    assert changes == [] and level.notice == ""
    count, changes = feed(level, 1, -50, texts_every=1, start_count=count)
    assert changes == [(1, NOTICE)]
    _, changes = feed(level, 10, -20, texts_every=4, start_count=count)
    assert changes == [(10, "")]


def test_complete_silence_without_text_never_warns():
    level = WeakInput()
    assert feed(level, 120, None) == (0, [])
    assert feed(level, 120, -60) == (0, [])
    assert level.notice == ""


def test_text_confirmed_later_in_the_same_weak_stretch_still_counts():
    # Confirmed text lags the audio: the first segment arrives at 38 s, inside the weak stretch.
    level = WeakInput()
    feed(level, 35, -55)
    assert level.notice == ""
    level.add(tone(3, -55), 1)
    assert level.notice == ""  # The fourth window closes at 40 s.
    level.add(tone(2, -55), 1)
    assert level.notice == NOTICE


def test_threshold_is_minus_40_dbfs():
    for dbfs, expected in ((-41, NOTICE), (-39, "")):
        level = WeakInput()
        feed(level, 30, dbfs, texts_every=3)
        assert level.notice == expected


def test_paused_time_never_counts_and_a_break_drops_the_partial_window():
    level = WeakInput()
    count, _ = feed(level, 25, -50, texts_every=3)
    level.pause()  # 5 s of the third window are discarded; nothing is fed while paused.
    count, _ = feed(level, 5, -50, texts_every=3, start_count=count)
    assert level.notice == ""
    feed(level, 5, -50, texts_every=3, start_count=count)
    assert level.notice == NOTICE


def test_weak_time_adds_up_only_while_someone_talks():
    level = WeakInput()
    feed(level, 60, None)  # A silent break before class, then weak speech in the same stretch.
    assert level.seconds == 0
    count, _ = feed(level, 40, -50, texts_every=3)
    assert level.seconds == 60  # 40 s of speech plus at most 20 s of the break (lag allowance).
    count, _ = feed(level, 20, -20, texts_every=3, start_count=count)
    assert level.seconds == 60 and level.notice == ""
    count, _ = feed(level, 20, -50, texts_every=3, start_count=count)
    assert level.seconds == 60  # 20 s never raised the notice.
    count, _ = feed(level, 10, -20, texts_every=3, start_count=count)
    feed(level, 30, -50, texts_every=3, start_count=count)
    assert level.seconds == 90


def test_cloud_style_bursts_of_text_count_the_weak_time_they_describe():
    level = WeakInput()
    count = 0
    for chunk in range(6):  # Text for 30 s of audio arrives once the 30 s have been uploaded.
        level.add(tone(30, -50), count)
        count += 2
    level.add(tone(10, -50), count)
    assert level.notice == NOTICE
    assert level.seconds == 180


@pytest.mark.parametrize("seconds, sentence", [
    (None, ""), (True, ""), (120, ""), (130, "本节课累计约 2 分钟收到的声音很弱，这些部分的转录可能不准。"),
    (400, "本节课累计约 7 分钟收到的声音很弱，这些部分的转录可能不准。")])
def test_summary_sentence_only_past_two_minutes(seconds, sentence):
    assert summary(seconds) == sentence


def test_snapshot_exposes_it_apart_from_notices(tmp_path):
    write_json(tmp_path / "session.json", {"course": "MATH421"})
    write_json(tmp_path / "asr-state.json", {"weak_input": NOTICE, "warning": "音频设备报告 2 次输入丢帧"})
    asr = sessions.snapshot(tmp_path)["asr"]
    assert asr["weak_input"] == NOTICE
    assert [notice["kind"] for notice in asr["notices"]] == ["warning"]
    write_json(tmp_path / "asr-state.json", {})
    assert sessions.snapshot(tmp_path)["asr"]["weak_input"] is None


def wav(tmp_path, pcm):
    path = tmp_path / "quiet.wav"
    path.write_bytes(api_capture.wav_audio(pcm))
    return path


def test_cloud_backend_sets_its_own_field_and_keeps_warning_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    path = wav(tmp_path, tone(41, -47))
    write_json(tmp_path / "session.json", dict(asr_backend="api", asr_api_base="https://example.invalid/v1",
               asr_api_model="test", language="zh", fast=True, audio_file=str(path)))
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"segments": [dict(start=0, end=1, text="老师在讲话。")]})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    state = read_json(tmp_path / "asr-state.json")
    assert state["weak_input"] == NOTICE
    assert "warning" not in state


def test_local_backend_sets_the_field_from_the_audio_it_feeds(tmp_path, monkeypatch):
    path = wav(tmp_path, tone(31, -50))
    write_json(tmp_path / "session.json", dict(asr_model="base", language="zh", fast=True, audio_file=str(path)))
    fronts = asyncio.Queue()

    class Processor:
        def __init__(self, **kwargs):
            self.fed = 0
        async def create_tasks(self):
            async def results():
                while (front := await fronts.get()) is not None:
                    yield front
            return results()
        async def process_audio(self, pcm):
            if not pcm:
                await fronts.put(None)
                return
            self.fed += 1
            # One confirmed sentence per 2 s of audio.
            if self.fed % 4 == 0:
                token = SimpleNamespace(start=self.fed / 2, end=self.fed / 2 + 0.4, text=f"第{self.fed}句。")
                await fronts.put(SimpleNamespace(status="ok", lines=[SimpleNamespace(speaker=1, tokens=[token])],
                                                 buffer_transcription="", remaining_time_transcription_processing=0))
                await asyncio.sleep(0)
        async def cleanup(self): pass

    monkeypatch.setitem(sys.modules, "whisperlivekit", SimpleNamespace(AudioProcessor=Processor))
    monkeypatch.setattr(capture, "build_engine", lambda meta: (None, "cpu", ""))
    asyncio.run(asyncio.wait_for(capture.record(tmp_path), 20))
    state = read_json(tmp_path / "asr-state.json")
    assert state["status"] == "转录完成"
    assert state["weak_input"] == NOTICE
    assert "warning" not in state
