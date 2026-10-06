"""Device selection for the pinned WLK factory; decoding policy stays upstream."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys
import sysconfig

# Display groups, not an accuracy or speed ranking across architectures.
QWEN_MODELS = {
    "qwen3-asr-1.7b": "Qwen/Qwen3-ASR-1.7B",
    "qwen3-asr-0.6b": "Qwen/Qwen3-ASR-0.6B",
}
_ASR_MODEL_ORDER = (
    "tiny", "tiny.en",
    "base", "base.en",
    "distil-small.en", "small", "small.en",
    "distil-medium.en", "medium", "medium.en",
    "large-v1",
    "distil-large-v2", "large-v2",
    "distil-large-v3", "distil-large-v3.5", "large", "large-v3",
    "large-v3-turbo", "turbo",
)


def asr_models() -> tuple[str, ...]:
    try:
        from faster_whisper.utils import available_models
    except ModuleNotFoundError as exc:
        if exc.name != "faster_whisper":
            raise
        return tuple(QWEN_MODELS)
    available = set(available_models())
    ordered = [name for name in _ASR_MODEL_ORDER if name in available]
    extras = sorted(available - set(ordered))
    return tuple(QWEN_MODELS) + tuple(ordered + extras)


def resolve_asr_model(name: str) -> str:
    name = (name or "").strip()
    if name.lower() in QWEN_MODELS:
        return name.lower()
    models = asr_models()
    if name not in models:
        raise ValueError(f"未知语音模型：{name or '(空)'}。运行 lecture models 查看可用名称。")
    return name


def capture_python(model: str) -> str:
    if model not in QWEN_MODELS:
        return sys.executable
    path = Path(__file__).resolve().parents[1] / ".venv-qwen" / "bin" / "python"
    if not path.is_file():
        raise ValueError("Qwen 运行环境未安装，请运行 ./install-qwen.sh")
    return str(path)


def capture_environment(model: str, environ=None):
    env = dict(os.environ if environ is None else environ)
    # Qwen's isolated CUDA PyTorch uses its own libraries, not the Whisper venv's.
    return env if model in QWEN_MODELS else runtime_environment(env)


def select_qwen_device(requested="auto"):
    import torch
    if requested not in ("auto", "cuda", "cpu"):
        raise ValueError("识别设备必须为 auto、cuda 或 cpu")
    if requested == "cpu":
        return "cpu", ""
    if torch.cuda.is_available():
        return "cuda", ""
    if requested == "cuda":
        raise RuntimeError("Qwen GPU 不可用：请运行 ./install-qwen.sh 并检查 NVIDIA 驱动。")
    return "cpu", "Qwen GPU 不可用，本次使用 CPU"


def runtime_environment(environ=None):
    env = dict(os.environ if environ is None else environ)
    # NVIDIA pip wheels keep their libraries inside this interpreter's environment.
    # Supply the search path before starting Python, including cuDNN's lazy sub-libraries.
    root = Path(sysconfig.get_path("purelib")) / "nvidia"
    paths = [str(root / name / "lib") for name in ("cublas", "cudnn", "cuda_nvrtc")
             if (root / name / "lib").is_dir()]
    existing = env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    paths.extend(path for path in existing if path and path not in paths)
    if paths:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(paths)
    return env


def select_device(requested="auto"):
    if requested == "cpu":
        return "cpu", ""
    if requested not in ("auto", "cuda"):
        raise ValueError("识别设备必须为 auto、cuda 或 cpu")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            raise RuntimeError("未检测到可用 NVIDIA GPU")
        for name in ("libcublasLt.so.12", "libcublas.so.12", "libcudnn.so.9"):
            ctypes.CDLL(name)
    except (OSError, RuntimeError) as exc:
        if requested == "cuda":
            raise RuntimeError("GPU 不可用：请运行 lecture doctor 检查 CUDA 依赖，或使用 --asr-device cpu。") from exc
        return "cpu", "GPU 不可用，本次使用 CPU"
    return "cuda", ""


@contextmanager
def backend(device):
    from faster_whisper import WhisperModel
    from whisperlivekit.local_agreement import whisper_online
    from whisperlivekit.model_paths import resolve_model_path

    original = whisper_online.FasterWhisperASR

    class DeviceASR(original):
        def load_model(self, model_size=None, cache_dir=None, model_dir=None):
            return WhisperModel(str(resolve_model_path(model_dir)) if model_dir else model_size,
                                device=device,
                                compute_type="float16" if device == "cuda" else "auto",
                                download_root=cache_dir)

    # WLK's pinned factory does not expose device/compute_type. Each capture process
    # builds one engine before starting its tasks; restore the factory on all exits.
    whisper_online.FasterWhisperASR = DeviceASR
    try:
        yield
    finally:
        whisper_online.FasterWhisperASR = original


def build_engine(meta):
    from whisperlivekit import TranscriptionEngine
    if meta["asr_model"] in QWEN_MODELS:
        if meta["language"] == "auto":
            raise ValueError("Qwen 流式识别需要指定语言，请使用 --language en 或 zh")
        device, notice = select_qwen_device(meta.get("asr_device", "auto"))
        engine = TranscriptionEngine(
            model_size=QWEN_MODELS[meta["asr_model"]], lan=meta["language"],
            backend="qwen3-streaming", pcm_input=True, diarization=False,
            qwen3_streaming_device=device,
            qwen3_streaming_dtype="bfloat16" if device == "cuda" else "float32",
            qwen3_streaming_context=meta.get("asr_context", ""),
        )
        return engine, device, notice
    device, notice = select_device(meta.get("asr_device", "auto"))
    with backend(device):
        engine = TranscriptionEngine(model_size=meta["asr_model"], lan=meta["language"],
                                     backend="faster-whisper", backend_policy="localagreement",
                                     pcm_input=True, diarization=False)
    return engine, device, notice


def session_context(meta):
    # Qwen receives its hint through qwen3_streaming_context at engine creation.
    return None if meta["asr_model"] in QWEN_MODELS else meta.get("asr_context", "")


if __name__ == "__main__":
    import json
    import sys
    try:
        selector = select_qwen_device if len(sys.argv) > 2 and sys.argv[2] in QWEN_MODELS else select_device
        device, notice = selector(sys.argv[1] if len(sys.argv) > 1 else "auto")
        print(json.dumps(dict(device=device, notice=notice), ensure_ascii=False))
    except RuntimeError as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False))
        sys.exit(1)
