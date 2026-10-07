"""Versioned configuration, migration, validation and key sources; no network."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lecture_cli import asr, cli, config, refinement
from lecture_cli.storage import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
KEY_VARIABLES = ("LECTURE_NOTES_API_KEY", "DEEPSEEK_API_KEY", "LECTURE_ASR_API_KEY", "LECTURE_QWEN_PYTHON")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # HOME also decides whether the legacy course folder exists.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for variable in KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "config" / "lecture-cli"


def write_v1(directory, data):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(json.dumps(data))


def test_migration_renames_model_and_backs_up_original(isolated):
    write_v1(isolated, {"model": "deepseek-v4-pro", "asr_model": "small.en"})
    original = (isolated / "config.json").read_text()
    loaded = config.load()
    assert loaded["notes_model"] == "deepseek-v4-pro" and "model" not in loaded
    saved = read_json(isolated / "config.json")
    assert saved["config_version"] == 2 and saved["notes_model"] == "deepseek-v4-pro" and "model" not in saved
    assert (isolated / "config.json.v1.bak").read_text() == original


@pytest.mark.parametrize("legacy, saved_root, expected", [
    (True, None, "legacy"), (False, None, None), (True, "/srv/courses", "/srv/courses"),
])
def test_migration_keeps_author_folder_only_where_it_exists(isolated, tmp_path, legacy, saved_root, expected):
    folder = tmp_path / "home" / "Downloads" / "Umass_CS_Class"
    if legacy:
        folder.mkdir(parents=True)
    write_v1(isolated, {"courses_dir": saved_root} if saved_root else {"language": "zh"})
    result = config.load()["courses_dir"]
    assert result == (str(folder) if expected == "legacy" else expected)
    assert read_json(isolated / "config.json")["courses_dir"] == result


@pytest.mark.parametrize("base, provider", [
    (None, "groq"), ("https://api.groq.com/openai/v1", "groq"), ("https://asr.example/v1", "custom"),
])
def test_migration_fills_provider_fields(isolated, base, provider):
    write_v1(isolated, {"asr_backend": "api"} | ({"asr_api_base": base} if base else {}))
    config.load()
    saved = read_json(isolated / "config.json")
    assert saved["notes_provider"] == "deepseek"
    assert saved["notes_api_base"] == "https://api.deepseek.com"
    assert saved["notes_extra_body"] == {"thinking": {"type": "disabled"}}
    assert saved["asr_provider"] == provider


def test_migration_keeps_unknown_keys(isolated):
    write_v1(isolated, {"model": "deepseek-flash", "my_note": {"keep": [1, 2]}})
    assert config.load()["my_note"] == {"keep": [1, 2]}
    assert read_json(isolated / "config.json")["my_note"] == {"keep": [1, 2]}


def test_migration_is_idempotent(isolated):
    v1 = {"model": "deepseek-flash", "asr_api_base": "https://asr.example/v1", "extra": 1}
    assert config.migrate(config.migrate(v1)) == config.migrate(v1)
    write_v1(isolated, v1)
    first = config.load()
    migrated = (isolated / "config.json").read_text()
    backup = (isolated / "config.json.v1.bak").read_text()
    assert config.load() == first
    assert (isolated / "config.json").read_text() == migrated
    assert (isolated / "config.json.v1.bak").read_text() == backup


def test_fresh_configuration_has_no_courses_dir_and_writes_nothing(isolated):
    loaded = config.load()
    assert loaded["courses_dir"] is None and loaded["config_version"] == 2
    assert loaded["refine_model"] == "qwen3-asr-1.7b" and loaded["qwen_python"] is None
    assert not isolated.exists()
    # Defaults are copied, so a caller's edit cannot leak into the next load.
    loaded["notes_extra_body"]["thinking"] = "changed"
    assert config.load()["notes_extra_body"] == {"thinking": {"type": "disabled"}}


def test_legacy_key_sources_still_work(isolated, monkeypatch):
    isolated.mkdir(parents=True)
    (isolated / "api-key").write_text(" legacy-file-key \n")
    assert config.key_status("notes") == {"set": True, "source": "file", "tail": "-key"}
    config.load_keys()
    assert os.environ["LECTURE_NOTES_API_KEY"] == "legacy-file-key"
    monkeypatch.delenv("LECTURE_NOTES_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-env-key")
    assert config.read_key("notes") == ("legacy-env-key", "env")


def test_new_key_names_take_precedence(isolated, monkeypatch):
    isolated.mkdir(parents=True)
    (isolated / "api-key").write_text("old-file\n")
    (isolated / "notes-api-key").write_text("new-file\n")
    assert config.read_key("notes") == ("new-file", "file")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-env")
    assert config.read_key("notes") == ("old-env", "env")
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "new-env")
    assert config.read_key("notes") == ("new-env", "env")


def test_write_and_delete_keys_never_touch_environment(isolated, monkeypatch):
    status = config.write_key("asr", "  secret-asr-1234 \n")
    assert status == {"set": True, "source": "file", "tail": "1234"}
    path = isolated / "asr-api-key"
    assert path.read_text() == "secret-asr-1234\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        config.write_key("notes", "   ")
    (isolated / "api-key").write_text("legacy\n")
    config.write_key("notes", "fresh")
    assert config.delete_key("notes") == {"set": False, "source": None, "tail": None}
    assert not (isolated / "api-key").exists() and not (isolated / "notes-api-key").exists()
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "from-env")
    assert config.delete_key("asr")["source"] == "env"
    assert not path.exists()


def valid_config(tmp_path, **changes):
    (tmp_path / "courses").mkdir(exist_ok=True)
    return dict(config.DEFAULTS, courses_dir=str(tmp_path / "courses")) | changes


def test_default_configuration_with_folder_is_valid(tmp_path):
    assert config.validate(valid_config(tmp_path)) == []


@pytest.mark.parametrize("changes, field", [
    ({"courses_dir": None}, "courses_dir"),
    ({"courses_dir": "/nonexistent/lecture-courses"}, "courses_dir"),
    ({"interval": 0}, "interval"),
    ({"interval": 3601}, "interval"),
    ({"interval": "60"}, "interval"),
    ({"asr_backend": "cloud"}, "asr_backend"),
    ({"asr_api_base": "ftp://asr.example"}, "asr_api_base"),
    ({"notes_api_base": "api.deepseek.com"}, "notes_api_base"),
    ({"notes_model": " "}, "notes_model"),
    ({"asr_backend": "api", "asr_api_model": ""}, "asr_api_model"),
    ({"notes_extra_body": "[]"}, "notes_extra_body"),
    ({"refine_model": "large-v3"}, "refine_model"),
])
def test_validate_reports_each_rule(tmp_path, changes, field):
    problems = config.validate(valid_config(tmp_path, **changes))
    assert [p.field for p in problems] == [field]
    assert problems[0].message


def test_empty_asr_model_is_fine_for_local_backend(tmp_path):
    assert config.validate(valid_config(tmp_path, asr_api_model="")) == []
    assert "尚未设置课程目录" in config.validate(valid_config(tmp_path, courses_dir=None))[0].message


@pytest.mark.parametrize("folder, notes, backend, asr_key, expected", [
    (True, True, "local", False, True),
    (True, True, "api", True, True),
    (True, True, "api", False, False),
    (True, False, "local", True, False),
    (False, True, "local", True, False),
    (None, True, "local", True, False),
])
def test_is_configured_truth_table(tmp_path, folder, notes, backend, asr_key, expected):
    root = None if folder is None else str(tmp_path / ("courses" if folder else "missing"))
    (tmp_path / "courses").mkdir()
    keys = {"notes": {"set": notes}, "asr": {"set": asr_key}}
    assert config.is_configured(dict(config.DEFAULTS, courses_dir=root, asr_backend=backend), keys) is expected


def test_qwen_interpreter_prefers_config_then_environment(tmp_path, monkeypatch):
    configured = tmp_path / "configured-python"
    from_env = tmp_path / "env-python"
    configured.touch()
    from_env.touch()
    monkeypatch.setattr(asr, "__file__", str(tmp_path / "repo/lecture_cli/asr.py"))
    monkeypatch.setenv("LECTURE_QWEN_PYTHON", str(from_env))
    assert asr.capture_python("qwen3-asr-0.6b", str(configured)) == str(configured)
    assert asr.capture_python("qwen3-asr-0.6b") == str(from_env)
    with pytest.raises(ValueError, match="missing-python"):
        asr.capture_python("qwen3-asr-0.6b", str(tmp_path / "missing-python"))
    monkeypatch.delenv("LECTURE_QWEN_PYTHON")
    with pytest.raises(ValueError, match="install-qwen.sh"):
        asr.capture_python("qwen3-asr-0.6b")
    assert asr.capture_python("base.en", str(configured)) == sys.executable


def test_refinement_loads_configured_model(tmp_path, monkeypatch):
    (tmp_path / "refinement.pcm").write_bytes(b"\0" * 64)
    write_json(tmp_path / "archive.json", {"bytes": 64, "complete": True, "error": ""})
    write_json(tmp_path / "session.json", {"refine_model": "qwen3-asr-0.6b", "asr_device": "cpu"})
    loaded = []
    def from_pretrained(name, **kwargs):
        loaded.append(name)
        raise RuntimeError("stop after choosing weights")
    monkeypatch.setattr(asr, "select_qwen_device", lambda requested: ("cpu", ""))
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(
        Qwen3ASRModel=SimpleNamespace(from_pretrained=from_pretrained)))
    assert refinement.refine(tmp_path) == 1
    assert loaded == ["Qwen/Qwen3-ASR-0.6B"]


def test_setup_requires_a_folder_when_none_is_saved(isolated, tmp_path, monkeypatch):
    prompts = []
    answers = iter(["", str(tmp_path)])
    def ask(prompt, **kwargs):
        prompts.append(prompt)
        return next(answers)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.console, "input", ask)
    monkeypatch.setattr(cli, "choose_asr_model", lambda current: current)
    monkeypatch.setattr(cli, "devices", lambda: None)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "typed-notes-key")
    with pytest.raises(ValueError, match="必须输入课程目录"):
        cli.setup(config.load())
    assert prompts == ["课程目录："]
    answers = iter([str(tmp_path), ""])
    cli.setup(config.load())
    assert read_json(isolated / "config.json")["courses_dir"] == str(tmp_path.resolve())
    assert (isolated / "notes-api-key").read_text() == "typed-notes-key\n"


def run_cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k not in KEY_VARIABLES}
    env.update(HOME=str(tmp_path / "home"), XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "lecture_cli", *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


def test_fresh_environment_courses_gives_clear_error(tmp_path):
    result = run_cli(tmp_path, "courses")
    assert result.returncode == 1
    assert "尚未设置课程目录，请运行 lecture setup 或 lecture gui" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("args", [
    ["start", "MATH421"], ["demo", "MATH421"], ["diagnose-asr", "MATH421"], ["setup"], ["models"],
    ["doctor", "--asr-backend", "api"], ["_complete-courses"],
])
def test_fresh_environment_never_mentions_author_folder(tmp_path, args):
    result = run_cli(tmp_path, *args)
    output = result.stdout + result.stderr
    assert "Umass_CS_Class" not in output and "Traceback" not in output
    if args[0] in ("start", "demo", "diagnose-asr"):
        assert "尚未设置课程目录" in output
