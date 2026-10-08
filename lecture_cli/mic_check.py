"""The live input level and the microphone verdict, before any recording.

Only levels leave this module: RMS and peak in dBFS per 100 ms block, and whether it clipped. No
audio is kept or sent. The GUI's GET /api/mic/level and `lecture doctor --mic-test` judge the
background with the one rule of PLAN-GUI-5 R2.3 (mic_gain.judge): "音量过高", "可能没有在工作" or
nothing. There is no speak-now test any more (R2.7); a lecture sets its own volume first (R2.1).
"""
from __future__ import annotations

import math
import queue
import time

from .mic_gain import CLIP_RATIO, judge, verdict_text

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.1          # one level every 100 ms
FLOOR_DBFS = -120.0          # digital silence, so every level is a finite number
PASSIVE_SECONDS = 2          # the level bar's verdict comes after this much background
LISTEN_SECONDS = 5           # doctor --mic-test listens this long
ADVICE = (
    "确认选对了输入设备（设置 → 麦克风，或运行 lecture devices）",
    "确认麦克风没有被静音、输入音量不是 0（pactl list sources 可以查看）",
    "ThinkPad 等笔记本请在 BIOS 的 Security → I/O Port Access 里确认 Microphone 已启用",
    "可以改用耳机麦克风、USB 麦克风，或用手机录音后通过 --audio-file 导入",
)


def dbfs(value: float) -> float:
    return round(20 * math.log10(value), 1) if value > 0 else FLOOR_DBFS


def block_level(block) -> dict:
    """RMS and peak of one block of float samples in [-1, 1], in dBFS, and whether it clipped
    (PLAN-GUI-5 R2.3: the level bar turns to the warning colour and says 过载)."""
    import numpy as np
    data = np.asarray(block, dtype=np.float64).ravel()
    if not data.size:
        return {"rms": FLOOR_DBFS, "peak": FLOOR_DBFS, "clipped": False}
    return {"rms": dbfs(float(np.sqrt(np.mean(data * data)))), "peak": dbfs(float(np.max(np.abs(data)))),
            "clipped": bool(np.count_nonzero(np.abs(data) >= 0.999) / data.size >= CLIP_RATIO)}


def verdict(levels: list[dict], *, adjusts: bool = True, volume: float | None = None) -> dict:
    """PLAN-GUI-5 R2.3 before a lecture: nothing is adjusted here, so only the 10 % floor can call the
    microphone dead. text is empty when the microphone is fine."""
    result = judge(levels, adjusts=adjusts, volume=volume)
    return {"verdict": result, "text": verdict_text(result, adjusts)}


def open_stream(device, callback):
    """The real input: 16 kHz mono float32 in 100 ms blocks; callback gets each block's samples."""
    import sounddevice as sd

    def adapt(indata, frames, timing, status):
        callback(indata[:, 0].copy())
    return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                          blocksize=int(SAMPLE_RATE * BLOCK_SECONDS), device=device, callback=adapt)


def run_test(device, seconds: float = LISTEN_SECONDS, factory=None, *, adjusts=True, volume=None) -> dict:
    """Blocking listen for the terminal; factory(device, callback) replaces the real stream in tests."""
    blocks: queue.Queue = queue.Queue()
    stream = (factory or open_stream)(device, lambda block: blocks.put(block_level(block)))
    levels = []
    stream.start()
    try:
        deadline = time.monotonic() + seconds + 2  # A stream that delivers nothing still ends.
        while len(levels) < round(seconds / BLOCK_SECONDS) and time.monotonic() < deadline:
            try:
                levels.append(blocks.get(timeout=0.5))
            except queue.Empty:
                continue
    finally:
        stream.stop()
        stream.close()
    values = [level["rms"] for level in levels]
    return verdict(levels, adjusts=adjusts, volume=volume) | {
        "blocks": len(levels),
        "rms": round(sum(values) / len(values), 1) if values else FLOOR_DBFS,
        "spread": round(max(values) - min(values), 1) if values else 0.0}


def describe(result: dict) -> list[str]:
    """Terminal lines: one of the three verdicts, with the numbers behind it."""
    numbers = f"背景声 {result['rms']} dBFS，起伏 {result['spread']} dB"
    if not result["verdict"]:
        return [f"✓ 麦克风正常：{numbers}"]
    lines = [f"✗ {result['text']}：{numbers}"]
    if result["verdict"] == "dead":
        lines += [f"  建议：{line}" for line in ADVICE]
    return lines
