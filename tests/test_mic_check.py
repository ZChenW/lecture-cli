"""Plan GUI-4 Q1.5: the live input level, the 5-second test and doctor --mic-test, on fake streams only."""
import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest

pytest.importorskip("starlette")
import uvicorn

from lecture_cli import cli, mic_check
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
    assert mic_check.block_level(np.zeros(1600)) == {"rms": mic_check.FLOOR_DBFS, "peak": mic_check.FLOOR_DBFS}


def test_test_passes_when_a_voice_rises_15_db_over_the_floor():
    result = mic_check.evaluate(levels_of([-60] * 5 + [-60] * 10 + [-25] * 10 + [-60] * 25))
    assert result["passed"] and result["floor"] == pytest.approx(-60, abs=0.5) and result["rise"] > 30


def test_test_fails_on_steady_noise_and_on_silence():
    # Incident 1: noise around -32 dBFS; its peaks sit about 12 dB above its RMS, never 15.
    noisy = mic_check.evaluate(levels_of([-32] * 50))
    assert not noisy["passed"] and 8 < noisy["rise"] < mic_check.PASS_RISE_DB
    assert not mic_check.evaluate(levels_of([None] * 50))["passed"]
    assert not mic_check.evaluate(levels_of([-60] * 5))["passed"]  # nothing after the floor


@pytest.mark.parametrize("values, expected", [
    ([-32] * 20, True),                       # steady noise: "麦克风可能没有在工作"
    ([-60] * 20, False),                      # a quiet room is fine
    ([None] * 20, False),
    ([-32, -32, -20, -35] * 5, False),        # a voice moves the level
])
def test_passive_check_of_the_first_2_seconds(values, expected):
    assert mic_check.still(levels_of(values)) is expected


# --- doctor --mic-test --------------------------------------------------------------------------------

def test_doctor_mic_test_passes_and_fails_on_injected_streams(monkeypatch, capsys):
    monkeypatch.setattr("lecture_cli.checks.run_checks", lambda config: [])
    config = {"device": "Fake Mic"}
    voice = factory(lambda i: -60 if i < 10 else -20 if i < 30 else (False if i >= 50 else -60))
    assert cli.doctor(config, mic_test=True, mic_stream=voice) == 0
    out = capsys.readouterr().out
    assert "麦克风测试通过" in out and FakeStream.opened[-1].device == "Fake Mic" and FakeStream.opened[-1].closed
    noise = factory(lambda i: -32 if i < 50 else False)
    assert cli.doctor(config, mic_test=True, mic_stream=noise) == 1
    out = capsys.readouterr().out.replace("\n", "")
    assert mic_check.FAILED in out and "I/O Port Access" in out and "--audio-file" in out


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


def test_levels_stream_with_the_passive_verdict_and_the_stream_closes_on_disconnect(home, serve):
    client = serve(sessions=FakeSessions(), mic_stream=factory(lambda i: -32, pace=0.01))
    login(client)
    with client.stream("GET", "/api/mic/level") as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        events = read_events(response, lambda e: e[-1][0] == "still" or len(e) > 40)
    levels = [data for name, data in events if name == "level"]
    assert len(levels) == 20 and set(levels[0]) == {"rms", "peak"}
    assert levels[0]["rms"] == pytest.approx(-32, abs=1)
    assert events[-1] == ("still", {"still": True})
    stream = FakeStream.opened[-1]
    assert stream.device == "Fake Mic"
    assert wait(lambda: stream.closed and not stream.running.is_set()), "the device stayed open after disconnect"


def test_test_mode_ends_with_its_result(home, serve):
    voice = factory(lambda i: -60 if i < 10 else -15 if i < 20 else -60, pace=0.002)
    client = serve(sessions=FakeSessions(), mic_stream=voice)
    login(client)
    with client.stream("GET", "/api/mic/level?test=1") as response:
        events = read_events(response, lambda e: False)  # the server ends the stream itself
    assert [name for name, _ in events].count("level") == 50
    assert events[-1][0] == "result" and events[-1][1]["passed"] is True
    assert "still" not in [name for name, _ in events]
    assert wait(lambda: FakeStream.opened[-1].closed)

    client = serve(sessions=FakeSessions(), mic_stream=factory(lambda i: -32, pace=0.002))
    login(client)
    with client.stream("GET", "/api/mic/level?test=1") as response:
        events = read_events(response, lambda e: False)
    assert events[-1][0] == "result" and events[-1][1]["passed"] is False


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
