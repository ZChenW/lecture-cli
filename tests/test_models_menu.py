import curses

import pytest

from lecture_cli import cli
from lecture_cli.asr import asr_models
from lecture_cli.storage import read_json


def test_choose_asr_model_uses_picker(monkeypatch):
    names = asr_models()
    start = names.index("base.en")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli, "pick_asr_model",
        lambda names, index, current: names[index + 1],
    )
    assert cli.choose_asr_model("base.en") == names[start + 1]


def test_curses_model_menu_down_and_enter(monkeypatch):
    names = ("tiny.en", "base.en", "small.en")
    keys = iter([curses.KEY_DOWN, 10])  # down to base.en, enter

    class FakeScr:
        def erase(self): pass
        def addstr(self, *a, **k): pass
        def refresh(self): pass
        def noutrefresh(self): pass
        def keypad(self, _): pass
        def getch(self): return next(keys)

    monkeypatch.setattr(curses, "curs_set", lambda *_: None)
    assert cli._curses_model_menu(FakeScr(), names, 0, "tiny.en") == "base.en"


def test_curses_model_menu_q_cancels(monkeypatch):
    class FakeScr:
        def erase(self): pass
        def addstr(self, *a, **k): pass
        def refresh(self): pass
        def noutrefresh(self): pass
        def keypad(self, _): pass
        def getch(self): return ord("q")

    monkeypatch.setattr(curses, "curs_set", lambda *_: None)
    with pytest.raises(ValueError, match="已取消"):
        cli._curses_model_menu(FakeScr(), ("base.en", "small.en"), 0, "base.en")


def test_models_command_saves_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "choose_asr_model", lambda current: "medium.en")
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    assert cli.main(["models"]) == 0
    assert read_json(tmp_path / "lecture-cli" / "config.json")["asr_model"] == "medium.en"
    assert "medium.en" in capsys.readouterr().out


def test_models_non_tty_lists_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: None)
    monkeypatch.setattr(cli, "choose_asr_model", lambda *_: pytest.fail("no picker"))
    assert cli.main(["models"]) == 0
    out = capsys.readouterr().out
    assert "tiny.en" in out and "medium.en" in out
    assert not (tmp_path / "lecture-cli" / "config.json").exists()
