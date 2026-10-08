"""Lower the PipeWire default source gain when captured audio clips, and raise it back later.

Plan GUI-4 Q2: a single clipping window (a tap on the desk) never lowers the volume; two in a row do,
by 6 dB. After a lowering in this run the volume may come back up by 6 dB, never above the start
value, once 3 minutes have passed since the last change without any clipping window and the
recording is in the weak-input state. A lowering within 60 s of a raise locks the volume down for
the rest of the run. All time is recorded audio, so pauses never count.
"""
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
GAIN_STEP = 10 ** (-6 / 60)  # 6 dB; PipeWire volume v represents linear gain v³.
CLIP_WINDOWS = 2        # consecutive clipping windows before one lowering (plan GUI-4 Q2.1)
RAISE_SECONDS = 180     # recorded audio since the last change, and since the last clipping window
LOCK_SECONDS = 60       # a lowering this soon after a raise stops all further raises this run
SAMPLE_RATE = 16000
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


def lowered_text(volume: float, target: float) -> str:
    """Plan GUI-4 Q2.3: the GUI banner after a lowering (never in the note)."""
    return f"刚才声音过大，麦克风音量已从 {volume:.0%} 调到 {target:.0%}"


def raised_text(target: float) -> str:
    """Plan GUI-4 Q2.3: the GUI banner after a raise (never in the note)."""
    return f"声音偏弱，麦克风音量已调回 {target:.0%}"


class MicGain:
    def __init__(self, device, runner=None, start=None):
        self.runner = runner or subprocess.run
        self.lock = threading.Lock()
        self.clipped = self.total = 0
        self.paused = False
        self.skip_next = False
        self.active = False
        self.adjusted = None  # The last volume this run set, so the controller can put the start value back.
        # Plan GUI-4 Q2: the raise rule's state, counted in seconds of recorded (unpaused) audio.
        self.pending_clip = False  # the previous window clipped and has not lowered anything yet
        self.lowered = False       # raises are considered only after a lowering in this run
        self.locked = False        # lowered again within LOCK_SECONDS of a raise: no more raises
        self.since_change = 0.0    # since the last adjustment, up or down
        self.since_clip = 0.0      # since the last window that reached CLIP_RATIO
        self.since_raise = None    # since the last raise; None before the first
        self.weak = False          # set by the capture loop: the weak-input notice (plan N3.4) is showing
        self.change = None         # {"id", "text"}: the GUI-only banner for the latest change
        self.notice = device_notice(device)
        if self.notice:
            return
        try:
            self.volume, muted = read_volume(self.runner)
        except VolumeError as exc:
            self.notice = str(exc)
            return
        # Never raise above the start value (session.json mic_volume_start), else the volume now.
        self.start = start if isinstance(start, (int, float)) and not isinstance(start, bool) and math.isfinite(start) else self.volume
        self.notice = MUTED_NOTICE if muted else f"{self.volume:.0%} · 自动调节麦克风音量已启用"
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
            self.pending_clip = False  # A clipping run never spans a pause.

    def poll(self, weak=None) -> bool:
        """Main loop only. Return whether the notice or the banner changed and needs publishing.
        weak: whether the weak-input notice is showing (else the attribute set by the caller)."""
        if weak is not None:
            self.weak = weak
        with self.lock:
            if not self.active or self.paused or self.total < WINDOW_SAMPLES:
                return False
            clipped, total = self.clipped, self.total
            self.clipped = self.total = 0
        seconds = total / SAMPLE_RATE
        clip = clipped / total >= CLIP_RATIO
        self.since_change += seconds
        self.since_clip = 0.0 if clip else self.since_clip + seconds
        if self.since_raise is not None:
            self.since_raise += seconds
        if self.skip_next:
            # The window right after a change waits for it to take effect; it starts no clip run.
            decision = decide_gain(self.volume, clipped, total, skip_next=True)
            self.skip_next = decision.skip_next
            self.pending_clip = False
            return False
        if not clip:
            self.pending_clip = False
            return self.maybe_raise()
        if not self.pending_clip:
            self.pending_clip = True  # One clipping window alone (a tap on the desk) only counts.
            return False
        self.pending_clip = False
        before = self.change
        try:
            self.volume, muted = self.read()
            decision = decide_gain(self.volume, clipped, total, muted=muted)
            if decision.volume is not None:
                run_wpctl(["set-volume", SOURCE, f"{decision.volume:.6f}"], self.runner)
                self.changed(lowered_text(self.volume, decision.volume))
                if self.since_raise is not None and self.since_raise <= LOCK_SECONDS:
                    self.locked = True
                self.lowered = True
                self.volume = self.adjusted = decision.volume
                self.skip_next = decision.skip_next
            if muted:
                self.active = False
        except VolumeError as exc:
            self.active = False
            decision = Decision(notice=str(exc))
        changed = decision.notice != self.notice or self.change is not before
        self.notice = decision.notice
        return changed

    def maybe_raise(self) -> bool:
        """Plan GUI-4 Q2.2: back up by 6 dB, never above the start value."""
        if not (self.lowered and not self.locked and self.weak
                and self.since_change >= RAISE_SECONDS and self.since_clip >= RAISE_SECONDS):
            return False
        try:
            self.volume, muted = self.read()
            if muted:
                self.active = False
                changed = self.notice != MUTED_NOTICE
                self.notice = MUTED_NOTICE
                return changed
            target = min(self.start, self.volume / GAIN_STEP)
            if self.start - target <= SAME_VOLUME:
                target = self.start
            if target - self.volume <= SAME_VOLUME:
                return False  # Already at (or above) the start value, e.g. raised by hand.
            run_wpctl(["set-volume", SOURCE, f"{target:.6f}"], self.runner)
        except VolumeError as exc:
            self.active = False
            self.notice = str(exc)
            return True
        self.notice = f"声音偏弱，麦克风音量 {self.volume:.0%} → {target:.0%}"
        self.changed(raised_text(target))
        self.since_raise = 0.0
        self.volume = self.adjusted = target
        self.skip_next = True
        return True

    def read(self) -> tuple[float, bool]:
        """The current volume. wpctl prints two decimals, so a reading within SAME_VOLUME of the value
        this run last set is taken as that value: otherwise 100% lowered to 0.794328 reads 0.79 and
        is raised back to 99%, not 100%. Any other reading is a change by the user and is used as is."""
        volume, muted = read_volume(self.runner)
        if self.adjusted is not None and abs(volume - self.adjusted) <= SAME_VOLUME:
            volume = self.adjusted
        return volume, muted

    def changed(self, text: str) -> None:
        self.since_change = 0.0
        self.change = {"id": (self.change or {}).get("id", 0) + 1, "text": text}
        # Start the settling window after wpctl returns.
        with self.lock:
            self.clipped = self.total = 0


def mic_gain(meta, runner=None) -> MicGain | None:
    if meta.get("audio_file") or meta.get("demo") or not meta.get("auto_gain", True):
        return None
    return MicGain(meta.get("device"), runner, start=meta.get("mic_volume_start"))


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
