"""Plan GUI-4 Q2: lower only after two clipping windows, by 6 dB; raise back while the input is weak."""
from types import SimpleNamespace

import numpy as np
import pytest

from lecture_cli import mic_gain
from lecture_cli.gui import sessions
from lecture_cli.storage import write_json

CLIP = np.ones(mic_gain.WINDOW_SAMPLES)
QUIET = np.full(mic_gain.WINDOW_SAMPLES, 0.01)
WINDOW = mic_gain.WINDOW_SAMPLES / mic_gain.SAMPLE_RATE  # 2 s of recorded audio
RAISE_WINDOWS = int(mic_gain.RAISE_SECONDS / WINDOW)       # 90


def sets(fake):
    return [float(c[3]) for c in fake.calls if c[1] == "set-volume"]


def feed(gain, samples, count=1, weak=True):
    results = []
    for _ in range(count):
        gain.observe(samples)
        results.append(gain.poll(weak=weak))
    return results


def lowered(fake, meta=None):
    gain = mic_gain.mic_gain(meta or {})
    feed(gain, CLIP, 2)
    assert sets(fake) == [pytest.approx(0.85 * mic_gain.GAIN_STEP)]
    return gain


def test_step_is_6_db_and_two_windows():
    assert mic_gain.GAIN_STEP == 10 ** (-6 / 60) and mic_gain.CLIP_WINDOWS == 2
    assert 20 * np.log10(mic_gain.GAIN_STEP ** 3) == pytest.approx(-6)


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


def test_floor_and_hardware_hint_unchanged(fake_wpctl):
    fake_wpctl.volume = 0.1
    gain = mic_gain.mic_gain({})
    assert feed(gain, CLIP, 2) == [False, True]
    assert sets(fake_wpctl) == [] and "Mic Boost" in gain.notice and gain.change is None


def test_raises_after_three_quiet_weak_minutes(fake_wpctl):
    gain = lowered(fake_wpctl)
    # The first window after the change only waits for it; 2 s each, 180 s after the lowering.
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 1))
    assert len(sets(fake_wpctl)) == 1
    assert feed(gain, QUIET) == [True]
    assert sets(fake_wpctl)[-1] == pytest.approx(0.85, abs=1e-6)
    assert gain.adjusted == pytest.approx(0.85, abs=1e-6)  # gain_volume follows raises too (plan Q2.2)
    assert gain.change == {"id": 2, "text": "声音偏弱，麦克风音量已调回 85%"}
    assert gain.notice == "声音偏弱，麦克风音量 68% → 85%"
    assert mic_gain.raised_text(1.0) == "声音偏弱，麦克风音量已调回 100%"
    # Already back at the start value: no further raise.
    feed(gain, QUIET, 3 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 2


def test_never_raises_without_a_lowering_in_this_run(fake_wpctl):
    fake_wpctl.volume = 0.3
    gain = mic_gain.mic_gain({"mic_volume_start": 0.85})
    feed(gain, QUIET, 3 * RAISE_WINDOWS)
    assert sets(fake_wpctl) == [] and gain.change is None


def test_never_raises_unless_weak(fake_wpctl):
    gain = lowered(fake_wpctl)
    feed(gain, QUIET, 3 * RAISE_WINDOWS, weak=False)
    assert len(sets(fake_wpctl)) == 1
    assert feed(gain, QUIET, weak=True) == [True]  # Once the weak notice shows, the time already counts.
    assert len(sets(fake_wpctl)) == 2


def test_never_raises_within_three_minutes_of_any_clipping_window(fake_wpctl):
    gain = lowered(fake_wpctl)
    feed(gain, QUIET, 50)
    assert feed(gain, CLIP) == [False]  # one window: no lowering, but it restarts the 3 minutes
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 1))
    assert len(sets(fake_wpctl)) == 1
    assert feed(gain, QUIET) == [True]


def test_never_raises_within_three_minutes_of_the_last_change(fake_wpctl):
    gain = lowered(fake_wpctl)
    gain.since_clip = 10 * mic_gain.RAISE_SECONDS  # isolate the "since the last change" condition
    assert not any(feed(gain, QUIET, RAISE_WINDOWS - 1))
    assert feed(gain, QUIET) == [True]


def test_raise_never_exceeds_the_start_volume(fake_wpctl):
    # The run started at 70% and the user raised it to 85% before the first lowering.
    gain = lowered(fake_wpctl, {"mic_volume_start": 0.7})
    feed(gain, QUIET, RAISE_WINDOWS)
    assert sets(fake_wpctl)[-1] == pytest.approx(0.7) and gain.change["text"] == "声音偏弱，麦克风音量已调回 70%"


def test_raises_one_step_at_a_time(fake_wpctl):
    gain = mic_gain.mic_gain({})
    feed(gain, CLIP, 2)
    feed(gain, CLIP, 3)  # skip window, then a new two-window run
    assert sets(fake_wpctl) == pytest.approx([0.675179, 0.536314], abs=1e-6)
    feed(gain, QUIET, RAISE_WINDOWS + 1)
    assert sets(fake_wpctl)[-1] == pytest.approx(0.675179, abs=1e-6)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert sets(fake_wpctl)[-1] == pytest.approx(0.85, abs=1e-6)
    assert [gain.change["id"], len(sets(fake_wpctl))] == [4, 4]


def test_manual_volume_above_start_is_left_alone(fake_wpctl):
    gain = lowered(fake_wpctl)
    fake_wpctl.volume = 0.9  # raised by hand
    feed(gain, QUIET, 2 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 1 and gain.change["id"] == 1


def test_muted_at_raise_time_is_never_unmuted(fake_wpctl):
    gain = lowered(fake_wpctl)
    fake_wpctl.muted = True
    feed(gain, QUIET, RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 1 and not gain.active and gain.notice == mic_gain.MUTED_NOTICE


def test_lowering_within_60_s_of_a_raise_locks_raises(fake_wpctl):
    gain = lowered(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 2
    feed(gain, QUIET)                # settling window
    feed(gain, CLIP, 2)              # lowered again 6 s after the raise
    assert len(sets(fake_wpctl)) == 3 and gain.locked
    assert gain.change == {"id": 3, "text": "刚才声音过大，麦克风音量已从 85% 调到 68%"}
    feed(gain, QUIET, 5 * RAISE_WINDOWS)
    assert len(sets(fake_wpctl)) == 3


def test_lowering_more_than_60_s_after_a_raise_does_not_lock(fake_wpctl):
    gain = lowered(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS)
    feed(gain, QUIET, int(mic_gain.LOCK_SECONDS / WINDOW))  # 60 s after the raise, then 2 more windows
    feed(gain, CLIP, 2)
    assert len(sets(fake_wpctl)) == 3 and not gain.locked
    feed(gain, QUIET, RAISE_WINDOWS + 1)
    assert len(sets(fake_wpctl)) == 4


def test_paused_time_never_counts(fake_wpctl):
    gain = lowered(fake_wpctl)
    feed(gain, QUIET, RAISE_WINDOWS - 10)
    gain.pause(True)
    assert not any(feed(gain, QUIET, 200))  # nothing counted while paused
    gain.pause(False)
    assert not any(feed(gain, QUIET, 9))
    assert len(sets(fake_wpctl)) == 1
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


def test_two_decimal_wpctl_readings_still_raise_back_to_the_exact_start():
    # The real wpctl prints two decimals: 1.0 lowered to 0.794328 reads back as 0.79 (found in the
    # Q2 screenshot run, where the raise first went to 99%).
    state = {"volume": 1.0}
    calls = []
    def runner(command, **kwargs):
        calls.append(command[1:])
        if command[1] == "set-volume":
            state["volume"] = float(command[3])
        return SimpleNamespace(stdout=f"Volume: {state['volume']:.2f}")
    gain = mic_gain.MicGain(None, runner, start=1.0)
    feed(gain, CLIP, 2)
    assert state["volume"] == pytest.approx(0.794328, abs=1e-6)
    feed(gain, QUIET, RAISE_WINDOWS)
    assert state["volume"] == 1.0 and gain.change["text"] == "声音偏弱，麦克风音量已调回 100%"
    assert gain.notice == "声音偏弱，麦克风音量 79% → 100%"
    # A second lowering (over 60 s later, so no lock) starts from the value set, not the rounded reading.
    feed(gain, QUIET, 31)
    feed(gain, CLIP, 2)
    assert not gain.locked
    assert state["volume"] == pytest.approx(0.794328, abs=1e-6)
    # A reading further away is the user's own change and is used as is.
    state["volume"] = 0.5
    feed(gain, QUIET, RAISE_WINDOWS)
    assert state["volume"] == pytest.approx(0.5 / mic_gain.GAIN_STEP, abs=1e-6)
