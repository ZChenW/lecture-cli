"""N1.1/N1.3/N1.4/N1.6 through the GUI API: discard, open, openers, trash and remembered language.

Nothing is launched or deleted for real: openers, D-Bus and gio are injected fakes.
"""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lecture_cli import config as settings
from lecture_cli import openers as desktop
from lecture_cli.gui import library
from lecture_cli.storage import ATTACHMENT_DIR, attachment_path
from tests.test_gui_server import controller_env, home, serve  # noqa: F401 (fixtures)
from tests.test_gui_server import fake_openers, save_config, wait_until


def note_with_attachments(course, stem="2026-10-07_143000-课堂笔记-a1b2c3", layout="new"):
    folder = course / "LectureNotes"
    (folder / ATTACHMENT_DIR).mkdir(parents=True, exist_ok=True)
    note = folder / f"{stem}.md"
    note.write_text(f"# {stem}\n")
    for kind in ("transcript", "live", "review"):
        target = attachment_path(note, kind) if layout == "new" else note.with_suffix(f".{kind}.md")
        target.write_text(f"{kind} of {stem}\n")
    return note


# --- N1.3 openers -------------------------------------------------------------------------------

def test_argv_for_each_mode_never_uses_a_shell(tmp_path):
    note = tmp_path / "LectureNotes" / "课 堂's note.md"
    note.parent.mkdir()
    note.write_text("x")
    openers, calls = fake_openers()
    assert openers.open({}, "reveal", note) == {"ok": True, "via": "dbus"}
    assert calls["dbus"] == [desktop.SHOW_ITEMS + [f"['{note.as_uri()}']", "''"]] and calls["spawn"] == []
    assert "'" not in note.as_uri()  # The URI is percent-encoded: no quote can end the GVariant string.
    openers.open({}, "terminal", note)
    openers.open({}, "editor", note)
    assert calls["spawn"] == [(["kitty", "--directory", str(note.parent)], note.parent),
                              (["code", str(note)], note.parent)]


def test_reveal_falls_back_to_the_file_manager_when_dbus_fails_or_is_missing(tmp_path):
    note = tmp_path / "n.md"
    note.write_text("x")
    for installed in (("nautilus", "gdbus"), ("nautilus",)):
        openers, calls = fake_openers(installed=installed, dbus=False)
        assert openers.open({}, "reveal", note) == {"ok": True, "via": "file_manager"}
        assert calls["spawn"] == [(["nautilus", str(tmp_path)], tmp_path)]
        assert len(calls["dbus"]) == ("gdbus" in installed)


def test_selection_order_custom_command_then_name_then_first_installed(tmp_path):
    note = tmp_path / "n.md"
    note.write_text("x")
    openers, calls = fake_openers(installed=("ghostty", "foot", "obsidian", "zed"))
    openers.open({}, "terminal", note)  # Auto: table order, so ghostty before foot.
    openers.open({"terminal": "foot"}, "terminal", note)
    openers.open({"terminal": "foot", "terminal_command": ["my-term", "-d", "{dir}"]}, "terminal", note)
    openers.open({"editor": "obsidian"}, "editor", note)
    openers.open({"editor_command": ["my-editor", "--new-window"]}, "editor", note)  # Target appended.
    assert [argv for argv, _ in calls["spawn"]] == [
        ["ghostty", f"--working-directory={tmp_path}"], ["foot", f"--working-directory={tmp_path}"],
        ["my-term", "-d", str(tmp_path)], ["obsidian", "obsidian://open?path=" + str(note)],
        ["my-editor", "--new-window", str(note)]]
    with pytest.raises(desktop.Unavailable, match="kitty"):
        openers.open({"terminal": "kitty"}, "terminal", note)  # Chosen but not installed: no silent substitute.
    with pytest.raises(desktop.Unavailable):
        fake_openers(installed=())[0].open({}, "editor", note)


def test_open_api_modes_paths_and_errors(home, serve, tmp_path):
    openers, calls = fake_openers()
    app = serve(openers=openers)
    app.login()
    save_config(home)
    note = note_with_attachments(home / "MATH421")
    for mode in ("reveal", "terminal", "editor"):
        assert app.post("/api/open", {"path": str(note), "mode": mode}).status_code == 200
    assert [argv[0] for argv in calls["dbus"]] == ["gdbus"]
    assert [argv for argv, _ in calls["spawn"]] == [["kitty", "--directory", str(note.parent)], ["code", str(note)]]
    # The relative form and a folder work for reveal and terminal; editor wants a file.
    assert app.post("/api/open", {"path": "MATH421/LectureNotes", "mode": "terminal"}).status_code == 200
    assert app.post("/api/open", {"path": "MATH421/LectureNotes", "mode": "editor"}).status_code == 404
    # Old modes, free commands and unknown fields are refused.
    for body in ({"path": str(note), "mode": "file"}, {"path": str(note), "mode": "folder"},
                 {"path": str(note), "mode": "editor", "command": ["sh", "-c", "id"]},
                 {"path": str(note), "mode": "editor", "program": "vim"}, {"mode": "editor"}):
        assert app.post("/api/open", body).status_code == 422
    assert app.post("/api/open", {"path": "../../etc/passwd", "mode": "editor"}).status_code == 403
    assert app.post("/api/open", {"path": str(tmp_path), "mode": "reveal"}).status_code == 403
    count = len(calls["spawn"])
    nothing, _ = fake_openers(installed=())
    bare = serve(openers=nothing)
    bare.login()
    response = bare.post("/api/open", {"path": str(note), "mode": "terminal"})
    assert response.status_code == 503 and "终端" in response.json()["error"]["message"]
    assert len(calls["spawn"]) == count


def test_openers_endpoint_and_config_accept_only_table_names(home, serve):
    openers, _ = fake_openers(installed=("dolphin", "thunar", "alacritty", "kate"))
    app = serve(openers=openers)
    app.login()
    save_config(home)
    listing = app.client.get("/api/openers").json()
    assert listing["file_manager"]["available"] == ["dolphin", "thunar"]
    assert listing["file_manager"]["effective"] == "dolphin" and listing["file_manager"]["selected"] is None
    assert listing["terminal"]["programs"] == list(desktop.TABLES["terminal"])
    assert app.client.put("/api/config", json={"file_manager": "thunar"},
                          headers=app.origin).status_code == 200
    assert app.client.get("/api/openers").json()["file_manager"]["effective"] == "thunar"
    for change in ({"terminal": "xterm"}, {"editor": "vim -c '!id'"}, {"editor": 1},
                   {"editor_command": ["sh", "-c", "id"]}, {"terminal_command": ["x"]}):
        response = app.client.put("/api/config", json=change, headers=app.origin)
        assert response.status_code == 422, change
    assert app.client.put("/api/config", json={"editor": None}, headers=app.origin).status_code == 200
    # A hand-edited command array is used and reported, but never shown as a table program.
    settings.save({**settings.load(), "editor_command": ["my-editor", "{path}"]})
    assert app.client.get("/api/openers").json()["editor"] == {
        "label": "编辑器", "programs": list(desktop.TABLES["editor"]), "available": ["kate"],
        "selected": None, "effective": None, "custom": True}
    assert settings.validate({**settings.DEFAULTS, "editor_command": "my-editor {path}"})[-1].field == "editor_command"


# --- N1.4 trash ----------------------------------------------------------------------------------

class FakeGio:
    def __init__(self, installed=True, returncode=0):
        self.installed, self.returncode, self.calls = installed, returncode, []

    def which(self, name):
        return f"/usr/bin/{name}" if self.installed and name == "gio" else None

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if self.returncode == 0:
            # Like the real trash, the files leave their place (here they are only renamed).
            for name in argv[3:]:
                Path(name).rename(Path(name).with_name(Path(name).name + ".trashed"))
        return SimpleNamespace(returncode=self.returncode, stderr="" if self.returncode == 0 else "Operation not supported")


def trash_with(gio):
    return lambda root, candidate: library.trash_note(root, candidate, which=gio.which, run=gio.run)


def test_delete_moves_note_and_both_layouts_of_attachments_to_trash(home, serve):
    gio = FakeGio()
    app = serve(trash=trash_with(gio))
    app.login()
    save_config(home)
    course = home / "MATH421"
    note = note_with_attachments(course)
    note.with_suffix(".live.md").write_text("old flat live")  # Same stem, older layout.
    other = note_with_attachments(course, "2026-10-08_090000-课堂笔记-ffffff")
    old = note_with_attachments(course, "2026-09-01_090000-课堂笔记-eeeeee", layout="old")
    response = app.client.delete("/api/notes", params={"path": str(note)}, headers=app.origin)
    assert response.status_code == 200, response.text
    expected = [attachment_path(note, "transcript"), attachment_path(note, "live"), note.with_suffix(".live.md"),
                attachment_path(note, "review"), note]
    assert gio.calls == [["gio", "trash", "--", *map(str, expected)]]
    assert response.json() == {"ok": True, "trashed": [str(path) for path in expected]}
    assert not any(path.exists() for path in expected)
    for kept in (other, old):
        assert kept.read_text().startswith("# ")
    assert attachment_path(other, "transcript").exists() and old.with_suffix(".transcript.md").exists()
    # The old-layout note goes with its flat attachments.
    assert app.client.delete("/api/notes", params={"path": "MATH421/LectureNotes/" + old.name},
                             headers=app.origin).status_code == 200
    assert gio.calls[-1][3:] == [str(old.with_suffix(f".{kind}.md")) for kind in ("transcript", "live", "review")] + [str(old)]


def test_delete_refuses_escapes_links_attachments_and_never_deletes_without_gio(home, serve, tmp_path):
    gio = FakeGio()
    app = serve(trash=trash_with(gio))
    app.login()
    save_config(home)
    note = note_with_attachments(home / "MATH421")
    outside = tmp_path / "outside" / "LectureNotes"
    outside.mkdir(parents=True)
    (outside / "x.md").write_text("SECRET")
    link = note.parent / "2026-10-07_150000-课堂笔记-b0b0b0.md"
    link.symlink_to(note)
    delete = lambda path: app.client.delete("/api/notes", params={"path": path}, headers=app.origin)
    assert delete(str(outside / "x.md")).status_code == 403
    assert delete("MATH421/../../outside/LectureNotes/x.md").status_code == 403
    assert delete(str(link)).status_code == 403
    assert delete(str(attachment_path(note, "transcript"))).status_code == 404
    assert delete(str(note.parent / "missing.md")).status_code == 404
    assert app.client.delete("/api/notes", params={"path": str(note)}).status_code == 403  # No Origin.
    assert gio.calls == []
    missing = FakeGio(installed=False)
    bare = serve(trash=trash_with(missing))
    bare.login()
    response = bare.client.delete("/api/notes", params={"path": str(note)}, headers=bare.origin)
    assert response.status_code == 503 and "gio" in response.json()["error"]["message"]
    failing = FakeGio(returncode=1)
    broken = serve(trash=trash_with(failing))
    broken.login()
    response = broken.client.delete("/api/notes", params={"path": str(note)}, headers=broken.origin)
    assert response.status_code == 500 and "Operation not supported" in response.json()["error"]["message"]
    assert note.exists() and attachment_path(note, "review").exists() and missing.calls == []


# --- N1.1 / N1.4 / N1.6 with an active session ------------------------------------------------------

class FakeSessions:
    """The parts of Sessions the routes use, around a hand-made workspace."""

    def __init__(self, directory=None, run_id=None):
        self.directory, self.run_id, self.started = directory, run_id, []

    def active(self):
        return {"run_id": self.run_id, "directory": self.directory, "pid": 1} if self.directory else None

    def snapshot(self, active):
        return {"run_id": active["run_id"]}

    def start(self, course, overrides, context):
        self.started.append((course, overrides))
        return {"run_id": "r", "directory": self.directory, "pid": 1}


def test_discard_route_only_while_recording_and_active_note_cannot_be_deleted(home, serve, tmp_path):
    save_config(home)
    note = note_with_attachments(home / "MATH421")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gio = FakeGio()
    app = serve(sessions=FakeSessions(workspace, note.stem), trash=trash_with(gio))
    app.login()
    for phase in ("starting", "draining", "refining", "finalizing", "saving"):
        (workspace / "controller-state.json").write_text(json.dumps({"phase": phase}))
        response = app.post("/api/runs/active/discard")
        assert response.status_code == 409 and response.json()["error"]["code"] == "not_recording"
        assert not (workspace / "discard").exists()
    (workspace / "controller-state.json").write_text(json.dumps({"phase": "recording"}))
    assert app.post("/api/runs/active/discard").status_code == 200 and (workspace / "discard").exists()
    response = app.client.delete("/api/notes", params={"path": str(note)}, headers=app.origin)
    assert response.status_code == 409 and gio.calls == []
    idle = serve(sessions=FakeSessions())
    idle.login()
    assert idle.post("/api/runs/active/discard").status_code == 404


def test_gui_start_remembers_the_language_per_course(home, serve, tmp_path):
    save_config(home)
    (home / "LING200").mkdir()
    sessions = FakeSessions(tmp_path)
    app = serve(sessions=sessions)
    app.login()
    assert app.post("/api/runs", {"course": "MATH421", "overrides": {"language": "zh"}}).status_code == 201
    assert app.post("/api/runs", {"course": "LING200", "overrides": {}}).status_code == 201
    assert app.post("/api/runs", {"course": "LING200", "overrides": {"demo": True, "language": "fr"}}).status_code == 201
    assert settings.load()["course_settings"] == {"MATH421": {"language": "zh"}}
    assert app.post("/api/runs", {"course": "MATH421", "overrides": {"language": "en"}}).status_code == 201
    assert settings.load()["course_settings"] == {"MATH421": {"language": "en"}}
    assert [course for course, _ in sessions.started] == ["MATH421", "LING200", "LING200", "MATH421"]
    assert app.client.put("/api/config", json={"course_settings": {"MATH421": {"language": ""}}},
                          headers=app.origin).status_code == 422


def test_discard_through_the_api_with_a_real_controller(controller_env, serve, isolated_run_registry):
    app = serve()
    app.login()
    course = controller_env / "MATH421"
    kept = note_with_attachments(course, "2026-10-01_090000-课堂笔记-0a0a0a")
    started = app.post("/api/runs", {"course": "MATH421", "overrides": {"demo": True, "interval": 1}})
    assert started.status_code == 201, started.text
    output = Path(started.json()["output"])
    wait_until(lambda: app.client.get("/api/runs/active").json()["transcript"]["count"] == 1)
    wait_until(lambda: output.exists())
    collected = []
    reader = threading.Thread(target=app.events, args=(collected,))
    reader.start()
    wait_until(lambda: collected)
    assert app.post("/api/runs/active/discard").status_code == 200
    reader.join(30)
    assert not reader.is_alive()
    finished = collected[-1][1]
    assert collected[-1][0] == "finished" and finished["status"] == "discarded" and finished["exit_code"] == 0
    assert not output.exists() and not any(output.stem in p.name for p in course.rglob("*"))
    assert kept.read_text().startswith("# ") and attachment_path(kept, "review").exists()
    assert not Path(finished["directory"]).exists()
    assert app.client.get("/api/runs/active").json() is None
