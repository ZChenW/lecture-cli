"""N1.1: discarding a lecture while it records, with the real controller and children."""
import json
import os
import subprocess
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from lecture_cli import cli, runs
from lecture_cli.storage import ATTACHMENT_DIR, ATTACHMENTS, attachment_path, write_json
from tests.test_lifecycle import own_session, stub_runtime, wait_until  # noqa: F401 (fixture)


def other_note(root):
    """A finished note of the same course in both layouts, which must survive untouched."""
    folder = root / "MATH421" / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True, exist_ok=True)
    note = folder / "2026-10-01_090000-课堂笔记-0f0f0f.md"
    files = {note: "kept note", attachment_path(note, "transcript"): "kept transcript",
             note.with_suffix(".review.md"): "kept legacy review"}
    for path, text in files.items():
        path.write_text(text)
    return files


def start(root, env, *extra):
    return subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421", "--headless", "--interval", "1", *extra], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def only_record(registry):
    records = [json.loads(path.read_text()) for path in registry.glob("*.json")]
    assert len(records) == 1
    return records[0]


def test_discard_while_recording_removes_exactly_this_run(stub_runtime, isolated_run_registry):
    root, env = stub_runtime
    kept = other_note(root)
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        meta = json.loads((directory / "session.json").read_text())
        output = Path(meta["output"])
        state = lambda: json.loads((directory / "controller-state.json").read_text())
        wait_until(lambda: state()["phase"] == "recording")
        # The live render has already written this run's files.
        wait_until(lambda: output.exists() and attachment_path(output, "transcript").exists())
        (directory / "discard").touch()
        stdout = proc.communicate(timeout=15)[0]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, stdout
    assert cli.DISCARDED in stdout and "已保存" not in stdout
    assert not directory.exists()
    assert not output.exists() and not any(attachment_path(output, kind).exists() for kind in ATTACHMENTS)
    assert not any(output.stem in path.name for path in (root / "MATH421").rglob("*"))
    for path, text in kept.items():
        assert path.read_text() == text
    assert (root / "MATH421" / "my-notes.md").read_text() == "user-owned"
    record = only_record(isolated_run_registry)
    assert record["status"] == "discarded" and record["exit_code"] == 0 and record["workspace_kept"] is None
    # Children were stopped rather than drained: no closing stage was ever entered.
    assert [stage["name"] for stage in record["stages"]] == ["录制与转录"]


def test_discard_removes_folders_it_created_only_when_empty(stub_runtime, isolated_run_registry):
    root, env = stub_runtime
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        state = lambda: json.loads((directory / "controller-state.json").read_text())
        wait_until(lambda: state()["phase"] == "recording")
        (directory / "discard").touch()
        stdout = proc.communicate(timeout=15)[0]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, stdout
    assert not (root / "MATH421" / "LectureNotes").exists()
    assert sorted(path.name for path in (root / "MATH421").iterdir()) == ["my-notes.md"]


def test_discard_after_closing_began_is_ignored(stub_runtime, isolated_run_registry, tmp_path):
    root, env = stub_runtime
    phases = tmp_path / "phases.jsonl"
    env["LECTURE_TEST_PHASES"] = str(phases)
    proc = start(root, env)
    try:
        directory = wait_until(lambda: own_session(root))
        state = lambda: json.loads((directory / "controller-state.json").read_text())
        wait_until(lambda: state()["phase"] == "recording")
        (directory / "stop").touch()
        wait_until(lambda: state()["phase"] in ("draining", "finalizing"))
        # A late request, as from a stale screen: the controller withdraws it and saves normally.
        (directory / "discard").touch()
        stdout = proc.communicate(timeout=20)[0]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0, stdout
    assert "已保存" in stdout and cli.DISCARDED not in stdout
    record = only_record(isolated_run_registry)
    note = Path(record["output"])
    assert record["status"] == "done" and "已结束" in note.read_text()
    assert "These are the final words." in attachment_path(note, "transcript").read_text()
    assert not directory.exists()


def crashed_workspace(root, phase, discard=True):
    """A workspace whose controller died; its children are long gone."""
    directory = Path(tempfile.mkdtemp(prefix=f"lecture-{os.getuid()}-", dir="/tmp"))
    folder = root / "MATH421" / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True, exist_ok=True)
    output = folder / f"2026-10-07_100000-课堂笔记-{uuid.uuid4().hex[:6]}.md"
    meta = {"app": "lecture-cli-v1", "course": "MATH421", "output": str(output), "started": "2026-10-07T10:00:00-04:00",
            "children": [], "refine": False}
    write_json(directory / "session.json", meta)
    write_json(directory / "controller-state.json", {"phase": phase})
    (directory / "transcript.jsonl").write_text(json.dumps(
        {"id": 1, "start": "00:00:00.00", "end": "00:00:01.00", "text": "Recovered words."}) + "\n")
    output.write_text("partial")
    attachment_path(output, "transcript").write_text("partial transcript")
    if discard:
        (directory / "discard").touch()
    runs.begin({**meta, "controller_pid": 999999}, directory)
    return directory, output


def test_reap_finishes_an_interrupted_discard(tmp_path, isolated_run_registry):
    root = tmp_path / "courses"
    kept = other_note(root)
    directory, output = crashed_workspace(root, "recording")
    try:
        cli.reap_stale_sessions()
    finally:
        if directory.exists():
            shutil.rmtree(directory)
            pytest.fail("workspace was not cleaned")
    assert not output.exists() and not attachment_path(output, "transcript").exists()
    assert all(path.read_text() == text for path, text in kept.items())
    assert runs.read(output.stem)["status"] == "discarded"


@pytest.mark.parametrize("phase,discard", [("finalizing", True), ("recording", False)])
def test_reap_recovers_when_no_discard_was_pending(tmp_path, isolated_run_registry, phase, discard):
    root = tmp_path / "courses"
    directory, output = crashed_workspace(root, phase, discard)
    try:
        cli.reap_stale_sessions()
    finally:
        if directory.exists():
            shutil.rmtree(directory)
            pytest.fail("workspace was not cleaned")
    assert "异常中断" in output.read_text()
    assert "Recovered words." in attachment_path(output, "transcript").read_text()
    assert runs.read(output.stem)["status"] == "recovered"


def test_discard_outputs_matches_only_exact_names(tmp_path):
    folder = tmp_path / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True)
    output = folder / "2026-10-07_100000-课堂笔记-abc123.md"
    mine = [output, attachment_path(output, "live"), folder / f".{output.name}.k3j4h5g6",
            folder / ATTACHMENT_DIR / f".{attachment_path(output, 'review').name}.a1b2c3d4"]
    others = [folder / "2026-10-07_100000-课堂笔记-abc123.md.bak", folder / "2026-10-07_100000-课堂笔记-abc1234.md",
              folder / f".{output.name}.too-long-suffix", folder / ATTACHMENT_DIR / "2026-10-07_100000-课堂笔记-abc123.extra.md",
              output.with_suffix(".transcript.md")]  # Old flat layout: never written by a new run.
    for path in mine + others:
        path.write_text("x")
    assert cli.discard_outputs(output) == []
    assert not any(path.exists() for path in mine) and all(path.exists() for path in others)
