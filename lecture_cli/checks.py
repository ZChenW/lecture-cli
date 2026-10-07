"""Environment checks as structured results; lecture doctor prints them, a GUI can show them."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .asr import QWEN_MODELS, capture_environment, capture_python


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    level: str  # "ok" | "warn" | "fail"; only "fail" makes doctor exit 1
    detail: str = ""
    hint: str = ""


def check(id, label, ok, detail="", hint="", bad="fail") -> Check:
    return Check(id, label, "ok" if ok else bad, detail, "" if ok else hint)


def weights_cached(model: str) -> bool:
    """Probe the Hugging Face cache only; never download."""
    try:
        if model in QWEN_MODELS:
            from huggingface_hub import snapshot_download
            snapshot_download(QWEN_MODELS[model], local_files_only=True)
        else:
            from faster_whisper.utils import download_model
            download_model(model, local_files_only=True)
        return True
    except Exception:
        return False


def mic_volume(runner) -> Check:
    from .mic_gain import VolumeError, read_volume
    try:
        volume, muted = read_volume(runner)
    except VolumeError as exc:
        # Recording works without automatic gain, so this never fails the whole check.
        return Check("mic_volume", "默认源麦克风音量", "warn", str(exc))
    return Check("mic_volume", "默认源麦克风音量", "ok", f"{volume:.0%}" + ("（已静音）" if muted else ""))


def asr_device(config, runner) -> Check:
    model = config["asr_model"]
    try:
        probe = (runner or subprocess.run)([capture_python(model, config.get("qwen_python")), "-m", "lecture_cli.asr",
                        config.get("asr_device", "auto"), model],
                       env=capture_environment(model), capture_output=True, text=True, timeout=30)
        result = json.loads(probe.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return Check("asr_device", "识别设备检查", "fail", hint="运行 ./install.sh 重新安装本地依赖")
    precision = "BF16" if model in QWEN_MODELS else "FP16"
    detail = (f"NVIDIA GPU · {precision}" if result.get("device") == "cuda" else
              result.get("error") or result.get("notice") or "CPU")
    hint = "./install-qwen.sh" if model in QWEN_MODELS else "./install.sh --gpu"
    return check("asr_device", "识别设备检查", probe.returncode == 0, detail, f"运行 {hint} 并检查 NVIDIA 驱动")


def refine_ready(config, cached) -> Check:
    model = config.get("refine_model", "qwen3-asr-1.7b")
    try:
        capture_python(model, config.get("qwen_python"))
    except ValueError as exc:
        return Check("refine", "课后校正", "fail", str(exc), "运行 ./install-qwen.sh，或在配置中设置 qwen_python")
    # Missing weights do not stop a lecture: refinement then falls back to the live transcript.
    return check("refine", "课后校正", cached(model), model, f"运行 lecture prepare --asr-model {model}", bad="warn")


# fontconfig has no serif property; CJK families say it in their names (Noto Serif CJK, Source Han
# Serif, AR PL UMing, SimSun, MS Mincho ...). Kai faces are neither, and count as neither.
CJK_SERIF = re.compile(r"serif|song|sun\b|ming|mincho|宋|明", re.I)
CJK_SANS = re.compile(r"sans|hei|gothic|黑", re.I)
FONT_HINT = "安装 Noto CJK 字体（含衬线的 Noto Serif CJK），例如 Arch 的 noto-fonts-cjk、Debian/Ubuntu 的 fonts-noto-cjk"


def cjk_font(runner) -> Check:
    """The UI uses a CJK sans; the reader's titles and body need a CJK serif, or fall back to sans."""
    try:
        listing = (runner or subprocess.run)(["fc-list", ":lang=zh", "family"],
                                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    families = sorted({name.strip() for line in listing.splitlines() for name in line.split(",") if name.strip()})
    def pick(pattern):
        found = [name for name in families if pattern.search(name)]
        return next((name for name in found if " SC" in name), found[0] if found else "")
    sans, serif = pick(CJK_SANS), pick(CJK_SERIF)
    detail = f"无衬线 {sans or '未找到'} · 衬线 {serif or '未找到'}"
    if not serif and sans:
        hint = "未找到中文衬线字体，笔记阅读界面的标题和正文将改用无衬线字体；" + FONT_HINT
    else:
        hint = FONT_HINT
    return check("cjk_font", "中文字体", bool(sans and serif), detail, hint, bad="warn")


def microphone(config) -> Check:
    try:
        import sounddevice as sd
        sd.check_input_settings(device=config.get("device"), channels=1, samplerate=16000)
        ok = True
    except Exception:
        ok = False
    return check("microphone", "麦克风格式（未开始录音）", ok, hint="运行 lecture devices 选择可用的麦克风")


def service(id, label, result) -> Check:
    return Check(id, label, result.level, result.message, "" if result.ok else "检查服务地址、模型名称与 key")


def run_checks(config, *, environ=None, runner=None, which=None, cached=None, transport=None) -> list[Check]:
    """Keys come from environ (default os.environ, after config.load_keys()); details never contain them."""
    from .providers import notes_label, test_asr, test_notes
    environ = os.environ if environ is None else environ
    which = which or shutil.which
    cached = cached or weights_cached
    notes_key = environ.get("LECTURE_NOTES_API_KEY", "")
    root = config.get("courses_dir")
    results = [
        mic_volume(runner),
        check("courses_dir", "课程目录", bool(root) and Path(root).is_dir(), root or "尚未设置",
              "运行 lecture setup 设置课程目录"),
        check("notes_key", "笔记服务 key", bool(notes_key),
              hint="运行 lecture setup，或设置 LECTURE_NOTES_API_KEY"),
        check("ffmpeg", "FFmpeg", bool(which("ffmpeg")), hint="用系统包管理器安装 ffmpeg"),
    ]
    if config.get("asr_backend") == "api":
        asr_key = environ.get("LECTURE_ASR_API_KEY", "")
        results.append(check("asr_key", "转录 key", bool(asr_key),
                             hint="设置 LECTURE_ASR_API_KEY 或写入配置目录的 asr-api-key 文件"))
        results.append(service("asr_service", "转录服务", test_asr(config, asr_key, transport)))
    else:
        results.append(check("whisperlivekit", "WhisperLiveKit", importlib.util.find_spec("whisperlivekit") is not None,
                             hint="运行 ./install.sh 安装本地转录依赖"))
        results.append(asr_device(config, runner))
        results.append(check("asr_weights", "语音模型权重", cached(config["asr_model"]), config["asr_model"],
                             f"运行 lecture prepare --asr-model {config['asr_model']}", bad="warn"))
        if config.get("refine"):
            results.append(refine_ready(config, cached))
    results += [
        microphone(config),
        check("wpctl", "wpctl", bool(which("wpctl")), hint="安装 WirePlumber 以启用自动降低削波音量", bad="warn"),
        cjk_font(runner),
        service("notes_service", f"笔记服务（{notes_label(config)}）", test_notes(config, notes_key, transport)),
    ]
    return results
