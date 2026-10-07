"""GUI backend over real loopback HTTP: security, configuration, keys, paths, sessions and events.

Starlette's TestClient buffers a whole streamed body, so the app runs under uvicorn on
127.0.0.1 with a system-assigned port, exactly as lecture gui serves it.
"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import httpx
import pytest

pytest.importorskip("starlette")
import uvicorn

from lecture_cli import runs
from lecture_cli.checks import Check
from lecture_cli.gui import server as gui_server
from lecture_cli.gui.sessions import Sessions, TranscriptTail, command, snapshot

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-" + "x" * 32
SECRET = "sk-test-SECRET-value-1234"

# Real controller and children; only the demo input and the notes service are replaced.
SHIM = '''
import sys, time, json, re
if "_demo" in sys.argv:
    from lecture_cli import cli, capture
    from lecture_cli.storage import write_json
    def demo(directory):
        transcript = capture.Transcript(directory)
        transcript.append("An eigenvector is nonzero.", 0, 1)
        write_json(directory / "asr-state.json", {"status": "test-ready", "seconds": 1, "count": 1, "level": 0.2})
        while not (directory / "stop").exists():
            time.sleep(0.05)
        transcript.append("These are the final words.", 1, 2)
        write_json(directory / "asr-state.json", {"status": "转录完成", "seconds": 2, "count": 2})
        return 0
    cli.demo_capture = demo
if "_worker" in sys.argv:
    from lecture_cli import worker
    def complete(*args):
        time.sleep(0.05)
        if len(args) > 2 and args[2] == 4000:
            ids = [int(i) for i in re.findall(r'\\\\[L(\\\\d+) ', args[0][-1]['content'].split('本批完整原文：')[1])]
            return json.dumps({"continues_previous": False, "topics": [
                {"title": "特征向量", "question": "为什么非零？", "first": ids[0], "last": ids[-1]}]})
        body = "- 保留特征向量的非零条件。 [L1]"
        return json.dumps({"body": body, "review": ""}) if len(args) > 2 and args[2] == 8000 else body
    worker.complete = complete
'''


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def home(tmp_path, monkeypatch, isolated_run_registry):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    for name in ("LECTURE_NOTES_API_KEY", "DEEPSEEK_API_KEY", "LECTURE_ASR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "courses"
    (root / "MATH421").mkdir(parents=True)
    return root


def save_config(root, **fields):
    from lecture_cli import config
    config.save({**config.DEFAULTS, "courses_dir": str(root), **fields})


class Running:
    def __init__(self, port):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.origin = {"Origin": self.base}
        self.client = httpx.Client(base_url=self.base, trust_env=False, timeout=30)

    def login(self):
        response = self.client.get(f"/?t={TOKEN}", follow_redirects=False)
        assert response.status_code == 302
        return response

    def post(self, path, body=None):
        return self.client.post(path, json=body, headers=self.origin)

    def events(self, collected, stop_after="finished"):
        with self.client.stream("GET", "/api/runs/active/events") as response:
            assert response.status_code == 200
            event = None
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    collected.append((event, json.loads(line[6:])))
                    if event == stop_after:
                        return


@pytest.fixture
def serve():
    started = []

    def start(**options):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        port = sock.getsockname()[1]
        app = gui_server.create_app(port, TOKEN, **options)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        wait_until(lambda: server.started)
        started.append((server, thread))
        return Running(port)

    yield start
    for server, thread in started:
        server.should_exit = True
        thread.join(5)


def test_host_token_cookie_and_origin_checks(home, serve):
    app = serve()
    response = app.client.get("/api/bootstrap")
    assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"
    assert app.client.get(f"/?t={TOKEN}", headers={"Host": f"evil.example:{app.port}"}).status_code == 400
    assert app.client.get("/api/bootstrap", headers={"Host": f"127.0.0.1:{app.port + 1}"}).status_code == 400
    wrong = app.client.get("/?t=wrong", follow_redirects=False)
    assert wrong.status_code == 200 and "set-cookie" not in wrong.headers and "Lecture" in wrong.text
    assert app.client.get("/api/bootstrap").status_code == 401

    cookie = app.login().headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and app.login().headers["location"] == "/"
    assert app.client.get("/api/bootstrap").status_code == 200
    assert app.client.get("/api/bootstrap", headers={"Host": f"localhost:{app.port}"}).status_code == 200
    save_config(home)
    for headers in ({}, {"Origin": "http://evil.example"}, {"Origin": f"http://localhost:{app.port}"}):
        response = app.client.post("/api/courses", json={"name": "CS101"}, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "bad_origin"
    assert app.post("/api/courses", {"name": "CS101"}).status_code == 201
    assert app.client.get("/api/nothing").json()["error"]["code"] == "not_found"



def test_every_response_carries_the_content_security_policy(home, serve):
    app = serve()
    responses = [app.client.get("/api/bootstrap"),  # 401
                 app.client.get("/", headers={"Host": "evil.example"}),  # 400
                 app.client.post("/api/quit", headers={"Origin": "http://evil.example"}),  # 403
                 app.login(),  # 302
                 app.client.get("/"),  # static HTML
                 app.client.get("/api/bootstrap"),  # JSON
                 app.client.get("/api/nothing"),  # 404 JSON
                 app.client.get("/missing.js")]  # static 404
    assert [r.status_code for r in responses] == [401, 400, 403, 302, 200, 200, 404, 404]
    for response in responses:
        policy = response.headers["content-security-policy"]
        assert policy == gui_server.CSP and "unsafe" not in policy
    directives = dict(part.strip().split(" ", 1) for part in gui_server.CSP.split(";"))
    assert directives["default-src"] == "'none'" and directives["script-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"

def test_bootstrap_on_empty_configuration(home, serve):
    app = serve()
    app.login()
    data = app.client.get("/api/bootstrap").json()
    assert data["configured"] is False and data["active_run"] is None
    assert {"field": "courses_dir", "message": "尚未设置课程目录"} in data["problems"]
    assert data["keys"]["notes"] == {"set": False, "source": None, "tail": None}
    assert data["config"]["courses_dir"] is None and "deepseek" in data["presets"]["notes"]
    assert data["version"]


def test_invalid_configuration_is_rejected_without_touching_the_file(home, serve, tmp_path):
    app = serve()
    app.login()
    path = tmp_path / "config" / "lecture-cli" / "config.json"
    # A missing courses folder does not block unrelated settings.
    assert app.client.put("/api/config", json={"interval": 30}, headers=app.origin).status_code == 200
    before = path.read_bytes()
    for change, field in (({"interval": 0}, "interval"), ({"notes_api_base": "ftp://x"}, "notes_api_base"),
                          ({"courses_dir": str(tmp_path / "missing")}, "courses_dir"),
                          ({"surprise": 1}, "surprise"), ({"config_version": 9}, "config_version")):
        response = app.client.put("/api/config", json=change, headers=app.origin)
        assert response.status_code == 422 and response.json()["error"]["field"] == field
        assert path.read_bytes() == before
    response = app.client.put("/api/config", json={"courses_dir": str(home), "language": "zh"}, headers=app.origin)
    assert response.status_code == 200 and response.json()["problems"] == []
    assert json.loads(path.read_text())["language"] == "zh"


def test_key_endpoints_never_return_key_values(home, serve, tmp_path, monkeypatch):
    seen = []

    def service(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    app = serve(transport=httpx.MockTransport(service))
    app.login()
    response = app.client.put("/api/keys/notes", json={"value": SECRET}, headers=app.origin)
    assert response.json() == {"set": True, "source": "file", "tail": "1234"}
    assert (tmp_path / "config" / "lecture-cli" / "notes-api-key").stat().st_mode & 0o777 == 0o600
    assert app.client.put("/api/keys/notes", json={"value": " "}, headers=app.origin).status_code == 422
    texts = [response.text, app.client.get("/api/bootstrap").text]
    tested = app.post("/api/test/notes")
    assert tested.json()["level"] == "ok" and seen[-1] == f"Bearer {SECRET}"
    unsaved = app.post("/api/test/notes", {"api_base": "https://other.example/v1", "model": "m", "key": "other-9999"})
    assert unsaved.json()["level"] == "warn" and seen[-1] == "Bearer other-9999"
    texts += [tested.text, unsaved.text]

    captured = {}

    def fake_checks(config, environ=None, **kwargs):
        captured.update(environ)
        return [Check("notes_key", "笔记服务 key", "ok")]

    monkeypatch.setattr("lecture_cli.checks.run_checks", fake_checks)
    checks = app.client.get("/api/checks")
    assert checks.json() == [{"id": "notes_key", "label": "笔记服务 key", "level": "ok", "detail": "", "hint": ""}]
    # Checks see the stored key; the backend's own environment stays untouched.
    assert captured["LECTURE_NOTES_API_KEY"] == SECRET and "LECTURE_NOTES_API_KEY" not in os.environ
    texts.append(checks.text)
    assert all(SECRET not in text for text in texts)

    assert app.client.delete("/api/keys/notes", headers=app.origin).json()["set"] is False
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "env-secret-5678")
    refused = app.client.delete("/api/keys/asr", headers=app.origin)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "key_from_env"
    assert "env-secret-5678" not in refused.text + app.client.get("/api/bootstrap").text
    assert app.client.put("/api/keys/other", json={"value": "x"}, headers=app.origin).status_code == 404



def test_connection_test_never_sends_the_saved_key_to_another_host(home, serve):
    seen = []

    def service(request):
        seen.append((request.url.host, request.headers["authorization"]))
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    app = serve(transport=httpx.MockTransport(service))
    app.login()
    save_config(home)
    app.client.put("/api/keys/notes", json={"value": SECRET}, headers=app.origin)
    for body in ({"api_base": "https://evil.example/v1"}, {"api_base": "https://evil.example/v1", "key": "  "}):
        refused = app.post("/api/test/notes", body)
        assert refused.status_code == 422 and refused.json()["error"]["field"] == "key"
    assert seen == []
    assert app.post("/api/test/notes", {"api_base": "https://evil.example/v1", "key": "typed-0000"}).status_code == 200
    assert seen.pop() == ("evil.example", "Bearer typed-0000")
    # The saved address (even with a trailing slash) keeps using the saved key.
    for body in ({}, {"api_base": "https://api.deepseek.com/"}, {"model": "other"}):
        assert app.post("/api/test/notes", body).status_code == 200
        assert seen.pop() == ("api.deepseek.com", f"Bearer {SECRET}")

def write_note(course, stem="2026-10-07_143000-课堂笔记-a1b2c3", attachments=("transcript",)):
    folder = course / "LectureNotes"
    folder.mkdir(exist_ok=True)
    (folder / f"{stem}.md").write_text(f"# {stem}\n")
    for kind in attachments:
        (folder / f"{stem}.{kind}.md").write_text(f"{kind} of {stem}\n")
    return folder / f"{stem}.md"


def test_paths_outside_courses_dir_are_forbidden(home, serve, tmp_path):
    opened = []
    app = serve(opener=opened.append)
    app.login()
    save_config(home)
    note = write_note(home / "MATH421")
    outside = tmp_path / "outside"
    (outside / "LectureNotes").mkdir(parents=True)
    (outside / "LectureNotes" / "secret.md").write_text("SECRET NOTES")
    (home / "MATH421" / "LectureNotes" / "linked.md").symlink_to(outside / "LectureNotes" / "secret.md")
    (home / "LINKED").symlink_to(outside)

    content = app.client.get("/api/notes/content", params={"path": str(note)}).json()
    assert content == {"main": f"# {note.stem}\n", "transcript": f"transcript of {note.stem}\n"}
    for path in (str(outside / "LectureNotes" / "secret.md"), "MATH421/../../outside/LectureNotes/secret.md",
                 str(home / "MATH421" / "LectureNotes" / "linked.md"), str(home / "LINKED" / "LectureNotes" / "secret.md")):
        response = app.client.get("/api/notes/content", params={"path": path})
        assert response.status_code == 403 and "SECRET" not in response.text
        assert app.post("/api/open", {"path": path, "mode": "file"}).status_code == 403
    for course in ("LINKED", "../outside"):
        assert app.client.get("/api/notes", params={"course": course}).status_code == 403
    (outside / "context.txt").write_text("background")
    escape = app.post("/api/runs", {"course": "MATH421", "overrides": {"context_path": str(outside / "context.txt")}})
    assert escape.status_code == 403
    assert app.post("/api/open", {"path": str(note), "mode": "folder"}).json() == {"ok": True}
    assert opened == [note.parent]


def test_courses_and_notes_listing(home, serve):
    app = serve()
    app.login()
    assert app.client.get("/api/courses").json()["error"]["code"] == "no_courses_dir"
    save_config(home)
    for name in ("a/b", ".hidden", "", "x\ny"):
        assert app.post("/api/courses", {"name": name}).status_code == 422
    assert app.post("/api/courses", {"name": "CS101"}).status_code == 201
    assert app.post("/api/courses", {"name": "CS101"}).status_code == 409
    write_note(home / "MATH421", "2026-10-01_090000-课堂笔记-aaaaaa", ())
    write_note(home / "MATH421", "2026-10-07_143000-演示笔记-bbbbbb", ("transcript", "review"))
    (home / "MATH421" / "glossary.json").write_text("[]")
    courses = app.client.get("/api/courses").json()
    assert [c["name"] for c in courses] == ["CS101", "MATH421"]
    assert courses[1] | {"path": None} == {"name": "MATH421", "path": None, "notes_count": 2,
                                           "last_note": "2026-10-07T14:30:00", "has_glossary": True}
    notes = app.client.get("/api/notes", params={"course": "MATH421"}).json()
    assert [n["started"] for n in notes] == ["2026-10-07T14:30:00", "2026-10-01T09:00:00"]
    assert notes[0]["kind"] == "演示笔记"
    assert notes[0]["attachments"] == {"transcript": True, "live": False, "review": True}
    assert app.client.get("/api/notes", params={"course": "NOPE"}).status_code == 404


@pytest.fixture
def controller_env(home, tmp_path, monkeypatch):
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM)
    # Inherited by every controller the backend starts, like the user's own environment.
    monkeypatch.setenv("PYTHONPATH", f"{shim}:{ROOT}")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    save_config(home)
    return home


def test_demo_session_through_the_api(controller_env, serve, isolated_run_registry):
    app = serve()
    app.login()
    started = app.post("/api/runs", {"course": "MATH421", "overrides": {"demo": True, "interval": 1}})
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["course"] == "MATH421" and run["run_id"] == Path(run["output"]).stem
    assert app.post("/api/runs", {"course": "MATH421", "overrides": {"demo": True}}).status_code == 409
    snapshot_now = wait_until(lambda: (s := app.client.get("/api/runs/active").json())
                              and s["transcript"]["count"] == 1 and s)
    assert snapshot_now["run_id"] == run["run_id"] and snapshot_now["phase"] == "recording"
    assert snapshot_now["transcript"]["tail"][0]["text"] == "An eigenvector is nonzero."
    assert app.post("/api/runs/active/pause").json()["paused"] is True
    assert app.post("/api/runs/active/resume").json()["paused"] is False

    collected = []
    reader = threading.Thread(target=app.events, args=(collected,))
    reader.start()
    wait_until(lambda: any(event == "snapshot" for event, _ in collected))
    assert app.post("/api/runs/active/stop").status_code == 200
    reader.join(30)
    assert not reader.is_alive()
    kinds = [event for event, _ in collected]
    assert kinds[0] == "snapshot" and kinds[-1] == "finished" and kinds.count("finished") == 1
    finished = collected[-1][1]
    assert finished["status"] == "done" and finished["exit_code"] == 0 and finished["run_id"] == run["run_id"]
    phases = [data["phase"] for event, data in collected if event == "snapshot"]
    assert phases[0] == "recording" and set(phases) <= {"recording", "draining", "finalizing", "saving"}

    assert app.client.get("/api/runs/active").json() is None
    assert app.post("/api/runs/active/stop").status_code == 404
    assert app.client.get("/api/runs").json()[0]["status"] == "done"
    note = Path(finished["output"])
    assert "These are the final words." in note.with_suffix(".transcript.md").read_text()
    assert "已结束" in note.read_text()
    # The failed end page (M6) reads the tail of this log.
    logs = list(isolated_run_registry.glob("*-controller.log"))
    assert [finished["log"]] == [str(path) for path in logs] and "笔记将保存到" in logs[0].read_text()


def test_backend_reattaches_a_session_started_from_the_command_line(controller_env, serve, isolated_run_registry):
    process = subprocess.Popen([sys.executable, "-m", "lecture_cli", "demo", "MATH421", "--headless", "--interval", "1"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True)
    try:
        record = wait_until(lambda: next((r for r in map(json.loads, (p.read_text() for p in isolated_run_registry.glob("*.json")))
                                          if r["controller_pid"] == process.pid), None))
        app = serve()
        app.login()
        active = app.client.get("/api/runs/active").json()
        assert active["run_id"] == record["run_id"] and active["course"] == "MATH421"
        assert app.client.get("/api/bootstrap").json()["active_run"]["run_id"] == record["run_id"]
        app.post("/api/runs/active/stop")
        output = process.communicate(timeout=30)[0]
        assert process.returncode == 0, output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    final = json.loads(runs.record_path(record["run_id"]).read_text())
    assert final["status"] == "done" and final["log"] is None  # Output went to a pipe, not a file.


def test_failed_start_returns_the_controller_log(home, serve, monkeypatch):
    app = serve()
    app.login()
    save_config(home)
    # No notes key: the controller refuses to start, as on the command line.
    response = app.post("/api/runs", {"course": "MATH421", "overrides": {"demo": True}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "start_failed" and "缺少笔记服务 key" in response.json()["error"]["log"]
    for overrides in ({"surprise": 1}, {"interval": 0}, {"refine": "yes"}, {"language": ""}):
        assert app.post("/api/runs", {"course": "MATH421", "overrides": overrides}).status_code == 422
    assert app.post("/api/runs", {"course": "NOPE"}).status_code == 404


def test_controller_command_lines():
    start = command("-odd name", {"language": "zh", "interval": 30, "refine": False, "auto_gain": True}, None)
    assert start[1:] == ["-m", "lecture_cli", "start", "--headless", "--interval=30", "--language=zh",
                         "--no-refine", "--auto-gain", "--", "-odd name"]
    demo = command("MATH421", {"demo": True, "language": "zh", "refine": True}, Path("/c/context.txt"))
    assert demo[3:] == ["demo", "--headless", "--context=/c/context.txt", "--", "MATH421"]


def test_stale_running_record_is_reaped_once(isolated_run_registry, tmp_path):
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "2026-10-07_143000-课堂笔记-a1b2c3.json").write_text(json.dumps({
        "run_id": "2026-10-07_143000-课堂笔记-a1b2c3", "status": "running",
        "controller_pid": int(finished.stdout), "directory": str(tmp_path)}))
    calls = []
    sessions = Sessions(reap=lambda: calls.append(1))
    assert sessions.active() is None and sessions.active() is None
    assert calls == [1]


def write_state(directory, name, value):
    (directory / name).write_text(json.dumps(value) if not isinstance(value, str) else value)


def test_snapshot_tolerates_missing_and_partial_state_files(tmp_path):
    empty = snapshot(tmp_path)
    assert empty["run_id"] is None and empty["phase"] == "starting" and empty["paused"] is False
    assert empty["asr"]["status"] == "启动中" and empty["asr"]["device_label"] is None
    assert empty["transcript"] == {"count": 0, "tail": [], "pending": ""}
    assert empty["notes"]["worker_alive"] is None and empty["notes"]["latest"] is None
    assert empty["refine"] == {"enabled": False, "status": None, "reason": None} and empty["stages"] == []

    write_state(tmp_path, "session.json", "{\"course\": ")
    (tmp_path / "asr-state.json").write_bytes(b"\xff\xfe")
    write_state(tmp_path, "controller-state.json", "[1, 2]")
    (tmp_path / "notes.sqlite").write_bytes(b"not a database")
    record = {"id": 1, "start": "00:00:00.00", "end": "00:00:01.00", "text": "one"}
    half = json.dumps({**record, "id": 2, "text": "two"})
    (tmp_path / "transcript.jsonl").write_text(json.dumps(record) + "\n" + half[:10])
    broken = snapshot(tmp_path)
    assert broken["course"] is None and broken["phase"] == "starting" and broken["notes"]["latest"] is None
    assert broken["transcript"]["count"] == 1

    write_state(tmp_path, "session.json", {"course": "MATH421", "output": "/n/2026-10-07_143000-课堂笔记-a1b2c3.md",
                                           "asr_model": "qwen3-asr-1.7b", "refine": True, "children": [1, 2 ** 22 + 7]})
    write_state(tmp_path, "asr-state.json", {"asr_device": "cuda", "lag": 1.0, "queued": 0.5, "count": 5,
                                             "pending": "then sum", "buffer": "them up", "gain_notice": "削波",
                                             "seconds": 61.7, "error": ""})
    write_state(tmp_path, "notes-state.json", {"cursor": 2, "status": "已更新"})
    write_state(tmp_path, "controller-state.json", {"phase": "refining", "can_skip": True, "stages": [{"name": "录制与转录"}]})
    (tmp_path / "notes.sqlite").unlink()
    with sqlite3.connect(tmp_path / "notes.sqlite") as db:
        db.execute("CREATE TABLE batches (first_id INTEGER PRIMARY KEY, last_id INTEGER, body TEXT, fallback INTEGER)")
        db.executemany("INSERT INTO batches VALUES (?, ?, ?, 0)", [(1, 1, "first"), (2, 3, "latest body")])
    db.close()
    with (tmp_path / "transcript.jsonl").open("a") as f:
        f.write(half[10:] + "\n")
    (tmp_path / "pause").touch()
    full = snapshot(tmp_path)
    assert full["run_id"] == "2026-10-07_143000-课堂笔记-a1b2c3" and full["paused"] is True
    assert full["phase"] == "refining" and full["can_skip"] is True and full["elapsed_seconds"] == 61
    assert full["asr"]["device_label"] == "NVIDIA GPU · BF16" and full["asr"]["backlog_seconds"] == 1.5
    assert full["asr"]["notices"] == [{"kind": "gain", "text": "削波"}] and full["asr"]["error"] is None
    assert full["transcript"]["pending"] == "then sum them up"
    assert full["notes"] | {"updated": None} == {"status": "已更新", "worker_alive": False, "unprocessed_segments": 3,
                                                 "updated": None, "latest": "latest body"}
    assert full["refine"]["status"] == "Qwen 1.7B · 下课后自动重转录"


def test_transcript_tail_reads_incrementally_and_follows_replacement(tmp_path):
    path = tmp_path / "transcript.jsonl"
    tail = TranscriptTail()
    lines = [json.dumps({"id": i, "start": "s", "end": "e", "text": f"t{i}"}) + "\n" for i in range(1, 11)]
    path.write_text("".join(lines[:9]))
    tail.read(path)
    offset = tail.offset
    with path.open("a") as f:
        f.write(lines[9])
    tail.read(path)
    assert tail.count == 10 and tail.offset == offset + len(lines[9].encode())
    assert [item["id"] for item in tail.tail] == list(range(3, 11))
    replacement = tmp_path / "new.jsonl"
    replacement.write_text(lines[0])
    os.replace(replacement, path)
    tail.read(path)
    assert tail.count == 1 and [item["id"] for item in tail.tail] == [1]


def test_models_devices_tasks_and_quit(home, serve, monkeypatch):
    quits = []
    app = serve(on_quit=lambda: quits.append(1))
    app.login()
    monkeypatch.setattr("lecture_cli.checks.weights_cached", lambda name: name == "base.en")
    models = {m["name"]: m for m in app.client.get("/api/asr-models").json()}
    assert models["base.en"] == {"name": "base.en", "family": "whisper", "cached": True,
                                 "env_ready": models["base.en"]["env_ready"]}
    assert models["qwen3-asr-1.7b"]["family"] == "qwen" and models["qwen3-asr-1.7b"]["cached"] is False
    assert app.post("/api/asr-models/no-such-model/prepare").status_code == 404

    fake = type(sys)("sounddevice")
    fake.default = type("Default", (), {"device": [1, 3]})
    fake.query_devices = lambda: [{"name": "HDMI", "max_input_channels": 0},
                                  {"name": "pipewire", "max_input_channels": 2}]
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    devices = app.client.get("/api/devices").json()
    assert devices["devices"] == [{"index": 1, "name": "pipewire", "channels": 2, "default": True}]
    assert set(devices["notes"]) == {"default", "pipewire"}

    tasks = gui_server.Tasks()
    task_id = tasks.start([sys.executable, "-c", "print('下载中'); print('完成')"])
    done = wait_until(lambda: (view := tasks.view(task_id))["status"] != "running" and view)
    assert done == {"id": task_id, "status": "done", "exit_code": 0, "output": ["下载中", "完成"]}
    assert app.client.get("/api/tasks/unknown").status_code == 404

    assert app.post("/api/quit").json() == {"ok": True}
    wait_until(lambda: quits)


def test_gui_no_window_serves_and_quits(home, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    process = subprocess.Popen([sys.executable, "-m", "lecture_cli", "gui", "--no-window"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = process.stdout.readline()
        assert "界面后端地址：http://127.0.0.1:" in line
        url = line.split("：", 1)[1].strip()
        base = url.split("/?")[0]
        runtime = tmp_path / "runtime" / "lecture-cli"
        assert json.loads((runtime / "gui.json").read_text())["url"] == url
        assert oct((runtime / "gui.json").stat().st_mode & 0o777) == "0o600"
        # A second launch finds the running instance and only reports its address.
        second = subprocess.run([sys.executable, "-m", "lecture_cli", "gui", "--no-window"], env=env,
                                capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and f"界面已在运行：{url}" in second.stdout
        with httpx.Client(trust_env=False, timeout=10) as client:
            assert client.get(url, follow_redirects=False).status_code == 302
            assert client.get(base + "/api/bootstrap").json()["configured"] is False
            assert client.post(base + "/api/quit", headers={"Origin": base}).json() == {"ok": True}
        assert process.wait(timeout=15) == 0
        assert not (runtime / "gui.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
