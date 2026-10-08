"""Plan GUI-4 Q1.4: steady noise and no text raise "可能只是噪声" (incident 1: about -32 dBFS, 9 minutes)."""
import httpx
import numpy as np
import pytest

from lecture_cli import api_capture
from lecture_cli.input_level import NO_SPEECH, NOTICE, STILL, WeakInput
from lecture_cli.storage import events, read_json, write_json


def noise(seconds, dbfs, seed=0):
    rng = np.random.default_rng(seed)
    samples = rng.standard_normal(int(seconds * 16000)) * 10 ** (dbfs / 20) * 32767
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def feed(level, seconds, dbfs, texts_every=None, start_count=0):
    count = start_count
    for second in range(seconds):
        if texts_every and second % texts_every == texts_every - 1:
            count += 1
        level.add(noise(1, dbfs, seed=second), count)
    return count


def test_constant_noise_without_text_raises_it_after_30_seconds_before_the_other_notices():
    level = WeakInput()
    feed(level, 29, -32)
    assert level.notice == ""
    feed(level, 1, -32)
    assert level.still == STILL and level.start == NO_SPEECH
    assert level.notice == STILL  # It explains the start notice, so it comes first.


def test_speech_never_raises_it():
    level = WeakInput()
    count = 0
    for second in range(120):  # A voice moves well over 3 dB from second to second.
        count += second % 3 == 2
        level.add(noise(1, -20 if second % 2 else -34, seed=second), count)
    assert level.still == "" and level.notice == ""
    # Even unrecognised speech (no text at all) varies too much to look like noise.
    level = WeakInput()
    for second in range(60):
        level.add(noise(1, -20 if second % 2 else -34, seed=second), 0)
    assert level.still == "" and level.notice == NO_SPEECH


def test_a_quiet_steady_room_is_not_noise():
    level = WeakInput()
    feed(level, 90, -60)
    assert level.still == "" and level.notice == NO_SPEECH


def test_text_arriving_within_the_30_seconds_prevents_it():
    level = WeakInput()
    feed(level, 120, -32, texts_every=10)
    assert level.still == ""


def test_new_text_clears_it():
    level = WeakInput()
    feed(level, 30, -32)
    assert level.notice == STILL
    assert level.add(noise(1, -32), 1) is True
    assert level.notice == "" and level.still == ""


def test_a_30_second_stretch_spanning_6_db_clears_it():
    level = WeakInput()
    feed(level, 30, -32)
    level.add(noise(1, -28), 0)  # 4 dB: still within "no change" for clearing
    assert level.notice == STILL
    level.add(noise(1, -25), 0)  # 7 dB across the last 30 s
    assert level.still == "" and level.notice == NO_SPEECH


def test_it_comes_before_the_weak_input_notice():
    level = WeakInput()
    count = feed(level, 30, -42, texts_every=3)
    assert level.notice == NOTICE
    feed(level, 30, -42, start_count=count)
    assert level.weak == NOTICE and level.notice == STILL


def test_paused_time_is_not_part_of_the_30_seconds():
    level = WeakInput()
    feed(level, 20, -32)
    level.pause()
    feed(level, 9, -32)
    assert level.notice == ""
    feed(level, 1, -32)
    assert level.notice == STILL


def test_audio_file_input_and_a_cloud_returning_dots_raise_it(tmp_path, monkeypatch):
    """Incident 1 on the cloud path: steady -32 dBFS noise, every upload answered with "."."""
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    path = tmp_path / "noise.wav"
    path.write_bytes(api_capture.wav_audio(noise(41, -32)))
    write_json(tmp_path / "session.json", dict(asr_backend="api", asr_api_base="https://example.invalid/v1",
               asr_api_model="test", language="zh", fast=True, audio_file=str(path)))

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"segments": [dict(start=0, end=20, text=".")]})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert read_json(tmp_path / "asr-state.json")["weak_input"] == STILL
    assert events(tmp_path) == []
