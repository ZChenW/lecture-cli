"""Plan GUI-4 Q2 lowering (unchanged) and PLAN-GUI-5 R2.2's two-way rule: lower after two clipping
windows by 6 dB, the ceiling follows; raise by 6 dB only when all four conditions hold."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from lecture_cli import mic_gain
from lecture_cli.gui import sessions
from lecture_cli.storage import write_json

CLIP = np.ones(mic_gain.WINDOW_SAMPLES)
QUIET = np.full(mic_gain.WINDOW_SAMPLES, 0.01)   # peak -40 dBFS: speech that is too quiet
LOUD = np.full(mic_gain.WINDOW_SAMPLES, 0.1)     # peak -20 dBFS: speech loud enough
WINDOW = mic_gain.WINDOW_SAMPLES / mic_gain.SAMPLE_RATE  # 2 s of recorded audio
RAISE_WINDOWS = int(mic_gain.RAISE_SECONDS / WINDOW)       # 30
TALK = ((0.0, 1e9),)  # confirmed text all along


def sets(fake):
    return [float(c[3]) for c in fake.calls if c[1] == "set-volume"]


def feed(gain, samples, count=1, texts=TALK):
    results = []
    for _ in range(count):
        gain.observe(samples)
        results.append(gain.poll(texts))
    return results


def lowered(fake, meta=None):
    gain = mic_gain.mic_gain(meta or {})
    feed(gain, CLIP, 2)
    assert sets(fake) == [pytest.approx(0.85 * mic_gain.GAIN_STEP)]
    return gain


def calibrated(fake, volume=0.3, background=-60.0, runner=None, put_back=None):
    """A gain after R2.1 in a quiet room, then turned back down to `volume` by hand. GUI5-twoway: R2.1
    itself now raises straight to the ceiling (measured there), so R2.2's raise only has room to work
    after the volume went below it by other means, as here."""
    fake.volume = volume
    gain = mic_gain.MicGain(None, runner)
    level = 10 ** (background / 20)

    async def listen(seconds):
        return np.random.default_rng(0).standard_normal(int(seconds * 16000)) * level
    result = asyncio.run(gain.calibrate(listen))
    assert result.reached and result.steps == 1 and gain.ceiling == result.volume > volume
    (put_back or (lambda: setattr(fake, "volume", volume)))()
    if hasattr(fake, "calls"):
        fake.calls.clear()
    return gain


def test_step_is_6_db_and_two_windows():
    assert mic_gain.GAIN_STEP == 10 ** (-6 / 60) and mic_gain.CLIP_WINDOWS == 2
    assert 20 * np.log10(mic_gain.GAIN_STEP ** 3) == pytest.approx(-6)
    assert (mic_gain.RAISE_SECONDS, mic_gain.RAISE_PEAK_DBFS, mic_gain.RAISE_PERCENTILE, mic_gain.LOCK_SECONDS) == (60, -30, 95, 60)


def test_single_clipping_window_never_lowers(fake_wpctl):
    gain = mic_gain.mic_gain({})
    assert feed(gain, CLIP) == [False]
    assert sets(fake_wpctl) == [] and gain.change is None
    # A window without clipping clears it: clip, quiet, clip is still no lowering.
    feed(gain, QUIET)
    assert feed(gain, CLIP) == [False]
    assert sets(fake_wpctl) == []
    assert feed(gain, CLIP) == [True]
    assert sets(fake_wpctl) == [pytest.approx(0.675179, abs=1e-6)]


def test_two_consecutive_windows_lower_by_6_db_with_a_banner(fake_wpctl):
    gain = lowered(fake_wpctl)
    assert gain.adjusted == pytest.approx(0.85 * mic_gain.GAIN_STEP)
    assert gain.notice == "检测到削波，麦克风音量 85% → 68%"
    assert gain.change == {"id": 1, "text": "刚才声音过大，麦克风音量已从 85% 调到 68%"}
    assert mic_gain.lowered_text(1.0, 1.0 * mic_gain.GAIN_STEP) == "刚才声音过大，麦克风音量已从 100% 调到 79%"


def test_a_lowering_takes_the_ceiling_down(fake_wpctl):
    gain = calibrated(fake_wpctl)
    assert gain.ceiling > 0.45  # R2.1 raised to 0.3 · 10^(13/60) ≈ 0.49 and measured it (GUI5-twoway)
    feed(gain, CLIP, 2)
    assert gain.ceiling == pytest.approx(0.3 * mic_gain.GAIN_STEP) == gain.volume
    assert not any(feed(gain, QUIET, 3 * RAISE_WINDOWS))  # never above it again
    assert len(sets(fake_wpctl)) == 1


def test_floor_and_hardware_hint_unchanged(fake_wpctl):
    fake_wpctl.volume = 0.1
    gain = mic_gain.mic_gain({})
    assert feed(gain, CLIP, 2) == [False, True]
    assert sets(fake_wpctl) == [] and "Mic Boost" in gain.notice and gain.change is None


def test_raises_after_60_s_of_quiet_speech(fake_wpctl):
    gain = calibrated(fake_wpctl)
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 1))
    assert sets(fake_wpctl) == []
    assert feed(gain, QUIET) == [True]
    assert sets(fake_wpctl) == [pytest.approx(0.3 / mic_gain.GAIN_STEP, abs=1e-6)]
    assert gain.adjusted == pytest.approx(0.3 / mic_gain.GAIN_STEP, abs=1e-6)  # gain_volume follows raises too
    assert gain.change == {"id": 1, "text": "声音偏弱，麦克风音量已调回 38%"}
    assert gain.notice == "声音偏弱，麦克风音量 30% → 38%"
    assert mic_gain.raised_text(1.0) == "声音偏弱，麦克风音量已调回 100%"


def test_no_raise_before_60_s_since_the_last_change(fake_wpctl):
    gain = calibrated(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 1
    # Also counted from the raise itself: 29 windows later nothing, the 30th raises again.
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 1))
    assert feed(gain, QUIET) == [True] and len(sets(fake_wpctl)) == 2


def test_no_raise_without_confirmed_text(fake_wpctl):
    gain = calibrated(fake_wpctl)
    assert not any(feed(gain, QUIET, 3 * RAISE_WINDOWS, texts=()))
    assert sets(fake_wpctl) == []
    # Text that ended over 60 s ago does not count either.
    assert not any(feed(gain, QUIET, 3, texts=((0.0, 10.0),)))
    # Text in the last 60 s does, at once: the 60 s since the calibration have long passed.
    assert feed(gain, QUIET, texts=((gain.clock - 10, gain.clock),)) == [True]


def test_no_raise_when_speech_peaks_are_loud_enough(fake_wpctl):
    gain = calibrated(fake_wpctl)
    assert not any(feed(gain, LOUD, 3 * RAISE_WINDOWS))
    assert sets(fake_wpctl) == []
    # The 95th percentile: 2 loud windows out of the last 30 keep it up; one (under 5 %) does not,
    # so it raises as soon as the first has left the 60 s.
    gain = calibrated(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS - 5)
    assert not any(feed(gain, LOUD, 2))
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 2))
    assert feed(gain, QUIET) == [True]


def test_only_windows_under_text_count(fake_wpctl):
    gain = calibrated(fake_wpctl)
    feed(gain, LOUD, RAISE_WINDOWS - 1, texts=())   # loud background, nobody talking
    start = gain.clock
    assert feed(gain, QUIET, texts=((start, start + 2),)) == [True]


def test_raise_never_exceeds_the_ceiling(fake_wpctl):
    gain = calibrated(fake_wpctl)
    ceiling = gain.ceiling
    for _ in range(6):
        feed(gain, QUIET, RAISE_WINDOWS)
    assert max(sets(fake_wpctl)) == pytest.approx(ceiling) and fake_wpctl.volume == pytest.approx(ceiling)
    count = len(sets(fake_wpctl))
    feed(gain, QUIET, 3 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == count
    # Without a calibration the ceiling is the volume found: never raised (the old "no lowering yet").
    fake_wpctl.calls.clear()
    fake_wpctl.volume = 0.3
    gain = mic_gain.mic_gain({"mic_volume_start": 0.85})
    feed(gain, QUIET, 3 * RAISE_WINDOWS)
    assert sets(fake_wpctl) == [] and gain.change is None


def test_raises_one_step_at_a_time(fake_wpctl):
    gain = calibrated(fake_wpctl)
    assert gain.ceiling >= 0.3 / mic_gain.GAIN_STEP ** 2
    feed(gain, QUIET, RAISE_WINDOWS)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert sets(fake_wpctl) == pytest.approx([0.3 / mic_gain.GAIN_STEP, 0.3 / mic_gain.GAIN_STEP ** 2], abs=1e-6)
    assert gain.change["id"] == 2


def test_manual_volume_above_the_ceiling_is_left_alone(fake_wpctl):
    gain = calibrated(fake_wpctl)
    fake_wpctl.volume = 0.9  # raised by hand
    feed(gain, QUIET, 2 * RAISE_WINDOWS)
    assert sets(fake_wpctl) == [] and gain.change is None


def test_muted_at_raise_time_is_never_unmuted(fake_wpctl):
    gain = calibrated(fake_wpctl)
    fake_wpctl.muted = True
    feed(gain, QUIET, RAISE_WINDOWS)
    assert sets(fake_wpctl) == [] and not gain.active and gain.notice == mic_gain.MUTED_NOTICE


def test_lowering_within_60_s_of_a_raise_locks_the_ceiling(fake_wpctl):
    gain = calibrated(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 1
    feed(gain, QUIET)                # settling window
    feed(gain, CLIP, 2)              # lowered again 6 s after the raise
    assert len(sets(fake_wpctl)) == 2 and gain.locked
    assert sets(fake_wpctl)[-1] == pytest.approx(0.3, abs=1e-6) and gain.ceiling == pytest.approx(0.3, abs=1e-6)
    assert gain.change == {"id": 2, "text": "刚才声音过大，麦克风音量已从 38% 调到 30%"}
    feed(gain, QUIET, 5 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 2


def test_lowering_more_than_60_s_after_a_raise_still_takes_the_ceiling_down(fake_wpctl):
    # R2.2's first rule (the ceiling follows any lowering) already covers the lock's effect; only the
    # flag tells them apart.
    gain = calibrated(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS)
    feed(gain, QUIET, int(mic_gain.LOCK_SECONDS / WINDOW), texts=())  # no text: no second raise
    feed(gain, CLIP, 2)
    assert len(sets(fake_wpctl)) == 2 and not gain.locked
    assert gain.ceiling == pytest.approx(0.3, abs=1e-6)
    feed(gain, QUIET, 3 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 2


def test_paused_time_never_counts(fake_wpctl):
    gain = calibrated(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS - 10)
    gain.pause(True)
    assert not any(feed(gain, QUIET, 200))  # nothing counted while paused
    gain.pause(False)
    assert not any(feed(gain, QUIET, 9))
    assert sets(fake_wpctl) == []
    assert feed(gain, QUIET) == [True]


def test_pause_ends_a_clipping_run(fake_wpctl):
    gain = mic_gain.mic_gain({})
    feed(gain, CLIP)
    gain.pause(True)
    gain.pause(False)
    assert feed(gain, CLIP) == [False]
    assert sets(fake_wpctl) == []


def test_start_volume_comes_from_session_meta(fake_wpctl):
    assert mic_gain.mic_gain({"mic_volume_start": 0.6}).start == 0.6
    for bad in (None, True, "0.6", float("nan")):
        assert mic_gain.mic_gain({"mic_volume_start": bad}).start == 0.85


def test_snapshot_passes_only_a_well_formed_change(tmp_path):
    write_json(tmp_path / "session.json", {"course": "MATH421", "output": str(tmp_path / "n.md")})
    write_json(tmp_path / "asr-state.json", {"status": "转录中", "gain_change": {"id": 2, "text": "声音偏弱，麦克风音量已调回 85%"}})
    assert sessions.snapshot(tmp_path)["asr"]["gain_change"] == {"id": 2, "text": "声音偏弱，麦克风音量已调回 85%"}
    for bad in ({"id": "2", "text": "x"}, {"id": 2, "text": ""}, "x", None):
        write_json(tmp_path / "asr-state.json", {"status": "转录中", "gain_change": bad})
        assert sessions.snapshot(tmp_path)["asr"]["gain_change"] is None


def test_two_decimal_wpctl_readings_still_reach_the_exact_ceiling():
    # The real wpctl prints two decimals: a value set as 0.629463 reads back as 0.63 (found in the
    # GUI-4 Q2 screenshot run). Readings that close are taken as the value this run set.
    state = {"volume": 0.5}
    def runner(command, **kwargs):
        if command[1] == "set-volume":
            state["volume"] = float(command[3])
        if command[1] == "inspect":
            return SimpleNamespace(stdout='node.name = "alsa_input.two-decimals"')
        return SimpleNamespace(stdout=f"Volume: {state['volume']:.2f}")
    gain = calibrated(SimpleNamespace(), background=-80.0, runner=runner, put_back=lambda: state.update(volume=0.5))
    assert gain.ceiling == 1.0  # a quiet room: R2.1's raise is capped at 100 %
    for expected in (0.5 / mic_gain.GAIN_STEP, 0.5 / mic_gain.GAIN_STEP ** 2):
        feed(gain, QUIET, RAISE_WINDOWS)
        assert state["volume"] == pytest.approx(expected, abs=1e-6)
    feed(gain, QUIET, RAISE_WINDOWS)
    # 0.7925 / step = 0.9976 is within reading precision of the ceiling: exactly 100 %.
    assert state["volume"] == 1.0 and gain.change["text"] == "声音偏弱，麦克风音量已调回 100%"
    assert gain.notice == "声音偏弱，麦克风音量 79% → 100%"
    # A reading further away is the user's own change and is used as is.
    state["volume"] = 0.5
    feed(gain, QUIET, RAISE_WINDOWS)
    assert state["volume"] == pytest.approx(0.5 / mic_gain.GAIN_STEP, abs=1e-6)
