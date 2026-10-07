"""Real subprocess lifecycle checks, with synthetic capture and a stub API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
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
if os.environ.get("LECTURE_TEST_KEYS"):
    for role in ("_worker", "_capture"):
        if role in sys.argv:
            names = [n for n in ("LECTURE_NOTES_API_KEY", "DEEPSEEK_API_KEY", "LECTURE_ASR_API_KEY") if n in os.environ]
            Path(os.environ["LECTURE_TEST_KEYS"], role).write_text(json.dumps(names))
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


def test_children_receive_only_their_own_key(stub_runtime, tmp_path):
    root, env = stub_runtime
    keys = tmp_path / "keys"
    keys.mkdir()
    env.update(LECTURE_TEST_KEYS=str(keys), LECTURE_ASR_API_KEY="synthetic-asr-key")
    proc = subprocess.Popen([sys.executable, "-m", "lecture_cli", "--courses-dir", str(root),
                             "start", "math421", "--interval", "1"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until(lambda: own_session(root) and (keys / "_worker").exists())
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    # The local capture child gets no key at all; the notes child gets only notes keys.
    assert json.loads((keys / "_capture").read_text()) == []
    worker_keys = json.loads((keys / "_worker").read_text())
    assert "LECTURE_NOTES_API_KEY" in worker_keys and "LECTURE_ASR_API_KEY" not in worker_keys


class FakeNotesService(BaseHTTPRequestHandler):
    """OpenAI-compatible chat completions on loopback, answering with valid citations."""
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeNotesService.requests.append((self.path, self.headers["Authorization"], body))
        prompt = body["messages"][-1]["content"]
        if body["max_tokens"] == 4000:
            ids = [int(i) for i in re.findall(r"\[L(\d+) ", prompt.split("本批完整原文：")[1])]
            content = json.dumps({"continues_previous": False, "topics": [
                {"title": "特征向量", "question": "为什么必须非零？", "first": ids[0], "last": ids[-1]}]})
        elif body["max_tokens"] == 8000:
            first = re.search(r"\[L(\d+) ", prompt.split("本章完整原始转录：")[1])[1]
            content = f"特征向量必须非零。[L{first}]\n<!-- REVIEW -->\n"
        else:
            first = re.search(r"\[L(\d+)\]", prompt.split("本批新增转录：")[1])[1]
            content = f"- 随堂：特征向量非零 [L{first}]"
        reply = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def test_demo_uses_configured_openai_compatible_notes_service(tmp_path):
    import threading
    FakeNotesService.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeNotesService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = tmp_path / "courses"
    (root / "MATH421").mkdir(parents=True)
    config = tmp_path / "config" / "lecture-cli"
    config.mkdir(parents=True)
    (config / "config.json").write_text(json.dumps({
        "config_version": 2, "courses_dir": str(root), "notes_provider": "custom",
        "notes_api_base": f"http://127.0.0.1:{server.server_port}/v1", "notes_model": "fake-notes",
        "notes_extra_body": {}}))
    # A proxy from the developer's shell must not intercept the loopback service.
    env = {k: v for k, v in os.environ.items()
           if k not in ("DEEPSEEK_API_KEY", "LECTURE_ASR_API_KEY") and "proxy" not in k.lower()}
    env.update(LECTURE_NOTES_API_KEY="loopback-test-key", PYTHONPATH=str(ROOT), XDG_CONFIG_HOME=str(tmp_path / "config"))
    try:
        result = subprocess.run([sys.executable, "-m", "lecture_cli", "demo", "MATH421", "--interval", "1"],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    note_path = next(p for p in (root / "MATH421" / "LectureNotes").glob("*.md")
                     if not p.stem.endswith((".transcript", ".live", ".review")))
    note = note_path.read_text()
    assert "已结束" in note and "详细课堂笔记" in note and "（未全部完成）" not in note
    assert "特征向量必须非零" in note and "待整理原文" not in note
    tokens = {body["max_tokens"] for _, _, body in FakeNotesService.requests}
    assert tokens == {2000, 4000, 8000}
    for path, auth, body in FakeNotesService.requests:
        assert path == "/v1/chat/completions" and auth == "Bearer loopback-test-key"
        assert body["model"] == "fake-notes" and "thinking" not in body
    assert "loopback-test-key" not in result.stdout + note
