"""N2 backend pieces: key status row and persist, missing items, onboarding flag, review counts and
the settings page's "试一下". Nothing is launched: openers are injected fakes."""
import os

from lecture_cli import config as settings
from lecture_cli.gui.library import review_count
from lecture_cli.storage import ATTACHMENT_DIR
from tests.test_gui_server import fake_openers, home, save_config, serve, SECRET  # noqa: F401 (fixtures)


# --- N2.3 key status and "保存到本机" ------------------------------------------------------------

def test_key_status_names_the_variable_and_whether_a_file_holds_it(home, monkeypatch):
    assert settings.key_status("notes") == {"set": False, "source": None, "tail": None, "variable": None, "stored": False}
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    assert settings.key_status("notes") == {"set": True, "source": "env", "tail": SECRET[-4:],
                                            "variable": "DEEPSEEK_API_KEY", "stored": False}
    settings.write_key("notes", "another-key-0000")  # A different stored key does not count as saved.
    assert settings.key_status("notes")["stored"] is False
    settings.write_key("notes", SECRET)
    assert settings.key_status("notes")["stored"] is True
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert settings.key_status("notes") == {"set": True, "source": "file", "tail": SECRET[-4:],
                                            "variable": None, "stored": True}


def test_persist_writes_the_environment_key_without_returning_it(home, serve, monkeypatch):
    app = serve()
    app.login()
    save_config(home)
    assert app.post("/api/keys/notes/persist").status_code == 409  # Nothing in the environment.
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", SECRET)
    response = app.post("/api/keys/notes/persist")
    assert response.status_code == 200 and SECRET not in response.text
    assert response.json() == {"set": True, "source": "env", "tail": SECRET[-4:],
                               "variable": "LECTURE_NOTES_API_KEY", "stored": True}
    stored = settings.config_dir() / "notes-api-key"
    assert stored.read_text().strip() == SECRET and oct(stored.stat().st_mode & 0o777) == "0o600"
    # Started from the application menu (no variable), the stored key is found.
    monkeypatch.delenv("LECTURE_NOTES_API_KEY")
    assert app.client.get("/api/bootstrap").json()["keys"]["notes"]["source"] == "file"
    assert app.post("/api/keys/notes/persist").status_code == 409
    assert app.post("/api/keys/other/persist").status_code == 404
    assert SECRET not in app.client.get("/api/bootstrap").text


# --- N2.4 missing items and the onboarding flag --------------------------------------------------

def test_bootstrap_lists_missing_items_in_order(home, serve, monkeypatch):
    app = serve()
    app.login()
    boot = app.client.get("/api/bootstrap").json()
    assert boot["missing"] == ["courses_dir", "notes_key"] and boot["configured"] is False
    save_config(home, asr_backend="api")
    assert app.client.get("/api/bootstrap").json()["missing"] == ["notes_key", "asr_key"]
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", SECRET)
    monkeypatch.setenv("LECTURE_ASR_API_KEY", SECRET)
    boot = app.client.get("/api/bootstrap").json()
    assert boot["missing"] == [] and boot["configured"] is True
    saved = app.client.put("/api/config", json={"asr_backend": "local"}, headers=app.origin).json()
    assert saved["missing"] == [] and saved["configured"] is True


def test_onboarded_flag_is_saved_and_validated(home, serve):
    app = serve()
    app.login()
    assert app.client.get("/api/bootstrap").json()["config"]["onboarded"] is False
    # Skipping everything works before any other setting exists.
    response = app.client.put("/api/config", json={"onboarded": True}, headers=app.origin)
    assert response.status_code == 200 and settings.load()["onboarded"] is True
    assert app.client.put("/api/config", json={"onboarded": "yes"}, headers=app.origin).status_code == 422
    assert "onboarded" in settings.GUI_ONLY


# --- N2.6 review counts --------------------------------------------------------------------------

REVIEW = """# 待核对与处理记录

## 处理提示

离线校正未完成，详细笔记使用实时转录；可能含听辨错误。

## 离线校正

阶段：加载模型

原因：Qwen 环境未就绪

- 讲义 R2 示意图方向有误 [live-L16](x.transcript.md#live-L16)。

- 右手法则的具体方向未完整出现 [live-L19](x.transcript.md#live-L19)。
  - 子项不单独计数

- 未确认的转录 [live-L21](x.transcript.md#live-L21)

## 待整理原文 · [live-L1–L2](x.transcript.md#live-L1)

> [live-L1](x.transcript.md#live-L1) An eigenvector is nonzero.
> [live-L2](x.transcript.md#live-L2) These are the final words.

模型写成段落的疑点也算一处 [L30]。
"""


def test_review_count_counts_points_to_check_not_processing_notes():
    assert review_count(REVIEW) == 5
    assert review_count("# 待核对与处理记录\n\n## 处理提示\n\n已跳过离线校正。\n") == 0
    assert review_count("") == 0
    assert review_count("# 待核对与处理记录\n\n1. 第一处\n2. 第二处\n") == 2


def test_notes_listing_returns_review_count_from_either_layout(home, serve):
    app = serve()
    app.login()
    save_config(home)
    folder = home / "MATH421" / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True)
    new, old, none = (f"2026-10-0{d}_090000-课堂笔记-a1b2c{d}" for d in (7, 6, 5))
    for stem in (new, old, none):
        (folder / f"{stem}.md").write_text("# x\n")
    (folder / ATTACHMENT_DIR / f"{new}.review.md").write_text(REVIEW)
    (folder / f"{old}.review.md").write_text("# 待核对与处理记录\n\n- 一处\n")
    listing = {n["name"][:-3]: n for n in app.client.get("/api/notes", params={"course": "MATH421"}).json()}
    assert [listing[s]["review_count"] for s in (new, old, none)] == [5, 1, 0]
    assert listing[none]["attachments"]["review"] is False


# --- N2.2 "试一下" -------------------------------------------------------------------------------

def test_try_launches_the_chosen_table_program_on_a_server_chosen_target(home, serve, tmp_path):
    openers, calls = fake_openers(installed=("nautilus", "thunar", "kitty", "ghostty", "code", "gdbus"))
    app = serve(openers=openers)
    app.login()
    save_config(home, terminal="kitty")
    # Unsaved choice: ghostty, though config still says kitty.
    response = app.post("/api/openers/terminal/try", {"program": "ghostty"})
    assert response.json() == {"ok": True, "program": "ghostty", "argv0": "ghostty"}
    assert calls["spawn"][-1] == (["ghostty", f"--working-directory={home}"], home)
    assert app.post("/api/openers/file_manager/try", {"program": None}).json()["program"] == "nautilus"
    assert calls["spawn"][-1] == (["nautilus", str(home)], home)
    assert calls["dbus"] == []  # Trying the file manager never goes through D-Bus.
    app.post("/api/openers/editor/try", {"program": "code"})
    sample = tmp_path / "state" / "lecture-cli" / "试一下.md"
    assert calls["spawn"][-1] == (["code", str(sample)], sample.parent) and "试一下" in sample.read_text()
    assert settings.load()["terminal"] == "kitty"  # Trying never saves.


def test_try_refuses_anything_but_a_table_name(home, serve):
    openers, calls = fake_openers(installed=("kitty",))
    app = serve(openers=openers)
    app.login()
    save_config(home)
    for kind, body in (("terminal", {"program": "xterm"}), ("terminal", {"program": "sh -c id"}),
                       ("terminal", {"program": "kitty", "path": "/etc"}), ("terminal", {"command": ["sh"]}),
                       ("editor", {"program": 1})):
        assert app.post(f"/api/openers/{kind}/try", body).status_code == 422, body
    assert app.post("/api/openers/shell/try", {"program": None}).status_code == 404
    response = app.post("/api/openers/editor/try", {"program": "zed"})  # In the table, not installed.
    assert response.status_code == 503 and "zed" in response.json()["error"]["message"]
    assert calls["spawn"] == []


def test_try_without_courses_folder_uses_home_and_custom_command_still_wins(home, serve, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    openers, calls = fake_openers(installed=("kitty",))
    app = serve(openers=openers)
    app.login()
    settings.save({**settings.DEFAULTS, "terminal_command": ["my-term", "-d", "{dir}"]})
    response = app.post("/api/openers/terminal/try", {"program": "kitty"})
    assert response.json()["program"] is None and response.json()["argv0"] == "my-term"
    assert calls["spawn"][-1] == (["my-term", "-d", str(tmp_path / "home")], tmp_path / "home")
    assert os.environ["HOME"] == str(tmp_path / "home")
