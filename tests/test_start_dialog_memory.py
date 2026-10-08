"""Plan GUI-3 item 3: what the start dialog's request makes the course remember."""
from lecture_cli import config as settings
from lecture_cli.gui.sessions import command
from tests.test_gui_server import home, save_config, serve  # noqa: F401 (fixtures)
from tests.test_qwen_live import FakeSessions


def test_a_model_sent_without_a_free_choice_leaves_the_remembered_one(home, serve, tmp_path):
    (home / "CHIN101").mkdir()
    save_config(home, course_settings={"CHIN101": {"language": "zh", "asr_model": "qwen3-asr-1.7b"}})
    fake = FakeSessions(tmp_path)
    app = serve(sessions=fake)
    app.login()
    # Qwen not ready: this lecture uses Whisper, the course keeps Qwen.
    response = app.post("/api/runs", {"course": "CHIN101", "overrides": {
        "language": "zh", "asr_model": "small", "remember_asr_model": False}})
    assert response.status_code == 201, response.text
    assert settings.load()["course_settings"]["CHIN101"]["asr_model"] == "qwen3-asr-1.7b"
    assert fake.started[-1][1]["asr_model"] == "small"
    # The flag never reaches the command line; the model does.
    argv = command("CHIN101", fake.started[-1][1], None)
    assert "--asr-model=small" in argv and not any("remember" in a for a in argv)
    # A free choice is remembered as before.
    app.post("/api/runs", {"course": "CHIN101", "overrides": {"asr_model": "small", "remember_asr_model": True}})
    assert settings.load()["course_settings"]["CHIN101"]["asr_model"] == "small"
    response = app.post("/api/runs", {"course": "CHIN101", "overrides": {"remember_asr_model": "no"}})
    assert response.status_code == 422 and response.json()["error"]["field"] == "remember_asr_model"


def test_the_refine_choice_is_remembered_for_the_dialog(home, serve, tmp_path):
    (home / "CHIN101").mkdir()
    save_config(home)
    app = serve(sessions=FakeSessions(tmp_path))
    app.login()
    app.post("/api/runs", {"course": "CHIN101", "overrides": {"language": "zh", "refine": False}})
    assert settings.load()["course_settings"]["CHIN101"] == {"language": "zh", "refine": "off"}
    app.post("/api/runs", {"course": "CHIN101", "overrides": {"refine": True}})
    assert settings.load()["course_settings"]["CHIN101"]["refine"] == "on"
    app.post("/api/runs", {"course": "CHIN101", "overrides": {"refine": False, "demo": True}})
    assert settings.load()["course_settings"]["CHIN101"]["refine"] == "on"  # A demo remembers nothing.
    assert settings.course_settings_valid({"A": {"refine": "off"}})
    assert not settings.course_settings_valid({"A": {"refine": "yes"}})
    assert not settings.course_settings_valid({"A": {"refine": True}})
