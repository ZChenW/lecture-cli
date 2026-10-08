"""The live input level and doctor --mic-test (plan GUI-4 Q1.5), judged by the PLAN-GUI-5 R2.3 verdict; fake streams only."""
import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest

pytest.importorskip("starlette")
import uvicorn

from lecture_cli import cli, mic_check, mic_gain
from lecture_cli.gui import server as gui_server
from lecture_cli.gui.sessions import Sessions

TOKEN = "test-token-" + "m" * 32


def block(dbfs, seed=0):
    """100 ms of noise at the given RMS; None is digital silence."""
    if dbfs is None:
        return np.zeros(1600, dtype=np.float32)
    return (np.random.default_rng(seed).standard_normal(1600) * 10 ** (dbfs / 20)).astype(np.float32)


class FakeStream:
    """Stands in for sounddevice.InputStream: a thread delivers the scripted 100 ms blocks."""
    opened = []

    def __init__(self, device, callback, levels, pace=0.0):
        self.device, self.callback, self.levels, self.pace = device, callback, levels, pace
        self.running = threading.Event()
        self.closed = False
        self.thread = None
        FakeStream.opened.append(self)

    def start(self):
        self.running.set()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        for index in range(10 ** 6):
            if not self.running.is_set():
                return
            level = self.levels(index)
            if level is False:
                return
            self.callback(block(level, seed=index))
            time.sleep(self.pace)

    def stop(self):
        self.running.clear()

    def close(self):
        self.closed = True


def factory(levels, pace=0.0):
    return lambda device, callback: FakeStream(device, callback, levels, pace)


# --- the rules --------------------------------------------------------------------------------------

def levels_of(values):
    return [mic_check.block_level(block(value, seed=i)) for i, value in enumerate(values)]


def test_block_level_reports_rms_and_peak_in_dbfs():
    sine = np.sin(2 * np.pi * 440 * np.arange(1600) / 16000) * 0.5
    level = mic_check.block_level(sine)
    assert level["peak"] == pytest.approx(-6.0, abs=0.1) and level["rms"] == pytest.approx(-9.0, abs=0.1)
    assert level["clipped"] is False
    assert mic_check.block_level(np.zeros(1600)) == {"rms": mic_check.FLOOR_DBFS, "peak": mic_check.FLOOR_DBFS, "clipped": False}
    assert mic_check.block_level(np.ones(1600))["clipped"] is True


@pytest.mark.parametrize("values, options, expected", [
    ([-32] * 20, {"adjusts": False}, "dead"),       # steady noise on an input never adjusted
    ([-32] * 20, {"volume": 0.85}, ""),             # the default source: the lecture will lower it
    ([-32] * 20, {"volume": 0.10}, "dead"),         # ... unless it is already at the 10 % floor
    ([-60] * 20, {"adjusts": False}, ""),           # a quiet room is fine
    ([None] * 20, {"adjusts": False}, ""),
    ([-32, -32, -20, -35] * 5, {"adjusts": False}, ""),  # a voice moves the level
    ([-12] * 20, {}, "high"),
])
def test_verdict_of_the_first_2_seconds(values, options, expected):
    result = mic_check.verdict(levels_of(values), **options)
    assert result["verdict"] == expected
    assert result["text"] == mic_gain.verdict_text(expected, options.get("adjusts", True))


# --- doctor --mic-test --------------------------------------------------------------------------------

def test_doctor_mic_test_prints_one_of_the_three_verdicts(monkeypatch, capsys, fake_wpctl):
    monkeypatch.setattr("lecture_cli.checks.run_checks", lambda config: [])
    config = {"device": "Fake Mic"}  # not the default source: nothing would adjust it
    quiet = factory(lambda i: -60 if i < 50 else False)
    assert cli.doctor(config, mic_test=True, mic_stream=quiet) == 0
    out = capsys.readouterr().out
    assert "麦克风正常" in out and "背景声" in out and "说几句话" not in out
    assert FakeStream.opened[-1].device == "Fake Mic" and FakeStream.opened[-1].closed
    noise = factory(lambda i: -32 if i < 50 else False)
    assert cli.doctor(config, mic_test=True, mic_stream=noise) == 1
    out = capsys.readouterr().out.replace("\n", "")
    assert mic_gain.DEAD_TEXT in out and "I/O Port Access" in out and "--audio-file" in out
    loud = factory(lambda i: -10 if i < 50 else False)
    assert cli.doctor(config, mic_test=True, mic_stream=loud) == 1
    assert mic_gain.HIGH_MANUAL_TEXT in capsys.readouterr().out
    # The default source with automatic adjustment: a lecture would lower it, and says so.
    assert cli.doctor({"device": None}, mic_test=True, mic_stream=loud) == 1
    assert mic_gain.HIGH_TEXT in capsys.readouterr().out
    assert not [c for c in fake_wpctl.calls if c[1] == "set-volume"]  # the check never adjusts


def test_plain_doctor_never_opens_the_microphone(monkeypatch):
    monkeypatch.setattr("lecture_cli.checks.run_checks", lambda config: [])
    monkeypatch.setattr(mic_check, "open_stream", lambda *a: pytest.fail("opened the microphone"))
    before = len(FakeStream.opened)
    assert cli.doctor({"device": None}) == 0
    assert len(FakeStream.opened) == before


def test_doctor_parser_offers_mic_test(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "doctor", lambda config, mic_test=False: seen.setdefault("mic_test", mic_test) and 0)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    cli.main(["doctor", "--mic-test", "--asr-backend", "api"])
    assert seen == {"mic_test": True}


# --- GET /api/mic/level ----------------------------------------------------------------------------

class FakeSessions(Sessions):
    def __init__(self):
        super().__init__(reap=lambda: None)
        self.current = None

    def active(self):
        return self.current


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    from lecture_cli import config
    config.save({**config.DEFAULTS, "device": "Fake Mic"})


@pytest.fixture
def serve():
    started = []

    def start(**options):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(gui_server.create_app(port, TOKEN, **options),
                                               log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        started.append((server, thread))
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20)
        return client

    yield start
    for server, thread in started:
        server.should_exit = True
        thread.join(5)


def login(client):
    assert client.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 302


def read_events(response, stop):
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
            if stop(events):
                break
    return events


def wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_levels_stream_with_the_verdict_and_the_stream_closes_on_disconnect(home, serve):
    client = serve(sessions=FakeSessions(), mic_stream=factory(lambda i: -32, pace=0.01))
    login(client)
    with client.stream("GET", "/api/mic/level") as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        events = read_events(response, lambda e: e[-1][0] == "verdict" or len(e) > 40)
    levels = [data for name, data in events if name == "level"]
    assert len(levels) == 20 and set(levels[0]) == {"rms", "peak", "clipped"}
    assert levels[0]["rms"] == pytest.approx(-32, abs=1) and levels[0]["clipped"] is False
    # "Fake Mic" is never adjusted, so steady noise is a dead microphone (PLAN-GUI-5 R2.3).
    assert events[-1] == ("verdict", {"verdict": "dead", "text": mic_gain.DEAD_TEXT})
    stream = FakeStream.opened[-1]
    assert stream.device == "Fake Mic"
    assert wait(lambda: stream.closed and not stream.running.is_set()), "the device stayed open after disconnect"


def test_clipping_shows_in_the_levels_and_as_too_loud(home, serve):
    client = serve(sessions=FakeSessions(), mic_stream=factory(lambda i: 0, pace=0.002))
    login(client)
    with client.stream("GET", "/api/mic/level") as response:
        events = read_events(response, lambda e: e[-1][0] == "verdict" or len(e) > 40)
    assert all(data["clipped"] for name, data in events if name == "level")
    assert events[-1] == ("verdict", {"verdict": "high", "text": mic_gain.HIGH_MANUAL_TEXT})


def test_the_test_mode_is_gone(home, serve):
    # PLAN-GUI-5 R2.7: ?test=1 no longer ends the stream with a result; it is the plain level stream.
    client = serve(sessions=FakeSessions(), mic_stream=factory(lambda i: -60, pace=0.002))
    login(client)
    with client.stream("GET", "/api/mic/level?test=1") as response:
        events = read_events(response, lambda e: len(e) >= 60)
    names = [name for name, _ in events]
    assert "result" not in names and names.count("verdict") == 1 and names.count("level") == 59
    assert dict(events)["verdict"] == {"verdict": "", "text": ""}
    assert wait(lambda: FakeStream.opened[-1].closed)


def test_refused_while_recording_and_ended_when_a_lecture_starts(home, serve, tmp_path):
    sessions = FakeSessions()
    sessions.current = {"directory": tmp_path, "run_id": "r"}
    opened = len(FakeStream.opened)
    client = serve(sessions=sessions, mic_stream=factory(lambda i: -32, pace=0.01))
    login(client)
    response = client.get("/api/mic/level")
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"
    assert len(FakeStream.opened) == opened  # never opened

    sessions.current = None
    with client.stream("GET", "/api/mic/level") as response:
        def started(events):
            if len(events) == 3:
                sessions.current = {"directory": tmp_path, "run_id": "r"}
            return events[-1][0] == "busy"
        events = read_events(response, started)
    assert events[-1][0] == "busy"
    assert wait(lambda: FakeStream.opened[-1].closed)


def test_needs_the_token_and_reports_a_device_that_cannot_open(home, serve):
    def broken(device, callback):
        raise RuntimeError("Device unavailable")
    client = serve(sessions=FakeSessions(), mic_stream=broken)
    assert client.get("/api/mic/level").status_code == 401
    login(client)
    response = client.get("/api/mic/level")
    assert response.status_code == 503 and "无法打开麦克风" in response.json()["error"]["message"]
