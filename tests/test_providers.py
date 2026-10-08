"""Configurable notes service, connection tests and structured checks; fake transports only."""
import sys
from types import SimpleNamespace

import httpx
import pytest

from lecture_cli import checks, providers, worker
from lecture_cli.config import DEFAULTS


class FakeClient:
    """Stands in for httpx.Client inside worker.complete and records each request."""
    requests = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, **kwargs):
        FakeClient.requests.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": " 笔记 "}}]})


@pytest.fixture
def fake_post(monkeypatch):
    FakeClient.requests = []
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "notes-secret")
    monkeypatch.setattr(worker.httpx, "Client", FakeClient)
    return FakeClient.requests


def use_service(monkeypatch, **meta):
    monkeypatch.setattr(worker, "service", providers.notes_service(meta))


def test_complete_posts_to_configured_service_with_extra_body(fake_post, monkeypatch):
    use_service(monkeypatch, notes_provider="custom", notes_api_base="http://127.0.0.1:8099/v1/",
                notes_extra_body={"temperature": 0.2, "model": "must-not-win"})
    assert worker.complete([{"role": "user", "content": "hi"}], "local-model", 4000) == "笔记"
    url, kwargs = fake_post[0]
    assert url == "http://127.0.0.1:8099/v1/chat/completions"
    assert kwargs["headers"] == {"Authorization": "Bearer notes-secret"}
    assert kwargs["json"] == {"temperature": 0.2, "model": "local-model", "stream": False, "max_tokens": 4000,
                              "messages": [{"role": "user", "content": "hi"}]}


def test_complete_without_extra_body_sends_no_thinking_field(fake_post, monkeypatch):
    use_service(monkeypatch, notes_provider="openai", notes_api_base="https://api.openai.com/v1",
                notes_extra_body={})
    worker.complete([], "gpt-test")
    url, kwargs = fake_post[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert "thinking" not in kwargs["json"]


def test_default_service_keeps_deepseek_request(fake_post, monkeypatch):
    use_service(monkeypatch)
    worker.complete([], "deepseek-flash")
    url, kwargs = fake_post[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["json"]["thinking"] == {"type": "disabled"}


def test_worker_reads_service_from_session_and_names_it_in_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "service", worker.service)  # restored after the test
    worker.configure({"notes_provider": "custom", "notes_api_base": "https://llm.example.org/v1",
                      "notes_extra_body": {}})
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "notes-secret")
    class Failing(FakeClient):
        def post(self, url, **kwargs):
            return SimpleNamespace(status_code=503)
    monkeypatch.setattr(worker.httpx, "Client", Failing)
    with pytest.raises(worker.TransientAPIError, match="llm.example.org HTTP 503"):
        worker.complete([], "m")


def test_labels_use_preset_or_host():
    assert providers.notes_label(DEFAULTS) == "DeepSeek"
    assert providers.notes_label({"notes_provider": "custom", "notes_api_base": "http://10.0.0.2:8000/v1"}) == "10.0.0.2"
    assert providers.asr_label({"asr_provider": "openai"}) == "OpenAI"
    assert providers.asr_label({"asr_provider": "custom", "asr_api_base": ""}) == "自定义服务"


def models_transport(status=200, body=None, error=None, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        if error:
            raise error
        return httpx.Response(status, json=body if body is not None else {"data": []})
    return httpx.MockTransport(handle)


NOTES = dict(DEFAULTS, notes_api_base="https://notes.example/v1", notes_model="chat-1",
             notes_provider="custom")
ASR = dict(DEFAULTS, asr_api_base="https://asr.example/v1/", asr_api_model="whisper-x", asr_provider="custom")


@pytest.mark.parametrize("transport, level, words", [
    (models_transport(body={"data": [{"id": "chat-1"}, {"id": "other"}]}), "ok", "可用"),
    (models_transport(body={"data": [{"id": "other"}]}), "warn", "没有 chat-1"),
    (models_transport(body={"unexpected": True}), "warn", "没有 chat-1"),
    (models_transport(401), "fail", "key 无效"),
    (models_transport(403), "fail", "key 无效"),
    (models_transport(500), "fail", "HTTP 500"),
    (models_transport(error=httpx.ConnectError("refused")), "fail", "无法连接"),
    (models_transport(error=httpx.ReadTimeout("slow")), "fail", "无法连接"),
])
def test_notes_connection_branches(transport, level, words):
    result = providers.test_notes(NOTES, "sk-notes-9876", transport)
    assert result.level == level and result.ok is (level != "fail")
    assert words in result.message and "notes.example" in result.message
    assert "sk-notes-9876" not in result.message and "unexpected" not in result.message


def test_asr_connection_requests_models_with_bearer():
    seen = []
    result = providers.test_asr(ASR, "asr-key", models_transport(body={"data": [{"id": "whisper-x"}]}, seen=seen))
    assert result == providers.Result(True, "ok", "asr.example 连接正常，模型 whisper-x 可用")
    assert str(seen[0].url) == "https://asr.example/v1/models"
    assert seen[0].headers["Authorization"] == "Bearer asr-key"


@pytest.mark.parametrize("transport, level", [
    (models_transport(body={"data": []}), "warn"), (models_transport(401), "fail"),
    (models_transport(502), "fail"), (models_transport(error=httpx.ConnectError("x")), "fail"),
])
def test_asr_connection_branches(transport, level):
    assert providers.test_asr(ASR, "asr-key", transport).level == level


def test_missing_key_is_reported_without_a_request():
    seen = []
    result = providers.test_notes(NOTES, "", models_transport(seen=seen))
    assert result.level == "fail" and "未设置 key" in result.message and not seen


FC_BOTH = ("Noto Sans CJK JP,Noto Sans CJK JP Regular\nNoto Sans Mono CJK SC\nNoto Sans CJK SC\n"
           "Noto Serif CJK TC\nNoto Serif CJK SC\n")


def fake_runner(fonts=True):
    def run(command, **kwargs):
        if command[0] == "fc-list":
            assert command == ["fc-list", ":lang=zh", "family"]
            return SimpleNamespace(stdout=FC_BOTH if fonts else "")
        if command[0] == "wpctl":
            return SimpleNamespace(stdout="Volume: 0.42")
        assert command[1:3] == ["-m", "lecture_cli.asr"]
        return SimpleNamespace(returncode=0, stdout='{"device": "cuda", "notice": ""}')
    return run


def test_run_checks_local_structure(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice",
                        SimpleNamespace(check_input_settings=lambda **kw: None))
    config = dict(DEFAULTS, courses_dir=str(tmp_path), asr_model="small.en", refine=True,
                  qwen_python=str(tmp_path / "missing-python"))
    environ = {"LECTURE_NOTES_API_KEY": "notes-key-1234"}
    results = checks.run_checks(config, environ=environ, runner=fake_runner(fonts=False),
                                which=lambda name: None if name == "wpctl" else f"/usr/bin/{name}",
                                cached=lambda model: model == "small.en",
                                transport=models_transport(body={"data": [{"id": "deepseek-flash"}]}))
    assert all(isinstance(item, checks.Check) for item in results)
    by_id = {item.id: item for item in results}
    assert list(by_id) == ["mic_volume", "courses_dir", "notes_key", "ffmpeg", "whisperlivekit", "asr_device",
                           "asr_weights", "refine", "microphone", "wpctl", "cjk_font", "notes_service"]
    assert by_id["mic_volume"].level == "ok" and by_id["mic_volume"].detail == "42%"
    assert by_id["asr_device"].detail == "NVIDIA GPU · FP16" and by_id["asr_device"].level == "ok"
    assert by_id["asr_weights"].level == "ok"
    assert by_id["refine"].level == "fail" and "qwen_python" in by_id["refine"].hint
    assert by_id["wpctl"].level == "warn" and by_id["cjk_font"].level == "warn" and by_id["cjk_font"].hint
    assert by_id["notes_service"].level == "ok" and by_id["notes_service"].label == "笔记服务（DeepSeek）"
    assert all(item.hint == "" for item in results if item.level == "ok")
    assert "notes-key-1234" not in repr(results)


def test_run_checks_api_structure_and_weights_warning(tmp_path):
    config = dict(DEFAULTS, courses_dir=None, asr_backend="api")
    results = checks.run_checks(config, environ={}, runner=fake_runner(), which=lambda name: None,
                                cached=lambda model: pytest.fail("api backend has no local weights"),
                                transport=models_transport(seen=[]))
    by_id = {item.id: item for item in results}
    assert {"asr_key", "asr_service"} <= set(by_id) and "asr_device" not in by_id
    assert by_id["courses_dir"].level == "fail" and by_id["courses_dir"].detail == "尚未设置"
    assert by_id["asr_service"].level == by_id["notes_service"].level == "fail"
    assert by_id["cjk_font"].level == "ok"


@pytest.mark.parametrize("listing, level, detail", [
    (FC_BOTH, "ok", "无衬线 Noto Sans CJK SC · 衬线 Noto Serif CJK SC"),
    ("Noto Sans CJK SC\nNoto Sans Mono CJK SC\nAR PL UKai CN\n", "warn", "无衬线 Noto Sans CJK SC · 衬线 未找到"),
    ("WenQuanYi Zen Hei,文泉驛正黑,文泉驿正黑\nAR PL UMing CN\n", "ok", "无衬线 WenQuanYi Zen Hei · 衬线 AR PL UMing CN"),
    ("Source Han Serif SC,思源宋体\n", "warn", "无衬线 未找到 · 衬线 Source Han Serif SC"),
    ("", "warn", "无衬线 未找到 · 衬线 未找到"),
])
def test_cjk_font_check_tells_serif_from_sans(listing, level, detail):
    result = checks.cjk_font(lambda command, **kw: SimpleNamespace(stdout=listing))
    assert (result.id, result.label, result.level, result.detail) == ("cjk_font", "中文字体", level, detail)
    assert bool(result.hint) == (level != "ok")
    if level != "ok":
        assert "Noto Serif CJK" in result.hint


def test_missing_cjk_serif_warns_that_the_reader_falls_back():
    result = checks.cjk_font(lambda command, **kw: SimpleNamespace(stdout="Noto Sans CJK SC\n"))
    assert result.level == "warn" and "阅读界面" in result.hint and "无衬线" in result.hint


def test_cjk_font_check_without_fontconfig():
    def missing(command, **kw):
        raise FileNotFoundError(command[0])
    result = checks.cjk_font(missing)
    assert result.level == "warn" and result.detail == "无衬线 未找到 · 衬线 未找到"
