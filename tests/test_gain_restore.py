"""N3.5: a lecture's automatic volume steps never carry over into the next lecture."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from lecture_cli import checks, mic_gain
from test_lifecycle import ROOT, own_session, stub_runtime, wait_until  # noqa: F401 (fixture)


def test_no_adjustment_never_touches_wpctl(fake_wpctl):
    assert mic_gain.restore_volume(0.85, None) == ""
    assert mic_gain.restore_volume(None, 0.54) == ""
    assert fake_wpctl.calls == []


def test_unchanged_adjustment_is_put_back(fake_wpctl):
    fake_wpctl.volume = 0.54  # wpctl prints two decimals of the 0.536313 auto-gain set.
    assert mic_gain.restore_volume(0.85, 0.536313) == "已恢复为开始时的 85%。"
    assert fake_wpctl.calls[-1] == ["wpctl", "set-volume", mic_gain.SOURCE, "0.850000"]
    assert fake_wpctl.volume == 0.85


def test_manual_change_after_the_last_step_is_left_alone(fake_wpctl):
    fake_wpctl.volume = 0.70
    notice = mic_gain.restore_volume(0.85, 0.536313)
    assert "手动调整" in notice and "70%" in notice and "85%" in notice
    assert [call[1] for call in fake_wpctl.calls] == ["get-volume"]
    assert fake_wpctl.volume == 0.70


def test_missing_wpctl_is_only_a_notice(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("wpctl")
    assert "wpctl 不可用" in mic_gain.restore_volume(0.85, 0.54, runner=missing)


def test_gain_records_the_last_volume_it_set(fake_wpctl):
    import numpy as np
    gain = mic_gain.mic_gain({})
    assert gain.adjusted is None
    gain.observe(np.ones(32000))
    assert not gain.poll() and gain.adjusted is None  # Plan GUI-4 Q2.1: one clipping window only counts.
    gain.observe(np.ones(32000))
    assert gain.poll()
    assert gain.adjusted == pytest.approx(0.85 * mic_gain.GAIN_STEP)


@pytest.mark.parametrize("meta,expected", [({}, 0.85), ({"device": "pulse"}, 0.85), ({"device": "hw:USB"}, None),
                                           ({"audio_file": "a.wav"}, None), ({"demo": True}, None),
                                           ({"auto_gain": False}, None)])
def test_start_volume_only_when_auto_gain_may_run(fake_wpctl, meta, expected):
    assert mic_gain.start_volume(meta) == expected
    assert bool(fake_wpctl.calls) == (expected is not None)


@pytest.mark.parametrize("volume,level", [(0.27, "warn"), (0.399, "warn"), (0.40, "ok"), (0.85, "ok")])
def test_doctor_warns_below_40_percent(fake_wpctl, volume, level):
    fake_wpctl.volume = volume
    result = checks.mic_volume(None)
    assert result.level == level
    assert ("上课时声音可能过弱" in result.hint) == (level == "warn")


# Real controller processes with a fake wpctl on PATH; the synthetic capture lowers the volume once.
FAKE_WPCTL = r'''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_WPCTL_STATE"]
with open(state) as f:
    volume = json.load(f)["volume"]
with open(state + ".log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1] == "set-volume":
    with open(state, "w") as f:
        json.dump({"volume": float(sys.argv[3])}, f)
else:
    print(f"Volume: {volume:.2f}")
'''

SHIM = '''
if os.environ.get("LECTURE_TEST_PRIVATE_APP"):
    # Plan GUI-4 Q2: any other lecture process of this user (a GUI server starting or finding a
    # stale record, any lecture command) runs cli.reap_stale_sessions(), which scans every
    # /tmp/lecture-<uid>-* workspace. Between the crash test killing its controller and running its
    # own recovery, such a foreign reaper could recover this workspace first, without the fake
    # wpctl on its PATH, and the test then found nothing to restore. The test's controller tags its
    # session.json with a private app name that foreign reapers skip; only processes running this
    # shim read the tag back as lecture-cli-v1.
    from lecture_cli import cli as _cli
    _write_json, _read_json = _cli.write_json, _cli.read_json
    def _private_write(path, value):
        if path.name == "session.json" and value.get("app") == "lecture-cli-v1":
            value = {**value, "app": "lecture-cli-v1+test-private"}
        _write_json(path, value)
    def _private_read(path):
        value = _read_json(path)
        if path.name == "session.json" and value.get("app") == "lecture-cli-v1+test-private":
            value = {**value, "app": "lecture-cli-v1"}
        return value
    _cli.write_json, _cli.read_json = _private_write, _private_read
if "_capture" in sys.argv and os.environ.get("LECTURE_TEST_GAIN"):
    from lecture_cli import capture, mic_gain
    from lecture_cli.storage import write_json
    def run(directory, _run=capture.run):
        gain = mic_gain.MicGain(None)
        mic_gain.run_wpctl(["set-volume", mic_gain.SOURCE, "0.536313"], gain.runner)
        write_json(directory / "asr-state.json", {"status": "test-ready", "gain_volume": 0.536313})
        manual = os.environ.get("LECTURE_TEST_MANUAL")
        if manual:
            mic_gain.run_wpctl(["set-volume", mic_gain.SOURCE, manual], gain.runner)
        transcript = capture.Transcript(directory)
        transcript.append("An eigenvector is nonzero.", 0, 1)
        (directory / "test-ready").touch()
        while not (directory / "stop").exists():
            time.sleep(0.05)
        write_json(directory / "asr-state.json", {"status": "转录完成", "gain_volume": 0.536313})
        return 0
    capture.run = run
'''


@pytest.fixture
def wpctl_runtime(stub_runtime, tmp_path):
    root, env = stub_runtime
    shim = Path(env["PYTHONPATH"].split(":")[0]) / "sitecustomize.py"
    shim.write_text(shim.read_text() + SHIM)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wpctl").write_text(FAKE_WPCTL)
    (bin_dir / "wpctl").chmod(0o755)
    state = tmp_path / "wpctl.json"
    state.write_text(json.dumps({"volume": 0.85}))
    env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_WPCTL_STATE=str(state), LECTURE_TEST_GAIN="1",
               LECTURE_TEST_PRIVATE_APP="1")
    return root, env, state


def start(root, env):
    return subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421", "--headless", "--interval", "1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def volume(state):
    return json.loads(state.read_text())["volume"]


@pytest.mark.parametrize("end", ["stop", "discard"])
def test_normal_end_and_discard_put_the_start_volume_back(wpctl_runtime, end):
    root, env, state = wpctl_runtime
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        assert json.loads((directory / "session.json").read_text())["mic_volume_start"] == 0.85
        assert volume(state) == 0.536313
        if end == "discard":
            wait_until(lambda: json.loads((directory / "controller-state.json").read_text())["phase"] == "recording")
        (directory / end).touch()
        output = proc.communicate(timeout=15)[0]
        assert proc.returncode == 0, output
        assert volume(state) == 0.85
        assert "麦克风音量：已恢复为开始时的 85%。" in output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_manual_change_during_class_is_kept(wpctl_runtime):
    root, env, state = wpctl_runtime
    env["LECTURE_TEST_MANUAL"] = "0.7"
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        (directory / "stop").touch()
        output = proc.communicate(timeout=15)[0]
        assert proc.returncode == 0, output
        assert volume(state) == 0.7
        assert "手动调整过音量（现为 70%）" in output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_run_without_adjustment_never_sets_the_volume(stub_runtime, tmp_path):
    root, env = stub_runtime
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wpctl").write_text(FAKE_WPCTL)
    (bin_dir / "wpctl").chmod(0o755)
    state = tmp_path / "wpctl.json"
    state.write_text(json.dumps({"volume": 0.85}))
    env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_WPCTL_STATE=str(state))
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        (directory / "stop").touch()
        output = proc.communicate(timeout=15)[0]
        assert proc.returncode == 0, output
        assert "set-volume" not in Path(str(state) + ".log").read_text()
        assert "麦克风音量" not in output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_without_wpctl_the_run_ends_normally(stub_runtime):
    root, env = stub_runtime
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                  if not (Path(p) / "wpctl").exists())
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        assert json.loads((directory / "session.json").read_text())["mic_volume_start"] is None
        (directory / "stop").touch()
        output = proc.communicate(timeout=15)[0]
        assert proc.returncode == 0, output
        assert "已保存" in output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_crashed_run_is_restored_on_the_next_launch(wpctl_runtime):
    root, env, state = wpctl_runtime
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        meta = json.loads((directory / "session.json").read_text())
        proc.kill()
        proc.wait()
        for pid in meta["children"]:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        def stopped(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
            except OSError:
                return True
        wait_until(lambda: all(stopped(pid) for pid in meta["children"]))
        assert volume(state) == 0.536313
        result = subprocess.run([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root), "courses"],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
        # The workspace was invisible to any other lecture process (see LECTURE_TEST_PRIVATE_APP).
        assert meta["app"] == "lecture-cli-v1+test-private"
        assert volume(state) == 0.85, result.stdout + result.stderr
        assert "上次录制的麦克风音量：已恢复为开始时的 85%。" in result.stdout
        assert not directory.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
