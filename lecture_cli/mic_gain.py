"""Lower the PipeWire default source gain when captured audio clips."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import subprocess
import threading

import numpy as np

SOURCE = "@DEFAULT_AUDIO_SOURCE@"
WINDOW_SAMPLES = 2 * 16000
CLIP_RATIO = 0.001
GAIN_STEP = 10 ** (-12 / 60)  # PipeWire volume v represents linear gain v³.
MIN_VOLUME = 0.10
MUTED_NOTICE = "默认麦克风已静音；请手动取消静音，自动调节不会取消静音。"
# wpctl get-volume prints two decimals, so a value set as 0.536313 reads back as 0.54.
SAME_VOLUME = 0.006


@dataclass(frozen=True)
class Decision:
    volume: float | None = None
    notice: str = ""
    skip_next: bool = False


def decide_gain(volume: float, clipped: int, total: int, *, muted=False, skip_next=False) -> Decision:
    """Evaluate one complete audio window, without touching system volume."""
    if muted:
        return Decision(notice=MUTED_NOTICE)
    if skip_next:
        return Decision()
    if clipped / total < CLIP_RATIO:
        return Decision()
    if volume <= MIN_VOLUME:
        return Decision(notice="麦克风音量已在 10% 下限或以下，仍检测到削波；请检查硬件增益（Mic Boost）。")
    target = max(MIN_VOLUME, volume * GAIN_STEP)
    return Decision(target, f"检测到削波，麦克风音量 {volume:.0%} → {target:.0%}", True)


def device_notice(device) -> str:
    if device in (None, "pipewire", "default", "pulse"):
        return ""
    return "所选麦克风不是 PipeWire 默认源，未启用自动音量调节。"


class VolumeError(Exception):
    """An unavailable wpctl command or invalid external volume response."""


def run_wpctl(arguments, runner):
    try:
        return runner(["wpctl", *arguments], check=True, capture_output=True, text=True, timeout=2)
    except FileNotFoundError as exc:
        raise VolumeError("未找到 wpctl，未启用自动麦克风音量调节。") from exc
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise VolumeError("wpctl 音量读写失败，已停止自动调节；录制照常。") from exc


def read_volume(runner=None) -> tuple[float, bool]:
    result = run_wpctl(["get-volume", SOURCE], runner or subprocess.run)
    match = re.fullmatch(r"Volume:\s+(\d+(?:\.\d+)?)(\s+\[MUTED\])?\s*", result.stdout.strip())
    if match is None or not math.isfinite(float(match[1])):
        raise VolumeError("无法读取默认麦克风音量，未启用自动调节；录制照常。")
    return float(match[1]), bool(match[2])


class MicGain:
    def __init__(self, device, runner=None):
        self.runner = runner or subprocess.run
        self.lock = threading.Lock()
        self.clipped = self.total = 0
        self.paused = False
        self.skip_next = False
        self.active = False
        self.adjusted = None  # The last volume this run set, so the controller can put the start value back.
        self.notice = device_notice(device)
        if self.notice:
            return
        try:
            self.volume, muted = read_volume(self.runner)
        except VolumeError as exc:
            self.notice = str(exc)
            return
        self.notice = MUTED_NOTICE if muted else f"{self.volume:.0%} · 自动降低削波音量已启用"
        self.active = not muted

    def observe(self, samples) -> None:
        # PortAudio callback: count only; never run wpctl or publish state here.
        with self.lock:
            if self.active and not self.paused:
                self.clipped += int(np.count_nonzero(np.abs(samples) >= 0.999))
                self.total += samples.size

    def pause(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused
            self.clipped = self.total = 0

    def poll(self) -> bool:
        """Main loop only. Return whether the notice changed and needs publishing."""
        with self.lock:
            if not self.active or self.paused or self.total < WINDOW_SAMPLES:
                return False
            clipped, total = self.clipped, self.total
            self.clipped = self.total = 0
        if self.skip_next:
            decision = decide_gain(self.volume, clipped, total, skip_next=True)
            self.skip_next = decision.skip_next
            return False
        if clipped / total < CLIP_RATIO:
            return False
        try:
            self.volume, muted = read_volume(self.runner)
            decision = decide_gain(self.volume, clipped, total, muted=muted)
            if decision.volume is not None:
                run_wpctl(["set-volume", SOURCE, f"{decision.volume:.6f}"], self.runner)
                self.volume = self.adjusted = decision.volume
                self.skip_next = decision.skip_next
                # Start the settling window after wpctl returns.
                with self.lock:
                    self.clipped = self.total = 0
            if muted:
                self.active = False
        except VolumeError as exc:
            self.active = False
            decision = Decision(notice=str(exc))
        changed = decision.notice != self.notice
        self.notice = decision.notice
        return changed


def mic_gain(meta, runner=None) -> MicGain | None:
    if meta.get("audio_file") or meta.get("demo") or not meta.get("auto_gain", True):
        return None
    return MicGain(meta.get("device"), runner)


def gain_applies(meta) -> bool:
    return not (meta.get("audio_file") or meta.get("demo") or not meta.get("auto_gain", True)
                or device_notice(meta.get("device")))


def start_volume(meta, runner=None) -> float | None:
    """The default source volume before recording, when this run may adjust it; None if unknown."""
    if not gain_applies(meta):
        return None
    try:
        return read_volume(runner)[0]
    except VolumeError:
        return None


def restore_volume(start, adjusted, runner=None) -> str:
    """Plan N3.5: put back the start volume after auto-gain changed it, unless the user changed it since.

    start is from session.json, adjusted is the last value auto-gain set (asr-state.json). Without an
    adjustment nothing is read or written. Returns a notice for the console, or "".
    """
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if not number(start) or not number(adjusted):
        return ""
    try:
        current, _ = read_volume(runner)
        if abs(current - adjusted) > SAME_VOLUME:
            return f"录制中手动调整过音量（现为 {current:.0%}），保持不变，未恢复为开始时的 {start:.0%}。"
        run_wpctl(["set-volume", SOURCE, f"{start:.6f}"], runner or subprocess.run)
    except VolumeError:
        return f"未能恢复为开始时的 {start:.0%}（wpctl 不可用），请在系统声音设置中调回。"
    return f"已恢复为开始时的 {start:.0%}。"
