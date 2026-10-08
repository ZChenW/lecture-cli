"""N1.5: refinement progress, its time estimate, and the closing estimate shown while recording."""
import json

import numpy as np
import pytest

from lecture_cli import refinement, runs
from lecture_cli.gui import sessions
from lecture_cli.gui.sessions import SnapshotCache, closing_estimate, closing_ratios, refine_progress, snapshot
from lecture_cli.storage import write_json


def archive(directory, seconds):
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "session.json", {"course": "MATH421", "output": str(directory / "n.md"), "refine": True})
    store = refinement.AudioArchive(directory)
    store.append(np.full(seconds * 16000, 1000, dtype="<i2").tobytes())
    store.close()


def test_refinement_state_reports_monotonic_progress_after_model_load(tmp_path, monkeypatch):
    archive(tmp_path, 230)  # About nine 20–30 s segments: three batches of four.
    written = []
    original = refinement.write_json

    def record(path, value):
        if path.name == "refinement-state.json":
            written.append(dict(value))
        original(path, value)

    monkeypatch.setattr(refinement, "write_json", record)
    clock = iter(range(1000, 2000))
    monkeypatch.setattr(refinement.time, "time", lambda: next(clock))
    assert refinement.refine(tmp_path, lambda pcm: "text") == 0
    assert written[0] == {"status": "加载离线 Qwen 模型", "done_seconds": 0, "total_seconds": 230.0, "started": None}
    done = [state["done_seconds"] for state in written]
    assert done == sorted(done) and done[-1] == 230.0 and len(set(done)) >= 4
    assert {state["total_seconds"] for state in written} == {230.0}
    # Set once, when the first segment starts: model loading is excluded.
    assert {state["started"] for state in written[1:]} == {1000}
    assert written[-1]["complete"] is True


def test_failed_refinement_keeps_its_progress(tmp_path):
    archive(tmp_path, 130)
    calls = iter(range(100))

    def transcribe(pcm):
        if next(calls) == 5:
            raise RuntimeError("out of memory")
        return "text"

    assert refinement.refine(tmp_path, transcribe) == 1
    state = json.loads((tmp_path / "refinement-state.json").read_text())
    assert state["status"] == refinement.WARNING and state["complete"] is False
    assert 0 < state["done_seconds"] < state["total_seconds"] == 130.0


def test_progress_and_eta_extrapolate_linearly_once_five_seconds_are_done():
    assert refine_progress({}) == (None, None)
    assert refine_progress({"status": "加载离线 Qwen 模型", "done_seconds": 0, "total_seconds": 600, "started": None}) == (0.0, None)
    assert refine_progress({"done_seconds": 4.9, "total_seconds": 600, "started": 100}, now=110) == (4.9 / 600, None)
    # 100 s of audio in 20 s: 500 s of audio left takes about 100 s.
    assert refine_progress({"done_seconds": 100, "total_seconds": 600, "started": 100}, now=120) == (100 / 600, 100)
    assert refine_progress({"done_seconds": 600, "total_seconds": 600, "started": 100, "complete": True}) == (1.0, 0)
    assert refine_progress({"done_seconds": "x", "total_seconds": 600}) == (None, None)


def record(seconds, audio, refine=None, notes=None, status="done", **flags):
    stages = [{"name": "录制与转录", "seconds": seconds}]
    if refine is not None:
        stages.append({"name": "离线校正", "seconds": refine})
    if notes is not None:
        stages.append({"name": "课后笔记", "seconds": notes})
    return {"status": status, "audio_seconds": audio, "stages": stages,
            "flags": {"refinement_failed": False, "refinement_skipped": False, **flags}}


def test_ratios_default_without_history_and_use_the_median_of_the_last_five():
    assert closing_ratios([]) == {"refine": 0.1, "notes": 0.05}
    # Records come newest first; old runs without audio_seconds or unsuccessful ones are ignored.
    history = [record(600, 600, refine=60, notes=30), record(600, 600, refine=600, refinement_failed=True),
               record(600, 600, refine=1, refinement_skipped=True), record(600, 600, refine=1, status="failed"),
               record(600, 600, refine=120, notes=60), record(600, 600, refine=30, notes=6),
               record(600, 600, refine=90, notes=12), record(600, 600, refine=66, notes=18),
               record(600, 600, refine=6000, notes=6000),  # Sixth successful run: too old.
               {"status": "done", "stages": [{"name": "离线校正", "seconds": 5}]}]
    ratios = closing_ratios(history)
    assert ratios == {"refine": pytest.approx(66 / 600), "notes": pytest.approx(18 / 600)}
    assert closing_ratios([record(600, 600, notes=30)]) == {"refine": 0.1, "notes": 0.05}


def test_closing_estimate_formula():
    defaults = {"refine": 0.1, "notes": 0.05}
    assert closing_estimate(3600, True, defaults) == 360 + 15 + 180
    assert closing_estimate(3600, False, defaults) == 180
    assert closing_estimate(0, True, defaults) == 15


def test_snapshot_estimates_closing_only_while_recording_and_reads_history_once(tmp_path, isolated_run_registry,
                                                                              monkeypatch):
    runs_dir = isolated_run_registry
    runs_dir.mkdir(parents=True)
    write_json(runs_dir / "2026-10-01_090000-课堂笔记-aaaaaa.json", record(1200, 1000, refine=200, notes=100))
    directory = tmp_path / "ws"
    directory.mkdir()
    write_json(directory / "session.json", {"course": "MATH421", "output": str(tmp_path / "n.md"), "refine": True})
    write_json(directory / "asr-state.json", {"seconds": 600})
    write_json(directory / "controller-state.json", {"phase": "recording"})
    cache = SnapshotCache()
    reads = []
    original = sessions.records

    def counted():
        reads.append(1)
        return original()

    monkeypatch.setattr(sessions, "records", counted)
    first = snapshot(directory, cache)
    assert first["closing_estimate_seconds"] == round(0.2 * 600 + 15 + 0.1 * 600)
    assert first["refine"]["progress"] is None and first["refine"]["eta_seconds"] is None
    snapshot(directory, cache)
    assert reads == [1]
    write_json(directory / "controller-state.json", {"phase": "refining"})
    write_json(directory / "refinement-state.json", {"done_seconds": 300, "total_seconds": 600, "started": 1})
    later = snapshot(directory, cache)
    assert later["closing_estimate_seconds"] is None and later["refine"]["progress"] == 0.5
    assert isinstance(later["refine"]["eta_seconds"], int)
    write_json(directory / "session.json", {"course": "MATH421", "output": str(tmp_path / "n.md"), "refine": False})
    write_json(directory / "controller-state.json", {"phase": "recording"})
    assert snapshot(directory, cache)["closing_estimate_seconds"] == round(0.1 * 600)
    assert snapshot(directory, cache)["refine"] == {"enabled": False, "status": None, "reason": None,
                                                    "progress": None, "eta_seconds": None}


def test_registry_without_history_gives_default_estimate(tmp_path, isolated_run_registry):
    directory = tmp_path / "ws"
    directory.mkdir()
    write_json(directory / "session.json", {"course": "MATH421", "output": str(tmp_path / "n.md"), "refine": True})
    write_json(directory / "asr-state.json", {"captured": 100, "seconds": 90})
    assert snapshot(directory)["closing_estimate_seconds"] == round(0.1 * 100 + 15 + 0.05 * 100)
    assert runs.runs_dir() == isolated_run_registry
