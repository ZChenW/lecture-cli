from array import array
import json
from pathlib import Path
import wave

import pytest

from lecture_cli import cli
from lecture_cli import asr_diagnostics as diagnostics


def wav(path: Path, samples, rate=16000):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(array("h", samples).tobytes())


def test_audio_metrics_distinguish_silence_signal_and_clipping(tmp_path):
    path = tmp_path / "sample.wav"
    # Two 20 ms silent frames, one ordinary signal frame, one clipped frame.
    samples = [0] * 640 + [4000, -4000] * 160 + [32767, -32768] * 160
    wav(path, samples)

    result = diagnostics.audio_metrics(path)

    assert result["duration_seconds"] == pytest.approx(0.08)
    assert result["silence_percent"] == pytest.approx(50.0)
    assert result["clipping_percent"] == pytest.approx(25.0)
    assert -7 < result["rms_dbfs"] < -5
    assert result["peak_dbfs"] == pytest.approx(0.0, abs=0.01)


def test_diagnostic_replays_one_normalized_audio_and_removes_it_by_default(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    source = tmp_path / "source.wav"
    wav(source, [1200, -1200] * 8000)
    calls = []

    def transcribe(audio, model, **kwargs):
        calls.append((audio, model, kwargs))
        return {
            "model": model,
            "returncode": 0,
            "elapsed_seconds": 1.25,
            "state": {"status": "转录完成", "asr_device": "cuda"},
            "records": [{"id": 1, "start": "00:00:00.00", "end": "00:00:01.00",
                         "text": f"raw words from {model}"}],
        }

    monkeypatch.setattr(diagnostics, "transcribe_audio", transcribe)
    result = diagnostics.run_diagnostic(
        course=course,
        seconds=30,
        models=("qwen3-asr-1.7b", "large-v3-turbo"),
        language="en",
        device=None,
        asr_device="cuda",
        context="linear transformation eigenvalue",
        audio_file=source,
        keep_audio=False,
        fast=True,
    )

    output = result["output"]
    assert result["ok"] is True
    assert [call[1] for call in calls] == ["qwen3-asr-1.7b", "large-v3-turbo"]
    assert calls[0][0] == calls[1][0]
    assert not calls[0][0].exists()
    assert not (output / "audio.wav").exists()
    assert "raw words from qwen3-asr-1.7b" in (output / "qwen3-asr-1.7b.txt").read_text()
    assert "raw words from large-v3-turbo" in (output / "large-v3-turbo.txt").read_text()
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["audio"]["duration_seconds"] == pytest.approx(1.0)
    report = (output / "report.md").read_text()
    assert "没有调用 DeepSeek" in report
    assert "诊断音频已自动删除" in report


def test_diagnostic_keeps_normalized_audio_only_when_requested(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    source = tmp_path / "source.wav"
    wav(source, [1000, -1000] * 8000)
    monkeypatch.setattr(diagnostics, "transcribe_audio", lambda audio, model, **kwargs: {
        "model": model, "returncode": 0, "elapsed_seconds": 1,
        "state": {"status": "转录完成"}, "records": [],
    })

    result = diagnostics.run_diagnostic(
        course=course, seconds=30, models=("base.en", "small.en"), language="en",
        device=None, asr_device="cpu", context="", audio_file=source,
        keep_audio=True, fast=True,
    )

    assert (result["output"] / "audio.wav").is_file()


def test_diagnostic_rejects_qwen_auto_language_before_recording(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    monkeypatch.setattr(diagnostics, "record_microphone",
                        lambda *args, **kwargs: pytest.fail("must reject before recording"))

    with pytest.raises(ValueError, match="--language en 或 zh"):
        diagnostics.run_diagnostic(
            course=course, seconds=30,
            models=("qwen3-asr-1.7b", "large-v3-turbo"), language="auto",
            device=None, asr_device="auto", context="", audio_file=None,
            keep_audio=False, fast=False,
        )

    assert not (course / "ASRDiagnostics").exists()


def test_cli_diagnostic_does_not_load_deepseek_credentials(tmp_path, monkeypatch, capsys):
    course = tmp_path / "MATH421"
    course.mkdir()
    audio = tmp_path / "input.wav"
    wav(audio, [0] * 16000)
    calls = []

    def config(load_key=True):
        calls.append(load_key)
        return {"courses_dir": str(tmp_path), "model": "deepseek-flash",
                "asr_model": "base.en", "language": "en", "interval": 60,
                "device": None, "asr_device": "auto"}

    monkeypatch.setattr(cli, "configuration", config)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    monkeypatch.setattr(diagnostics, "run_diagnostic", lambda **kwargs: {
        "ok": True, "output": course / "ASRDiagnostics" / "result", "models": [],
    })

    assert cli.main(["diagnose-asr", "MATH421", "--audio-file", str(audio), "--seconds", "30"]) == 0
    assert calls == [False]
    assert "ASRDiagnostics" in capsys.readouterr().out


@pytest.mark.parametrize("seconds", [0, 29, 61, 1000])
def test_cli_rejects_diagnostic_duration_outside_30_to_60(tmp_path, monkeypatch, seconds):
    (tmp_path / "MATH421").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    assert cli.main(["--courses-dir", str(tmp_path), "diagnose-asr", "MATH421",
                     "--seconds", str(seconds)]) == 1
