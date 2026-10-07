"""Run registry: one small JSON record per session, outliving its /tmp workspace.

Records never contain transcript text or keys.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

from .storage import write_json

KEEP = 200


class Unavailable(OSError):
    """A record exists but cannot be read back; callers treat the registry as unavailable."""


def runs_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "lecture-cli" / "runs"


def run_id(output) -> str:
    return Path(output).stem


def record_path(identifier: str) -> Path:
    return runs_dir() / f"{identifier}.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def prune(keep: int = KEEP) -> None:
    # Identifiers start with the local start time, so name order is age order.
    for path in sorted(runs_dir().glob("*.json"))[:-keep]:
        path.unlink(missing_ok=True)


def begin(meta: dict, directory: Path) -> str:
    identifier = run_id(meta["output"])
    runs_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(record_path(identifier), {
        "run_id": identifier, "course": meta["course"], "output": meta["output"],
        "started": meta["started"], "controller_pid": meta["controller_pid"],
        "directory": str(directory), "status": "running"})
    prune()
    return identifier


def read(identifier: str) -> dict:
    path = record_path(identifier)
    try:
        record = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:  # Invalid UTF-8 or invalid JSON.
        raise Unavailable(f"登记记录无法读取：{path.name}") from exc
    if not isinstance(record, dict):
        raise Unavailable(f"登记记录格式错误：{path.name}")
    return record


def update(identifier: str, **fields) -> None:
    # Never overwrite a record we cannot read: it may hold the only trace of that run.
    write_json(record_path(identifier), {**read(identifier), **fields})


def mark_recovered(meta: dict) -> None:
    """A crashed session's text was recovered by a later lecture command."""
    identifier = run_id(meta["output"])
    if record_path(identifier).exists():
        update(identifier, status="recovered", finished=now())
