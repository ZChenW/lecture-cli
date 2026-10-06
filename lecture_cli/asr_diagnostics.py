"""Short, same-audio ASR comparisons without invoking the notes model."""
from __future__ import annotations

from array import array
from datetime import datetime
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave

from .asr import capture_environment, capture_python, resolve_asr_model
from .storage import atomic_text, events, read_json, source_text, write_json


SAMPLE_RATE = 16000
DEFAULT_MODELS = ("qwen3-asr-1.7b", "large-v3-turbo")
SILENCE_DBFS = -45.0
FRAME_SECONDS = 0.02


def _dbfs(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20 * math.log10(value / 32768.0)


def audio_metrics(path: Path) -> dict:
    """Measure normalized 16 kHz mono PCM without retaining samples."""
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.getnframes()
        pcm = source.readframes(frames)
    if channels != 1 or width != 2 or rate != SAMPLE_RATE:
        raise ValueError("诊断音频必须是 16 kHz、单声道、16-bit PCM WAV")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise ValueError("诊断音频为空")

    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    peak = max(abs(sample) for sample in samples)
    clipped = sum(abs(sample) >= 32760 for sample in samples)
    frame_size = round(SAMPLE_RATE * FRAME_SECONDS)
    silence_threshold = 32768 * 10 ** (SILENCE_DBFS / 20)
    silent_frames = total_frames = 0
    for start in range(0, len(samples), frame_size):
        frame = samples[start:start + frame_size]
        if not frame:
            continue
        frame_rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        silent_frames += frame_rms < silence_threshold
        total_frames += 1
    return {
        "duration_seconds": round(len(samples) / SAMPLE_RATE, 3),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "rms_dbfs": round(_dbfs(rms), 2),
        "peak_dbfs": round(_dbfs(peak), 2),
        "silence_threshold_dbfs": SILENCE_DBFS,
        "silence_percent": round(100 * silent_frames / total_frames, 2),
        "clipping_percent": round(100 * clipped / len(samples), 4),
    }


def _write_wave(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)


def record_microphone(path: Path, seconds: int, device=None) -> str:
    try:
        import sounddevice as sd

        frames = round(seconds * SAMPLE_RATE)
        recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                           device=device, blocking=True)
        _write_wave(path, recording.reshape(-1).tobytes())
        info = sd.query_devices(device, kind="input")
        return str(info.get("name", device if device is not None else "系统默认麦克风"))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        raise ValueError(f"无法从麦克风录制诊断音频（{type(exc).__name__}）") from exc


def normalize_audio(source: Path, destination: Path, seconds: int) -> None:
    try:
        subprocess.run([
            "ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(source),
            "-t", str(seconds), "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(destination),
        ], check=True)
    except FileNotFoundError as exc:
        raise ValueError("缺少 FFmpeg，无法读取诊断音频") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("无法解码诊断音频") from exc


def transcribe_audio(audio: Path, model: str, *, language: str, asr_device: str,
                     context: str, fast: bool) -> dict:
    """Run the production capture adapter in the model's isolated interpreter."""
    model = resolve_asr_model(model)
    with tempfile.TemporaryDirectory(prefix="lecture-asr-model-", dir="/tmp") as temporary:
        directory = Path(temporary)
        write_json(directory / "session.json", {
            "asr_model": model,
            "asr_device": asr_device,
            "language": language,
            "asr_context": context,
            "audio_file": str(audio),
            "fast": fast,
        })
        env = capture_environment(model)
        env.pop("DEEPSEEK_API_KEY", None)
        env["OMP_NUM_THREADS"] = "4"
        command = [
            capture_python(model), "-c",
            "import sys; from pathlib import Path; "
            "from lecture_cli.capture import run; sys.exit(run(Path(sys.argv[1])))",
            str(directory),
        ]
        began = time.monotonic()
        try:
            process = subprocess.run(command, env=env, capture_output=True, text=True,
                                     timeout=180 + 5 * audio_metrics(audio)["duration_seconds"])
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"{model} 转录超过诊断时限") from exc
        state = read_json(directory / "asr-state.json")
        records = events(directory)
        result = {
            "model": model,
            "returncode": process.returncode,
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "state": state,
            "records": records,
        }
        if process.returncode:
            error = state.get("error") or state.get("status") or "语音识别进程失败"
            result["error"] = error
            # Logs contain no API credentials; keep only a short failure tail.
            result["log_tail"] = (process.stdout + process.stderr)[-2000:]
        return result


def _safe_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model)


def _audio_assessment(metrics: dict) -> list[str]:
    findings = []
    if metrics["silence_percent"] >= 70:
        findings.append("大部分音频低于 -45 dBFS，麦克风距离、输入设备或音量可能是主要问题。")
    elif metrics["rms_dbfs"] < -35:
        findings.append("整体输入电平很低，远场语音可能难以可靠识别。")
    if metrics["clipping_percent"] >= 0.1:
        findings.append("音频存在明显削波，输入增益可能过高。")
    if not findings:
        findings.append("基础电平统计未发现大面积静音或明显削波；仍需结合两份转录判断混响、噪声和模型差异。")
    return findings


def _render_report(course: Path, metrics: dict, results: list[dict], *, keep_audio: bool,
                   source_label: str, context: str, fast: bool) -> str:
    audio_state = "已保留为 `audio.wav`" if keep_audio else "诊断音频已自动删除"
    lines = [
        f"# {course.name} ASR A/B 诊断报告",
        "",
        "> 本次只运行语音识别，没有调用 DeepSeek，也没有生成或修改课堂笔记。",
        "",
        "## 输入",
        "",
        f"- 来源：{source_label}",
        f"- 音频生命周期：{audio_state}",
        f"- 时长：{metrics['duration_seconds']:.3f} 秒；16 kHz 单声道 PCM",
        f"- 处理速度：{'尽快处理' if fast else '按实时速度重放'}",
        f"- 课程 context：{len(context)} 字符",
        "",
        "## 音频统计",
        "",
        f"- RMS：{metrics['rms_dbfs']:.2f} dBFS",
        f"- Peak：{metrics['peak_dbfs']:.2f} dBFS",
        f"- 近静音占比：{metrics['silence_percent']:.2f}%（20 ms 帧，阈值 -45 dBFS）",
        f"- 削波样本占比：{metrics['clipping_percent']:.4f}%",
        "",
        "### 自动提示",
        "",
    ]
    lines.extend(f"- {finding}" for finding in _audio_assessment(metrics))
    lines.extend(["", "## 模型结果", ""])
    for result in results:
        model = result["model"]
        records = result["records"]
        status = "完成" if result["returncode"] == 0 else f"失败：{result.get('error', '未知错误')}"
        lines.extend([
            f"### {model}",
            "",
            f"- 状态：{status}",
            f"- 用时：{result['elapsed_seconds']:.2f} 秒",
            f"- 已确认片段：{len(records)}",
            f"- 原始转录：[`{_safe_name(model)}.txt`]({_safe_name(model)}.txt)",
            "",
        ])
    lines.extend([
        "## 判断方法",
        "",
        "比较两份原始转录中完整句子、数学术语和漏句情况。没有人工参考文本时，模型之间的一致并不等于正确；如果两者都很差而近静音占比很高，应先改善收音。",
        "",
    ])
    return "\n".join(lines)


def run_diagnostic(*, course: Path, seconds: int, models: tuple[str, str], language: str,
                   device, asr_device: str, context: str, audio_file: Path | None,
                   keep_audio: bool, fast: bool) -> dict:
    if not 30 <= seconds <= 60:
        raise ValueError("诊断时长必须在 30–60 秒之间")
    if len(models) != 2 or models[0] == models[1]:
        raise ValueError("ASR 诊断需要两个不同的模型")
    models = tuple(resolve_asr_model(model) for model in models)
    for model in models:
        capture_python(model)
    if language == "auto" and any(model.startswith("qwen3-asr-") for model in models):
        raise ValueError("Qwen 流式识别需要 --language en 或 zh")
    from .glossary import validate_asr_context
    validate_asr_context(context)
    if audio_file is not None and not audio_file.is_file():
        raise ValueError("诊断音频文件不存在")

    output_root = course / "ASRDiagnostics"
    if output_root.is_symlink():
        raise ValueError("ASRDiagnostics 不能是指向其他目录的符号链接")
    output_root.mkdir(exist_ok=True)
    now = datetime.now().astimezone()
    output = output_root / f"{now:%Y-%m-%d_%H%M%S}-{uuid.uuid4().hex[:6]}"

    with tempfile.TemporaryDirectory(prefix=f"lecture-asr-diagnostic-{os.getuid()}-", dir="/tmp") as temporary:
        audio = Path(temporary) / "audio.wav"
        if audio_file is None:
            source_label = record_microphone(audio, seconds, device)
        else:
            normalize_audio(audio_file.resolve(), audio, seconds)
            source_label = str(audio_file.resolve())
        metrics = audio_metrics(audio)
        output.mkdir()
        if keep_audio:
            shutil.copy2(audio, output / "audio.wav")
        if context:
            atomic_text(output / "context.txt", context.rstrip() + "\n")

        results = []
        for model in models:
            try:
                result = transcribe_audio(audio, model, language=language,
                                          asr_device=asr_device, context=context, fast=fast)
            except (OSError, ValueError) as exc:
                result = {"model": model, "returncode": 1, "elapsed_seconds": 0,
                          "state": {}, "records": [], "error": str(exc)}
            results.append(result)
            raw = source_text(result["records"]) or "（没有产生已确认转录）"
            atomic_text(output / f"{_safe_name(model)}.txt", raw + "\n")

        serializable = {
            "created": now.isoformat(timespec="seconds"),
            "course": course.name,
            "audio": metrics,
            "source": source_label,
            "audio_retained": keep_audio,
            "language": language,
            "asr_device": asr_device,
            "context_characters": len(context),
            "fast": fast,
            "models": [{key: value for key, value in result.items()
                        if key not in ("records", "log_tail")} | {"record_count": len(result["records"])}
                       for result in results],
        }
        write_json(output / "metrics.json", serializable)
        atomic_text(output / "report.md", _render_report(
            course, metrics, results, keep_audio=keep_audio, source_label=source_label,
            context=context, fast=fast,
        ))
    return {"ok": all(result["returncode"] == 0 for result in results),
            "output": output, "models": results}
