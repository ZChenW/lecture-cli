"""Bounded local audio archive and atomic offline transcription replacement."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .storage import atomic_text, read_json, write_json

MODEL = "qwen3-asr-1.7b"
BYTES_PER_SECOND = 32000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
WARNING = "离线校正未完成，详细笔记使用实时转录；可能含听辨错误。"


class AudioArchive:
    """Archive only PCM accepted by capture, with fail-open recording semantics."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.size = 0
        self.error = ""
        self.file = None
        try:
            self.file = (directory / "refinement.pcm").open("xb", buffering=0)
        except OSError:
            self.error = "无法建立离线音频暂存"

    def append(self, pcm: bytes):
        if self.error:
            return
        try:
            if self.size + len(pcm) > MAX_ARCHIVE_BYTES:
                raise OSError("archive limit")
            if self.file.write(pcm) != len(pcm):
                raise OSError("short write")
            self.size += len(pcm)
        except OSError:
            self.error = "离线音频暂存空间不足或写入失败"

    def close(self):
        try:
            if self.file:
                self.file.close()
        except OSError:
            self.error = "离线音频暂存关闭失败"
        try:
            write_json(self.directory / "archive.json", {
                "bytes": self.size, "error": self.error, "complete": not self.error,
            })
        except OSError:
            # Missing manifest rejects refinement; do not interrupt ASR cleanup.
            self.error = "无法保存离线音频清单"


def timeout_seconds(directory: Path) -> float:
    size = read_json(directory / "archive.json").get("bytes", 0)
    return 180 + 120 * math.ceil(size / BYTES_PER_SECOND / 20)


def segment_cut(pcm: bytes) -> int:
    """Choose the end of the quietest 20 ms frame between 20 and 30 s."""
    import numpy as np
    if len(pcm) < 30 * BYTES_PER_SECOND:
        return len(pcm)
    samples = np.frombuffer(pcm[:30 * BYTES_PER_SECOND], dtype="<i2").astype(np.float32)
    energy = np.mean(samples[20 * 16000:].reshape(-1, 320) ** 2, axis=1)
    return (20 * 16000 + (int(np.argmin(energy)) + 1) * 320) * 2


def segments(path: Path):
    """20–30 s non-overlapping segments; choose a low-energy 20 ms boundary."""
    offset = 0
    with path.open("rb") as source:
        while pcm := source.read(30 * BYTES_PER_SECOND):
            if len(pcm) % 2:
                raise ValueError("incomplete PCM sample")
            cut = segment_cut(pcm)
            source.seek(cut - len(pcm), 1)
            end = offset + cut
            yield offset / BYTES_PER_SECOND, end / BYTES_PER_SECOND, pcm[:cut]
            offset = end


def refine(directory: Path, transcribe=None) -> int:
    from .capture import timestamp
    state_path = directory / "refinement-state.json"
    stage = "检查音频归档"
    try:
        archive = read_json(directory / "archive.json")
        path = directory / "refinement.pcm"
        if (not archive.get("complete") or archive.get("error") or
                not archive.get("bytes") or path.stat().st_size != archive["bytes"]):
            raise ValueError("离线音频不完整或不存在")
        meta = read_json(directory / "session.json")
        stage = "加载离线 Qwen 模型"
        write_json(state_path, {"status": stage})
        if transcribe is None:
            import numpy as np
            from qwen_asr import Qwen3ASRModel
            from .asr import select_qwen_device, QWEN_MODELS
            device, _ = select_qwen_device(meta.get("asr_device", "auto"))
            model = Qwen3ASRModel.from_pretrained(
                QWEN_MODELS[MODEL], dtype="bfloat16" if device == "cuda" else "float32",
                device_map="cuda:0" if device == "cuda" else "cpu",
                max_inference_batch_size=1, max_new_tokens=1024,
            )
            language = meta.get("language", "en")
            language = {"en": "English", "zh": "Chinese", "auto": None}.get(language, language)

            def transcribe(pcm):
                audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
                return model.transcribe(audio=(audio, 16000), language=language,
                                        context=meta.get("asr_context", ""))[0].text

        records = []
        for index, (start, end, pcm) in enumerate(segments(path), 1):
            stage = f"离线校正第 {index} 段 · {timestamp(start)}–{timestamp(end)}"
            write_json(state_path, {"status": stage,
                                   "seconds": start})
            text = transcribe(pcm).strip()
            # Empty ASR is not evidence of silence. Reject the replacement rather
            # than silently discard this interval (including the lecture tail).
            if not text:
                import numpy as np
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                if np.max(np.abs(samples), initial=0) > 32:
                    raise ValueError("有声片段的离线转录为空")
                text = "[近静音片段，未识别到文字]"
            records.append(dict(id=index, start=timestamp(start), end=timestamp(end), text=text))
        atomic_text(directory / "refined.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        write_json(state_path, {"status": "离线校正完成", "complete": True,
                               "count": len(records), "seconds": archive["bytes"] / BYTES_PER_SECOND})
        return 0
    except Exception as exc:
        write_json(state_path, {"status": WARNING, "complete": False,
                               "stage": stage, "error": type(exc).__name__,
                               "reason": f"{type(exc).__name__}: {exc}"})
        return 1
