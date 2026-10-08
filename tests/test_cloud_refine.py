"""Plan N4: after-class refinement through the cloud transcription service.

Every service here is local: httpx.MockTransport in-process, or a loopback HTTP server for the real
controller. No real key, no network, no GPU; Qwen is never imported.
"""
import builtins
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import wave

import httpx
import numpy as np
import pytest

from lecture_cli import api_capture, checks, cli, cloud_refine, config, refinement
from lecture_cli.gui.sessions import snapshot
from lecture_cli.storage import Journal, events, final_events, read_json, write_json

KEY = "fake-asr-key-1234"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LECTURE_ASR_API_KEY", KEY)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-notes-key")
    # Cloud refinement must never touch the local Qwen stack.
    original = builtins.__import__
    def guarded(name, *args, **kw):
        assert name.split(".")[0] not in ("qwen_asr", "torch", "faster_whisper", "whisperlivekit"), name
        return original(name, *args, **kw)
    monkeypatch.setattr(builtins, "__import__", guarded)


def session(root, **extra):
    meta = dict(course="MATH421", started="today", notes_model="deepseek-flash", output=str(root / "notes.md"),
                refine=True, refine_backend="api", refine_api_model="whisper-large-v3", language="zh",
                asr_backend="local", asr_api_base="https://example.invalid/v1/", asr_api_model="whisper-large-v3-turbo",
                asr_context="")
    meta.update(extra)
    write_json(root / "session.json", meta)
    (root / "transcript.jsonl").write_text(json.dumps(dict(id=1, start="00:00:00", end="00:01:01",
                                                         text="LIVE 实时转录")) + "\n")
    return meta


def archive(root, pcm):
    store = refinement.AudioArchive(root)
    store.append(pcm)
    store.close()


def voiced(seconds, quiet_at=25):
    samples = np.full(int(seconds * 16000), 1000, dtype="<i2")
    if seconds > quiet_at:
        samples[quiet_at * 16000:quiet_at * 16000 + 320] = 0
    return samples.tobytes()


def uploaded(request):
    body = request.content
    start = body.index(b"RIFF")
    size = int.from_bytes(body[start + 4:start + 8], "little") + 8
    with wave.open(io.BytesIO(body[start:start + size])) as audio:
        return audio.readframes(audio.getnframes())


def field(request, name):
    body = request.content
    marker = f'name="{name}"\r\n\r\n'.encode()
    if marker not in body:
        return None
    start = body.index(marker) + len(marker)
    return body[start:body.index(b"\r\n--", start)].decode()


class Service:
    """A fake OpenAI-compatible transcription service for httpx.MockTransport."""
    def __init__(self, replies=None, models=200):
        self.replies = list(replies or [])
        self.models = models
        self.requests = []

    def __call__(self, request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        if request.method == "GET":
            assert request.url.path == "/v1/models"
            return httpx.Response(self.models, json={"data": [{"id": "whisper-large-v3"}]})
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else {"text": f"第{len(self.requests)}段。"}
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)


def run(root, service, sleep=None):
    if sleep is not None:
        original = cloud_refine.CloudRefiner.__init__
        def init(self, meta, transport=None, sleep_=None):
            original(self, meta, transport, sleep)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cloud_refine.CloudRefiner, "__init__", init)
            return refinement.refine(root, transport=httpx.MockTransport(service))
    return refinement.refine(root, transport=httpx.MockTransport(service))


def test_uploads_every_segment_once_and_replaces_the_live_source(tmp_path):
    session(tmp_path)
    pcm = voiced(61)
    archive(tmp_path, pcm)
    service = Service([{"segments": [dict(start=0, end=10, text="第一句，"), dict(start=10, end=20, text="第二句。")]},
                       {"text": "第二段。"}, {"text": "第三段。"}])
    assert run(tmp_path, service) == 0
    # The existing 20–30 s rule, the same cut as live cloud transcription: nothing lost, nothing twice.
    assert b"".join(uploaded(r) for r in service.requests) == pcm
    assert [len(uploaded(r)) for r in service.requests] == [
        len(chunk) for _, _, chunk in refinement.segments(tmp_path / "refinement.pcm")]
    records = [json.loads(line) for line in (tmp_path / "refined.jsonl").read_text().splitlines()]
    assert [r["text"] for r in records] == ["第一句，第二句。", "第二段。", "第三段。"]
    assert [r["start"] for r in records] == ["00:00:00.00", "00:00:25.02", "00:00:45.04"]
    assert "LIVE" not in json.dumps(final_events(tmp_path), ensure_ascii=False)
    state = read_json(tmp_path / "refinement-state.json")
    assert state["complete"] is True and state["count"] == 3 and state["done_seconds"] == 61


def test_request_uses_the_refine_model_and_a_chinese_punctuation_prompt(tmp_path):
    session(tmp_path, asr_context="eigenvalue, eigenvector")
    archive(tmp_path, voiced(50))
    service = Service()
    assert run(tmp_path, service) == 0
    first, second = service.requests
    assert first.url.path == "/v1/audio/transcriptions"
    assert field(first, "model") == "whisper-large-v3"
    assert field(first, "language") == "zh"
    assert field(first, "prompt") == cloud_refine.PUNCTUATION_PROMPT + "\neigenvalue, eigenvector"
    # The previous segment's refined text follows, as in live cloud transcription.
    assert field(second, "prompt") == cloud_refine.PUNCTUATION_PROMPT + "\neigenvalue, eigenvector\n第1段。"
    assert "，" in cloud_refine.PUNCTUATION_PROMPT and cloud_refine.PUNCTUATION_PROMPT.endswith("。")


@pytest.mark.parametrize("language", ["en", "auto"])
def test_no_chinese_prompt_for_other_languages(tmp_path, language):
    session(tmp_path, language=language)
    archive(tmp_path, voiced(5))
    service = Service([{"text": "Hello there."}])
    assert run(tmp_path, service) == 0
    assert field(service.requests[0], "prompt") is None
    assert (field(service.requests[0], "language") is None) == (language == "auto")


def test_default_model_is_large_v3_not_turbo():
    assert config.DEFAULTS["refine_backend"] == "local"
    assert config.DEFAULTS["refine_api_model"] == "whisper-large-v3" == cloud_refine.DEFAULT_MODEL
    assert cloud_refine.request_meta(dict(asr_api_model="whisper-large-v3-turbo", language="en"))["asr_api_model"] \
        == "whisper-large-v3"


def test_long_course_terms_are_cut_at_a_term_to_the_cloud_budget():
    terms = ", ".join(f"term{i:03d}" for i in range(200))
    cut = cloud_refine.context(dict(asr_context=terms, language="en"))
    assert len(cut) <= cloud_refine.CONTEXT_LIMIT and terms.startswith(cut) and not cut.endswith(",")
    assert terms[len(cut):].startswith(", ")


def test_english_segments_keep_their_spaces():
    texts = cloud_refine.Texts()
    for part in ("We define", "the kernel.", "第一", "第二", "x"):
        texts.append(part, 0, 1)
    assert texts.text() == "We define the kernel. 第一第二 x"


def test_hallucination_filter_and_repetition_marker_are_api_captures(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    service = Service([{"segments": [
        dict(start=0, end=1, text="字幕由志愿者提供", no_speech_prob=0.9, avg_logprob=-1.5),
        dict(start=1, end=2, text="重复重复重复", compression_ratio=3.0),
        dict(start=2, end=3, text="真正的内容。")]}])
    assert run(tmp_path, service) == 0
    assert final_events(tmp_path)[0]["text"] == "[疑似重复，待核对] 重复重复重复真正的内容。"


@pytest.mark.parametrize("reply", [
    {"text": ""}, {"text": "   "},
    # Everything filtered as non-speech: a voiced segment came back empty.
    {"segments": [dict(start=0, end=1, text="谢谢观看", no_speech_prob=0.9, avg_logprob=-2.0)]}])
def test_voiced_segment_with_empty_result_falls_back(tmp_path, reply):
    session(tmp_path)
    archive(tmp_path, voiced(50))
    assert run(tmp_path, Service([{"text": "第一段。"}, reply])) == 1
    assert not (tmp_path / "refined.jsonl").exists()
    assert final_events(tmp_path) == events(tmp_path)
    state = read_json(tmp_path / "refinement-state.json")
    assert state["complete"] is False and "有声片段" in state["reason"]


def test_near_silent_segments_are_not_uploaded(tmp_path):
    session(tmp_path)
    archive(tmp_path, np.full(31 * 16000, 20, dtype="<i2").tobytes())
    service = Service()
    assert run(tmp_path, service) == 0
    assert service.requests == []
    assert {r["text"] for r in final_events(tmp_path)} == {"[近静音片段，未识别到文字]"}


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_rejected_segment_falls_back_without_leaking(tmp_path, status):
    session(tmp_path)
    archive(tmp_path, voiced(50))
    service = Service([{"text": "第一段。"}, httpx.Response(status, text=f"secret body {KEY}")])
    assert run(tmp_path, service) == 1
    assert final_events(tmp_path) == events(tmp_path)
    state = read_json(tmp_path / "refinement-state.json")
    assert f"HTTP {status}" in state["reason"] and "secret" not in json.dumps(state)


def test_unavailable_service_is_retried_then_falls_back(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    delays = []
    async def sleep(delay):
        delays.append(delay)
    service = Service([httpx.Response(503)] * 10)
    assert run(tmp_path, service, sleep) == 1
    assert delays == [5, 10, 20] and len(service.requests) == cloud_refine.RETRIES + 1
    state = read_json(tmp_path / "refinement-state.json")
    assert cloud_refine.RETRY_FAILED in state["reason"] and final_events(tmp_path) == events(tmp_path)


def test_a_brief_outage_recovers(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    async def sleep(delay):
        pass
    service = Service([httpx.Response(429), httpx.Response(502), {"text": "恢复了。"}])
    assert run(tmp_path, service, sleep) == 0
    assert final_events(tmp_path)[0]["text"] == "恢复了。"


def test_failed_service_check_falls_back_before_any_upload(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    service = Service(models=401)
    assert run(tmp_path, service) == 1
    assert service.requests == []
    state = read_json(tmp_path / "refinement-state.json")
    assert state["stage"] == "连接云端转录服务" and "转录服务验证失败" in state["reason"]


def test_malformed_response_falls_back(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    assert run(tmp_path, Service([{"segments": [{"start": 0}]}])) == 1
    assert "响应格式错误" in read_json(tmp_path / "refinement-state.json")["reason"]


def test_key_is_never_written_to_the_workspace(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(50))
    assert run(tmp_path, Service([{"text": "第一段。"}, httpx.Response(401, text=KEY)])) == 1
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path


def test_local_backend_is_unchanged(tmp_path):
    session(tmp_path, refine_backend="local")
    archive(tmp_path, voiced(5))
    assert refinement.refine(tmp_path, lambda pcm: "本机。", transport=httpx.MockTransport(
        lambda request: pytest.fail("no upload for local refinement"))) == 0
    assert read_json(tmp_path / "refinement-state.json")["complete"] is True


def test_configuration_validation():
    base = dict(config.DEFAULTS, courses_dir=None)
    fields = lambda cfg: {p.field for p in config.validate(cfg)}
    assert not {"refine_backend", "refine_api_model"} & fields(base)
    assert "refine_backend" in fields(dict(base, refine_backend="cloud"))
    assert "refine_api_model" in fields(dict(base, refine_backend="api", refine_api_model=" "))
    assert "refine_api_model" not in fields(dict(base, refine_backend="local", refine_api_model=""))


def fake_children(monkeypatch, calls, refine_rc=0):
    class Process:
        pid = 123
        returncode = 0
        def poll(self): return 0
    def spawn(command, **kw):
        role = command[-2]
        calls[role] = (command, kw["env"])
        directory = Path(command[-1])
        assert KEY not in (directory / "session.json").read_text()
        if role == "_capture":
            archive(directory, voiced(1))
            write_json(directory / "asr-state.json", {"status": "转录完成"})
        return Process()
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)


def test_spawn_gives_the_key_to_the_refine_child_only(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    calls = {}
    def capture_python(model, *_):
        assert model != "qwen3-asr-1.7b", "cloud refinement must not need the Qwen interpreter"
        return "/usr/bin/local-capture-python"
    monkeypatch.setattr(cli, "capture_python", capture_python)
    monkeypatch.setattr(cli, "capture_environment", lambda model, env: dict(env, HF_HUB_OFFLINE="1"))
    fake_children(monkeypatch, calls)
    cfg = cli.configuration()
    cfg.update(refine=True, refine_backend="api", asr_model="base.en")
    assert cli.session(SimpleNamespace(command="start"), cfg, course) == 0
    assert set(calls) == {"_capture", "_worker", "_refine"}
    refine_command, refine_env = calls["_refine"]
    assert refine_command[0] == sys.executable
    assert refine_env["LECTURE_ASR_API_KEY"] == KEY
    assert "DEEPSEEK_API_KEY" not in refine_env and "LECTURE_NOTES_API_KEY" not in refine_env
    for role in ("_capture", "_worker"):
        assert "LECTURE_ASR_API_KEY" not in calls[role][1], role
        assert KEY not in calls[role][0]
    assert KEY not in refine_command


def test_local_refinement_child_still_gets_no_key(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    calls = {}
    monkeypatch.setattr(cli, "capture_python", lambda *_: sys.executable)
    monkeypatch.setattr(cli, "capture_environment", lambda model, env: env)
    fake_children(monkeypatch, calls)
    cfg = cli.configuration()
    cfg.update(refine=True, refine_backend="local")
    assert cli.session(SimpleNamespace(command="start"), cfg, course) == 0
    assert all("LECTURE_ASR_API_KEY" not in env for _, env in calls.values())


def test_start_without_transcription_key_stops_before_recording(tmp_path, monkeypatch, capsys):
    course = tmp_path / "courses" / "MATH421"
    course.mkdir(parents=True)
    monkeypatch.delenv("LECTURE_ASR_API_KEY")
    config.save(dict(config.DEFAULTS, refine=True, refine_backend="api"))
    monkeypatch.setattr(cli, "capture_python", lambda model, *_: sys.executable)
    monkeypatch.setattr(cli, "resolve_asr_model", lambda model: model)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **kw: pytest.fail("must fail before spawn"))
    assert cli.main(["--courses-dir", str(course.parent), "start", "MATH421"]) == 1
    assert cli.CLOUD_REFINE_NO_KEY in capsys.readouterr().out.replace("\n", "")


def test_cloud_live_transcription_is_still_never_refined(tmp_path, monkeypatch):
    course = tmp_path / "MATH421"
    course.mkdir()
    calls = {}
    fake_children(monkeypatch, calls)
    cfg = cli.configuration()
    cfg.update(refine=True, refine_backend="api", asr_backend="api")
    assert cli.session(SimpleNamespace(command="start"), cfg, course) == 0
    assert "_refine" not in calls


def test_labels_name_the_cloud_model(tmp_path):
    meta = session(tmp_path)
    assert refinement.label(meta) == "云端 whisper-large-v3 · 下课后重新转录"
    assert refinement.label(dict(meta, refine_backend="local")) == "Qwen 1.7B · 下课后自动重转录"
    assert snapshot(tmp_path)["refine"]["status"] == "云端 whisper-large-v3 · 下课后重新转录"


def test_note_says_which_refinement_it_used(tmp_path):
    session(tmp_path)
    (tmp_path / "refined.jsonl").write_text(json.dumps(dict(id=1, start="00:00:00", end="00:00:30",
                                                          text="云端校正。")) + "\n")
    write_json(tmp_path / "refinement-state.json", {"complete": True, "count": 1})
    journal = Journal(tmp_path)
    journal.render()
    journal.close()
    note = (tmp_path / "notes.md").read_text()
    assert "详细笔记依据云端离线重转录（whisper-large-v3）" in note and "Qwen" not in note


def test_environment_check_for_cloud_refinement():
    seen = []
    def handle(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"data": [{"id": "whisper-large-v3"}]})
    cfg = dict(config.DEFAULTS, refine=True, refine_backend="api")
    found = checks.cloud_refine_ready(cfg, KEY, httpx.MockTransport(handle))
    assert [(c.id, c.level) for c in found] == [("refine", "ok"), ("refine_service", "ok")]
    assert seen == [f"Bearer {KEY}"] and KEY not in json.dumps([c.__dict__ for c in found], ensure_ascii=False)
    missing = checks.cloud_refine_ready(cfg, "", httpx.MockTransport(lambda r: pytest.fail("no key, no request")))
    assert [(c.id, c.level) for c in missing] == [("refine", "fail")]
    assert "LECTURE_ASR_API_KEY" in missing[0].hint


# --- The real controller, with the refine child talking to a loopback fake service -------------

SHIM = '''
import sys, json, os
from lecture_cli import cli
def capture_python(model, *args):
    assert model != "qwen3-asr-1.7b", "cloud refinement asked for the Qwen interpreter"
    return sys.executable
cli.capture_python = capture_python
if "_capture" in sys.argv:
    from lecture_cli import capture
    from lecture_cli.storage import write_json
    def capture_run(directory):
        assert "LECTURE_ASR_API_KEY" not in os.environ
        from lecture_cli.refinement import AudioArchive
        import numpy as np
        a = AudioArchive(directory)
        a.append(np.full(2 * 16000, 1000, dtype="<i2").tobytes())
        a.close()
        capture.Transcript(directory).append("LIVE_SOURCE", 0, 1)
        write_json(directory / "asr-state.json", {"status": "转录完成"})
        return 0
    capture.run = capture_run
if "_refine" in sys.argv:
    assert "DEEPSEEK_API_KEY" not in os.environ and "LECTURE_NOTES_API_KEY" not in os.environ
    sys.modules["qwen_asr"] = None  # Importing Qwen would now fail.
if "_worker" in sys.argv:
    assert "LECTURE_ASR_API_KEY" not in os.environ
    from lecture_cli import worker
    def complete(messages, model, max_tokens=2000):
        content = messages[-1]["content"]
        if max_tokens == 4000:
            return json.dumps({"continues_previous": False, "topics": [
                {"title": "测试主题", "question": "结论是什么？", "first": 1, "last": 1}]})
        source = "CLOUD_CORRECTED" if "CLOUD_CORRECTED" in content else "LIVE_SOURCE"
        body = source + " [L1]"
        return json.dumps({"body": body, "review": ""}) if max_tokens == 8000 else body
    worker.complete = complete
'''


class Handler(BaseHTTPRequestHandler):
    status = 200
    seen = []

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.seen.append(("GET", self.path, self.headers.get("Authorization"), b""))
        self.reply(200, {"data": [{"id": "whisper-large-v3"}]})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        self.reply(Handler.status, {"text": "CLOUD_CORRECTED。"} if Handler.status == 200 else {"error": "x"})


@pytest.mark.parametrize("status", [200, 401])
def test_real_controller_refines_through_a_local_fake_service(tmp_path, status, isolated_run_registry):
    root = Path(__file__).resolve().parents[1]
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM)
    Handler.status, Handler.seen = status, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    courses = tmp_path / "courses"
    (courses / "MATH421").mkdir(parents=True)
    config.save(dict(
        config.DEFAULTS, refine=True, refine_backend="api", asr_model="base.en",
        asr_api_base=f"http://127.0.0.1:{server.server_port}/v1"))
    # A proxy from the developer's shell must not intercept the loopback service.
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.update(PYTHONPATH=f"{shim}:{root}", XDG_CONFIG_HOME=str(tmp_path / "config"),
               DEEPSEEK_API_KEY="test-not-real", LECTURE_ASR_API_KEY=KEY)
    try:
        process = subprocess.run([sys.executable, "-m", "lecture_cli", "--courses-dir", str(courses),
                                  "start", "MATH421", "--language", "zh", "--headless"],
                                 env=env, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert process.returncode == 0, process.stdout + process.stderr
    assert "课后校正：云端 whisper-large-v3（课堂音频会上传到转录服务）" in process.stdout
    posts = [s for s in Handler.seen if s[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "/v1/audio/transcriptions"
    assert all(s[2] == f"Bearer {KEY}" for s in Handler.seen)
    assert cloud_refine.PUNCTUATION_PROMPT.encode() in posts[0][3]
    note_path = next(p for p in (courses / "MATH421" / "LectureNotes").glob("*.md")
                     if not p.stem.endswith((".live", ".review", ".transcript")))
    note = note_path.read_text()
    record = json.loads(next(isolated_run_registry.glob("*.json")).read_text())
    if status == 200:
        assert "CLOUD_CORRECTED" in note and refinement.WARNING not in note
        assert record["flags"]["refinement_failed"] is False
    else:
        # The same fallback as local refinement: the live transcript, and the usual warning.
        assert "CLOUD_CORRECTED" not in note and "LIVE_SOURCE" in note and refinement.WARNING in note
        assert record["flags"]["refinement_failed"] is True
    assert KEY not in json.dumps(record) and KEY not in process.stdout + process.stderr
    for path in note_path.parent.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


# --- Plan GUI-3 item 7: Chinese punctuation for Chinese refinement --------------------------------

@pytest.mark.parametrize("text, expected", [
    ("我们定义核,然后看例子.", "我们定义核，然后看例子。"),
    ("第3章:向量空间;下一节!对吗?", "第3章：向量空间；下一节！对吗？"),
    ("π约等于3.14,对吧?", "π约等于3.14，对吧？"),
    ("中文, English words.", "中文，English words."),  # The space after a converted mark goes with it.
    ("e.g. this, that: 10:30.", "e.g. this, that: 10:30."),  # No Chinese neighbour: untouched.
    ("He said: 中文 . 好", "He said: 中文 . 好"),  # A space is not Chinese either.
    ("好的...我们继续", "好的...我们继续"),  # A run of dots is not a full stop.
    ("版本v1.2发布", "版本v1.2发布"),
    ("（括号）.", "（括号）。"),  # Full-width forms count as Chinese neighbours.
])
def test_full_width_only_beside_chinese(text, expected):
    assert cloud_refine.full_width(text) == expected


def test_chinese_refinement_gets_full_width_punctuation_and_others_do_not(tmp_path):
    session(tmp_path)
    archive(tmp_path, voiced(5))
    assert run(tmp_path, Service([{"text": "特征值,特征向量. e.g. 3.14"}])) == 0
    records = [json.loads(line) for line in (tmp_path / "refined.jsonl").read_text().splitlines()]
    assert [r["text"] for r in records] == ["特征值，特征向量。e.g. 3.14"]
    english = tmp_path / "en"
    english.mkdir()
    session(english, language="en")
    archive(english, voiced(5))
    assert run(english, Service([{"text": "特征值,特征向量."}])) == 0
    records = [json.loads(line) for line in (english / "refined.jsonl").read_text().splitlines()]
    assert [r["text"] for r in records] == ["特征值,特征向量."]
