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

def walk(result):
    """The calibration's path as (seconds, volume, background) for the log and the asserts."""
    return [(t, v, r) for t, v, r, _ in result.path]


def test_from_100_percent_the_fixture_reaches_the_target_within_3_seconds(fake_wpctl):
    # GUI5-fix. 100 % clips: down 18 dB to 50.1 % (about -21 dBFS in the table), then the computed step
    # aims 2 dB under -45: 50.1 % × 10^((-47 + 21) / 60) ≈ 18.4 %, about -46.9 dBFS. Confirmed; done.
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.reached and not result.timed_out and not result.clipped and result.steps == 2
    assert sets(fake_wpctl)[0] == pytest.approx(10 ** (-18 / 60), abs=1e-6) == pytest.approx(0.501187, abs=1e-6)
    assert result.volume == pytest.approx(0.184, abs=0.002) and result.ceiling == result.volume
    assert room_dbfs(result.volume) < mic_gain.BACKGROUND_TARGET and result.background < mic_gain.BACKGROUND_TARGET
    assert result.background == pytest.approx(room_dbfs(result.volume), abs=0.3)
    # 1 s first, then 0.3 s settling and 0.5 s measuring per step: 2.6 s in all.
    assert [s for _, s in room.heard] == [1.0, 0.3, 0.5, 0.3, 0.5] and room.now == pytest.approx(2.6) and room.now <= 3
    assert [round(v, 3) for _, v, _ in walk(result)] == [1.0, 0.501, round(result.volume, 3)]
    # Without the 2 dB margin the same step would land just above the target (the table is a little
    # steeper than v³ here) and need the correction, 3.4 s.
    plain = 0.501187 * 10 ** ((mic_gain.BACKGROUND_TARGET - room_dbfs(0.501187)) / 60)
    assert room_dbfs(plain) > mic_gain.BACKGROUND_TARGET


def test_without_clipping_one_computed_step_reaches_the_target(fake_wpctl):
    fake_wpctl.volume = 0.45  # -24 dBFS in the table
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.steps == 1 and result.reached and room.now == pytest.approx(1.8)
    assert sets(fake_wpctl) == [pytest.approx(0.45 * 10 ** ((-47 + 24) / 60), abs=0.002)]
    assert room_dbfs(result.volume) < -45 and result.ceiling == result.volume


@pytest.mark.parametrize("start", [1.0, 0.6, 0.35, 0.27, 0.2])
def test_every_start_in_the_table_lands_under_the_target_in_time(fake_wpctl, start):
    fake_wpctl.volume = start
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.reached and room_dbfs(result.volume) < -45 and room.now <= 3
    assert result.volume >= mic_gain.MIN_VOLUME and result.steps <= 2


def test_at_most_one_correction_and_an_unmet_target_is_not_the_ceiling(fake_wpctl):
    # A room that follows the volume at only half the expected rate: the computed step and the one
    # correction both stay above -45. The run carries on where it stopped and never raises above it.
    fake_wpctl.volume = 0.6
    room = Room(fake_wpctl, dbfs=lambda volume: -20 + 30 * math.log10(volume / 0.6))
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    assert result.steps == 2 and not result.reached and not result.timed_out
    assert room.now == pytest.approx(2.6) and result.background > -45
    assert result.ceiling == result.volume == gain.ceiling == pytest.approx(fake_wpctl.volume, abs=1e-6)
    state = {}
    gain.publish(state)
    assert state["mic_target_met"] is False and state["mic_ceiling"] == pytest.approx(result.volume, abs=1e-6)
    assert mic_gain.remember_levels(state) is False and not mic_gain.levels_path().exists()


def test_a_clip_drop_that_went_too_far_comes_back_up_to_the_target(fake_wpctl):
    # Clips at 100 % (a loud input stage) but is quiet below: 18 dB lower is far under -45; the computed
    # step goes back up, never to a volume that clipped (at most 6 dB under it).
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl, dbfs=lambda volume: -40 + 60 * math.log10(volume))
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.reached and result.steps == 2
    assert sets(fake_wpctl)[0] == pytest.approx(0.501187, abs=1e-6)
    assert 0.501187 < result.volume <= mic_gain.GAIN_STEP + 1e-6
    assert result.background == pytest.approx(-47, abs=0.5)


def test_the_timeout_carries_on_and_nothing_is_remembered(fake_wpctl, monkeypatch):
    # Time is up after the 18 dB drop: still above -45 at 50 %, so 50 % is this run's ceiling and the
    # next lecture starts from the system volume again, not from 50 %.
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 1.5)
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl)
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    assert result.steps == 1 and result.timed_out and not result.reached and room.now == pytest.approx(1.8)
    assert result.volume == pytest.approx(0.501187, abs=1e-6) and result.ceiling == result.volume
    state = {}
    gain.publish(state)
    assert state["mic_target_met"] is False and mic_gain.remember_levels(state) is False
    assert mic_gain.remembered(gain.node) is None


def test_a_met_target_is_remembered_and_the_next_lecture_needs_no_step(fake_wpctl):
    fake_wpctl.volume = 1.0
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, Room(fake_wpctl))
    state = {}
    gain.publish(state)
    assert state["mic_target_met"] is True and mic_gain.remember_levels(state)
    fake_wpctl.volume, fake_wpctl.calls = 1.0, []  # R2.5 put the volume back after the lecture
    room = Room(fake_wpctl)
    again = calibrate(mic_gain.MicGain(None), room)
    assert sets(fake_wpctl) == [pytest.approx(result.volume, abs=1e-6)] and again.steps == 0 and again.reached
    assert room.now == pytest.approx(1.0)


def test_a_quiet_room_is_raised_to_the_ceiling(fake_wpctl):
    # GUI5-twoway (was: no step, ceiling estimated at about 18.7 %). 15 % is -52.8 in the table: straight up
    # to 15 % × 10^((-47 + 52.8) / 60) ≈ 18.7 %, confirmed there, and that measured volume is the ceiling.
    fake_wpctl.volume = 0.15
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert result.steps == 1 and result.reached and not result.backed_off and room.now == pytest.approx(1.8)
    assert sets(fake_wpctl) == [pytest.approx(0.15 * 10 ** ((-47 - room_dbfs(0.15)) / 60), abs=0.002)]
    assert result.volume == pytest.approx(0.187, abs=0.005) and result.ceiling == result.volume
    assert result.background == pytest.approx(room_dbfs(result.volume), abs=0.3)
    assert result.background < -45


@pytest.mark.parametrize("start", [0.10, 0.12, 0.15])
def test_low_starts_are_raised_in_one_step(fake_wpctl, start):
    # The user's case: 12 % stayed at 12 %. Each low start in the table goes straight up near -47 dBFS.
    fake_wpctl.volume = start
    room = Room(fake_wpctl)
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    assert result.steps == 1 and result.reached and room.now == pytest.approx(1.8)
    assert result.volume > start and -48.5 < result.background < -45 and -48.5 < room_dbfs(result.volume) < -45
    assert result.ceiling == result.volume == gain.ceiling
    state = {}
    gain.publish(state)
    assert state["mic_target_met"] is True and mic_gain.remember_levels(state)


def test_a_raise_that_goes_over_the_target_backs_off(fake_wpctl):
    # A room twice as steep as the table above 12 %: the computed raise to about 18.6 % lands near -36 dBFS.
    # Back to 12 %, where the target was met, and no second raise.
    fake_wpctl.volume = 0.12
    room = Room(fake_wpctl, dbfs=lambda volume: room_dbfs(0.12) + 120 * math.log10(volume / 0.12))
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    assert [round(v, 3) for _, v, _ in walk(result)] == [0.12, round(sets(fake_wpctl)[0], 3), 0.12]
    assert walk(result)[1][2] > -45 and result.backed_off and result.reached and result.steps == 2
    assert sets(fake_wpctl)[-1] == pytest.approx(0.12) and result.ceiling == result.volume == pytest.approx(0.12)
    assert room.now == pytest.approx(2.6)


def test_a_raise_that_clips_backs_off_and_the_ceiling_stays_under_it(fake_wpctl):
    fake_wpctl.volume = 0.12
    room = Room(fake_wpctl, clip_at=0.16)  # an input stage that clips just above 16 %
    gain = mic_gain.MicGain(None)
    result = calibrate(gain, room)
    assert [c for _, _, _, c in result.path] == [False, True, False]
    assert result.backed_off and result.reached and result.volume == pytest.approx(0.12) == gain.ceiling
    # R2.2 never raises past it either: the ceiling is the volume that met the target.
    assert gain.ceiling < 0.16


def test_a_raise_left_unconfirmed_is_undone_and_not_remembered(fake_wpctl):
    # The settling listen takes far longer than asked (a stalled device): the 6 s are up before the
    # raise is confirmed and its measurement is over the target. Back to the volume that met it, without
    # measuring again; not met at the end, so nothing is remembered.
    fake_wpctl.volume = 0.12
    room = Room(fake_wpctl, dbfs=lambda volume: room_dbfs(0.12) + 120 * math.log10(volume / 0.12))
    real = room.listen

    async def slow(seconds):
        if seconds == mic_gain.CALIBRATE_SETTLE:
            room.now += 5.0
        return await real(seconds)
    gain = mic_gain.MicGain(None)
    result = asyncio.run(gain.calibrate(slow, room.clock))
    assert result.steps == 1 and result.backed_off and not result.reached and result.timed_out
    assert sets(fake_wpctl)[-1] == pytest.approx(0.12) and fake_wpctl.volume == pytest.approx(0.12)
    assert result.volume == result.ceiling == pytest.approx(0.12) and len(result.path) == 2
    state = {}
    gain.publish(state)
    assert state["mic_target_met"] is False and mic_gain.remember_levels(state) is False


def test_no_raise_without_time_to_confirm_it(fake_wpctl, monkeypatch):
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 1.5)
    fake_wpctl.volume = 0.12
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
    assert result.steps == 0 and sets(fake_wpctl) == [] and result.reached and result.ceiling == 0.12


def test_a_small_raise_is_not_worth_a_step(fake_wpctl):
    # Under 1 dB (RAISE_MIN_DB) below the computed volume: met, no step.
    fake_wpctl.volume = 0.18  # about -46.3 in the table, -47 is 0.7 dB higher
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
    assert result.steps == 0 and sets(fake_wpctl) == [] and result.reached and result.ceiling == 0.18


def test_a_remembered_low_volume_is_still_the_start_and_is_raised(fake_wpctl):
    # The last lecture ended at 12 % in a louder room; this one starts there (not at the system 100 %),
    # finds the background well under the target and raises in one step.
    assert mic_gain.remember("alsa_input.fake-test-mic", 0.12, 0.12)
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl)
    result = calibrate(mic_gain.MicGain(None), room)
    assert sets(fake_wpctl)[0] == pytest.approx(0.12) and walk(result)[0][1] == pytest.approx(0.12)
    assert result.steps == 1 and result.reached and 0.18 < result.volume < 0.2 and room.now == pytest.approx(1.8)


def test_the_10_percent_floor_and_a_dead_microphone(fake_wpctl):
    # Noise that no volume change moves: incident 1's dead microphone at -30 dBFS. The computed step and
    # the correction reach the floor; lowering by 9 dB left the background where it was.
    fake_wpctl.volume = 0.3
    room = Room(fake_wpctl, dbfs=lambda volume: -30.0)
    gain = mic_gain.MicGain(None)
    state, published = {}, []
    asyncio.run(capture.calibrate(gain, FakeListener(room), state, published.append, clock=room.clock))
    result = gain.calibration
    assert result.volume == mic_gain.MIN_VOLUME and sets(fake_wpctl)[-1] == mic_gain.MIN_VOLUME
    assert result.steps == 2 and not result.timed_out and not result.reached and result.unmoved
    assert state["mic_verdict"] == mic_gain.DEAD_TEXT == "麦克风可能没有在工作：声音没有变化"
    assert state["status"] == mic_gain.CALIBRATING and published == [True]


def test_a_room_too_loud_even_at_the_floor(fake_wpctl):
    # 25 dB louder than the table: two 18 dB drops while it clips, then the computed step is under 10 %.
    fake_wpctl.volume = 1.0
    room = Room(fake_wpctl, dbfs=lambda volume: room_dbfs(volume) + 25, clip_at=0.4)
    result = calibrate(mic_gain.MicGain(None), room)
    assert [round(v, 3) for _, v, _ in walk(result)] == [1.0, 0.501, 0.251, 0.1]
    assert result.volume == mic_gain.MIN_VOLUME and not result.reached and result.ceiling == mic_gain.MIN_VOLUME
    assert mic_gain.judge(list(result.levels), volume=result.volume) == "dead"  # the floor rule (R2.3)


def test_at_most_10_steps(fake_wpctl, monkeypatch):
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 60.0)
    monkeypatch.setattr(mic_gain, "CLIP_DROP_DB", 1.0)
    fake_wpctl.volume = 1.0
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl, clip_at=0.0))
    assert result.steps == mic_gain.CALIBRATE_STEPS == 10 and len(sets(fake_wpctl)) == 10


class FakeListener:
    def __init__(self, room):
        self.room = room
        self.closed = False

    async def listen(self, seconds):
        return await self.room.listen(seconds)

    def close(self):
        self.closed = True


def test_a_calibration_cut_short_says_nothing(fake_wpctl, monkeypatch):
    # Time ran out before any step: whether the volume moves the background is unknown, so no "dead".
    monkeypatch.setattr(mic_gain, "CALIBRATE_TIMEOUT", 0.5)
    fake_wpctl.volume = 1.0
    gain = mic_gain.MicGain(None)
    listener = FakeListener(Room(fake_wpctl, dbfs=lambda volume: -30.0))
    state = {}
    asyncio.run(capture.calibrate(gain, listener, state, lambda force=False: None, clock=listener.room.clock))
    assert gain.calibration.timed_out and gain.calibration.steps == 0 and not gain.calibration.unmoved
    assert "mic_verdict" not in state and listener.closed and gain.ceiling == 1.0


def test_the_remembered_volume_is_only_for_its_own_source(fake_wpctl):
    assert mic_gain.remember("alsa_input.other-mic", 0.3, 0.3)
    fake_wpctl.volume = 0.15
    result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
    # GUI5-twoway: 15 % is now raised, but measured from 15 % first (was: no set-volume at all).
    assert walk(result)[0][1] == 0.15 and 0.3 not in sets(fake_wpctl)


def test_an_unreadable_levels_file_starts_from_the_current_volume(fake_wpctl):
    path = mic_gain.levels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for broken in ("{not json", "[1, 2]", json.dumps({"alsa_input.fake-test-mic": {"volume": "0.2"}}),
                   json.dumps({"alsa_input.fake-test-mic": {"volume": float("nan")}})):
        path.write_text(broken)
        fake_wpctl.volume, fake_wpctl.calls = 0.15, []
        result = calibrate(mic_gain.MicGain(None), Room(fake_wpctl))
        # GUI5-twoway: measured from 15 % and raised from there (was: no set-volume, volume 0.15).
        assert walk(result)[0][1] == 0.15 and len(sets(fake_wpctl)) == 1


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


@pytest.mark.parametrize("state", [{}, {"mic_node": "x", "mic_volume": 0.2},
                                   {"mic_node": "x", "mic_volume": 0.2, "mic_ceiling": 0.2},  # mic_target_met missing (GUI5-fix)
                                   {"mic_node": "x", "mic_volume": 0.2, "mic_ceiling": 0.2, "mic_target_met": False}, {"mic_node": "", "mic_volume": 0.2, "mic_ceiling": 0.2},
                                   {"mic_node": "x", "mic_volume": True, "mic_ceiling": 0.2},
                                   {"mic_node": "x", "mic_volume": 0.2, "mic_ceiling": float("nan")}])
def test_nothing_to_remember_writes_nothing(state):
    assert mic_gain.remember_levels(state) is False and not mic_gain.levels_path().exists()


def test_a_met_target_writes_the_levels():
    state = {"mic_node": "x", "mic_volume": 0.2, "mic_ceiling": 0.25, "mic_target_met": True}
    assert mic_gain.remember_levels(state) and json.loads(mic_gain.levels_path().read_text())["x"]["ceiling"] == 0.25


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
