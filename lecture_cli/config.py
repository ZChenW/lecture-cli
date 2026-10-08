"""Versioned user configuration: defaults, migration, validation and API keys."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path

from .providers import ASR_PRESETS, NOTES_PRESETS
from .storage import atomic_text, write_json

CONFIG_VERSION = 2
DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "courses_dir": None,
    "notes_provider": "deepseek",
    "notes_api_base": NOTES_PRESETS["deepseek"]["api_base"],
    "notes_model": NOTES_PRESETS["deepseek"]["model"],
    "notes_extra_body": NOTES_PRESETS["deepseek"]["extra_body"],
    "asr_backend": "local",
    "asr_model": "base.en",
    "asr_device": "auto",
    "asr_provider": "groq",
    "asr_api_base": ASR_PRESETS["groq"]["api_base"],
    "asr_api_model": ASR_PRESETS["groq"]["model"],
    "language": "en",
    "interval": 60,
    "device": None,
    "refine": False,
    "refine_model": "qwen3-asr-1.7b",
    "auto_gain": True,
    "qwen_python": None,
    # Program names from lecture_cli.openers tables; None picks the first one installed.
    "file_manager": None,
    "terminal": None,
    "editor": None,
    # Per-course remembered choices, e.g. {"MATH421": {"language": "zh"}}.
    "course_settings": {},
}
# Environment variables first, then files in the configuration directory; legacy names last.
KEY_SOURCES = {
    "notes": (("LECTURE_NOTES_API_KEY", "DEEPSEEK_API_KEY"), ("notes-api-key", "api-key")),
    "asr": (("LECTURE_ASR_API_KEY",), ("asr-api-key",)),
}
# Child processes receive keys only under these names.
KEY_VARIABLES = {"notes": "LECTURE_NOTES_API_KEY", "asr": "LECTURE_ASR_API_KEY"}


# Fields only the GUI uses; command-line lectures never stop on them.
GUI_ONLY = ("courses_dir", "file_manager", "terminal", "editor", "file_manager_command", "terminal_command",
            "editor_command", "course_settings")


@dataclass(frozen=True)
class Problem:
    field: str
    message: str


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lecture-cli"


def config_path() -> Path:
    return config_dir() / "config.json"


def migrate(raw: dict) -> dict:
    """Upgrade an unversioned (v1) configuration; unknown keys are kept."""
    if "config_version" in raw:
        return dict(raw)
    result = dict(raw)
    if "model" in result:
        result.setdefault("notes_model", result.pop("model"))
    if "courses_dir" not in result:
        # v1 silently defaulted to the author's folder; keep that only where it really exists.
        legacy = Path.home() / "Downloads" / "Umass_CS_Class"
        result["courses_dir"] = str(legacy) if legacy.is_dir() else None
    for key in ("notes_provider", "notes_api_base", "notes_extra_body"):
        result.setdefault(key, copy.deepcopy(DEFAULTS[key]))
    base = result.get("asr_api_base", DEFAULTS["asr_api_base"])
    result.setdefault("asr_provider", "groq" if isinstance(base, str) and "groq.com" in base else "custom")
    result["config_version"] = CONFIG_VERSION
    return result


def load() -> dict:
    path = config_path()
    try:
        text = path.read_text()
        raw = json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError):
        text, raw = None, {}  # As before, an unreadable file falls back to defaults untouched.
    if not isinstance(raw, dict):
        raw = {}
    if text is not None and raw and "config_version" not in raw:
        # Keep the original bytes before the migrated file replaces it.
        atomic_text(path.with_name("config.json.v1.bak"), text)
        raw = migrate(raw)
        save(raw)
    return {**copy.deepcopy(DEFAULTS), **raw}


def save(config: dict) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(directory / "config.json", config)


def _http_url(value) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(config: dict) -> list[Problem]:
    from .asr import QWEN_MODELS
    problems = []
    root = config.get("courses_dir")
    if not root:
        problems.append(Problem("courses_dir", "尚未设置课程目录"))
    elif not Path(root).expanduser().is_dir():
        problems.append(Problem("courses_dir", f"课程目录不存在：{root}"))
    interval = config.get("interval")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 3600:
        problems.append(Problem("interval", "间隔必须在 1–3600 秒之间"))
    if config.get("asr_backend") not in ("local", "api"):
        problems.append(Problem("asr_backend", "采集后端必须为 local 或 api"))
    if not _http_url(config.get("asr_api_base")):
        problems.append(Problem("asr_api_base", "转录服务地址须以 http:// 或 https:// 开头"))
    if config.get("asr_backend") == "api" and not _nonempty(config.get("asr_api_model")):
        problems.append(Problem("asr_api_model", "云端转录须配置非空模型名称"))
    if not _http_url(config.get("notes_api_base")):
        problems.append(Problem("notes_api_base", "笔记服务地址须以 http:// 或 https:// 开头"))
    if not _nonempty(config.get("notes_model")):
        problems.append(Problem("notes_model", "笔记模型名称不能为空"))
    if not isinstance(config.get("notes_extra_body"), dict):
        problems.append(Problem("notes_extra_body", "notes_extra_body 必须是 JSON 对象"))
    if config.get("refine_model") not in QWEN_MODELS:
        problems.append(Problem("refine_model", "课后校正模型必须是 " + "、".join(QWEN_MODELS) + " 之一"))
    from .openers import problems as opener_problems
    problems += [Problem(field, message) for field, message in opener_problems(config)]
    if not course_settings_valid(config.get("course_settings")):
        problems.append(Problem("course_settings", "course_settings 必须形如 {\"课程名\": {\"language\": \"zh\"}}"))
    return problems


def course_settings_valid(value) -> bool:
    return isinstance(value, dict) and all(
        isinstance(name, str) and isinstance(entry, dict) and all(
            key == "language" and isinstance(language, str) and language.strip() for key, language in entry.items())
        for name, entry in value.items())


def course_language(config: dict, course: str) -> str | None:
    """The language remembered for this course, if any; unknown courses simply have none."""
    entry = config.get("course_settings")
    entry = entry.get(course) if isinstance(entry, dict) else None
    language = entry.get("language") if isinstance(entry, dict) else None
    return language if isinstance(language, str) and language.strip() else None


def remember_course_language(course: str, language: str) -> None:
    current = load()
    remembered = current.get("course_settings")
    remembered = dict(remembered) if course_settings_valid(remembered) else {}
    remembered[course] = {**remembered.get(course, {}), "language": language}
    save({**current, "course_settings": remembered})


def is_configured(config: dict, keys: dict) -> bool:
    """keys maps "notes"/"asr" to key_status() results."""
    root = config.get("courses_dir")
    return (bool(root) and Path(root).expanduser().is_dir() and keys["notes"]["set"]
            and (config.get("asr_backend") != "api" or keys["asr"]["set"]))


def read_key(kind: str) -> tuple[str, str | None]:
    variables, filenames = KEY_SOURCES[kind]
    for variable in variables:
        if value := os.environ.get(variable, "").strip():
            return value, "env"
    for filename in filenames:
        try:
            if value := (config_dir() / filename).read_text().strip():
                return value, "file"
        except FileNotFoundError:
            continue
    return "", None


def key_status(kind: str) -> dict:
    value, source = read_key(kind)
    return {"set": bool(value), "source": source, "tail": value[-4:] if value else None}


def write_key(kind: str, value: str) -> dict:
    value = value.strip()
    if not value or any(c in value for c in "\r\n"):
        raise ValueError("key 不能为空，也不能包含换行")
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkstemp creates the file with mode 600 before any byte is written.
    atomic_text(directory / KEY_SOURCES[kind][1][0], value + "\n")
    return key_status(kind)


def delete_key(kind: str) -> dict:
    """Remove stored key files, including the legacy name; environment variables stay."""
    for filename in KEY_SOURCES[kind][1]:
        (config_dir() / filename).unlink(missing_ok=True)
    return key_status(kind)


def load_keys() -> None:
    """Expose each resolved key to this process under its single transport name."""
    for kind, variable in KEY_VARIABLES.items():
        value, _ = read_key(kind)
        if value:
            os.environ[variable] = value
