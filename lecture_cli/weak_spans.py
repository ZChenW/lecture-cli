"""Plan GUI-4 Q3.3–Q3.5: after class, find where the sound was weak and where nothing was recognised.

The notes worker calls analyse() once, before the final notes. It reads the 10 s window levels the
capture process appended to levels.jsonl (input_level.WeakInput) and both transcript versions, all on
the same clock: recorded audio without pauses.

- Reference level: the median RMS of the windows that overlap at least one live segment with
  content. Fewer than REFERENCE_WINDOWS such windows: no judgement at all.
- A weak window is WEAK_BELOW_DB or more below the reference. Neighbouring weak windows (next to
  each other in levels.jsonl) form a span; spans shorter than MIN_SPAN_SECONDS are dropped, and so
  are spans with no text in them (live or refined). Those are written to weak-spans.json.
- Every final segment (refined when complete, else live) that overlaps a span gets PREFIX; see
  storage.final_events. Transcript files are never changed.
- Stretches of GAP_SECONDS or more with no content segment in either version are listed as a
  processing hint only; they are not points to check.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .storage import TEXT_MARKER, events, has_content, read_json, refined_events, write_json

REFERENCE_WINDOWS = 6
WEAK_BELOW_DB = 15.0
MIN_SPAN_SECONDS = 20.0
GAP_SECONDS = 180.0
PREFIX = "[这一段收音很弱，待核对] "
MARK = "这一段收音很弱"
FILE = "weak-spans.json"


def clock(value) -> float | None:
    """"00:46:58.20" → seconds; None when it is not a transcript time."""
    try:
        hours, minutes, seconds = value.split(":")
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (AttributeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def interval(record) -> tuple[float, float] | None:
    start, end = clock(record.get("start")), clock(record.get("end"))
    if start is None or end is None:
        return None
    return start, max(start, end)


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def with_content(records) -> list[tuple[float, float]]:
    """Intervals of the segments that hold text (a 待核对 prefix or a near-silent placeholder alone is not text)."""
    found = []
    for record in records or []:
        span = interval(record)
        if span and isinstance(record.get("text"), str) and has_content(TEXT_MARKER.sub("", record["text"])):
            found.append(span)
    return found


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_levels(directory: Path) -> list[dict]:
    levels = []
    try:
        lines = (directory / "levels.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue  # e.g. a line cut short by a crash
        if (isinstance(value, dict) and all(number(value.get(k)) for k in ("start", "end", "rms_dbfs"))
                and value["end"] > value["start"]):
            levels.append(value)
    return levels


def median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def find_spans(levels: list[dict], live, refined) -> tuple[float | None, list[dict]]:
    """(reference dBFS or None, weak spans with text in them)."""
    spoken = with_content(live)
    reference = [w["rms_dbfs"] for w in levels if any(overlaps((w["start"], w["end"]), s) for s in spoken)]
    if len(reference) < REFERENCE_WINDOWS:
        return None, []
    level = median(reference)
    runs, current = [], None
    for window in levels:
        if window["rms_dbfs"] <= level - WEAK_BELOW_DB:
            if current is None:
                current = [window["start"], window["end"]]
                runs.append(current)
            else:
                current[1] = window["end"]
        else:
            current = None
    texts = spoken + with_content(refined)
    spans = [{"start": start, "end": end} for start, end in runs
             if end - start >= MIN_SPAN_SECONDS and any(overlaps((start, end), t) for t in texts)]
    return round(level, 1), spans


def find_gaps(live, refined, total: float) -> list[dict]:
    """Stretches of GAP_SECONDS or more without any content segment, from 0 to the end of the audio."""
    texts = sorted(with_content(live) + with_content(refined))
    gaps, reached = [], 0.0
    for start, end in texts:
        if start - reached >= GAP_SECONDS:
            gaps.append({"start": reached, "end": start})
        reached = max(reached, end)
    if total - reached >= GAP_SECONDS:
        gaps.append({"start": reached, "end": total})
    return gaps


def clock_text(seconds: float) -> str:
    """12:30 within the first hour, 1:02:30 after it (recorded time, as in the notes)."""
    seconds = int(seconds)
    hours, minutes = seconds // 3600, seconds // 60 % 60
    return f"{hours}:{minutes:02}:{seconds % 60:02}" if hours else f"{minutes}:{seconds % 60:02}"


def ranges(items: list[dict]) -> str:
    return "、".join(f"{clock_text(item['start'])}–{clock_text(item['end'])}" for item in items)


def spans_hint(spans: list[dict]) -> str:
    return f"{ranges(spans)} 收音很弱，相关内容已标为待核对。" if spans else ""


def gaps_hint(gaps: list[dict]) -> str:
    return f"{ranges(gaps)} 没有识别出内容（可能是课间，也可能没有收到声音）。" if gaps else ""


def audio_seconds(directory: Path, levels: list[dict], live, refined) -> float:
    state = read_json(directory / "asr-state.json")
    for key in ("captured", "seconds"):
        if number(state.get(key)):
            return float(state[key])
    ends = [w["end"] for w in levels] + [end for _, end in with_content(live) + with_content(refined)]
    return max(ends, default=0.0)


def analyse(directory: Path) -> list[str]:
    """Write weak-spans.json and return the processing hints (weak spans first, then long gaps)."""
    live, refined = events(directory), refined_events(directory)
    levels = load_levels(directory)
    reference, spans = find_spans(levels, live, refined)
    gaps = find_gaps(live, refined, audio_seconds(directory, levels, live, refined))
    try:
        write_json(directory / FILE, {"reference_dbfs": reference, "spans": spans, "gaps": gaps})
    except OSError:
        spans = []  # Unmarked segments must not be announced as marked.
    return [hint for hint in (spans_hint(spans), gaps_hint(gaps)) if hint]


def load(directory: Path) -> list[dict]:
    """The weak spans analyse() wrote, or [] before it ran."""
    spans = read_json(directory / FILE).get("spans")
    if not isinstance(spans, list):
        return []
    return [s for s in spans if isinstance(s, dict) and number(s.get("start")) and number(s.get("end"))]


def mark(records: list[dict], spans: list[dict]) -> list[dict]:
    """Copies of the records, with PREFIX on each one that overlaps a weak span."""
    if not spans:
        return records
    marked = []
    for record in records:
        span = interval(record)
        if span and MARK not in record.get("text", "") and any(overlaps(span, (s["start"], s["end"])) for s in spans):
            record = {**record, "text": PREFIX + record["text"]}
        marked.append(record)
    return marked


def live_text(live, record) -> str:
    """The live version over the time range of a refined segment."""
    span = interval(record)
    if span is None:
        return ""
    return " ".join(r["text"].strip() for r in live or []
                    if (s := interval(r)) and overlaps(s, span) and isinstance(r.get("text"), str) and r["text"].strip())


def review_line(record: dict, version: str, live) -> str:
    """One point to check for a marked segment: both versions when the refined one is used."""
    text = record["text"].replace(PREFIX, "", 1).replace("\n", " ").strip()
    if version == "refined":
        other = live_text(live, record).replace("\n", " ")
        if not other:
            versions = f"校正版本：{text}"
        elif other == text:
            versions = f"校正版本与实时版本相同：{text}"
        else:
            versions = f"校正版本：{text}／实时版本：{other}"
    else:
        versions = f"实时版本：{text}"
    return f"- 这一段收音很弱，待核对 [L{record['id']}]：{versions}"
