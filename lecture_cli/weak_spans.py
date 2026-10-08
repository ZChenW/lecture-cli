"""Plan GUI-4 Q3.3–Q3.5: after class, find where the sound was weak and where nothing was recognised.

The notes worker calls analyse() once, before the final notes. It reads the 10 s window levels the
capture process appended to levels.jsonl (input_level.WeakInput) and both transcript versions, all on
the same clock: recorded audio without pauses.

- Reference level: the REFERENCE_PERCENTILE-th percentile (linear interpolation) of the RMS of the
  windows that overlap at least one live segment with content. A percentile rather than the median,
  so that a lecture whose weak parts were still transcribed (weak windows with text outnumbering
  normal ones) keeps the normal level as its reference.
- A window is weak when either rule holds:
  - relative: it is WEAK_BELOW_DB or more below the reference. Only with REFERENCE_WINDOWS or more
    reference windows; with fewer there is no reference and this rule is skipped;
  - absolute floor: it overlaps a segment with content (live or refined) and its RMS is below
    FLOOR_DBFS, the level of the weak-input notice while recording (input_level.WEAK_DBFS). This
    rule needs no reference, so it also applies to short recordings.
- Neighbouring weak windows (next to each other in levels.jsonl) form a span, whichever rule made
  each of them weak; spans shorter than MIN_SPAN_SECONDS are dropped, and so are spans with no text
  in them (live or refined). Those are written to weak-spans.json.
- Every final segment (refined when complete, else live) that overlaps a span gets PREFIX; see
  storage.final_events. Transcript files are never changed.
- The review file lists each span as ONE point (review_item): the time range as its title, the
  marked segments cited after it, and the full text of both versions inside it.
- Stretches of GAP_SECONDS or more with no content segment in either version are listed as a
  processing hint only; they are not points to check.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import quote

from .input_level import WEAK_DBFS
from .storage import TEXT_MARKER, events, has_content, read_json, refined_events, write_json

REFERENCE_WINDOWS = 6
REFERENCE_PERCENTILE = 80
FLOOR_DBFS = WEAK_DBFS  # -40 dBFS
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


def percentile(values: list[float], share: float) -> float:
    """Linear interpolation between the closest ranks (numpy's default); share in 0..100."""
    values = sorted(values)
    position = (len(values) - 1) * share / 100
    low = math.floor(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def find_spans(levels: list[dict], live, refined) -> tuple[float | None, list[dict]]:
    """(reference dBFS or None, weak spans with text in them)."""
    spoken = with_content(live)
    texts = spoken + with_content(refined)
    reference = [w["rms_dbfs"] for w in levels if any(overlaps((w["start"], w["end"]), s) for s in spoken)]
    level = percentile(reference, REFERENCE_PERCENTILE) if len(reference) >= REFERENCE_WINDOWS else None

    def weak(window) -> bool:
        if level is not None and window["rms_dbfs"] <= level - WEAK_BELOW_DB:
            return True
        return (window["rms_dbfs"] < FLOOR_DBFS
                and any(overlaps((window["start"], window["end"]), t) for t in texts))

    runs, current = [], None
    for window in levels:
        if weak(window):
            if current is None:
                current = [window["start"], window["end"]]
                runs.append(current)
            else:
                current[1] = window["end"]
        else:
            current = None
    spans = [{"start": start, "end": end} for start, end in runs
             if end - start >= MIN_SPAN_SECONDS and any(overlaps((start, end), t) for t in texts)]
    return (None if level is None else round(level, 1)), spans


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
        if span and not str(record.get("text", "")).startswith(PREFIX) and any(overlaps(span, (s["start"], s["end"])) for s in spans):
            record = {**record, "text": PREFIX + record["text"]}
        marked.append(record)
    return marked


def id_runs(ids: list[int]) -> list[tuple[int, int]]:
    runs: list[list[int]] = []
    for i in sorted(set(ids)):
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a, b) for a, b in runs]


def run_label(first: int, last: int) -> str:
    return f"L{first}–L{last}" if last > first else f"L{first}"


def ids_text(ids: list[int]) -> str:
    """[L3–L5] [L8]: one bare citation per run of consecutive ids (storage.linked_sources links them)."""
    return " ".join(f"[{run_label(a, b)}]" for a, b in id_runs(ids))


def joined(records) -> str:
    return " ".join(r["text"].replace(PREFIX, "", 1).replace("\n", " ").strip() for r in records
                    if isinstance(r.get("text"), str) and r["text"].replace(PREFIX, "", 1).strip())


def covering(records, span: tuple[float, float]) -> list[dict]:
    return [r for r in records or [] if (s := interval(r)) and overlaps(s, span)]


NO_TEXT = "（这一段没有文字）"


def review_item(span: dict, final: list[dict], version: str, live, filename: str) -> str:
    """The one point to check for a weak span (GUI-4 fix): its time range as the title, the final
    segments it marked (the ones carrying PREFIX in the notes input) cited after the title, and
    the full text of each version underneath, as a nested list so that the whole entry stays one
    list item for gui.review.parse. With the refined version, the live text is taken over the span
    widened to the refined segments it marked, so both versions cover the same audio."""
    window = (span["start"], span["end"])
    marked = covering(final, window)
    title = f"- **{MARK}，待核对：{clock_text(span['start'])}–{clock_text(span['end'])}**"
    if version != "refined":
        sources = ids_text([r["id"] for r in marked])
        return f"{title} {sources}\n  - 实时版本：{joined(marked) or NO_TEXT}"
    extent = (min([window[0]] + [interval(r)[0] for r in marked]), max([window[1]] + [interval(r)[1] for r in marked]))
    heard = covering(live, extent)
    refined_text, live_text = joined(marked), joined(heard)
    if marked:
        sources = ids_text([r["id"] for r in marked])
    else:  # Refinement wrote nothing here: point at the live segments instead.
        sources = " ".join(f"[live-{run_label(a, b)}]({quote(filename)}#live-L{a})"
                           for a, b in id_runs([r["id"] for r in heard]))
    if refined_text and refined_text == live_text:
        body = f"  - 校正版本与实时版本相同：{refined_text}"
    else:
        body = f"  - 校正版本：{refined_text or NO_TEXT}\n  - 实时版本：{live_text or NO_TEXT}"
    return f"{title} {sources}".rstrip() + "\n" + body


def review_items(directory: Path, final: list[dict], version: str, live, filename: str) -> list[tuple[float, str]]:
    """(start, point) for each weak span analyse() recorded, in time order."""
    return [(span["start"], review_item(span, final, version, live, filename)) for span in load(directory)]
