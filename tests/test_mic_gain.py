import asyncio
import io
import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, capture, cli, mic_gain
from lecture_cli.storage import read_json, write_json


def test_clipping_step_uses_cubic_volume_scale():
    decision = mic_gain.decide_gain(0.85, 32, 32000)
    assert decision.volume == pytest.approx(0.85 * 10 ** (-6 / 60))  # Plan GUI-4 Q2.1: 6 dB, was 12 dB.
    assert 20 * np.log10((decision.volume / 0.85) ** 3) == pytest.approx(-6)
    assert decision.notice == "检测到削波，麦克风音量 85% → 68%"
    assert decision.skip_next


def test_repeated_steps_stop_at_floor_and_warn_about_hardware():
    volume = 0.85
    adjusted = []
    while volume > 0.1:
        decision = mic_gain.decide_gain(volume, 32000, 32000)
        adjusted.append(decision.volume)
        volume = decision.volume
    assert adjusted == pytest.approx([0.675179, 0.536314, 0.426009, 0.338391, 0.268794, 0.21351, 0.169597,
                                      0.134716, 0.107009, 0.1], abs=1e-6)
    decision = mic_gain.decide_gain(volume, 32, 32000)
    assert decision.volume is None and "Mic Boost" in decision.notice


@pytest.mark.parametrize("volume,clipped", [(0.85, 0), (0.85, 31), (0.05, 0)])
def test_no_clipping_never_raises_or_lowers_gain(volume, clipped):
    assert mic_gain.decide_gain(volume, clipped, 32000) == mic_gain.Decision()


def test_skip_window_clears_skip_flag_without_changing_volume():
    first = mic_gain.decide_gain(0.85, 32, 32000)
    skipped = mic_gain.decide_gain(first.volume, 32000, 32000, skip_next=first.skip_next)
    assert skipped == mic_gain.Decision()
    assert mic_gain.decide_gain(first.volume, 32, 32000, skip_next=skipped.skip_next).volume < first.volume


def test_muted_decision_has_only_manual_notice():
    decision = mic_gain.decide_gain(0.85, 32000, 32000, muted=True)
    assert decision.volume is None and "手动" in decision.notice and "静音" in decision.notice


@pytest.mark.parametrize("device", [None, "pipewire", "default", "pulse"])
def test_default_devices_enable_gain(device, fake_wpctl):
    gain = mic_gain.mic_gain({"device": device})
    assert gain.active
    assert fake_wpctl.calls == [["wpctl", "get-volume", mic_gain.SOURCE]]


@pytest.mark.parametrize("device", [0, "12", "hw:ThinkPad", "USB Microphone"])
def test_specific_device_never_calls_wpctl(device, fake_wpctl):
    assert "不是 PipeWire 默认源" in mic_gain.device_notice(device)
    gain = mic_gain.mic_gain({"device": device})
    gain.observe(np.ones(32000))
    assert not gain.poll() and not gain.active
    assert fake_wpctl.calls == []


@pytest.mark.parametrize("meta", [{"audio_file": "lecture.wav"}, {"demo": True}, {"auto_gain": False}])
def test_file_demo_and_disabled_never_call_wpctl(meta, fake_wpctl):
    assert mic_gain.mic_gain(meta) is None
    assert fake_wpctl.calls == []


@pytest.mark.parametrize("output,expected", [("Volume: 0.85\n", (0.85, False)),
                                           ("Volume: 0.85 [MUTED]\n", (0.85, True))])
def test_read_volume_parses_external_output(output, expected):
    def runner(command, **kwargs):
        assert command == ["wpctl", "get-volume", mic_gain.SOURCE]
        assert kwargs == dict(check=True, capture_output=True, text=True, timeout=2)
        return SimpleNamespace(stdout=output)
    assert mic_gain.read_volume(runner) == expected


@pytest.mark.parametrize("failure,notice", [
    (FileNotFoundError(), "未找到 wpctl"),
    (subprocess.CalledProcessError(1, "wpctl"), "读写失败"),
    (subprocess.TimeoutExpired("wpctl", 2), "读写失败"),
    (OSError("unavailable"), "读写失败"),
    ("invalid", "无法读取"),
])
def test_wpctl_missing_or_read_failure_leaves_recording_available(failure, notice):
    def runner(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return SimpleNamespace(stdout=failure)
    gain = mic_gain.mic_gain({}, runner)
    gain.observe(np.ones(32000))
    assert not gain.active and not gain.poll() and notice in gain.notice


def test_muted_source_never_sets_volume(fake_wpctl):
    fake_wpctl.muted = True
    gain = mic_gain.mic_gain({})
    gain.observe(np.ones(32000))
    assert not gain.poll() and "已静音" in gain.notice
    assert [c[1] for c in fake_wpctl.calls] == ["get-volume"]


def test_callback_counts_raw_samples_and_waits_for_complete_window(fake_wpctl):
    gain = mic_gain.mic_gain({})
    block = np.zeros(8000, dtype=np.float32)
    block[:3] = [0.999, -0.999, 1.2]
    block[3:5] = [0.998, -0.998]
    for _ in range(3):
        gain.observe(block)
        assert not gain.poll()
    assert gain.clipped == 9 and gain.total == 24000
    assert len(fake_wpctl.calls) == 1, "observe must not spawn wpctl"
    block[:32] = 1
    gain.observe(block)
    assert not gain.poll(), "one clipping window only counts"
    assert len(fake_wpctl.calls) == 1
    for _ in range(4):
        gain.observe(block)
    assert gain.poll()
    assert fake_wpctl.calls[-1] == ["wpctl", "set-volume", mic_gain.SOURCE, "0.675179"]


def test_poll_skips_one_window_then_can_adjust_again(fake_wpctl):
    gain = mic_gain.mic_gain({})
    # Clip run, lower, settling window, a new clip run, lower again.
    for expected_sets in (0, 1, 1, 1, 2):
        gain.observe(np.ones(32000))
        gain.poll()
        assert len([c for c in fake_wpctl.calls if c[1] == "set-volume"]) == expected_sets


def test_pause_discards_partial_window_and_excludes_paused_audio(fake_wpctl):
    gain = mic_gain.mic_gain({})
    gain.observe(np.ones(16000))
    gain.pause(True)
    gain.observe(np.ones(32000))
    assert not gain.poll() and gain.total == 0
    gain.pause(False)
    gain.observe(np.ones(16000))
    assert not gain.poll()
    gain.observe(np.ones(16000))
    assert not gain.poll() and gain.total == 0 and gain.pending_clip  # a complete clipping window
    gain.observe(np.ones(32000))
    assert gain.poll()
    assert len(fake_wpctl.calls) == 3


def test_poll_reads_current_volume_before_lowering(fake_wpctl):
    gain = mic_gain.mic_gain({})
    fake_wpctl.volume = 0.35  # A user changed the volume during recording.
    gain.observe(np.ones(32000))
    assert not gain.poll()
    gain.observe(np.ones(32000))
    assert gain.poll()
    assert fake_wpctl.volume == pytest.approx(0.35 * mic_gain.GAIN_STEP, abs=1e-6)
    assert "35% → 28%" in gain.notice


def test_source_muted_during_recording_is_never_unmuted(fake_wpctl):
    gain = mic_gain.mic_gain({})
    fake_wpctl.muted = True
    gain.observe(np.ones(32000))
    assert not gain.poll()
    gain.observe(np.ones(32000))
    assert gain.poll() and "已静音" in gain.notice and not gain.active
    assert [c[1] for c in fake_wpctl.calls] == ["get-volume", "get-volume"]


def test_write_failure_disables_gain_without_aborting_recording():
    def runner(command, **kwargs):
        if command[1] == "set-volume":
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="Volume: 0.85")
    gain = mic_gain.mic_gain({}, runner)
    gain.observe(np.ones(32000))
    assert not gain.poll()
    gain.observe(np.ones(32000))
    assert gain.poll() and not gain.active and "录制照常" in gain.notice


@pytest.mark.parametrize("backend", ["local", "api"])
@pytest.mark.parametrize("missing", [False, True])
def test_backends_adjust_outside_callback_and_persist_notice(tmp_path, monkeypatch, fake_wpctl, backend, missing):
    write_json(tmp_path / "session.json", dict(language="en", asr_model="base.en",
               asr_api_base="https://example.invalid/v1", asr_api_model="test", auto_gain=True,
               refine=backend == "local"))
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-key")
    if missing:
        def unavailable(*args, **kwargs):
            raise FileNotFoundError()
        monkeypatch.setattr(mic_gain.subprocess, "run", unavailable)
    producer = None
    eof = asyncio.Event()
    recognised = []
    # PLAN-GUI-5 R2.1: the stream first serves the start calibration; it is over when the listener closes.
    calibrated = threading.Event()
    close = mic_gain.Listener.close

    def closing(self):
        close(self)
        calibrated.set()
    monkeypatch.setattr(mic_gain.Listener, "close", closing)

    class Processor:
        def __init__(self, **kwargs): pass
        async def create_tasks(self):
            async def results():
                await eof.wait()
                if False: yield None
            return results()
        async def process_audio(self, pcm):
            recognised.append(len(pcm))
            if not pcm:
                eof.set()
        async def cleanup(self): pass

    class Stream:
        def __init__(self, callback, **kwargs): self.callback = callback
        def start(self):
            nonlocal producer
            producer = asyncio.create_task(self.produce())
        def feed(self, value):
            thread = threading.Thread(target=self.callback, args=(
                np.full((8000, 1), value, dtype=np.float32), 8000, None, SimpleNamespace(input_overflow=False)))
            thread.start()
            thread.join()
        async def produce(self):
            try:
                if not missing:
                    # 1 s of quiet background (-60 dBFS): nothing to lower, and none of it is recorded.
                    for _ in range(2):
                        self.feed(0.001)
                    for _ in range(100):
                        if calibrated.is_set():
                            break
                        await asyncio.sleep(0.02)
                    assert calibrated.is_set(), "the start calibration never finished"
                    await asyncio.sleep(0.05)
                for window in range(3):
                    for _ in range(4):
                        self.feed(1.0)
                    await asyncio.sleep(0.15)
                    state = read_json(tmp_path / "asr-state.json")
                    if missing:
                        assert "未找到 wpctl" in state["gain_notice"]
                    else:
                        # Plan GUI-4 Q2.1: a clip run of two windows, then the settling window.
                        expected = (0, 1, 1)[window]
                        assert len([c for c in fake_wpctl.calls if c[1] == "set-volume"]) == expected
                        assert ("检测到削波" in state["gain_notice"]) == (window > 0)
            finally:
                (tmp_path / "stop").touch()
        def stop(self): pass
        def close(self): pass

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))
    monkeypatch.setitem(sys.modules, "whisperlivekit", SimpleNamespace(AudioProcessor=Processor))
    monkeypatch.setattr(capture, "build_engine", lambda meta: (None, "cpu", ""))

    async def exercise():
        if backend == "local":
            await capture.record(tmp_path)
        else:
            await api_capture.record(tmp_path, httpx.MockTransport(
                lambda request: httpx.Response(200, json={} if request.method == "GET" else {"text": "tail."})))
        await producer
    asyncio.run(asyncio.wait_for(exercise(), 5))
    state = read_json(tmp_path / "asr-state.json")
    # Only the six clipping seconds count: the calibration second never reached the duration,
    assert state["status"] == "转录完成" and state["captured"] == 6
    if backend == "local":
        # ... recognition or the refinement archive.
        assert sum(recognised) == 6 * 16000 * 2
        assert (tmp_path / "refinement.pcm").stat().st_size == 6 * 16000 * 2
    assert calibrated.is_set() is not missing
    if not missing:
        assert fake_wpctl.volume == pytest.approx(0.85 * mic_gain.GAIN_STEP, abs=1e-6)
        assert "85% → 68%" in state["gain_notice"]
        assert state["gain_change"] == {"id": 1, "text": "刚才声音过大，麦克风音量已从 85% 调到 68%"}
        # PLAN-GUI-5 R2.1/R2.2: the bottom bar's volume; the lowering took the ceiling down with it.
        assert state["mic_volume"] == pytest.approx(0.85 * mic_gain.GAIN_STEP, abs=1e-6)
        assert state["mic_ceiling"] == pytest.approx(0.85 * mic_gain.GAIN_STEP, abs=1e-6)
        assert state["mic_node"] == "alsa_input.fake-test-mic" and "mic_verdict" not in state
    else:
        assert "mic_volume" not in state


def test_display_shows_microphone_notice(tmp_path):
    write_json(tmp_path / "session.json", dict(course="MATH421", output="notes.md"))
    write_json(tmp_path / "asr-state.json", dict(gain_notice="检测到削波，麦克风音量 85% → 54%"))
    output = io.StringIO()
    cli.Console(file=output, width=120).print(cli.display(tmp_path))
    assert "麦克风音量" in output.getvalue() and "85% → 54%" in output.getvalue()


@pytest.mark.parametrize("option,saved,expected", [(None, None, True), (None, False, False),
                                                  ("--auto-gain", False, True), ("--no-auto-gain", True, False)])
def test_cli_gain_default_config_and_overrides(tmp_path, monkeypatch, option, saved, expected):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-key")
    (tmp_path / "MATH421").mkdir()
    if saved is not None:
        path = tmp_path / "config" / "lecture-cli"
        path.mkdir(parents=True)
        write_json(path / "config.json", {"auto_gain": saved})
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    def session(args, config, course):
        assert config["auto_gain"] is expected
        return 0
    monkeypatch.setattr(cli, "session", session)
    args = ["--courses-dir", str(tmp_path), "start", "MATH421", "--asr-backend", "api"]
    assert cli.main(args + ([option] if option else [])) == 0
