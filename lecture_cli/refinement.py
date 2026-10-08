"""Bounded local audio archive and atomic offline transcription replacement."""
from __future__ import annotations

from contextlib import ExitStack
from itertools import islice
import json
import math
import time
from pathlib import Path

from .storage import atomic_text, has_content, read_json, write_json

# Default only; each session reads refine_model from its configuration snapshot.
MODEL = "qwen3-asr-1.7b"
BYTES_PER_SECOND = 32000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
# Measured on an 8 GB RTX 5060 with 20–30 s segments: 4 is about 2.8x faster than 1
# at roughly 5.0 GiB reserved; 16 runs out of memory.
BATCH = 4
WARNING = "离线校正未完成，详细笔记使用实时转录；可能含听辨错误。"
SKIPPED = "已跳过离线校正，详细笔记依据实时转录；可能含听辨错误。"
# Plan N4: said wherever cloud refinement is chosen or announced.
UPLOAD_NOTICE = "课堂音频会上传到转录服务"


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


def label(meta: dict) -> str:
    """What the after-class pass will run, before it reports its own status."""
    if meta.get("refine_backend") == "api":
        return f"云端 {meta.get('refine_api_model') or 'whisper-large-v3'} · 下课后重新转录"
    return "Qwen 1.7B · 下课后自动重转录"


def refine(directory: Path, transcribe=None, transport=None) -> int:
    from .capture import timestamp
    state_path = directory / "refinement-state.json"
    stage = "检查音频归档"
    # Progress for observers: audio seconds done of the total, and when the first segment began
    # (model loading excluded, so a rate can be extrapolated from it).
    progress = {}
    stack = ExitStack()
    try:
        archive = read_json(directory / "archive.json")
        path = directory / "refinement.pcm"
        if (not archive.get("complete") or archive.get("error") or
                not archive.get("bytes") or path.stat().st_size != archive["bytes"]):
            raise ValueError("离线音频不完整或不存在")
        meta = read_json(directory / "session.json")
        # Plan N4: refine_backend "api" uploads the segments to the transcription service instead.
        cloud = transcribe is None and meta.get("refine_backend") == "api"
        stage = "连接云端转录服务" if cloud else "加载离线 Qwen 模型"
        progress.update(done_seconds=0, total_seconds=archive["bytes"] / BYTES_PER_SECOND, started=None)
        write_json(state_path, {"status": stage, **progress})
        if cloud:
            from .cloud_refine import CloudRefiner
            refiner = stack.enter_context(CloudRefiner(meta, transport))
            transcribe = refiner.transcribe
        if transcribe is None:
            import numpy as np
            from qwen_asr import Qwen3ASRModel
            from .asr import select_qwen_device, QWEN_MODELS
            device, _ = select_qwen_device(meta.get("asr_device", "auto"))
            model = Qwen3ASRModel.from_pretrained(
                QWEN_MODELS[meta.get("refine_model", MODEL)], dtype="bfloat16" if device == "cuda" else "float32",
                device_map="cuda:0" if device == "cuda" else "cpu",
                max_inference_batch_size=BATCH, max_new_tokens=1024,
            )
            language = meta.get("language", "en")
            language = {"en": "English", "zh": "Chinese", "auto": None}.get(language, language)

            def transcribe_batch(batch):
                audio = [(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768, 16000)
                         for _, _, pcm in batch]
                return [result.text for result in model.transcribe(
                    audio=audio, language=language, context=meta.get("asr_context", ""))]
        else:
            def transcribe_batch(batch):
                return [transcribe(pcm) for _, _, pcm in batch]

        records = []
        source = segments(path)
        while batch := list(islice(source, BATCH)):
            first = len(records) + 1
            stage = (f"离线校正第 {first}–{first + len(batch) - 1} 段 · "
                     f"{timestamp(batch[0][0])}–{timestamp(batch[-1][1])}")
            if progress["started"] is None:
                progress["started"] = time.time()
            progress["done_seconds"] = batch[0][0]
            write_json(state_path, {"status": stage, "seconds": batch[0][0], **progress})
            for (start, end, pcm), text in zip(batch, transcribe_batch(batch), strict=True):
                text = text.strip()
                # Plan GUI-4 Q1.1: punctuation alone is "no text recognized", under the rule below.
                if not has_content(text):
                    text = ""
                # Empty ASR is not evidence of silence. Reject the replacement rather
                # than silently discard this interval (including the lecture tail).
                if not text:
                    import numpy as np
                    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                    if np.max(np.abs(samples), initial=0) > 32:
                        raise ValueError("有声片段的离线转录为空")
                    text = "[近静音片段，未识别到文字]"
                records.append(dict(id=len(records) + 1, start=timestamp(start), end=timestamp(end), text=text))
            progress["done_seconds"] = batch[-1][1]
            write_json(state_path, {"status": stage, "seconds": batch[-1][1], **progress})
        atomic_text(directory / "refined.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
        progress["done_seconds"] = progress["total_seconds"]
        write_json(state_path, {"status": "离线校正完成", "complete": True,
                               "count": len(records), "seconds": archive["bytes"] / BYTES_PER_SECOND, **progress})
        return 0
    except Exception as exc:
        write_json(state_path, {**progress, "status": WARNING, "complete": False,
                               "stage": stage, "error": type(exc).__name__,
                               "reason": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        stack.close()
