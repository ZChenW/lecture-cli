import asyncio
import builtins
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import wave

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, asr, cli, refinement
from lecture_cli.capture import Transcript
from lecture_cli.storage import events, read_json, write_json


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-notes-key")
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)


def session(directory, **extra):
    meta = dict(asr_backend="api", asr_api_base="https://example.invalid/v1/",
                asr_api_model="whisper-large-v3-turbo", language="en", fast=True)
    meta.update(extra)
    write_json(directory / "session.json", meta)
    return meta


def test_shared_cut_preserves_all_pcm_and_chooses_quiet_frame(tmp_path):
    samples = np.full(65 * 16000, 1000, dtype="<i2")
    samples[25 * 16000:25 * 16000 + 320] = 0
    pcm = samples.tobytes()
    path = tmp_path / "audio.pcm"
    path.write_bytes(pcm)
    chunks = list(refinement.segments(path))
    pending = pcm
    cuts = []
    while pending:
        cut = refinement.segment_cut(pending)
        cuts.append(pending[:cut])
        pending = pending[cut:]
    assert cuts == [chunk[2] for chunk in chunks]
    assert b"".join(cuts) == pcm
    assert chunks[0][1] == 25.02
    assert all(20 <= end - start <= 30 for start, end, _ in chunks[:-1])
    assert all(left[1] == right[0] for left, right in zip(chunks, chunks[1:]))
    assert chunks[-1][1] == 65


def test_response_offsets_ids_sentence_and_length_merging(tmp_path):
    transcript = Transcript(tmp_path)
    api_capture.consume_response(transcript, {"segments": [
        dict(start=1, end=2, text="First"), dict(start=2, end=3, text="sentence."),
        dict(start=3, end=4, text="tail"),
    ]}, 0, 25)
    api_capture.consume_response(transcript, {"segments": [
        dict(start=1, end=2, text="x" * 240), dict(start=2, end=3, text="last"),
    ]}, 25, 5)
    records = events(tmp_path)
    assert [r["id"] for r in records] == [1, 2, 3, 4]
    assert records[0] == dict(id=1, start="00:00:01.00", end="00:00:03.00", text="First sentence.")
    assert records[2]["start"] == "00:00:26.00"
    assert records[3]["end"] == "00:00:28.00"


def test_silence_filter_and_repetition_marker(tmp_path):
    transcript = Transcript(tmp_path)
    api_capture.consume_response(transcript, {"segments": [
        dict(start=0, end=1, text="hallucination", no_speech_prob=0.7, avg_logprob=-1.1),
        dict(start=1, end=2, text="keep.", no_speech_prob=0.7, avg_logprob=-0.9),
        dict(start=2, end=3, text="repeat.", compression_ratio=2.5),
    ]}, 20, 3)
    assert [r["text"] for r in events(tmp_path)] == ["keep.", "[疑似重复，待核对] repeat."]


@pytest.mark.parametrize("result", [None, {}, {"text": 1}, {"segments": None},
    {"segments": [{}]}, {"segments": [dict(start=0, end="1", text="bad")]},
    {"segments": [dict(start=2, end=1, text="bad")]},
    {"segments": [dict(start=0, end=1, text="bad", avg_logprob=float("nan"))]},
    {"segments": [dict(start=0, end=1, text="good."), {}]}])
def test_malformed_response_is_fatal_without_partial_records(tmp_path, result):
    with pytest.raises(RuntimeError, match="响应格式"):
        api_capture.consume_response(Transcript(tmp_path), result, 0, 1)
    assert events(tmp_path) == []


@pytest.mark.parametrize("context,previous,language", [
    ("eigenvalue", "x" * 250, "en"), ("", "", "auto"), ("", "previous.", "zh")])
def test_multipart_prompt_wav_and_timeout(context, previous, language):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "ok"})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=api_capture.API_TIMEOUT) as client:
            return await api_capture.transcribe(client, dict(asr_api_base="https://example.invalid/v1",
                asr_api_model="turbo", asr_context=context, language=language),
                b"\x01\x00" * 16000, previous, {}, lambda: None)
    assert asyncio.run(execute()) == {"text": "ok"}
    request = requests[0]
    body = request.content
    expected = "\n".join(filter(None, [context, previous[-200:]]))
    if expected:
        assert b'name="prompt"\r\n\r\n' + expected.encode() + b"\r\n" in body
    else:
        assert b'name="prompt"' not in body
    assert (b'name="language"' in body) == (language != "auto")
    assert b'name="model"\r\n\r\nturbo\r\n' in body
    assert b'name="response_format"\r\n\r\nverbose_json\r\n' in body
    assert b'name="temperature"\r\n\r\n0\r\n' in body
    wav = body[body.index(b"RIFF"):body.index(b"RIFF") + 32044]
    with wave.open(io.BytesIO(wav)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        assert audio.readframes(16000) == b"\x01\x00" * 16000
    assert request.extensions["timeout"] == dict(connect=10, read=60, write=60, pool=60)


def test_retry_keeps_same_segment_and_clears_warning(tmp_path):
    requests, delays, states = [], [], []
    state = {}
    failures = [503, 429, "network", "timeout", 500, 502]
    def handle(request):
        requests.append(request)
        if len(requests) <= len(failures):
            failure = failures[len(requests) - 1]
            if failure == "network":
                raise httpx.ConnectError("secret response", request=request)
            if failure == "timeout":
                raise httpx.ReadTimeout("secret response", request=request)
            return httpx.Response(failure, text="secret response")
        return httpx.Response(200, json={"text": "once."})
    async def sleep(delay):
        delays.append(delay)
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await api_capture.transcribe(client, session(tmp_path), b"\x64\x00" * 16000,
                "", state, lambda: states.append(dict(state)), sleep=sleep)
            api_capture.consume_response(Transcript(tmp_path), result, 0, 1)
    asyncio.run(execute())
    assert delays == [5, 10, 20, 40, 60, 60]
    assert len(events(tmp_path)) == 1
    assert all(s["warning"] == api_capture.RETRY_WARNING for s in states[:-1])
    assert "warning" not in states[-1]
    for request in requests:
        assert api_capture.wav_audio(b"\x64\x00" * 16000) in request.content


@pytest.mark.parametrize("status", [400, 401, 403])
def test_other_4xx_is_fatal_without_leaking_body(tmp_path, status):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="secret-key-response"))) as client:
            await api_capture.transcribe(client, session(tmp_path), b"\x64\x00", "", {}, lambda: None)
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as error:
        asyncio.run(execute())
    assert "secret" not in str(error.value)


def test_audio_file_end_to_end_with_mock_service(tmp_path):
    samples = np.full(61 * 16000, 1000, dtype="<i2")
    samples[25 * 16000:25 * 16000 + 320] = 0
    pcm = samples.tobytes()
    path = tmp_path / "input.wav"
    path.write_bytes(api_capture.wav_audio(pcm))
    session(tmp_path, audio_file=str(path), asr_context="eigenvalue")
    uploads, prompts = [], []
    def handle(request):
        assert request.headers["Authorization"] == "Bearer fake-asr-key"
        if request.method == "GET":
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": []})
        body = request.content
        start = body.index(b"RIFF")
        size = int.from_bytes(body[start + 4:start + 8], "little") + 8
        with wave.open(io.BytesIO(body[start:start + size])) as audio:
            uploads.append(audio.readframes(audio.getnframes()))
        prompts.append(body)
        index = len(uploads)
        return httpx.Response(200, json={"segments": [dict(start=0, end=1, text=f"part {index}.")]})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert b"".join(uploads) == pcm
    records = events(tmp_path)
    assert [r["id"] for r in records] == [1, 2, 3]
    assert [r["start"] for r in records] == ["00:00:00.00", "00:00:25.02", "00:00:45.04"]
    assert b"eigenvalue\npart 1." in prompts[1]
    state = read_json(tmp_path / "asr-state.json")
    assert state["status"] == "转录完成"
    assert state["seconds"] == state["captured"] == 61
    assert state["queued"] == state["lag"] == 0
    assert state["pending"] == state["buffer"] == ""
    assert state["count"] == 3 and state["asr_device"] == "api"
    assert not (tmp_path / "refinement.pcm").exists()


@pytest.mark.parametrize("sample", [-32, 0, 32])
def test_near_silence_never_uploads(tmp_path, sample):
    path = tmp_path / "quiet.wav"
    path.write_bytes(api_capture.wav_audio(np.full(31 * 16000, sample, dtype="<i2").tobytes()))
    session(tmp_path, audio_file=str(path))
    requests = []
    def handle(request):
        requests.append(request.method)
        return httpx.Response(200, json={})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert requests == ["GET"] and events(tmp_path) == []
    assert read_json(tmp_path / "asr-state.json")["seconds"] == 31


@pytest.mark.parametrize("failure", [401, 503, "network"])
def test_preflight_failure_does_not_open_microphone(tmp_path, monkeypatch, failure):
    session(tmp_path)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        InputStream=lambda **kw: pytest.fail("microphone opened before preflight")))
    def handle(request):
        assert request.method == "GET"
        if failure == "network":
            raise httpx.ConnectError("secret-key-response", request=request)
        return httpx.Response(failure, text="secret-key-response")
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 1
    state = read_json(tmp_path / "asr-state.json")
    assert state["status"] == "转录失败" and "secret" not in state["error"]
    assert events(tmp_path) == []


def test_stop_bounds_inflight_upload_and_closes_microphone(tmp_path, monkeypatch):
    session(tmp_path)
    closed = []
    class Stream:
        def __init__(self, **kw): self.callback = kw["callback"]
        def start(self):
            self.callback(np.full((30 * 16000, 1), 0.1, dtype=np.float32), 30 * 16000,
                          None, SimpleNamespace(input_overflow=False))
        def stop(self): closed.append("stop")
        def close(self): closed.append("close")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))
    monkeypatch.setattr(api_capture, "drain_timeout", lambda state: 0.02)
    async def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={})
        (tmp_path / "stop").touch()
        await asyncio.sleep(1)
        return httpx.Response(200, json={"text": "late"})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 1
    state = read_json(tmp_path / "asr-state.json")
    assert state["error"] == "转录收尾超时"
    assert state["lag"] == 30 and state["seconds"] == 0
    assert closed == ["stop", "close"]


def test_configuration_key_precedence_and_no_key_completion(tmp_path, monkeypatch):
    directory = tmp_path / "config" / "lecture-cli"
    directory.mkdir(parents=True)
    (directory / "asr-api-key").write_text(" file-asr-key \n")
    config = cli.configuration(load_key=False)
    assert config["asr_backend"] == "local"
    assert config["asr_api_model"] == "whisper-large-v3-turbo"
    assert config["asr_api_base"] == "https://api.groq.com/openai/v1"
    cli.configuration()
    assert os.environ["LECTURE_ASR_API_KEY"] == "fake-asr-key"
    monkeypatch.delenv("LECTURE_ASR_API_KEY")
    cli.configuration(load_key=False)
    assert "LECTURE_ASR_API_KEY" not in os.environ
    cli.configuration()
    assert os.environ["LECTURE_ASR_API_KEY"] == "file-asr-key"


def test_api_start_and_spawn_isolate_keys_and_skip_local_imports(tmp_path, monkeypatch, capsys):
    course = tmp_path / "courses" / "MATH421"
    course.mkdir(parents=True)
    calls = {}
    original_import = builtins.__import__
    def guarded(name, *args, **kw):
        assert name.split(".")[0] not in ("faster_whisper", "whisperlivekit", "torch")
        return original_import(name, *args, **kw)
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(cli, "capture_python", lambda *_: pytest.fail("local interpreter checked"))
    monkeypatch.setattr(cli, "capture_environment", lambda *_: pytest.fail("local environment checked"))
    monkeypatch.setattr(cli, "resolve_asr_model", lambda *_: pytest.fail("local model checked"))
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    class Process:
        pid = 123
        returncode = 0
        def poll(self): return 0
    def spawn(command, **kw):
        role = command[-2]
        calls[role] = (command, kw["env"])
        directory = Path(command[-1])
        meta = read_json(directory / "session.json")
        assert meta["asr_backend"] == "api" and meta["asr_model"] == "custom-cloud"
        assert meta["refine"] is False
        assert "fake-asr-key" not in json.dumps(meta)
        if role == "_capture":
            Transcript(directory).append("Cloud source.", 0, 1)
            write_json(directory / "asr-state.json", {"status": "转录完成", "asr_device": "api",
                       "gain_notice": "检测到削波，麦克风音量 85% → 54%"})
        return Process()
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    assert cli.main(["--courses-dir", str(course.parent), "start", "MATH421", "--asr-backend", "api",
                     "--asr-api-model", "custom-cloud", "--refine"]) == 0
    assert set(calls) == {"_capture", "_worker"}
    capture_command, capture_env = calls["_capture"]
    assert capture_command[0] == sys.executable
    assert capture_env["LECTURE_ASR_API_KEY"] == "fake-asr-key"
    assert "DEEPSEEK_API_KEY" not in capture_env
    assert calls["_worker"][1]["DEEPSEEK_API_KEY"] == "fake-notes-key"
    assert "LECTURE_ASR_API_KEY" not in calls["_worker"][1]
    assert not (tmp_path / "config" / "lecture-cli" / "config.json").exists()
    assert "麦克风音量：检测到削波，麦克风音量 85% → 54%" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["key", "context"])
def test_api_start_rejects_missing_key_or_long_glossary_before_spawn(tmp_path, monkeypatch, capsys, problem):
    course = tmp_path / "courses" / "MATH421"
    course.mkdir(parents=True)
    if problem == "key":
        monkeypatch.delenv("LECTURE_ASR_API_KEY")
    else:
        write_json(course / "glossary.json", [dict(term="x" * 601, translation="词")])
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **kw: pytest.fail("must fail before spawn"))
    assert cli.main(["--courses-dir", str(course.parent), "start", "MATH421", "--asr-backend", "api"]) == 1
    output = capsys.readouterr().out
    assert ("LECTURE_ASR_API_KEY" in output and "asr-api-key" in output) if problem == "key" else "600" in output


def test_lightweight_models_and_completion_without_faster_whisper(monkeypatch, capsys):
    original_import = builtins.__import__
    def missing(name, *args, **kw):
        if name.startswith("faster_whisper"):
            raise ModuleNotFoundError("missing", name="faster_whisper")
        return original_import(name, *args, **kw)
    monkeypatch.setattr(builtins, "__import__", missing)
    assert asr.asr_models() == tuple(asr.QWEN_MODELS)
    assert cli.main(["_complete-asr-models"]) == 0
    assert capsys.readouterr().out.splitlines() == list(asr.QWEN_MODELS)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["models"]) == 0
    output = capsys.readouterr().out
    assert all(name in output for name in asr.QWEN_MODELS)


def test_capture_role_routes_to_api_module(tmp_path, monkeypatch):
    session(tmp_path, controller_pid=os.getppid())
    monkeypatch.setattr(api_capture, "run", lambda directory: 17 if directory == tmp_path else 1)
    assert cli.main(["_capture", str(tmp_path)]) == 17


def test_api_doctor_skips_local_probe(tmp_path, monkeypatch, capsys, fake_wpctl):
    config = cli.configuration()
    config.update(courses_dir=str(tmp_path), asr_backend="api")
    monkeypatch.setattr(cli, "capture_python", lambda *_: pytest.fail("local probe"))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(check_input_settings=lambda **kw: None))
    factory = httpx.Client
    def handle(request):
        assert request.method == "GET" and request.url.path == "/openai/v1/models"
        assert request.headers["Authorization"] == "Bearer fake-asr-key"
        return httpx.Response(200)
    monkeypatch.setattr(httpx, "Client", lambda **kw: factory(transport=httpx.MockTransport(handle), **kw))
    assert cli.doctor(config) == 0
    output = capsys.readouterr().out
    assert "转录服务" in output and "转录 key" in output and "WhisperLiveKit" not in output
    assert "✓ 默认源麦克风音量：85%" in output
    assert fake_wpctl.calls == [["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]]


def test_microphone_keeps_buffering_during_retry(tmp_path, monkeypatch):
    session(tmp_path)
    uploads, sleeps = [], []
    stream_instance = []
    class Stream:
        def __init__(self, **kw):
            self.callback = kw["callback"]
            stream_instance.append(self)
        def push(self, seconds):
            self.callback(np.full((seconds * 16000, 1), 0.1, dtype=np.float32), seconds * 16000,
                          None, SimpleNamespace(input_overflow=False))
        def start(self): self.push(30)
        def stop(self): pass
        def close(self): pass
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))
    original_transcribe = api_capture.transcribe
    async def sleep(delay):
        sleeps.append(delay)
        stream_instance[0].push(2)
        (tmp_path / "stop").touch()
        await asyncio.sleep(0.1)
        state = read_json(tmp_path / "asr-state.json")
        assert state["captured"] == 32 and state["queued"] == 2
        assert state["warning"] == api_capture.RETRY_WARNING
    async def transcribe(*args):
        return await original_transcribe(*args, sleep=sleep)
    monkeypatch.setattr(api_capture, "transcribe", transcribe)
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={})
        uploads.append(request.content)
        if len(uploads) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"text": f"part {len(uploads) - 1}."})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert sleeps == [5] and len(uploads) == 3
    records = events(tmp_path)
    assert [record["text"] for record in records] == ["part 1.", "part 2."]
    assert records[0]["end"] == records[1]["start"] == "00:00:20.02"
    state = read_json(tmp_path / "asr-state.json")
    assert state["seconds"] == state["captured"] == 32
    assert "warning" not in state


def test_microphone_pause_resume_and_stop_flush_tail(tmp_path, monkeypatch):
    session(tmp_path)
    transitions = []
    control = None
    class Stream:
        def __init__(self, **kw): self.callback = kw["callback"]
        def start(self):
            nonlocal control
            transitions.append("start")
            self.callback(np.full((16000, 1), 0.1, dtype=np.float32), 16000,
                          None, SimpleNamespace(input_overflow=False))
            if control is None:
                control = asyncio.create_task(steer())
        def stop(self): transitions.append("stop")
        def close(self): transitions.append("close")
    async def steer():
        (tmp_path / "pause").touch()
        await asyncio.sleep(0.12)
        assert read_json(tmp_path / "asr-state.json")["status"] == "已暂停"
        (tmp_path / "pause").unlink()
        await asyncio.sleep(0.12)
        (tmp_path / "stop").touch()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))
    def handle(request):
        return httpx.Response(200, json={} if request.method == "GET" else {"text": "tail."})
    assert api_capture.run(tmp_path, transport=httpx.MockTransport(handle)) == 0
    assert transitions == ["start", "stop", "start", "stop", "close"]
    assert events(tmp_path) == [dict(id=1, start="00:00:00.00", end="00:00:02.00", text="tail.")]


@pytest.mark.parametrize("mode", ["demo", "refine"])
def test_other_child_roles_never_receive_asr_key(tmp_path, monkeypatch, mode):
    course = tmp_path / "MATH421"
    course.mkdir()
    calls = {}
    monkeypatch.setattr(cli, "capture_python", lambda *_: sys.executable)
    monkeypatch.setattr(cli, "capture_environment", lambda model, env: env)
    class Process:
        pid = 123
        returncode = 0
        def poll(self): return 0
    def spawn(command, **kw):
        calls[command[-2]] = kw["env"]
        return Process()
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    config = cli.configuration()
    config["refine"] = mode == "refine"
    args = SimpleNamespace(command="demo" if mode == "demo" else "start")
    assert cli.session(args, config, course) == 0
    assert "_demo" in calls if mode == "demo" else "_refine" in calls
    for role, env in calls.items():
        # Both modes here use the local backend, whose capture child has no use for the key.
        assert "LECTURE_ASR_API_KEY" not in env
        assert ("DEEPSEEK_API_KEY" in env) == (role == "_worker")


@pytest.mark.parametrize("argument,expected", [("--api", 1), ("", 2), ("--gpu", 3)])
def test_install_modes_use_lightweight_or_original_dependencies(tmp_path, argument, expected):
    project = tmp_path / "project"
    project.mkdir()
    (project / "install.sh").write_text((Path(__file__).resolve().parents[1] / "install.sh").read_text())
    executable = project / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_TEST_LOG"\n')
    uv.chmod(0o755)
    log = tmp_path / "uv.log"
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
               XDG_BIN_HOME=str(tmp_path / "bin"), XDG_DATA_HOME=str(tmp_path / "data"),
               UV_TEST_LOG=str(log))
    command = ["bash", str(project / "install.sh")]
    if argument:
        command.append(argument)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == expected
    if argument == "--api":
        assert "requirements" not in calls[0] and "torch" not in calls[0]
        assert "--no-deps" not in calls[0] and "-e " in calls[0]
    else:
        assert "--torch-backend cpu" in calls[0] and "requirements.lock" in calls[0]
        assert "--no-deps -e" in calls[1]
        if argument == "--gpu":
            assert "requirements-gpu.lock" in calls[2]


def test_display_names_cloud_device(tmp_path):
    session(tmp_path, course="MATH421", output="notes.md", asr_model="whisper-large-v3-turbo")
    write_json(tmp_path / "asr-state.json", {"asr_device": "api"})
    output = io.StringIO()
    cli.Console(file=output, width=100).print(cli.display(tmp_path))
    assert "云端 API" in output.getvalue() and "whisper-large-v3-turbo" in output.getvalue()
