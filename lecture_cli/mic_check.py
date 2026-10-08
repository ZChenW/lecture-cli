"""Plan GUI-4 Q1.5: a live input level and a 5-second microphone test, before any recording.

Only levels leave this module: RMS and peak in dBFS per 100 ms block. No audio is kept or sent.
The GUI's GET /api/mic/level and `lecture doctor --mic-test` share the rules below.
"""
from __future__ import annotations

import math
import queue
import time

from .input_level import STILL_MEAN_DBFS, STILL_RANGE_DB

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.1          # one level every 100 ms
FLOOR_DBFS = -120.0          # digital silence, so every level is a finite number
PASSIVE_SECONDS = 2          # the start dialog judges the first 2 s it has open
TEST_SECONDS = 5             # the 测试 button and doctor --mic-test listen this long
FLOOR_BLOCKS = 5             # the starting noise floor: RMS of the first 0.5 s of the test
PASS_RISE_DB = 15.0          # a peak this far above the floor means the microphone hears a voice
STILL_HINT = "麦克风可能没有在工作：声音没有变化"
FAILED = "没有检测到明显的声音变化"
ADVICE = (
    "确认选对了输入设备（设置 → 麦克风，或运行 lecture devices）",
    "确认麦克风没有被静音、输入音量不是 0（pactl list sources 可以查看）",
    "ThinkPad 等笔记本请在 BIOS 的 Security → I/O Port Access 里确认 Microphone 已启用",
    "可以改用耳机麦克风、USB 麦克风，或用手机录音后通过 --audio-file 导入",
)


def dbfs(value: float) -> float:
    return round(20 * math.log10(value), 1) if value > 0 else FLOOR_DBFS


def block_level(block) -> dict:
    """RMS and peak of one block of float samples in [-1, 1], in dBFS."""
    import numpy as np
    data = np.asarray(block, dtype=np.float64).ravel()
    if not data.size:
        return {"rms": FLOOR_DBFS, "peak": FLOOR_DBFS}
    return {"rms": dbfs(float(np.sqrt(np.mean(data * data)))), "peak": dbfs(float(np.max(np.abs(data))))}


def still(levels: list[dict]) -> bool:
    """The start dialog's passive check: signal above -45 dBFS on average that moves less than 3 dB.
    The same thresholds as the recording's steady-noise notice (input_level, plan GUI-4 Q1.4)."""
    values = [level["rms"] for level in levels]
    return bool(values) and sum(values) / len(values) > STILL_MEAN_DBFS and max(values) - min(values) < STILL_RANGE_DB


def evaluate(levels: list[dict]) -> dict:
    """The 5-second test: passed when a peak after the first 0.5 s rises 15 dB or more above the
    noise floor of that first 0.5 s (its mean power)."""
    head, rest = levels[:FLOOR_BLOCKS], levels[FLOOR_BLOCKS:]
    power = [10 ** (level["rms"] / 10) for level in head]
    floor = 10 * math.log10(sum(power) / len(power)) if power and sum(power) > 0 else FLOOR_DBFS
    peak = max((level["peak"] for level in rest), default=FLOOR_DBFS)
    rise = peak - floor
    return {"passed": bool(rest) and rise >= PASS_RISE_DB, "floor": round(floor, 1), "peak": round(peak, 1),
            "rise": round(rise, 1)}


def open_stream(device, callback):
    """The real input: 16 kHz mono float32 in 100 ms blocks; callback gets each block's samples."""
    import sounddevice as sd

    def adapt(indata, frames, timing, status):
        callback(indata[:, 0].copy())
    return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                          blocksize=int(SAMPLE_RATE * BLOCK_SECONDS), device=device, callback=adapt)


def run_test(device, seconds: float = TEST_SECONDS, factory=None) -> dict:
    """Blocking test for the terminal; factory(device, callback) replaces the real stream in tests."""
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
    return evaluate(levels) | {"blocks": len(levels)}


def describe(result: dict) -> list[str]:
    """Terminal lines for a test result."""
    numbers = f"底噪 {result['floor']} dBFS，峰值 {result['peak']} dBFS，高出 {result['rise']} dB"
    if result["passed"]:
        return [f"✓ 麦克风测试通过：{numbers}"]
    return [f"✗ {FAILED}：{numbers}（至少需要 {PASS_RISE_DB:g} dB）"] + [f"  建议：{line}" for line in ADVICE]
