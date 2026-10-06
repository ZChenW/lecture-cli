import sys
from types import SimpleNamespace

import pytest

from lecture_cli import asr


def test_asr_models_include_whisper_sizes_and_reject_unknown():
    names = asr.asr_models()
    assert "base.en" in names and "medium.en" in names and "large-v3" in names
    assert asr.resolve_asr_model("small.en") == "small.en"
    with pytest.raises(ValueError, match="未知语音模型"):
        asr.resolve_asr_model("not-a-model")


def test_asr_models_ordered_weakest_to_strongest():
    names = asr.asr_models()
    assert names.index("tiny.en") < names.index("base.en") < names.index("small.en")
    assert names.index("small.en") < names.index("medium.en") < names.index("large-v3")
    assert names.index("distil-small.en") < names.index("small.en")
    assert names.index("large-v3") < names.index("turbo")


def test_cpu_does_not_probe_or_override_cuda_visibility(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2")
    monkeypatch.setattr(asr.ctypes, "CDLL", lambda *a: pytest.fail("CPU must not load CUDA"))
    assert asr.select_device("cpu") == ("cpu", "")
    assert asr.runtime_environment()["CUDA_VISIBLE_DEVICES"] == "2"


def test_auto_falls_back_but_explicit_cuda_fails_when_libraries_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    def missing(*args):
        raise OSError("missing CUDA runtime")
    monkeypatch.setattr(asr.ctypes, "CDLL", missing)
    device, notice = asr.select_device("auto")
    assert device == "cpu" and notice
    with pytest.raises(RuntimeError, match="--asr-device cpu"):
        asr.select_device("cuda")


def test_gpu_factory_changes_device_without_changing_decoder_and_restores_on_error(monkeypatch):
    calls = []
    class Original:
        def transcribe(self): return "upstream beam search"
    factory = SimpleNamespace(FasterWhisperASR=Original)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **k: calls.append((a, k))))
    monkeypatch.setitem(sys.modules, "whisperlivekit.local_agreement", SimpleNamespace(whisper_online=factory))
    monkeypatch.setitem(sys.modules, "whisperlivekit.model_paths", SimpleNamespace(resolve_model_path=lambda p: p))
    with pytest.raises(RuntimeError, match="stop"):
        with asr.backend("cuda"):
            adapter = factory.FasterWhisperASR()
            adapter.load_model("base.en", "/cache")
            assert adapter.transcribe() == "upstream beam search"
            raise RuntimeError("stop")
    assert factory.FasterWhisperASR is Original
    assert calls == [(("base.en",), dict(device="cuda", compute_type="float16", download_root="/cache"))]


def test_runtime_paths_are_local_idempotent_and_keep_existing_entries(tmp_path, monkeypatch):
    for name in ("cublas", "cudnn", "cuda_nvrtc"):
        (tmp_path / "nvidia" / name / "lib").mkdir(parents=True)
    monkeypatch.setattr(asr.sysconfig, "get_path", lambda _: str(tmp_path))
    env = asr.runtime_environment({"LD_LIBRARY_PATH": "/custom/lib", "CUDA_VISIBLE_DEVICES": "0"})
    assert env["LD_LIBRARY_PATH"].endswith(":/custom/lib")
    assert len(env["LD_LIBRARY_PATH"].split(":")) == 4
    assert asr.runtime_environment(env) == env
