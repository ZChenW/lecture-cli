"""N3.1: a Chinese course can switch its live transcription to Qwen, and the course remembers it."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lecture_cli import cli
from lecture_cli import config as settings
from lecture_cli.gui import qwen_live, server as gui_server
from lecture_cli.gui.sessions import command
from tests.test_gui_server import home, save_config, serve  # noqa: F401 (fixtures)


@pytest.fixture
def started(tmp_path, monkeypatch, isolated_run_registry, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LECTURE_ASR_API_KEY", "fake-asr-key")
    root = tmp_path / "courses"
    for course in ("MATH421", "CHIN101"):
        (root / course).mkdir(parents=True)
    calls = []
    monkeypatch.setattr(cli, "session", lambda args, config, course: calls.append(config["asr_model"]) or 0)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    qwen = SimpleNamespace(installed=True)

    def capture_python(model, configured=None):
        # The real check looks for .venv-qwen; tests must not depend on whether a stand-in exists.
        if model.startswith("qwen") and not qwen.installed:
            raise ValueError("Qwen 运行环境未安装，请运行 ./install-qwen.sh")
        return "python"
    monkeypatch.setattr(cli, "capture_python", capture_python)

    def start(*argv, **config):
        settings.save({**settings.DEFAULTS, "courses_dir": str(root), "language": "zh", **config})
        calls.clear()
        capsys.readouterr()
        assert cli.main(["start", *argv, "--headless"]) == 0
        line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("本次实时转录"))
        return calls[0], line

    start.qwen = qwen
    return start


REMEMBERED = {"course_settings": {"CHIN101": {"language": "zh", "asr_model": "qwen3-asr-1.7b"}}, "asr_model": "small"}


def test_flag_then_course_then_default_and_the_line_says_which(started):
    assert started("CHIN101", **REMEMBERED) == (
        "qwen3-asr-1.7b", "本次实时转录模型：qwen3-asr-1.7b（课程 CHIN101 记住的选择）")
    assert started("CHIN101", "--asr-model", "base", **REMEMBERED) == (
        "base", "本次实时转录模型：base（命令行 --asr-model 指定）")
    assert started("MATH421", **REMEMBERED) == ("small", "本次实时转录模型：small（默认设置）")


def test_a_remembered_whisper_choice_also_applies(started):
    remembered = {"course_settings": {"CHIN101": {"asr_model": "base"}}, "asr_model": "small"}
    assert started("CHIN101", **remembered)[0] == "base"


def test_unavailable_remembered_qwen_never_costs_a_lecture(started):
    started.qwen.installed = False
    model, line = started("CHIN101", **REMEMBERED)
    assert model == "small"
    assert line.startswith("本次实时转录模型：small（默认设置；课程 CHIN101 记住的 qwen3-asr-1.7b 不可用：")
    remembered = {"course_settings": {"CHIN101": {"asr_model": "no-such-model"}}, "asr_model": "small"}
    assert started("CHIN101", **remembered)[0] == "small"


def test_cloud_transcription_ignores_the_remembered_local_model(started):
    model, line = started("CHIN101", asr_backend="api", asr_api_model="whisper-large-v3-turbo", **REMEMBERED)
    assert model == "small"  # Untouched here; the session uses asr_api_model for a cloud run.
    assert line == "本次实时转录：云端 whisper-large-v3-turbo（默认设置）"


def test_course_settings_accept_the_model_and_keep_the_language(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert settings.course_settings_valid({"A": {"language": "zh", "asr_model": "qwen3-asr-1.7b"}})
    assert not settings.course_settings_valid({"A": {"asr_model": ""}})
    assert not settings.course_settings_valid({"A": {"asr_model": 3}})
    assert not settings.course_settings_valid({"A": {"device": "x"}})
    settings.save({**settings.DEFAULTS, "course_settings": {"CHIN101": {"language": "zh"}}})
    settings.remember_course_asr_model("CHIN101", "qwen3-asr-1.7b")
    settings.remember_course_language("CHIN101", "zh")
    assert settings.load()["course_settings"] == {"CHIN101": {"language": "zh", "asr_model": "qwen3-asr-1.7b"}}
    assert settings.course_asr_model(settings.load(), "CHIN101") == "qwen3-asr-1.7b"
    assert settings.course_asr_model(settings.load(), "MATH421") is None


def test_command_line_carries_the_model_for_a_lecture_only():
    start = command("CHIN101", {"language": "zh", "asr_model": "qwen3-asr-1.7b"}, None)
    assert start[-4:] == ["--language=zh", "--asr-model=qwen3-asr-1.7b", "--", "CHIN101"]
    assert not any(a.startswith("--asr-model") for a in command("CHIN101", {"demo": True, "asr_model": "base"}, None))


class FakeSessions:
    def __init__(self, directory):
        self.directory, self.started = directory, []
    def active(self):
        return None
    def start(self, course, overrides, context):
        self.started.append((course, overrides))
        return self.directory
    def snapshot(self, directory):
        return {"course": "CHIN101"}


def test_starting_remembers_the_choice_and_rejects_unknown_models(home, serve, tmp_path):
    (home / "CHIN101").mkdir()
    save_config(home)
    fake = FakeSessions(tmp_path)
    app = serve(sessions=fake)
    app.login()
    response = app.post("/api/runs", {"course": "CHIN101", "overrides": {"language": "zh", "asr_model": "qwen3-asr-1.7b"}})
    assert response.status_code == 201
    assert settings.load()["course_settings"]["CHIN101"] == {"language": "zh", "asr_model": "qwen3-asr-1.7b"}
    assert app.post("/api/runs", {"course": "CHIN101", "overrides": {"asr_model": "base"}}).status_code == 201
    assert settings.load()["course_settings"]["CHIN101"]["asr_model"] == "base"
    for bad in ("rm -rf /", "", 3, None):
        response = app.post("/api/runs", {"course": "CHIN101", "overrides": {"asr_model": bad}})
        assert response.status_code == 422 and response.json()["error"]["field"] == "asr_model"
    assert len(fake.started) == 2


@pytest.fixture
def probe(monkeypatch):
    qwen_live._gpu.clear()
    state = SimpleNamespace(env=True, cached=True, device="cuda", calls=[])

    def capture_python(model, configured=None):
        if not state.env:
            raise ValueError("missing")
        return "/qwen/bin/python"

    def runner(argv, **kwargs):
        state.calls.append(argv)
        assert kwargs["timeout"] == 30
        return SimpleNamespace(stdout=json.dumps({"device": state.device, "notice": ""}), returncode=0)
    monkeypatch.setattr(qwen_live, "capture_python", capture_python)
    monkeypatch.setattr(qwen_live.checks, "weights_cached", lambda name: state.cached)
    state.runner = runner
    yield state
    qwen_live._gpu.clear()


def test_ready_needs_environment_weights_and_gpu(probe):
    ready = qwen_live.readiness({}, probe.runner)
    assert ready == {"model": "qwen3-asr-1.7b", "env_ready": True, "cached": True, "gpu": True, "ready": True,
                     "install": "./install.sh --with-qwen"}
    assert probe.calls == [["/qwen/bin/python", "-m", "lecture_cli.asr", "auto", "qwen3-asr-1.7b"]]
    qwen_live.readiness({}, probe.runner)
    assert len(probe.calls) == 1  # The GPU probe imports torch; it runs once per interpreter.


def test_not_ready_without_gpu_weights_or_environment(probe):
    probe.device = "cpu"
    assert qwen_live.readiness({}, probe.runner)["ready"] is False
    qwen_live._gpu.clear()
    probe.cached = False
    assert qwen_live.readiness({}, probe.runner) | {"install": None} == {
        "model": "qwen3-asr-1.7b", "env_ready": True, "cached": False, "gpu": False, "ready": False, "install": None}
    probe.env = False
    assert qwen_live.readiness({"refine_model": "qwen3-asr-0.6b"}, probe.runner)["model"] == "qwen3-asr-0.6b"
    assert qwen_live.readiness({}, probe.runner)["env_ready"] is False


def test_a_failed_probe_is_not_remembered(probe):
    def broken(argv, **kwargs):
        raise OSError("no python")
    assert qwen_live.readiness({}, broken)["gpu"] is False
    assert qwen_live.readiness({}, probe.runner)["gpu"] is True
