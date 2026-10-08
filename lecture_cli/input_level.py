"""Tell the user, while recording, when the voice reaching the recogniser is too weak."""
from __future__ import annotations

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

    def add(self, pcm: bytes, confirmed: int) -> bool:
        """Account s16le mono audio; confirmed is the transcript's segment count. True if notice or seconds changed."""
        import numpy as np  # Lazily: the controller imports this module only for summary().
        data = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
        changed = self.check_start(data, confirmed)
        while data.size:
            if self.samples == 0:
                self.window_count = confirmed
            part, data = data[:WINDOW_SECONDS * SAMPLE_RATE - self.samples], data[WINDOW_SECONDS * SAMPLE_RATE - self.samples:]
            self.squares += float(np.dot(part, part))
            self.samples += part.size
            if self.samples >= WINDOW_SECONDS * SAMPLE_RATE:
                changed |= self.close_window(confirmed)
        return changed

    def close_window(self, confirmed: int) -> bool:
        rms = (self.squares / self.samples) ** 0.5
        self.squares, self.samples = 0.0, 0
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

    def pause(self) -> None:
        # Audio before and after a break is not one continuous stretch: drop the partial window.
        self.squares, self.samples = 0.0, 0

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
        """What the GUI shows; the weak-input notice needs confirmed text, which clears the start one."""
        return self.weak or self.start

    def set(self, notice: str) -> bool:
        before = self.notice
        self.weak = notice
        return self.notice != before


def summary(seconds) -> str:
    """One sentence for the note when weak input added up to more than two minutes, else ""."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= SUMMARY_SECONDS:
        return ""
    return f"本节课累计约 {round(seconds / 60)} 分钟收到的声音很弱，这些部分的转录可能不准。"
