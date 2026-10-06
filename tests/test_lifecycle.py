"""Real subprocess lifecycle checks, with synthetic capture and a stub API."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def stub_runtime(tmp_path):
    # Every child is a real Python process. sitecustomize substitutes only external inputs.
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text('''
import sys, time, json, os, re
from pathlib import Path
if "_worker" in sys.argv:
    from lecture_cli import worker
    def complete(*args):
        if os.environ.get("LECTURE_TEST_OUTAGE"):
            raise worker.APIError("offline")
        time.sleep(0.1)
        if len(args) > 2 and args[2] == 4000:
            ids = [int(i) for i in re.findall(r'\\[L(\\d+) ', args[0][-1]['content'].split('本批完整原文：')[1])]
            return json.dumps({"continues_previous": False, "topics": [
                {"title": "特征向量", "question": "为什么非零？", "first": ids[0], "last": ids[-1]}]})
        body = "- 保留特征向量的非零条件。 [L1]"
        return json.dumps({"body": body, "review": ""}) if len(args) > 2 and args[2] == 8000 else body
    worker.complete = complete
if "_capture" in sys.argv:
    from lecture_cli import capture
    from lecture_cli.storage import write_json
    def run(directory):
        transcript = capture.Transcript(directory)
        transcript.append("An eigenvector is nonzero.", 0, 1)
        write_json(directory / "asr-state.json", {"status": "test-ready", "count": 1})
        (directory / "test-ready").touch()
        while not (directory / "stop").exists():
            time.sleep(0.05)
        transcript.append("These are the final words.", 1, 2)
        write_json(directory / "asr-state.json", {"status": "转录完成", "count": 2})
        return 0
    capture.run = run
''')
    root = tmp_path / "courses"
    (root / "MATH421").mkdir(parents=True)
    # Unrelated user files must survive every lifecycle.
    (root / "MATH421" / "my-notes.md").write_text("user-owned")
    env = dict(os.environ, DEEPSEEK_API_KEY="synthetic-test-key", PYTHONPATH=f"{shim}:{ROOT}",
               XDG_CONFIG_HOME=str(tmp_path / 'config'))
    return root, env


def own_session(root):
    for path in Path("/tmp").glob(f"lecture-{os.getuid()}-*"):
        try:
            meta = json.loads((path / "session.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if meta["output"].startswith(str(root)) and (path / "test-ready").exists():
            return path


@pytest.mark.parametrize("outage", [False, True])
def test_sigterm_drains_capture_preserves_note_cleans_tmp(stub_runtime, outage):
    root, env = stub_runtime
    if outage:
        env["LECTURE_TEST_OUTAGE"] = "yes"
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421", "--interval", "1"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        directory = wait_until(lambda: own_session(root))
        proc.send_signal(signal.SIGTERM)
        stdout = proc.communicate(timeout=15)[0]
        assert proc.returncode == 0, stdout
        assert not directory.exists()
        files = list((root / "MATH421" / "LectureNotes").iterdir())
        note_path = next(p for p in files if not p.stem.endswith(('.transcript', '.live', '.review')))
        note = note_path.read_text()
        assert 'These are the final words.' in note_path.with_suffix('.transcript.md').read_text()
        assert "已结束" in note
        if outage:
            assert "待整理原文" in note_path.with_suffix('.review.md').read_text()
            assert "详细笔记未全部完成" in stdout
        else:
            assert "L1–L2" in note
        assert (root / "MATH421" / "my-notes.md").read_text() == "user-owned"
        assert "synthetic-test-key" not in stdout + note
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_sigkill_ends_children_and_next_launch_recovers_stale_session(stub_runtime):
    root, env = stub_runtime
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421"], env=env, stdout=subprocess.DEVNULL)
    try:
        directory = wait_until(lambda: own_session(root))
        meta = json.loads((directory / "session.json").read_text())
        proc.kill()
        proc.wait()
        def stopped(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
            except FileNotFoundError:
                return True
        wait_until(lambda: all(stopped(pid) for pid in meta["children"]))
        result = subprocess.run([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root), "courses"],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not directory.exists()
        note = Path(meta["output"]).read_text()
        assert "异常中断" in note
        assert "An eigenvector is nonzero." in Path(meta['output']).with_suffix('.transcript.md').read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_terminal_pause_resume_and_q(stub_runtime):
    root, env = stub_runtime
    env["TERM"] = "xterm-256color"
    master, slave = os.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421"], env=env, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    try:
        directory = wait_until(lambda: own_session(root))
        os.write(master, b"p")
        wait_until(lambda: (directory / "pause").exists())
        os.write(master, b"p")
        wait_until(lambda: not (directory / "pause").exists())
        os.write(master, b"q")
        # Drain the terminal so rendering cannot fill the PTY buffer.
        import select
        output = bytearray()
        deadline = time.monotonic() + 15
        while proc.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        proc.wait(timeout=3)
        assert proc.returncode == 0
        assert not directory.exists()
        rendered = output.decode("utf-8", errors="replace")
        assert "DeepSeek" in rendered and "输入音量" in rendered and "[P]" in rendered
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)


@pytest.mark.parametrize('blocked', ['directory', 'transcript'])
def test_unwritable_output_preserves_tmp_until_recovery(stub_runtime, blocked):
    root, env = stub_runtime
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        directory = wait_until(lambda: own_session(root))
        target = root / "MATH421" / "LectureNotes"
        moved = root / "MATH421" / "moved-notes"
        meta = json.loads((directory / 'session.json').read_text())
        attachment = Path(meta['output']).with_suffix('.transcript.md')
        saved_attachment = attachment.with_suffix('.saved')
        if blocked == 'directory':
            target.rename(moved)
        else:
            attachment.rename(saved_attachment)
            attachment.mkdir()
        proc.send_signal(signal.SIGTERM)
        output = proc.communicate(timeout=15)[0]
        assert proc.returncode == 1
        assert directory.exists() and "尚未成功保存" in output
        if blocked == 'directory':
            moved.rename(target)
        else:
            attachment.rmdir()
            saved_attachment.rename(attachment)
        result = subprocess.run([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root), "courses"],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert not directory.exists()
        note = Path(meta['output']).read_text()
        # Depending on startup timing, the worker may not have reached the API before
        # the output directory disappeared. Both saved notes and raw fallback are valid.
        transcript = attachment.read_text()
        assert 'An eigenvector is nonzero.' in transcript and 'These are the final words.' in transcript
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
