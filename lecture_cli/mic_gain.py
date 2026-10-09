"""Keep the PipeWire default source volume where the classroom sounds right, without any button.

PLAN-GUI-5 R2 replaces the fourth round's "lower only, raise back to the start value":

- R2.1, before any audio reaches recognition (GUI5-fix, two-way since GUI5-twoway): 18 dB down while a
  short background measurement clips, then straight to the volume computed from it (CALIBRATE_MARGIN
  under BACKGROUND_TARGET), up or down, confirmed by one more measurement. A lowering is corrected at
  most once; a raise that clips or goes over the target goes back to the last volume that met it. A
  volume where the background met the target is the run's ceiling; an unmet target is never remembered. The audio
  heard meanwhile is never recognised, counted or archived.
- R2.2, during the lecture: two consecutive 2 s clipping windows lower it by 6 dB and the ceiling
  follows. It goes up by 6 dB, never above the ceiling, once 60 s have passed since the last
  change, text was confirmed in those 60 s and the peaks under that text stay below
  RAISE_PEAK_DBFS. A lowering within 60 s of a raise locks the ceiling at the lowered value.
  All time is recorded audio, so pauses never count.
- R2.3: judge() is the one verdict on a stretch of background ("high", "dead" or ""), shared by
  the start dialog's level bar, the start of a recording and `lecture doctor --mic-test`.
- R2.4: the last volume and ceiling per source (node.name) are kept in mic-levels.json, when R2.1 met the target.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import numpy as np

from .input_level import STILL_MEAN_DBFS, STILL_RANGE_DB  # "no change", as the steady-noise notice

SOURCE = "@DEFAULT_AUDIO_SOURCE@"
WINDOW_SAMPLES = 2 * 16000
CLIP_RATIO = 0.001
GAIN_STEP = 10 ** (-6 / 60)  # 6 dB; PipeWire volume v represents linear gain v³.
CLIP_WINDOWS = 2        # consecutive clipping windows before one lowering (plan GUI-4 Q2.1)
RAISE_SECONDS = 60      # PLAN-GUI-5 R2.2: recorded audio since the last change before a raise
RAISE_PEAK_DBFS = -30.0  # ... and the 95th percentile of peaks under confirmed text below this
RAISE_PERCENTILE = 95
LOCK_SECONDS = 60       # a lowering this soon after a raise locks the ceiling at the lowered value
SAMPLE_RATE = 16000
MIN_VOLUME = 0.10
# PLAN-GUI-5 R2.1: the start calibration.
BACKGROUND_TARGET = -45.0  # dBFS; the background should sit below this
CALIBRATE_FIRST = 1.0      # seconds of background measured first
CALIBRATE_SETTLE = 0.3     # after each step, audio left to settle (discarded)
CALIBRATE_MEASURE = 0.5    # then measured again
CALIBRATE_STEPS = 10
CALIBRATE_TIMEOUT = 6.0    # never hold the lecture up longer; carry on with the value reached
# GUI5-fix: the start adjustment computes its step instead of walking 6 dB at a time. PipeWire's volume v
# is the linear gain v³, so a change of D dB is v · 10^(D/60) (GAIN_STEP is the 6 dB case); PLAN-GUI-5
# section 0's measured table follows this closely (about 61 dB per tenfold volume between 60 % and 10 %).
CLIP_DROP_DB = 18.0        # a clipped measurement says too little about the real level: first this much down
CALIBRATE_MARGIN = 2.0     # dB under BACKGROUND_TARGET the computed step aims at (the table is a little steeper
                           # than v³ near the target, so aiming at the target itself lands just above it)
CALIBRATE_CORRECTIONS = 1  # computed steps after the first one, at most
MOVED_DB = 3.0             # lowering by 6 dB or more that moves the background less than this: it is not the room
# GUI5-twoway: the start adjustment also raises, straight to the computed volume (at most 100 %, and 6 dB
# under any volume that clipped in this run), once; a raise that clips or goes over the target is undone.
CALIBRATE_RAISES = 1       # raises per start adjustment
RAISE_MIN_DB = 1.0         # a computed raise smaller than this is not worth a step
CALIBRATING = "正在调整麦克风音量"
# PLAN-GUI-5 R2.3: the shared verdict.
BLOCK_SAMPLES = 1600       # 100 ms, the level bar's block
HIGH_DBFS = -20.0          # background above this with nobody talking: too loud
HIGH_CLIPPED_SHARE = 0.2   # background clipping: at least this share of 100 ms blocks clipped
HIGH_TEXT = "麦克风音量过高，开始上课时会自动调低"
HIGH_MANUAL_TEXT = "麦克风音量过高，请在系统声音设置中调低"  # an input this program never adjusts
DEAD_TEXT = "麦克风可能没有在工作：声音没有变化"
LEVELS_FILE = "mic-levels.json"
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


def dbfs(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else -120.0


def blocks(samples) -> list[dict]:
    """The level bar's 100 ms blocks of float samples: RMS in dBFS and whether the block clipped."""
    data = np.asarray(samples, dtype=np.float64).ravel()
    result = []
    for begin in range(0, data.size - BLOCK_SAMPLES + 1, BLOCK_SAMPLES):
        block = data[begin:begin + BLOCK_SAMPLES]
        result.append({"rms": dbfs(float(np.sqrt(np.mean(block * block)))),
                       "clipped": bool(np.count_nonzero(np.abs(block) >= 0.999) / block.size >= CLIP_RATIO)})
    return result


def judge(levels: list[dict], *, adjusts: bool = True, volume: float | None = None, settled: bool = False) -> str:
    """PLAN-GUI-5 R2.3: "high", "dead" or "" for a stretch of background (100 ms blocks of RMS dBFS and
    clipping), checked in this order.

    high: the background clips (HIGH_CLIPPED_SHARE of the blocks) or its median is above HIGH_DBFS.
    dead: otherwise, background above -45 dBFS that moves less than 3 dB, when no adjustment can
    explain it any more: R2.1 lowered the volume and the background did not follow (settled) or this
    input is never adjusted (adjusts False); or
    the volume is already at the 10 % floor and the background is still above BACKGROUND_TARGET.
    The start dialog has neither, so before a lecture "dead" only follows from the floor."""
    if not levels:
        return ""
    values = [level["rms"] for level in levels]
    clipped = sum(1 for level in levels if level.get("clipped"))
    if clipped / len(levels) >= HIGH_CLIPPED_SHARE or float(np.median(values)) > HIGH_DBFS:
        return "high"
    mean = sum(values) / len(values)
    steady = mean > STILL_MEAN_DBFS and max(values) - min(values) < STILL_RANGE_DB
    if steady and (settled or not adjusts):
        return "dead"
    if adjusts and volume is not None and volume <= MIN_VOLUME + SAME_VOLUME and mean > BACKGROUND_TARGET:
        return "dead"
    return ""


def verdict_text(verdict: str, adjusts: bool = True) -> str:
    """The status line under the level bar; nothing when the microphone is fine."""
    if verdict == "high":
        return HIGH_TEXT if adjusts else HIGH_MANUAL_TEXT
    return DEAD_TEXT if verdict == "dead" else ""


@dataclass(frozen=True)
class Calibration:
    """What R2.1 found: the volume reached, the run's ceiling and the last background measured."""
    volume: float
    ceiling: float
    background: float      # RMS dBFS of the last measurement
    clipped: bool
    steps: int
    timed_out: bool
    levels: tuple = ()     # its 100 ms blocks, for judge()
    reached: bool = False  # GUI5-fix: the last measurement met BACKGROUND_TARGET without clipping
    unmoved: bool = False  # the volume went down 6 dB or more and the background did not follow
    path: tuple = ()       # (seconds since the start, volume, background dBFS, clipped) per measurement
    backed_off: bool = False  # GUI5-twoway: a raise clipped or went over the target and was undone


class Listener:
    """PLAN-GUI-5 R2.1: while the start calibration runs, the input callback hands its samples here
    instead of the recording, so they never reach recognition, the duration or the archive."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = True
        self.parts: list = []
        self.size = 0

    def feed(self, samples) -> bool:
        """PortAudio callback: True while calibrating (the samples are taken), else False."""
        with self.lock:
            if not self.active:
                return False
            self.parts.append(np.array(samples, dtype=np.float32, copy=True))
            self.size += len(samples)
            return True

    def close(self) -> None:
        with self.lock:
            self.active = False
            self.parts, self.size = [], 0

    async def listen(self, seconds: float):
        """The next `seconds` of input. A stream that stalls ends the wait a second late, with what came."""
        with self.lock:
            self.parts, self.size = [], 0
        wanted = int(seconds * SAMPLE_RATE)
        deadline = time.monotonic() + seconds + 1
        while time.monotonic() < deadline:
            with self.lock:
                if self.size >= wanted:
                    break
            await asyncio.sleep(0.02)
        with self.lock:
            data = np.concatenate(self.parts) if self.parts else np.zeros(0, dtype=np.float32)
            self.parts, self.size = [], 0
        return data[:wanted]


def background(samples) -> tuple[float, bool, list[dict]]:
    """RMS dBFS of a measurement, whether it clipped (CLIP_RATIO of its samples) and its blocks."""
    data = np.asarray(samples, dtype=np.float64).ravel()
    if not data.size:
        return -120.0, False, []
    rms = dbfs(float(np.sqrt(np.mean(data * data))))
    return rms, bool(np.count_nonzero(np.abs(data) >= 0.999) / data.size >= CLIP_RATIO), blocks(data)


def source_name(runner=None) -> str | None:
    """node.name of the default source (`wpctl inspect`), the key of mic-levels.json; None if unknown."""
    try:
        result = run_wpctl(["inspect", SOURCE], runner or subprocess.run)
    except VolumeError:
        return None
    match = re.search(r'node\.name\s*=\s*"([^"]+)"', result.stdout or "")
    return match[1] if match else None


def levels_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "lecture-cli" / LEVELS_FILE


def is_volume(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= 1.5


def remembered(node: str | None) -> float | None:
    """PLAN-GUI-5 R2.4: the volume this source ended the last normal lecture with; None when unknown.
    An unreadable file or a missing entry is not an error."""
    if not node:
        return None
    try:
        entry = json.loads(levels_path().read_text()).get(node)
        volume = entry.get("volume") if isinstance(entry, dict) else None
    except (OSError, ValueError, AttributeError):
        return None
    return float(volume) if is_volume(volume) else None


def remember(node, volume, ceiling) -> bool:
    """PLAN-GUI-5 R2.4: after a normal end only. Other sources' entries stay; failures are silent."""
    if not isinstance(node, str) or not node or not is_volume(volume) or not is_volume(ceiling):
        return False
    path = levels_path()
    try:
        current = json.loads(path.read_text())
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current[node] = {"volume": round(float(volume), 6), "ceiling": round(float(ceiling), 6),
                     "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        from .storage import write_json
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, current)
    except OSError:
        return False
    return True


class MicGain:
    def __init__(self, device, runner=None, start=None):
        self.runner = runner or subprocess.run
        self.lock = threading.Lock()
        self.clipped = self.total = 0
        self.peak = 0.0
        self.paused = False
        self.skip_next = False
        self.active = False
        self.adjusted = None  # The last volume this run set, so the controller can put the start value back.
        # PLAN-GUI-5 R2.2: the two-way rule's state, counted in seconds of recorded (unpaused) audio.
        self.pending_clip = False  # the previous window clipped and has not lowered anything yet
        self.locked = False        # lowered again within LOCK_SECONDS of a raise: the ceiling stays put
        self.clock = 0.0           # recorded audio since the start calibration
        self.since_change = 0.0    # since the last adjustment, up or down (or the calibration)
        self.since_raise = None    # since the last raise; None before the first
        self.windows: list = []    # (start, end, peak dBFS) of the windows of the last RAISE_SECONDS
        self.change = None         # {"id", "text"}: the GUI-only banner for the latest change
        self.calibration: Calibration | None = None
        self.node = None           # node.name of the default source, once the calibration asked
        self.notice = device_notice(device)
        if self.notice:
            return
        try:
            self.volume, muted = read_volume(self.runner)
        except VolumeError as exc:
            self.notice = str(exc)
            return
        # The value before the lecture (session.json mic_volume_start), else the volume now.
        self.start = start if is_volume(start) else self.volume
        self.ceiling = self.volume  # until the calibration sets it
        self.notice = MUTED_NOTICE if muted else f"{self.volume:.0%} · 自动调节麦克风音量已启用"
        self.active = not muted

    def observe(self, samples) -> None:
        # PortAudio callback: count only; never run wpctl or publish state here.
        with self.lock:
            if self.active and not self.paused:
                self.clipped += int(np.count_nonzero(np.abs(samples) >= 0.999))
                self.total += samples.size
                if samples.size:
                    self.peak = max(self.peak, float(np.max(np.abs(samples))))

    def pause(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused
            self.clipped = self.total = 0
            self.peak = 0.0
            self.pending_clip = False  # A clipping run never spans a pause.

    async def calibrate(self, listen, clock=time.monotonic) -> Calibration | None:
        """PLAN-GUI-5 R2.1 as changed by GUI5-fix and GUI5-twoway, before any audio reaches recognition.
        listen(seconds) returns that much input (Listener.listen). Starts from the volume remembered for
        this source when there is one, then measures the background for CALIBRATE_FIRST seconds:

        - clipping: down CLIP_DROP_DB (again while it still clips);
        - above BACKGROUND_TARGET: straight down to the volume computed from the measurement (aiming
          CALIBRATE_MARGIN under the target), measured again to confirm, and corrected at most
          CALIBRATE_CORRECTIONS more times the same way;
        - at or below it: straight up to the computed volume when that is RAISE_MIN_DB or more above,
          at most 100 % and 6 dB under any volume that clipped, once (CALIBRATE_RAISES) and only when
          there is time left to confirm it. When the confirming measurement clips or is over the
          target, back to the last volume that met it (no further raise).

        Each step waits CALIBRATE_SETTLE and measures CALIBRATE_MEASURE seconds; never below MIN_VOLUME,
        at most CALIBRATE_STEPS steps and not past CALIBRATE_TIMEOUT. A raise left unconfirmed when the
        limits stop the run is undone too. The run's ceiling is the volume where it ended: one at which
        the background was measured to meet the target, or, when it was never met, the volume reached
        (R2.2 never raises above it) and nothing is remembered (publish: mic_target_met). No banner."""
        if not self.active:
            return None
        begin = clock()
        steps = computed = raised = 0
        path = []
        good = None          # the last volume measured to meet the target
        raising = False      # the last step was a raise, not yet confirmed
        backed_off = False
        try:
            self.node = source_name(self.runner)
            last = remembered(self.node)
            if last is not None:
                last = min(1.0, max(MIN_VOLUME, last))
                if abs(last - self.volume) > SAME_VOLUME:
                    self.set(last)
            rms, clipped, levels = background(await listen(CALIBRATE_FIRST))
            path.append((clock() - begin, self.volume, rms, clipped))
            clipped_at = None  # the lowest volume that clipped: a computed step stays under it
            while steps < CALIBRATE_STEPS and clock() - begin < CALIBRATE_TIMEOUT:
                met = not clipped and rms <= BACKGROUND_TARGET
                if raising and not met and good is not None:
                    # The raise clipped or went over the target: back where it was last met, no more raises.
                    if clipped:
                        clipped_at = self.volume
                    target, raised, backed_off = good, CALIBRATE_RAISES, True
                    raising = False
                elif met:
                    good, raising = self.volume, False
                    if raised >= CALIBRATE_RAISES:
                        break
                    if clock() - begin + CALIBRATE_SETTLE + CALIBRATE_MEASURE > CALIBRATE_TIMEOUT:
                        break  # no time to confirm a raise: stay where the target is met
                    target = self.volume * 10 ** ((BACKGROUND_TARGET - CALIBRATE_MARGIN - rms) / 60)
                    target = min(target, 1.0 if clipped_at is None else clipped_at * GAIN_STEP)
                    if target <= self.volume * 10 ** (RAISE_MIN_DB / 60):
                        break
                    raised, raising = raised + 1, True
                elif clipped:
                    clipped_at = self.volume
                    target = self.volume * 10 ** (-CLIP_DROP_DB / 60)
                else:
                    if computed > CALIBRATE_CORRECTIONS:
                        break
                    computed += 1
                    target = self.volume * 10 ** ((BACKGROUND_TARGET - CALIBRATE_MARGIN - rms) / 60)
                    target = min(target, self.volume)
                target = max(MIN_VOLUME, min(1.0, target))
                if abs(target - self.volume) <= 1e-6:
                    break  # at the floor (or nothing left to change)
                self.set(target)
                steps += 1
                await listen(CALIBRATE_SETTLE)
                rms, clipped, levels = background(await listen(CALIBRATE_MEASURE))
                path.append((clock() - begin, self.volume, rms, clipped))
            reached = not clipped and rms <= BACKGROUND_TARGET
            if raising and not reached and good is not None:
                # The limits stopped the run on an unconfirmed raise: undo it without measuring again.
                self.set(good)
                backed_off = True
        except VolumeError as exc:
            self.active = False
            self.notice = str(exc)
            return None
        # Measured to meet the target here; or never met, so never raised above where it stopped.
        ceiling = self.ceiling = self.volume
        first = next(((v, r) for _, v, r, c in path if not c), None)
        unmoved = bool(first and 60 * math.log10(first[0] / self.volume) >= 6 and first[1] - rms < MOVED_DB)
        self.calibration = Calibration(self.volume, ceiling, round(rms, 1), clipped, steps,
                                       not reached and clock() - begin >= CALIBRATE_TIMEOUT, tuple(levels),
                                       reached, unmoved,
                                       tuple((round(t, 3), round(v, 6), round(r, 1), c) for t, v, r, c in path),
                                       backed_off)
        self.notice = f"{self.volume:.0%} · 自动调节麦克风音量已启用"
        with self.lock:
            self.clipped = self.total = 0
            self.peak = 0.0
        self.clock = self.since_change = 0.0
        self.windows = []
        return self.calibration

    def set(self, volume: float) -> None:
        run_wpctl(["set-volume", SOURCE, f"{volume:.6f}"], self.runner)
        self.volume = self.adjusted = volume

    def poll(self, texts=()) -> bool:
        """Main loop only. Return whether the notice or the banner changed and needs publishing.
        texts: (start, end) in recorded seconds of the confirmed segments, for the raise rule."""
        with self.lock:
            if not self.active or self.paused or self.total < WINDOW_SAMPLES:
                return False
            clipped, total, peak = self.clipped, self.total, self.peak
            self.clipped = self.total = 0
            self.peak = 0.0
        seconds = total / SAMPLE_RATE
        clip = clipped / total >= CLIP_RATIO
        self.windows = [w for w in self.windows if w[1] > self.clock + seconds - RAISE_SECONDS]
        self.windows.append((self.clock, self.clock + seconds, dbfs(peak)))
        self.clock += seconds
        self.since_change += seconds
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
            return self.maybe_raise(texts)
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
                    self.locked = True  # back down, and never above this again this run
                # R2.2: the ceiling follows a lowering down.
                self.ceiling = min(self.ceiling, decision.volume)
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

    def under_text(self, texts) -> list[float]:
        """Peaks of the windows of the last RAISE_SECONDS that overlap a confirmed segment."""
        spans = [(float(a), float(b)) for a, b in texts if b > self.clock - RAISE_SECONDS]
        return [peak for start, end, peak in self.windows
                if end > self.clock - RAISE_SECONDS and any(a < end and b > start for a, b in spans)]

    def maybe_raise(self, texts) -> bool:
        """PLAN-GUI-5 R2.2: up by 6 dB when all hold: RAISE_SECONDS since the last change; text
        confirmed in that time; the 95th percentile of the peaks under it below RAISE_PEAK_DBFS;
        and the result not above the ceiling."""
        if self.since_change < RAISE_SECONDS:
            return False
        peaks = self.under_text(texts)
        if not peaks or float(np.percentile(peaks, RAISE_PERCENTILE)) >= RAISE_PEAK_DBFS:
            return False
        try:
            self.volume, muted = self.read()
            if muted:
                self.active = False
                changed = self.notice != MUTED_NOTICE
                self.notice = MUTED_NOTICE
                return changed
            target = min(self.ceiling, self.volume / GAIN_STEP)
            if self.ceiling - target <= SAME_VOLUME:
                target = self.ceiling
            if target - self.volume <= SAME_VOLUME:
                return False  # Already at (or above) the ceiling, e.g. raised by hand.
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
            self.peak = 0.0

    def publish(self, state: dict) -> None:
        """The asr-state.json fields this rule owns."""
        state["gain_notice"] = self.notice
        if self.adjusted is not None:
            state["gain_volume"] = self.adjusted  # Plan N3.5: restored after the run when unchanged.
        if self.change:
            state["gain_change"] = self.change  # Plan GUI-4 Q2.3: GUI banner only, never the note.
        if self.active and self.calibration is not None:
            # PLAN-GUI-5 R2.1/R2.4: the bottom bar's "22% · 自动", and what a normal end remembers.
            state.update(mic_volume=round(self.volume, 6), mic_ceiling=round(self.ceiling, 6), mic_node=self.node,
                         mic_target_met=self.calibration.reached)  # GUI5-fix: only then is it remembered
        elif not self.active:
            state.pop("mic_volume", None)


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


def remember_levels(state: dict) -> bool:
    """PLAN-GUI-5 R2.4, from the controller after a normal end: the capture's last volume and ceiling.
    GUI5-fix: only when the start adjustment met the target; a ceiling never measured is not kept."""
    if state.get("mic_target_met") is not True:
        return False
    return remember(state.get("mic_node"), state.get("mic_volume"), state.get("mic_ceiling"))
