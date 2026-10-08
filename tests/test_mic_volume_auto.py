"""PLAN-GUI-5 R2: the microphone volume sets itself. R2.1 the start calibration, R2.3 the shared
verdict, R2.4 the remembered volume, R2.6 the steadier "可能只是噪声" rule. Fake wpctl (conftest),
fake input and a fake clock only: no microphone, no PipeWire."""
import asyncio
import json
import math

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, capture, mic_check, mic_gain
from lecture_cli.gui import sessions
from lecture_cli.input_level import STILL, WeakInput
from lecture_cli.storage import read_json, write_json

# PLAN-GUI-5 section 0, measured on the user's machine: background RMS (dBFS) by system volume;
# at 100 % 7.3 % of the samples clipped. Linear in log(volume) between the points.
TABLE = [(1.00, -4.3), (0.60, -15.8), (0.45, -24.0), (0.35, -29.7), (0.27, -37.0), (0.20, -44.6),
         (0.15, -52.8), (0.10, -63.0)]


def room_dbfs(volume):
    points = sorted(TABLE)
    if volume <= points[0][0]:
        return points[0][1]
    for (v0, d0), (v1, d1) in zip(points, points[1:]):
        if v0 <= volume <= v1:
            return d0 + (d1 - d0) * math.log(volume / v0) / math.log(v1 / v0)
    return points[-1][1]


class Room:
    """The fake input: listen(seconds) returns that much background at the fake wpctl's volume and
    moves the fake clock on by the same amount."""
    def __init__(self, fake, dbfs=room_dbfs, clip_at=0.99):
        self.fake, self.dbfs, self.clip_at = fake, dbfs, clip_at
        self.now = 0.0
        self.heard = []  # (volume, seconds)

    def clock(self):
        return self.now

    async def listen(self, seconds):
        self.now += seconds
        self.heard.append((self.fake.volume, seconds))
        level = 10 ** (self.dbfs(self.fake.volume) / 20)
        samples = np.random.default_rng(len(self.heard)).standard_normal(int(seconds * 16000)) * level
        if self.fake.volume >= self.clip_at:
            samples[::14] = 1.0  # 7.3 % of the samples at full scale, as measured
        return samples


def sets(fake):
    return [float(c[3]) for c in fake.calls if c[1] == "set-volume"]


def calibrate(gain, room):
    return asyncio.run(gain.calibrate(room.listen, room.clock))


# --- R2.1 ----------------------------------------------------------------------------------------

def test_from_100_percent_the_fixture_reaches_the_target(fake_wpctl, monkeypatch):
    # The plan's walk: 100 % (-4.3, clipping) down 6 dB at a time; 20 % is still -44.6, about 16 %
    # is below -45. Eight steps take 1 + 8 × 0.8 = 7.4 s, so the 6 s limit is lifted here (see below).
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 10.0)
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.steps == 8 and not result.timed_out and not result.clipped
    assert result.volume == pytest.approx(mic_gain.GAIN_STEP ** 8) == pytest.approx(0.158, abs=0.001)
    assert result.background < mic_gain.BACKGROUND_TARGET and result.ceiling == result.volume
    assert sets(fake_wpctl) == pytest.approx([mic_gain.GAIN_STEP ** n for n in range(1, 9)], abs=1e-6)
    assert room_dbfs(mic_gain.GAIN_STEP ** 7) > -45 > room_dbfs(mic_gain.GAIN_STEP ** 8)
    # 1 s first, then 0.3 s settling and 0.5 s measuring per step.
    assert [s for _, s in room.heard] == [1.0] + [0.3, 0.5] * 8 and room.now == pytest.approx(7.4)


def test_the_6_second_limit_carries_on_with_the_value_reached(fake_wpctl):
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl)
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    # Checked before each step: after 7 steps 6.6 s have passed, so the 8th never starts.
    assert result.steps == 7 and result.timed_out and room.now == pytest.approx(6.6)
    assert result.volume == pytest.approx(mic_gain.GAIN_STEP ** 7) == pytest.approx(0.1995, abs=0.0005)
    assert result.background == pytest.approx(-44.6, abs=0.3) and result.ceiling == result.volume
    # R2.4: remembered after a normal end, the next lecture starts there and needs one step.
    assert mic_gain.remember(gain.node, gain.volume, gain.ceiling)
    fake_wpctl.volume, fake_wpctl.calls = 1.0, []  # R2.5 put the volume back after the lecture
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert sets(fake_wpctl)[0] == pytest.approx(mic_gain.GAIN_STEP ** 7, abs=1e-6)
    assert result.steps == 1 and not result.timed_out
    assert result.volume == pytest.approx(mic_gain.GAIN_STEP ** 8, abs=1e-6)


def test_a_quiet_room_needs_no_step_and_estimates_the_ceiling(fake_wpctl):
    fake_wpctl.volume = 0.15
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
    assert result.steps == 0 and sets(fake_wpctl) == [] and result.volume == 0.15
    # Where the background would reach -45 dBFS: about 20 %, which the measured table agrees with.
    assert result.ceiling == pytest.approx(0.15 * 10 ** ((-45 - result.background) / 60), abs=1e-3)  # background is rounded
    assert result.ceiling == pytest.approx(0.20, abs=0.01)


def test_it_stops_at_10_percent_and_calls_the_microphone_dead(fake_wpctl):
    # Noise that no volume change moves: incident 1's dead microphone at -30 dBFS.
    fake_wpctl.volume = 0.3
    room = Room(fake_wpctl, dbfs=lambda volume: -30.0)
    gain = mic_gain.MicGain(None)
    state, published = {}, []
    asyncio.run(capture.calibrate(gain, FakeListener(room), state, published.append, clock=room.clock))
    result = gain.calibration
    assert result.volume == mic_gain.MIN_VOLUME and sets(fake_wpctl)[-1] == mic_gain.MIN_VOLUME
    assert result.steps == 5 and not result.timed_out
    assert state["mic_verdict"] == mic_gain.DEAD_TEXT == "麦克风可能没有在工作：声音没有变化"
    assert state["status"] == mic_gain.CALIBRATING and published == [True]


def test_at_most_10_steps(fake_wpctl, monkeypatch):
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 60.0)
    monkeypatch.setattr(mic_gain, "MIN_VOLUME", 0.01)
    fake_wpctl.volume = 1.0
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl, dbfs=lambda volume: -30.0))
    assert result.steps == mic_gain.CALIBRATE_STEPS == 10 and len(sets(fake_wpctl)) == 10


class FakeListener:
    def __init__(self, room):
        self.room = room
        self.closed = False

    async def listen(self, seconds):
        return await self.room.listen(seconds)

    def close(self):
        self.closed = True


def test_a_timed_out_calibration_says_nothing(fake_wpctl, monkeypatch):
    # Still loud when time ran out: R2.2 goes on lowering; "dead" would only be a guess.
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 2.0)
    fake_wpctl.volume = 1.0
    gain = mic_gain.MicGain(None)
    listener = FakeListener(Room(fake_wpctl, dbfs=lambda volume: -30.0))
    state = {}
    asyncio.run(capture.calibrate(gain, listener, state, lambda force=False: None, clock=listener.room.clock))
    assert gain.calibration.timed_out and "mic_verdict" not in state and listener.closed


def test_the_remembered_volume_is_only_for_its_own_source(fake_wpctl):
    assert mic_gain.remember("alsa_input.other-mic", 0.3, 0.3)
    fake_wpctl.volume = 0.15
    calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
    assert sets(fake_wpctl) == []


def test_an_unreadable_levels_file_starts_from_the_current_volume(fake_wpctl):
    path = mic_gain.levels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for broken in ("{not json", "[1, 2]", json.dumps({"alsa_input.fake-test-mic": {"volume": "0.2"}}),
                   json.dumps({"alsa_input.fake-test-mic": {"volume": float("nan")}})):
        path.write_text(broken)
        fake_wpctl.volume, fake_wpctl.calls = 0.15, []
        result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
        assert sets(fake_wpctl) == [] and result.volume == 0.15


def test_without_wpctl_or_muted_nothing_is_calibrated(fake_wpctl, monkeypatch):
    fake_wpctl.muted = True
    room = Room(fake_wpctl)
    assert calibrate(mic_gain.MicGain(None), room) is None and room.heard == [] and sets(fake_wpctl) == []
    assert mic_gain.MicGain("USB Microphone").active is False  # never adjusted: capture makes no listener


def test_a_wpctl_failure_midway_stops_adjusting_and_records_on(fake_wpctl, monkeypatch):
    fake_wpctl.volume = 1.0
    real = mic_gain.subprocess.run

    def failing(command, **kwargs):
        if command[1] == "set-volume":
            raise FileNotFoundError()
        return real(command, **kwargs)
    monkeypatch.setattr(mic_gain.subprocess, "run", failing)
    gain = mic_gain.MicGain(None)
    assert gain.active
    assert calibrate(gain, Room(fake_wpctl)) is None
    assert not gain.active and "未找到 wpctl" in gain.notice


def test_the_listener_hands_over_once_closed():
    listener = mic_gain.Listener()

    async def run():
        task = asyncio.create_task(listener.listen(0.2))
        await asyncio.sleep(0)
        assert listener.feed(np.ones(1600)) and listener.feed(np.ones(1600))
        return await task
    data = asyncio.run(run())
    assert data.size == 3200
    listener.close()
    assert listener.feed(np.ones(1600)) is False  # from now on the recording takes the samples


def test_the_bottom_bar_gets_the_volume_after_the_calibration(fake_wpctl, tmp_path):
    fake_wpctl.volume = 0.6
    gain = mic_gain.MicGain(None)
    state = {}
    gain.publish(state)
    assert "mic_volume" not in state  # not before the calibration
    calibrate(gain, Room(fake_wpctl))
    gain.publish(state)
    assert state["mic_volume"] == pytest.approx(gain.volume, abs=1e-6) and state["mic_node"] == "alsa_input.fake-test-mic"
    write_json(tmp_path / "session.json", {"course": "MATH421", "output": str(tmp_path / "n.md")})
    write_json(tmp_path / "asr-state.json", {"status": "录制中", **state, "mic_verdict": mic_gain.DEAD_TEXT})
    asr = sessions.snapshot(tmp_path)["asr"]
    assert asr["mic_volume"] == pytest.approx(gain.volume, abs=1e-4) and asr["mic_verdict"] == mic_gain.DEAD_TEXT
    for bad in ("0.2", True, float("inf"), -1, 3):
        write_json(tmp_path / "asr-state.json", {"status": "录制中", "mic_volume": bad, "mic_verdict": ""})
        asr = sessions.snapshot(tmp_path)["asr"]
        assert asr["mic_volume"] is None and asr["mic_verdict"] is None


# --- R2.3 ----------------------------------------------------------------------------------------

def levels(values, clipped=()):
    return [{"rms": value, "clipped": index in clipped} for index, value in enumerate(values)]


@pytest.mark.parametrize("stretch, options, expected", [
    (levels([-50] * 20, clipped=range(4)), {}, "high"),                       # clipping background
    (levels([-50] * 20, clipped=range(3)), {}, ""),                           # under a fifth of it
    (levels([-15] * 20), {}, "high"),                                         # loud with nobody talking
    (levels([-15] * 20), {"settled": True}, "high"),                          # high comes before dead
    (levels([-30] * 20), {"settled": True}, "dead"),                          # steady after R2.1
    (levels([-30] * 20), {}, ""),                                             # steady, R2.1 may still fix it
    (levels([-30] * 20), {"adjusts": False}, "dead"),                         # never adjusted
    (levels([-30, -36] * 10), {"volume": 0.1}, "dead"),                       # at the 10 % floor, still loud
    (levels([-30, -36] * 10), {"volume": 0.3}, ""),
    (levels([-50] * 20), {"volume": 0.1, "settled": True}, ""),               # a quiet room is fine
    (levels([-20, -40] * 10), {"settled": True}, ""),                         # it moves: someone talks
    ([], {}, ""),
])
def test_judge_three_results_in_order(stretch, options, expected):
    assert mic_gain.judge(stretch, **options) == expected


def test_verdict_texts():
    assert mic_gain.verdict_text("high") == "麦克风音量过高，开始上课时会自动调低"
    assert mic_gain.verdict_text("high", adjusts=False) == mic_gain.HIGH_MANUAL_TEXT
    assert mic_gain.verdict_text("dead") == "麦克风可能没有在工作：声音没有变化"
    assert mic_gain.verdict_text("") == ""


def test_block_level_reports_clipping():
    quiet = np.full(1600, 0.01)
    loud = quiet.copy()
    loud[:2] = 1.0
    assert mic_check.block_level(quiet)["clipped"] is False and mic_check.block_level(loud)["clipped"] is True
    assert mic_check.verdict([mic_check.block_level(loud)] * 20) == {"verdict": "high", "text": mic_gain.HIGH_TEXT}


# --- R2.4 ----------------------------------------------------------------------------------------

def test_remember_keeps_other_sources_and_survives_a_broken_file():
    path = mic_gain.levels_path()
    assert mic_gain.remembered("alsa_input.a") is None  # no file yet
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken")
    assert mic_gain.remembered("alsa_input.a") is None
    assert mic_gain.remember("alsa_input.a", 0.22, 0.25)
    assert mic_gain.remember("alsa_input.b", 0.5, 0.5)
    stored = json.loads(path.read_text())
    assert stored["alsa_input.a"]["volume"] == 0.22 and stored["alsa_input.a"]["ceiling"] == 0.25
    assert set(stored) == {"alsa_input.a", "alsa_input.b"} and stored["alsa_input.a"]["updated"]
    assert mic_gain.remembered("alsa_input.a") == 0.22
    assert oct(path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("state", [{}, {"mic_node": "x", "mic_volume": 0.2}, {"mic_node": "", "mic_volume": 0.2, "mic_ceiling": 0.2},
                                   {"mic_node": "x", "mic_volume": True, "mic_ceiling": 0.2},
                                   {"mic_node": "x", "mic_volume": 0.2, "mic_ceiling": float("nan")}])
def test_nothing_to_remember_writes_nothing(state):
    assert mic_gain.remember_levels(state) is False and not mic_gain.levels_path().exists()


# --- R2.6 ----------------------------------------------------------------------------------------

def noise(seconds, dbfs, seed=0):
    samples = np.random.default_rng(seed).standard_normal(int(seconds * 16000)) * 10 ** (dbfs / 20) * 32767
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def test_after_the_first_30_seconds_it_takes_120():
    level = WeakInput()
    for second in range(30):  # a voice first: the 30 s rule passes
        level.add(noise(1, -20 if second % 2 else -34, seed=second), 0)
    assert level.still == ""
    for second in range(119):
        level.add(noise(1, -42, seed=second), 0)
    assert level.still == ""
    level.add(noise(1, -42), 0)
    assert level.still == STILL


def test_closed_once_it_never_returns():
    level = WeakInput()
    for second in range(30):
        level.add(noise(1, -32, seed=second), 0)
    assert level.still == STILL
    assert level.dismiss_still() and level.still == "" and level.still_off
    for second in range(400):
        level.add(noise(1, -32, seed=second), 0)
    assert level.still == ""


def test_the_capture_process_honours_the_closed_notice(tmp_path, monkeypatch):
    # The GUI writes the sentinel (POST /api/runs/active/dismiss-still); here it is there from the start.
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    path = tmp_path / "noise.wav"
    path.write_bytes(api_capture.wav_audio(noise(41, -32)))
    write_json(tmp_path / "session.json", dict(asr_backend="api", asr_api_base="https://example.invalid/v1",
               asr_api_model="test", language="zh", fast=True, audio_file=str(path)))
    (tmp_path / "dismiss-still").touch()

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"segments": [dict(start=0, end=20, text=".")]})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert read_json(tmp_path / "asr-state.json").get("weak_input") != STILL
