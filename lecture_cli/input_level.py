"""Tell the user, while recording, when the voice reaching the recogniser is too weak."""
from __future__ import annotations

import json
import math

SAMPLE_RATE = 16000
WINDOW_SECONDS = 10
WEAK_WINDOWS = 3  # 30 seconds in a row
WEAK_DBFS = -40.0
LAG_WINDOWS = 3  # Confirmed text may describe up to 30 s of earlier audio (a cloud upload is 20–30 s).
NOTICE = "收到的声音很弱，转录可能不准。请把麦克风靠近讲话人，或调高输入音量"
SUMMARY_SECONDS = 120  # More weak time than this in one lecture goes into the note's processing hints.
# Plan GUI-3 item 5: the first 30 s of recorded audio (pauses excluded) with no confirmed text.
START_SECONDS = 30
SILENT_DBFS = -70.0
NO_SPEECH = "还没有听到讲话。如果已经开始上课，请把麦克风靠近讲话人，或调高输入音量"
NO_SIGNAL = "麦克风几乎没有信号，请检查是否选对了麦克风、是否被静音"
# Plan GUI-4 Q1.4: a microphone that delivers only steady noise (incident 1: about -32 dBFS that
# barely moved for 9 minutes). Per-second RMS over 30 s of fed audio, pauses excluded.
STILL_SECONDS = 30
# PLAN-GUI-5 R2.6: after the first 30 s the levels must hold still this long before the notice, and
# once the user closes it the rule stays quiet for the rest of the recording.
STILL_LATER_SECONDS = 120
DISMISS_STILL = "dismiss-still"  # the sentinel the GUI writes when the notice is closed
STILL_RANGE_DB = 3.0      # max - min of the 30 per-second levels below this is "no change"
STILL_MEAN_DBFS = -45.0   # ... and their mean above this: there is signal, it just never moves
STILL_CLEAR_DB = 6.0      # a later 30 s whose levels span this much clears the notice
FLOOR_DBFS = -120.0       # what digital silence counts as, so the levels stay finite
STILL = "收到的声音几乎没有变化，可能只是噪声。请检查麦克风是否正常、是否选对了输入设备"


class WeakInput:
    """RMS level per 10 s of the audio fed to recognition, so paused time never counts.

    The notice needs 30 s of weak audio in a row and some text confirmed since that stretch began:
    text means someone is talking, while a silent break stays quiet. Confirmed text lags the audio,
    so text arriving later in the same weak stretch still counts; the price is that weak speech right
    after a silent break of 20 s or more raises the notice after its first 10 s. A window at or above the threshold
    clears the notice.

    The start of the recording has a rule of its own (plan GUI-3 item 5): when the first 30 s of
    audio fed to recognition bring no confirmed text, notice says so, worded by whether the RMS of
    those 30 s reached -70 dBFS. It clears with the first confirmed text and is checked only once per
    recording; it adds nothing to seconds. Weak speech later in the lecture that yields no text at
    all is not detected (a known limitation).

    seconds adds up, for the note, weak time while someone talked: within a stretch that raised the
    notice, a weak window counts once new text is confirmed, together with the uncounted weak windows
    just before it (at most LAG_WINDOWS), so a silent break before weak speech adds at most 20 s.
    """
    def __init__(self):
        self.squares = 0.0
        self.samples = 0
        self.window_count = 0     # confirmed segments when the current window began
        self.streak = 0           # consecutive weak windows
        self.streak_count = 0     # confirmed segments when the weak stretch began
        self.weak = ""            # the weak-input notice
        self.start = ""           # the start-of-recording notice
        self.start_squares = 0.0
        self.start_samples = 0
        self.start_done = False   # the start rule has been decided, either way
        self.seconds = 0          # weak time while someone was talking, whole windows only
        self.uncounted = 0        # weak windows of this stretch not yet matched with confirmed text
        self.counted_upto = 0     # confirmed segments already matched with weak windows
        self.streak_seconds = 0   # matched weak time of this stretch, kept only if it raises the notice
        self.still = ""           # plan GUI-4 Q1.4: steady noise and no text
        self.still_count = 0      # confirmed segments when the still notice was raised
        self.second_squares = 0.0
        self.second_samples = 0
        self.second_count = 0     # confirmed segments when the current second began
        self.levels = []          # (dBFS, confirmed segments when that second began), the last 120 s
        self.level_seconds = 0    # whole seconds measured so far (PLAN-GUI-5 R2.6)
        self.still_off = False    # closed by the user: never again this recording (PLAN-GUI-5 R2.6)
        # Plan GUI-4 Q3.2: each 10 s window's RMS is appended to this file (levels.jsonl) when set, on
        # the transcript's clock: every sample fed here, a partial window dropped at a pause included.
        self.levels_path = None
        self.clock = 0            # samples fed so far
        self.window_start = 0     # clock when the current window began

    def add(self, pcm: bytes, confirmed: int) -> bool:
        """Account s16le mono audio; confirmed is the transcript's segment count. True if notice or seconds changed."""
        import numpy as np  # Lazily: the controller imports this module only for summary().
        data = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
        changed = self.check_start(data, confirmed)
        changed |= self.check_still(data, confirmed)
        while data.size:
            if self.samples == 0:
                self.window_count = confirmed
                self.window_start = self.clock
            part, data = data[:WINDOW_SECONDS * SAMPLE_RATE - self.samples], data[WINDOW_SECONDS * SAMPLE_RATE - self.samples:]
            self.squares += float(np.dot(part, part))
            self.samples += part.size
            self.clock += part.size
            if self.samples >= WINDOW_SECONDS * SAMPLE_RATE:
                changed |= self.close_window(confirmed)
        return changed

    def close_window(self, confirmed: int) -> bool:
        rms = (self.squares / self.samples) ** 0.5
        self.squares, self.samples = 0.0, 0
        self.record_level(rms)
        if rms > 0 and 20 * math.log10(rms) >= WEAK_DBFS:
            self.streak = self.uncounted = self.streak_seconds = 0
            return self.set("")
        if self.streak == 0:
            self.streak_count = self.counted_upto = self.window_count
        self.streak += 1
        self.uncounted += 1
        added = 0
        if confirmed > self.counted_upto:
            added = min(self.uncounted, LAG_WINDOWS) * WINDOW_SECONDS
            self.uncounted, self.counted_upto = 0, confirmed
            self.streak_seconds += added
        if self.weak:
            self.seconds += added
            return bool(added)
        if self.streak >= WEAK_WINDOWS and confirmed > self.streak_count:
            self.seconds += self.streak_seconds
            return self.set(NOTICE)
        return False

    def record_level(self, rms: float) -> None:
        """Plan GUI-4 Q3.2: one line per 10 s window; a failed write never affects the recording."""
        if self.levels_path is None:
            return
        line = json.dumps({"start": round(self.window_start / SAMPLE_RATE, 2), "end": round(self.clock / SAMPLE_RATE, 2),
                           "rms_dbfs": round(20 * math.log10(rms), 1) if rms > 0 else FLOOR_DBFS})
        try:
            with open(self.levels_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def pause(self) -> None:
        # Audio before and after a break is not one continuous stretch: drop the partial window.
        self.squares, self.samples = 0.0, 0
        self.second_squares, self.second_samples = 0.0, 0

    def dismiss_still(self) -> bool:
        """PLAN-GUI-5 R2.6: the user closed the steady-noise notice. True if the notice changed."""
        before = self.notice
        self.still_off, self.still = True, ""
        return self.notice != before

    def check_still(self, data, confirmed: int) -> bool:
        """Plan GUI-4 Q1.4: per-second levels that hardly move, above -45 dBFS on average, with no new
        confirmed text: probably only noise. PLAN-GUI-5 R2.6: the first 30 s of the recording keep that
        rule; after them the levels must hold still for 120 s; a dismissed notice never comes back.
        New text, or a later 30 s spanning 6 dB or more, clears it."""
        before = self.notice
        if self.still and confirmed > self.still_count:
            self.still = ""
        while data.size:
            if self.second_samples == 0:
                self.second_count = confirmed
            part, data = data[:SAMPLE_RATE - self.second_samples], data[SAMPLE_RATE - self.second_samples:]
            self.second_squares += float(part @ part)
            self.second_samples += part.size
            if self.second_samples < SAMPLE_RATE:
                break
            rms = (self.second_squares / self.second_samples) ** 0.5
            self.second_squares, self.second_samples = 0.0, 0
            self.levels = (self.levels + [(20 * math.log10(rms) if rms > 0 else FLOOR_DBFS, self.second_count)])[-STILL_LATER_SECONDS:]
            self.level_seconds += 1
            if self.still_off or len(self.levels) < STILL_SECONDS:
                continue
            recent = [level for level, _ in self.levels[-STILL_SECONDS:]]
            if self.still and max(recent) - min(recent) >= STILL_CLEAR_DB:
                self.still = ""
                continue
            if self.still:
                continue
            # The recording's first 30 s, else the last 120 s.
            window = self.levels if self.level_seconds == STILL_SECONDS else (
                self.levels if len(self.levels) >= STILL_LATER_SECONDS else None)
            if window is None:
                continue
            values = [level for level, _ in window]
            if (max(values) - min(values) < STILL_RANGE_DB and sum(values) / len(values) > STILL_MEAN_DBFS
                    and confirmed == window[0][1]):
                self.still, self.still_count = STILL, confirmed
        return self.notice != before

    def check_start(self, data, confirmed: int) -> bool:
        before = self.notice
        if confirmed > 0:
            self.start, self.start_done = "", True
        elif not self.start_done:
            part = data[:START_SECONDS * SAMPLE_RATE - self.start_samples]
            self.start_squares += float(part @ part)
            self.start_samples += part.size
            if self.start_samples >= START_SECONDS * SAMPLE_RATE:
                rms = (self.start_squares / self.start_samples) ** 0.5
                self.start_done = True
                self.start = NO_SPEECH if rms > 0 and 20 * math.log10(rms) > SILENT_DBFS else NO_SIGNAL
        return self.notice != before

    @property
    def notice(self) -> str:
        """What the GUI shows; the weak-input notice needs confirmed text, which clears the start one.
        Steady noise (plan GUI-4 Q1.4) comes first: it explains the other two."""
        return self.still or self.weak or self.start

    def set(self, notice: str) -> bool:
        before = self.notice
        self.weak = notice
        return self.notice != before


def summary(seconds) -> str:
    """One sentence for the note when weak input added up to more than two minutes, else ""."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= SUMMARY_SECONDS:
        return ""
    return f"本节课累计约 {round(seconds / 60)} 分钟收到的声音很弱，这些部分的转录可能不准。"
