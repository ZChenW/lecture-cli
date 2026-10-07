"""Presets for OpenAI-compatible notes and transcription services, and connection tests."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

NOTES_PRESETS = {
    "deepseek": {"label": "DeepSeek", "api_base": "https://api.deepseek.com",
                 "model": "deepseek-flash", "extra_body": {"thinking": {"type": "disabled"}}},
    "openai": {"label": "OpenAI", "api_base": "https://api.openai.com/v1",
               "model": "", "extra_body": {}},
    "custom": {"label": "自定义（OpenAI 兼容）", "api_base": "", "model": "", "extra_body": {}},
}
ASR_PRESETS = {
    "groq": {"label": "Groq", "api_base": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo"},
    "openai": {"label": "OpenAI", "api_base": "https://api.openai.com/v1", "model": "whisper-1"},
    "custom": {"label": "自定义（OpenAI 兼容）", "api_base": "", "model": ""},
}
TEST_TIMEOUT = httpx.Timeout(10)


@dataclass(frozen=True)
class Result:
    ok: bool
    level: str  # "ok" | "warn" | "fail"
    message: str


def service_label(presets: dict, provider: str | None, api_base: str | None) -> str:
    # A custom service is named by its host; the preset label "自定义" says nothing useful.
    if provider in presets and provider != "custom":
        return presets[provider]["label"]
    return urlsplit(api_base or "").hostname or "自定义服务"


def notes_label(config: dict) -> str:
    return service_label(NOTES_PRESETS, config.get("notes_provider", "deepseek"),
                         config.get("notes_api_base"))


def asr_label(config: dict) -> str:
    return service_label(ASR_PRESETS, config.get("asr_provider", "groq"), config.get("asr_api_base"))


def notes_service(config: dict) -> dict:
    """Request settings for the notes process; missing fields mean the DeepSeek preset."""
    preset = NOTES_PRESETS["deepseek"]
    return {"api_base": config.get("notes_api_base", preset["api_base"]),
            "extra_body": config.get("notes_extra_body", preset["extra_body"]),
            "label": notes_label(config)}


def _client(transport):
    # Only pass a transport when injected, so a patched httpx.Client keeps its own.
    return httpx.Client(timeout=TEST_TIMEOUT, follow_redirects=False,
                        **({"transport": transport} if transport else {}))


def check_models(api_base: str, model: str, key: str, label: str, transport=None) -> Result:
    """GET {api_base}/models. Messages never contain the key or the response body."""
    if not key:
        return Result(False, "fail", f"{label} 未设置 key，未测试连接")
    try:
        with _client(transport) as client:
            response = client.get(api_base.rstrip("/") + "/models",
                                  headers={"Authorization": f"Bearer {key}"})
    except (httpx.HTTPError, ValueError):
        return Result(False, "fail", f"无法连接 {label}，请检查地址和网络")
    if response.status_code in (401, 403):
        return Result(False, "fail", f"{label} key 无效（HTTP {response.status_code}）")
    if response.status_code != 200:
        return Result(False, "fail", f"{label} 连接失败（HTTP {response.status_code}）")
    try:
        listed = {item.get("id") for item in response.json()["data"]}
    except (ValueError, KeyError, TypeError, AttributeError):
        listed = set()
    if model in listed:
        return Result(True, "ok", f"{label} 连接正常，模型 {model} 可用")
    # Some services list only part of their models; this is not proof the model is missing.
    return Result(True, "warn", f"{label} 连接正常，但模型列表中没有 {model or '(未填写)'}")


def test_notes(config: dict, key: str, transport=None) -> Result:
    return check_models(config.get("notes_api_base", ""), config.get("notes_model", ""), key,
                        notes_label(config), transport)


def test_asr(config: dict, key: str, transport=None) -> Result:
    return check_models(config.get("asr_api_base", ""), config.get("asr_api_model", ""), key,
                        asr_label(config), transport)


# Library functions, not pytest cases, even when imported into a test module.
test_notes.__test__ = test_asr.__test__ = False
