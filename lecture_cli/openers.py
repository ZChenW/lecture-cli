"""Show notes in a file manager, terminal or editor chosen from fixed program tables.

Nothing here runs a shell or accepts a command from the GUI or its API: programs come from the
tables below, or from a hand-edited "<kind>_command" argument array in config.json.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable
from urllib.parse import quote

# Order is the automatic choice's preference order. "{path}" is the note (or folder) itself,
# "{dir}" the folder that holds it, "{path_uri}" the percent-encoded absolute path.
TABLES: dict[str, dict[str, list[str]]] = {
    "file_manager": {
        "nautilus": ["nautilus", "{dir}"],
        "dolphin": ["dolphin", "{dir}"],
        "thunar": ["thunar", "{dir}"],
        "nemo": ["nemo", "{dir}"],
        "caja": ["caja", "{dir}"],
        "pcmanfm": ["pcmanfm", "{dir}"],
    },
    "terminal": {
        "kitty": ["kitty", "--directory", "{dir}"],
        "ghostty": ["ghostty", "--working-directory={dir}"],
        "alacritty": ["alacritty", "--working-directory", "{dir}"],
        "foot": ["foot", "--working-directory={dir}"],
        "wezterm": ["wezterm", "start", "--cwd", "{dir}"],
        "gnome-terminal": ["gnome-terminal", "--working-directory={dir}"],
        "konsole": ["konsole", "--workdir", "{dir}"],
        "xfce4-terminal": ["xfce4-terminal", "--working-directory={dir}"],
    },
    "editor": {
        "code": ["code", "{path}"],
        "codium": ["codium", "{path}"],
        "zed": ["zed", "{path}"],
        "obsidian": ["obsidian", "obsidian://open?path={path_uri}"],
        "gnome-text-editor": ["gnome-text-editor", "{path}"],
        "kate": ["kate", "{path}"],
        "gedit": ["gedit", "{path}"],
        "mousepad": ["mousepad", "{path}"],
    },
}
LABELS = {"file_manager": "文件管理器", "terminal": "终端", "editor": "编辑器"}
# Without a placeholder a custom command receives the target as its last argument.
DEFAULT_TARGET = {"file_manager": "{dir}", "terminal": "{dir}", "editor": "{path}"}
SHOW_ITEMS = ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
              "--object-path", "/org/freedesktop/FileManager1",
              "--method", "org.freedesktop.FileManager1.ShowItems"]
DBUS_TIMEOUT = 5


class Unavailable(Exception):
    """No usable program for this kind; the API answers 503."""


def custom_command(config: dict, kind: str) -> list[str] | None:
    value = config.get(f"{kind}_command")
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return list(value)
    return None


def problems(config: dict) -> list[tuple[str, str]]:
    """(field, message) pairs for config.validate()."""
    found = []
    for kind, table in TABLES.items():
        name = config.get(kind)
        if name is not None and name not in table:
            found.append((kind, f"{LABELS[kind]}必须是 " + "、".join(table) + " 之一，或留空自动选择"))
        if config.get(f"{kind}_command") is not None and custom_command(config, kind) is None:
            found.append((f"{kind}_command", f"{kind}_command 必须是非空字符串数组（每项一个参数）"))
    return found


def expand(template: list[str], kind: str, path: Path) -> list[str]:
    folder = path if path.is_dir() else path.parent
    values = {"{path}": str(path), "{dir}": str(folder), "{path_uri}": quote(str(path), safe="/")}
    if not any(key in item for item in template for key in values):
        template = template + [DEFAULT_TARGET[kind]]
    argv = []
    for item in template:
        for key, value in values.items():
            item = item.replace(key, value)
        argv.append(item)
    return argv


def spawn(argv: list[str], cwd: Path) -> None:
    # Detached: the program outlives this request and never inherits our output streams.
    subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def call(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=DBUS_TIMEOUT).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@dataclass
class Openers:
    """Injected in tests so that nothing is ever launched."""
    which: Callable[[str], str | None] = shutil.which
    spawn: Callable[[list[str], Path], None] = spawn
    call: Callable[[list[str]], bool] = call

    def available(self, kind: str) -> list[str]:
        return [name for name in TABLES[kind] if self.which(name)]

    def effective(self, config: dict, kind: str) -> str | None:
        """The table program that would run (custom commands report None)."""
        name = config.get(kind)
        if name in TABLES[kind]:
            return name if self.which(name) else None
        return next(iter(self.available(kind)), None)

    def template(self, config: dict, kind: str) -> list[str]:
        if custom := custom_command(config, kind):
            return custom
        name = config.get(kind)
        effective = self.effective(config, kind)
        if effective is None:
            if name in TABLES[kind]:
                raise Unavailable(f"未找到所选{LABELS[kind]}：{name}")
            raise Unavailable(f"未找到可用的{LABELS[kind]}；可在设置中选择，或在 config.json 中填写 {kind}_command")
        return TABLES[kind][effective]

    def describe(self, config: dict) -> dict:
        return {kind: {"label": LABELS[kind], "programs": list(TABLES[kind]), "available": self.available(kind),
                       "selected": config.get(kind) if config.get(kind) in TABLES[kind] else None,
                       "effective": None if custom_command(config, kind) else self.effective(config, kind),
                       "custom": custom_command(config, kind) is not None}
                for kind in TABLES}

    def launch(self, config: dict, kind: str, path: Path) -> list[str]:
        argv = expand(self.template(config, kind), kind, path)
        self.spawn(argv, path if path.is_dir() else path.parent)
        return argv

    def open(self, config: dict, mode: str, path: Path) -> dict:
        """mode is reveal, terminal or editor; path is already checked to be inside the courses folder."""
        if mode == "reveal":
            # Desktop file managers select the item over D-Bus; fall back to opening its folder.
            if self.which("gdbus") and self.call(SHOW_ITEMS + [f"['{path.as_uri()}']", "''"]):
                return {"ok": True, "via": "dbus"}
            folder = path if path.is_dir() else path.parent
            self.launch(config, "file_manager", folder)
            return {"ok": True, "via": "file_manager"}
        if mode == "terminal":
            self.launch(config, "terminal", path if path.is_dir() else path.parent)
            return {"ok": True, "via": "terminal"}
        self.launch(config, "editor", path)
        return {"ok": True, "via": "editor"}
