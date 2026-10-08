"""N1.6: a course remembers its language; --language wins, then the course, then the global default."""
import pytest

from lecture_cli import cli
from lecture_cli import config as settings


@pytest.fixture
def started(tmp_path, monkeypatch, isolated_run_registry):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "synthetic-test-key")
    root = tmp_path / "courses"
    for course in ("MATH421", "LING200"):
        (root / course).mkdir(parents=True)
    calls = []
    # Only the configuration the lecture would start with is observed; nothing records.
    monkeypatch.setattr(cli, "session", lambda args, config, course: calls.append((course.name, config["language"])) or 0)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)

    def start(*argv, **config):
        settings.save({**settings.DEFAULTS, "courses_dir": str(root), **config})
        calls.clear()
        assert cli.main(["start", *argv, "--headless"]) == 0
        return calls[0]

    return start


def test_command_line_then_course_then_global_language(started):
    remembered = {"course_settings": {"MATH421": {"language": "zh"}}, "language": "en"}
    assert started("math421", **remembered) == ("MATH421", "zh")
    assert started("math421", "--language", "fr", **remembered) == ("MATH421", "fr")
    assert started("LING200", **remembered) == ("LING200", "en")
    assert started("MATH421", language="de") == ("MATH421", "de")


def test_unknown_or_malformed_course_settings_never_stop_a_lecture(started):
    assert started("MATH421", course_settings={"NOPE": {"language": "zh"}}) == ("MATH421", "en")
    assert started("MATH421", course_settings={"MATH421": {"language": ""}}) == ("MATH421", "en")
    assert started("MATH421", course_settings=["MATH421"]) == ("MATH421", "en")
    assert started("MATH421", course_settings={"MATH421": "zh"}, editor="not-a-table-name") == ("MATH421", "en")
    assert settings.course_language({"course_settings": {"MATH421": {"language": "zh"}}}, "LING200") is None


def test_remembering_a_language_keeps_other_courses(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    settings.save({**settings.DEFAULTS, "course_settings": {"LING200": {"language": "fr"}}})
    settings.remember_course_language("MATH421", "zh")
    assert settings.load()["course_settings"] == {"LING200": {"language": "fr"}, "MATH421": {"language": "zh"}}
    settings.save({**settings.DEFAULTS, "course_settings": "broken"})
    settings.remember_course_language("MATH421", "en")
    assert settings.load()["course_settings"] == {"MATH421": {"language": "en"}}
