"""Bounded /tmp PCM ring buffer shared by the capture callback and ASR consumer."""
import os
import tempfile
import threading
from pathlib import Path


class AudioBuffer:
    # About 35 minutes at mono 16 kHz s16le; recycled, never an unbounded recording.
    def __init__(self, directory: Path, capacity: int = 64 * 1024 * 1024):
        self.file = tempfile.TemporaryFile(dir=directory)
        self.capacity = capacity
        self.written = self.read = 0
        self.lock = threading.Lock()
        self.level = 0.0
        self.overflows = 0
        self.error = ""
        self.accepting = True

    def push(self, pcm: bytes, level: float, host_overflow: bool) -> None:
        with self.lock:
            if not self.accepting or self.error:
                return
            self.level = level
            self.overflows += int(host_overflow)
            if self.written - self.read + len(pcm) > self.capacity:
                self.error = "音频暂存已达上限，已停止采集；正在处理已接收音频。"
                return
            try:
                offset = self.written % self.capacity
                first = min(len(pcm), self.capacity - offset)
                for position, block in ((offset, pcm[:first]), (0, pcm[first:])):
                    if block and os.pwrite(self.file.fileno(), block, position) != len(block):
                        raise OSError("short PCM write")
                self.written += len(pcm)
            except OSError:
                self.error = "无法写入 /tmp 音频暂存，已停止采集；正在处理已接收音频。"

    def pop(self, size: int = 16000) -> bytes:
        with self.lock:
            size = min(size, self.written - self.read)
            offset = self.read % self.capacity
            first = min(size, self.capacity - offset)
            result = os.pread(self.file.fileno(), first, offset)
            if first < size:
                result += os.pread(self.file.fileno(), size - first, 0)
            if len(result) != size:
                raise OSError("无法完整读取音频暂存")
            self.read += size
            return result

    def snapshot(self) -> dict:
        with self.lock:
            return dict(captured=self.written / 32000, queued=(self.written - self.read) / 32000,
                        level=self.level, input_overflows=self.overflows, capture_error=self.error)

    def pause(self, paused: bool) -> None:
        with self.lock:
            self.accepting = not paused
            if paused:
                self.level = 0

    def close(self) -> None:
        with self.lock:
            self.accepting = False
            self.file.close()


def drain_timeout(state: dict) -> float:
    return max(90.0, 3 * (state.get("queued", 0) + state.get("lag", 0)) + 90)
