"""Start, discover and re-attach headless controllers, and build the snapshot the GUI shows."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import quote

from .. import runs
from ..asr import QWEN_MODELS
from ..refinement import label as refine_label
from ..storage import TEXT_MARKER, has_content

START_TIMEOUT = 15
# Closing estimate (seconds of closing work per recorded second), used until the registry has history.
REFINE_RATIO = 0.1
NOTES_RATIO = 0.05
MODEL_LOAD_SECONDS = 15
HISTORY = 5
ETA_MIN_SECONDS = 5
TAIL_SEGMENTS = 8
LOG_TAIL_LINES = 20


def alive(pid) -> bool:
    """A zombie has exited even though its pid still answers signals."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError, TypeError):
        return False
    return stat.rsplit(")", 1)[-1].split()[:1] not in (["Z"], ["X"])


def workspace(value) -> Path | None:
    """Only our own /tmp session directories may receive sentinel files."""
    if not isinstance(value, str):
        return None
    path = Path(value)
    if (path.parent != Path("/tmp") or not path.name.startswith(f"lecture-{os.getuid()}-")
            or path.is_symlink() or not path.is_dir()):
        return None
    return path


def read_state(path: Path) -> dict:
    # Snapshots must survive any file the GUI cannot parse; read_json only covers the usual cases.
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def number(value, default=0.0) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def clock_seconds(value, default=None):
    """"00:46:58.20" (the transcript's audio clock) → seconds; default when it is not one."""
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (AttributeError, ValueError):
        return default


class TranscriptTail:
    """Reads transcript.jsonl incrementally; the file grows for the whole lecture."""

    def __init__(self):
        self.inode = None
        self.offset = 0
        self.count = 0
        self.tail = deque(maxlen=TAIL_SEGMENTS)
        self.last_text = None  # plan GUI-4 Q3.1: end (audio seconds) of the latest segment with content

    def read(self, path: Path) -> None:
        try:
            stat = path.stat()
            if stat.st_ino != self.inode or stat.st_size < self.offset:
                self.__init__()
                self.inode = stat.st_ino
            if stat.st_size == self.offset:
                return
            with path.open("rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return
        # Like storage.events(): a line without its newline is still being written.
        end = data.rfind(b"\n") + 1
        for line in data[:end].splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                self.count += 1
                self.tail.append({key: record.get(key) for key in ("id", "start", "end", "text")})
                if isinstance(record.get("text"), str) and has_content(TEXT_MARKER.sub("", record["text"])):
                    self.last_text = clock_seconds(record.get("end"), self.last_text)
        self.offset += end


class SnapshotCache:
    def __init__(self):
        self.transcript = TranscriptTail()
        self.latest = None
        self.ratios = None  # History does not change while a session runs; read the registry once.


def median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def closing_ratios(history: list[dict] | None = None) -> dict:
    """Median closing seconds per recorded second over the latest successful runs, else defaults."""
    found = {"离线校正": [], "课后笔记": []}
    for record in records() if history is None else history:
        audio = number(record.get("audio_seconds"))
        stages = record.get("stages")
        flags = record.get("flags") if isinstance(record.get("flags"), dict) else {}
        if record.get("status") != "done" or audio <= 0 or not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, dict) or stage.get("name") not in found:
                continue
            if stage["name"] == "离线校正" and (flags.get("refinement_failed") or flags.get("refinement_skipped")):
                continue
            if len(found[stage["name"]]) < HISTORY and number(stage.get("seconds"), -1) >= 0:
                found[stage["name"]].append(number(stage["seconds"]) / audio)
    return {"refine": median(found["离线校正"]) if found["离线校正"] else REFINE_RATIO,
            "notes": median(found["课后笔记"]) if found["课后笔记"] else NOTES_RATIO}


def closing_estimate(recorded: float, refine: bool, ratios: dict) -> int:
    """How long closing would take if the lecture ended now."""
    seconds = ratios["notes"] * recorded
    if refine:
        seconds += ratios["refine"] * recorded + MODEL_LOAD_SECONDS
    return round(seconds)


def refine_progress(state: dict, now: float | None = None) -> tuple[float | None, int | None]:
    """Fraction of the audio refined, and remaining seconds extrapolated from the speed so far."""
    done, total = number(state.get("done_seconds"), None), number(state.get("total_seconds"), None)
    if done is None or not total or total <= 0:
        return None, None
    done = min(max(done, 0.0), total)
    if state.get("complete"):
        return 1.0, 0
    started = number(state.get("started"), None)
    elapsed = (time.time() if now is None else now) - started if started is not None else 0
    if done < ETA_MIN_SECONDS or elapsed <= 0:
        return done / total, None
    return done / total, round((total - done) * elapsed / done)


def latest_batch(directory: Path, previous):
    path = directory / "notes.sqlite"
    if not path.exists():
        return previous
    try:
        # Read-only and short timeout: never contend with the notes process for a write lock.
        db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=1)
        try:
            row = db.execute("SELECT body FROM batches ORDER BY first_id DESC LIMIT 1").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return previous
    return row[0] if row else previous


def device_label(asr: dict, meta: dict):
    # Same wording as cli.display().
    device = asr.get("asr_device")
    if not device:
        return None
    precision = "BF16" if meta.get("asr_model") in QWEN_MODELS else "FP16"
    return "云端 API" if device == "api" else f"NVIDIA GPU · {precision}" if device == "cuda" else "CPU"


def input_label(meta: dict) -> str:
    """What the lecture is listening to, for the recording screen's header."""
    if meta.get("demo"):
        return "演示输入"
    if isinstance(meta.get("audio_file"), str):
        return "音频文件 · " + Path(meta["audio_file"]).name
    device = meta.get("device")
    return "系统默认" if device in (None, "") else str(device)


# Chinese, Japanese and Korean text and their full-width punctuation: never joined with a space.
CJK = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\uff00-\uffef]")


def join_live(pending, buffer) -> str:
    """Plan N3.3: a space between pending and buffer only when both sides are Latin text, so the
    unconfirmed Chinese line never reads "老师 说"."""
    pending, buffer = (pending or "").strip(), (buffer or "").strip()
    if not pending or not buffer:
        return pending or buffer
    return pending + ("" if CJK.match(pending[-1]) or CJK.match(buffer[0]) else " ") + buffer


def gain_change(value):
    if isinstance(value, dict) and isinstance(value.get("text"), str) and value["text"] and isinstance(value.get("id"), int):
        return {"id": value["id"], "text": value["text"]}
    return None


def snapshot(directory: Path, cache: SnapshotCache | None = None) -> dict:
    """The only session shape the frontend depends on; field meanings follow cli.display()."""
    cache = cache or SnapshotCache()
    meta = read_state(directory / "session.json")
    asr = read_state(directory / "asr-state.json")
    notes = read_state(directory / "notes-state.json")
    refine = read_state(directory / "refinement-state.json")
    controller = read_state(directory / "controller-state.json")
    cache.transcript.read(directory / "transcript.jsonl")
    cache.latest = latest_batch(directory, cache.latest)
    children = meta.get("children") if isinstance(meta.get("children"), list) else []
    count = int(number(asr.get("count"), cache.transcript.count))
    enabled = bool(meta.get("refine"))
    output = meta.get("output") if isinstance(meta.get("output"), str) else None
    phase = controller.get("phase") or "starting"
    elapsed = number(asr.get("captured", asr.get("seconds")))
    progress, eta = refine_progress(refine) if enabled else (None, None)
    estimate = None
    if phase in ("starting", "recording"):
        if cache.ratios is None:
            cache.ratios = closing_ratios()
        estimate = closing_estimate(elapsed, enabled, cache.ratios)
    return {
        "run_id": runs.run_id(output) if output else None,
        "course": meta.get("course"),
        "output": output,
        "started": meta.get("started"),
        "phase": phase,
        # Wall-clock start of the current phase; the closing screen times the draining step with it.
        "phase_since": number(controller.get("since"), None),
        "input": input_label(meta),
        "paused": (directory / "pause").exists(),
        "can_skip": bool(controller.get("can_skip")),
        "elapsed_seconds": int(elapsed),
        # Recording only: "if the lecture ended now, closing would take about this long".
        "closing_estimate_seconds": estimate,
        "asr": {
            "status": asr.get("status") or "启动中",
            "device_label": device_label(asr, meta),
            "model": meta.get("asr_model"),
            "level": number(asr.get("level")),
            "backlog_seconds": number(asr.get("lag")) + number(asr.get("queued")),
            "queued_seconds": number(asr.get("queued")),
            "notices": [{"kind": kind, "text": asr[field]}
                        for kind, field in (("device", "device_notice"), ("gain", "gain_notice"), ("warning", "warning"))
                        if asr.get(field)],
            "error": asr.get("error") or None,
            # Plan N3.4: long weak input while someone talks. The mic cell and a dismissible banner show it;
            # it is not one of the notices, which are not dismissible.
            "weak_input": asr.get("weak_input") or None,
            # Plan GUI-4 Q2.3: the latest automatic volume change, a dismissible banner; id tells a
            # repeated text apart, so the same change happening again shows again.
            "gain_change": gain_change(asr.get("gain_change")),
        },
        "transcript": {
            "count": cache.transcript.count,
            "tail": list(cache.transcript.tail),
            "pending": join_live(asr.get("pending"), asr.get("buffer")),
            # Plan GUI-4 Q3.1: the recording screen shows "上次出字 … 前" from elapsed_seconds minus this.
            "last_text_seconds": cache.transcript.last_text,
        },
        "notes": {
            "status": notes.get("status") or "等待新增转录",
            # Unknown until the controller has recorded its children.
            "worker_alive": alive(children[1]) if len(children) > 1 else None,
            "unprocessed_segments": max(0, count - int(number(notes.get("cursor")))),
            "updated": notes.get("updated"),
            "latest": cache.latest,
        },
        "refine": {
            "enabled": enabled,
            "status": (refine.get("status") or asr.get("refinement_warning") or refine_label(meta)) if enabled else None,
            "reason": refine.get("reason"),
            "progress": progress,
            "eta_seconds": eta,
        },
        "stages": controller.get("stages") if isinstance(controller.get("stages"), list) else [],
    }


def with_log_tail(record: dict) -> dict:
    """A failed run carries the end of its controller log, read only from the registry's own folder."""
    log = record.get("log")
    if record.get("status") != "failed" or not isinstance(log, str):
        return record
    path = Path(log)
    try:
        inside = path.resolve().parent == runs.runs_dir().resolve() and not path.is_symlink()
    except OSError:
        inside = False
    return {**record, "log_tail": log_tail(path) if inside else ""}


class Busy(Exception):
    """Another session is already active."""


class StartError(Exception):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def log_tail(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def records() -> list[dict]:
    """Registry records, newest first; unreadable ones are skipped."""
    result = []
    for path in sorted(runs.runs_dir().glob("*.json"), reverse=True):
        try:
            record = runs.read(path.stem)
        except OSError:
            continue
        if record:
            # The record page falls back to this list when the event stream drops at the very end.
            result.append(with_log_tail(record))
    return result


def command(course: str, overrides: dict, context: Path | None) -> list[str]:
    demo = overrides.get("demo", False)
    args = [sys.executable, "-m", "lecture_cli", "demo" if demo else "start", "--headless"]
    if "interval" in overrides:
        args.append(f"--interval={overrides['interval']}")
    if context:
        args.append(f"--context={context}")
    if not demo:
        # demo has no such options: it neither records nor refines.
        if "language" in overrides:
            args.append(f"--language={overrides['language']}")
        if "refine" in overrides:
            args.append("--refine" if overrides["refine"] else "--no-refine")
        if "auto_gain" in overrides:
            args.append("--auto-gain" if overrides["auto_gain"] else "--no-auto-gain")
        if "asr_model" in overrides:
            args.append(f"--asr-model={overrides['asr_model']}")
    # "--" keeps a course name such as "-x" from being read as an option.
    return args + ["--", course]


class Sessions:
    def __init__(self, reap=None):
        from ..cli import reap_stale_sessions
        self.reap = reap or reap_stale_sessions
        self.lock = threading.Lock()
        self.starting = threading.Lock()
        self.children: dict[int, subprocess.Popen] = {}
        self.reaped: set = set()
        self.caches: dict[Path, SnapshotCache] = {}

    def active(self) -> dict | None:
        with self.lock:
            for pid, process in list(self.children.items()):
                if process.poll() is not None:  # Collect exited controllers so they do not linger as zombies.
                    del self.children[pid]
            stale = False
            for record in records():
                if record.get("status") != "running":
                    continue
                directory, pid = workspace(record.get("directory")), record.get("controller_pid")
                if directory and alive(pid):
                    return {"run_id": record.get("run_id"), "directory": directory, "pid": pid}
                if pid not in self.reaped:
                    self.reaped.add(pid)
                    stale = True
            if stale:
                # Crashed controllers: recover their notes and mark the records, as the CLI would.
                self.reap()
            return None

    def snapshot(self, active: dict) -> dict:
        cache = self.caches.setdefault(active["directory"], SnapshotCache())
        return snapshot(active["directory"], cache)

    def final_record(self, active: dict) -> dict | None:
        """The finished registry record, or None while the controller still runs."""
        record = self._final_record(active)
        return record if record is None else with_log_tail(record)

    def _final_record(self, active: dict) -> dict | None:
        try:
            record = runs.read(active["run_id"])
        except OSError:
            record = {}
        if record.get("status") not in (None, "running"):
            return record
        if alive(active["pid"]):
            return None
        # The controller writes its final record before exiting, so it crashed or lost the registry.
        with self.lock:
            self.reap()
        try:
            record = runs.read(active["run_id"]) or {}
        except OSError:
            record = {}
        if record.get("status") in (None, "running"):
            record = {**record, "run_id": active["run_id"], "status": "failed"}
        return record

    def start(self, course: str, overrides: dict, context: Path | None) -> dict:
        # Held through the whole wait so two clicks cannot start two lectures.
        with self.starting:
            if self.active():
                raise Busy()
            self.reap()
            return self._spawn(course, overrides, context)

    def _spawn(self, course: str, overrides: dict, context: Path | None) -> dict:
        with self.lock:
            directory = runs.runs_dir()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            log_path = directory / f"{datetime.now():%Y-%m-%d_%H%M%S}-controller.log"
            with log_path.open("a") as log:
                # A new session: the controller outlives this backend and keeps its own process group.
                process = subprocess.Popen(command(course, overrides, context), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            self.children[process.pid] = process
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            for record in records():
                if record.get("controller_pid") == process.pid and workspace(record.get("directory")):
                    return {"run_id": record.get("run_id"), "directory": Path(record["directory"]), "pid": process.pid}
            if process.poll() is not None:
                raise StartError(f"控制器已退出（退出码 {process.returncode}）", log_tail(log_path))
            time.sleep(0.1)
        process.terminate()  # It still saves whatever it has; better than an invisible recording.
        raise StartError(f"控制器 {START_TIMEOUT} 秒内未就绪，已请求它结束", log_tail(log_path))
