"""lecture gui: single instance, loopback port and token, then a window or a printed address."""
from __future__ import annotations

import fcntl
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading

from .. import runs
from ..storage import read_json, write_json

BROWSERS = ("chromium", "google-chrome-stable", "brave", "microsoft-edge-stable")
MISSING = "缺少界面依赖，请运行：uv pip install --python .venv/bin/python -r requirements-gui.lock"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return Path(base) / "lecture-cli" if base else runs.runs_dir().parent


def detached(args: list[str]) -> None:
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_external(url: str) -> None:
    browser = next(filter(None, map(shutil.which, BROWSERS)), None)
    if browser:
        detached([browser, f"--app={url}"])
        return
    if shutil.which("xdg-open"):
        detached(["xdg-open", url])
    print(f"请在浏览器中打开：{url}", flush=True)


def run(no_window: bool = False) -> int:
    os.umask(0o077)
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (directory / "gui.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        url = read_json(directory / "gui.json").get("url")
        if not url:
            print("界面已在运行，但读不到它的地址；请稍后再试。", flush=True)
            return 1
        print(f"界面已在运行：{url}", flush=True) if no_window else open_external(url)
        return 0
    try:
        return serve(directory, no_window)
    except KeyboardInterrupt:
        # GUI5-fix: Ctrl+C in the terminal is how `lecture gui --no-window` (or the browser-window
        # launch) is closed: uvicorn has shut down and re-raised the signal; quit quietly. A lecture
        # runs in its own process and goes on, as when the window closes.
        return 0
    finally:
        (directory / "gui.json").unlink(missing_ok=True)
        lock.close()


def serve(directory: Path, no_window: bool) -> int:
    try:
        import uvicorn
        from .server import create_app
    except ImportError:
        print(MISSING, flush=True)
        return 1
    from ..cli import reap_stale_sessions
    reap_stale_sessions()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))  # The system picks a free port.
    sock.listen(128)  # Connections queue until uvicorn accepts, so a window may open first.
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/?t={token}"
    window = {}

    def quit_app():
        server.should_exit = True
        if window.get("handle"):
            window["handle"].destroy()

    server = uvicorn.Server(uvicorn.Config(create_app(port, token, on_quit=quit_app),
                                           log_level="warning", lifespan="off"))
    write_json(directory / "gui.json", {"url": url, "pid": os.getpid()})
    if no_window:
        print(f"界面后端地址：{url}", flush=True)
    elif importlib.util.find_spec("webview"):
        import webview
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        window["handle"] = webview.create_window("Lecture", url, min_size=(1100, 700))
        webview.start()
        # The window is gone; any session keeps running and is re-attached next time.
        server.should_exit = True
        thread.join(5)
        return 0
    else:
        open_external(url)
    server.run(sockets=[sock])
    return 0
