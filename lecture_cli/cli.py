from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .storage import Journal, events, final_events, refined_events, read_json, write_json
from .audio_buffer import drain_timeout
from .asr import QWEN_MODELS, asr_models, resolve_asr_model, capture_python, capture_environment
from .glossary import load_glossary

console = Console()


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lecture-cli"


def configuration(load_key=True) -> dict:
    result = {"courses_dir": str(Path.home() / "Downloads" / "Umass_CS_Class"),
              "model": "deepseek-flash", "asr_model": "base.en", "language": "en",
              "interval": 60, "device": None, "asr_device": "auto", "refine": False,
              "asr_backend": "local", "asr_api_base": "https://api.groq.com/openai/v1",
              "asr_api_model": "whisper-large-v3-turbo"}
    result.update(read_json(config_dir() / "config.json"))
    if load_key:
        for variable, filename in (("DEEPSEEK_API_KEY", "api-key"),
                                   ("LECTURE_ASR_API_KEY", "asr-api-key")):
            if not os.environ.get(variable):
                key_file = config_dir() / filename
                if key_file.exists():
                    os.environ[variable] = key_file.read_text().strip()
    return result


def reap_stale_sessions() -> None:
    """Recover completed text and remove our unlocked workspaces after a crash."""
    for directory in Path("/tmp").glob(f"lecture-{os.getuid()}-*"):
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid():
            continue
        meta = read_json(directory / "session.json")
        if meta.get("app") != "lecture-cli-v1":
            continue
        try:
            with (directory / "owner.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Children die with their controller; give them a chance to close SQLite.
                def still_running(pid):
                    proc = Path(f"/proc/{pid}")
                    try:
                        return (proc / "stat").read_text().split()[2] != "Z" and str(directory).encode() in (proc / "cmdline").read_bytes().split(b"\0")
                    except FileNotFoundError:
                        return False
                for _ in range(20):
                    if not any(still_running(pid) for pid in meta.get("children", [])):
                        break
                    time.sleep(0.05)
                if any(still_running(pid) for pid in meta.get("children", [])):
                    continue
                preserve_tail(directory)
                journal = Journal(directory)
                try:
                    journal.set_info("warning", "上次录制异常中断；已恢复可取得的文字，未识别的音频未保存。")
                    if meta.get("refine") and refined_events(directory) is None:
                        from .refinement import WARNING
                        journal.add_warning(WARNING)
                    journal.fallback()
                    journal.preserve_detail_tail()
                    journal.render(finished=True)
                finally:
                    journal.close()
                shutil.rmtree(directory)
                console.print(f"已恢复上次中断的笔记并清理临时文件：{meta['output']}", markup=False)
        except BlockingIOError:
            continue  # Another lecture controller is still using this workspace.
        except OSError:
            console.print(f"暂时无法恢复或清理上次记录：{directory}", style="yellow", markup=False)


def course_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"课程目录不存在：{root}")
    return sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                   and not p.is_symlink()), key=lambda p: p.name.casefold())


def select_course(root: Path, requested: str | None) -> Path:
    courses = course_paths(root)
    if not courses:
        raise ValueError("课程目录下没有课程文件夹")
    if requested:
        def alias(name):
            return name.casefold().replace("_hw", "").replace(" ", "").replace("-", "")
        exact = [p for p in courses if p.name.casefold() == requested.casefold()]
        matches = exact or [p for p in courses if alias(p.name) == alias(requested)]
        if len(matches) != 1:
            raise ValueError("无法唯一匹配课程；请先运行 lecture courses 查看名称")
        return matches[0]
    if not sys.stdin.isatty():
        raise ValueError("非交互模式需要指定课程名")
    for i, course in enumerate(courses, 1):
        console.print(f"  {i}. {course.name}", markup=False)
    answer = console.input("\n选择课程编号：").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(courses):
        raise ValueError("无效的课程编号")
    return courses[int(answer) - 1]


@contextlib.contextmanager
def keyboard():
    settings = None
    if sys.stdin.isatty():
        settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
    try:
        yield lambda: sys.stdin.read(1).lower() if settings and select.select([sys.stdin], [], [], 0)[0] else ""
    finally:
        if settings:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, settings)


def display(directory: Path, stage: str = "", worker_dead=False):
    meta = read_json(directory / "session.json")
    asr = read_json(directory / "asr-state.json")
    notes = read_json(directory / "notes-state.json")
    seconds = int(asr.get("captured", asr.get("seconds", 0)))
    title = f"{meta['course']} · {seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("状态", Text(stage or asr.get("status", "启动中")))
    if asr.get("asr_device"):
        precision = "BF16" if meta.get("asr_model") in QWEN_MODELS else "FP16"
        table.add_row("识别设备", "云端 API" if asr["asr_device"] == "api" else
                      f"NVIDIA GPU · {precision}" if asr["asr_device"] == "cuda" else "CPU")
    table.add_row("语音模型", Text(meta.get("asr_model", "")))
    if meta.get("refine"):
        table.add_row("课后校正", Text(read_json(directory / "refinement-state.json").get(
            "status", asr.get("refinement_warning") or "Qwen 1.7B · 下课后自动重转录")))
    if asr.get("device_notice"):
        table.add_row("设备提示", Text(asr["device_notice"]))
    level = min(20, int(asr.get("level", 0) * 150))
    table.add_row("输入音量", "▰" * level + "▱" * (20 - level))
    table.add_row("转录积压", f"{asr.get('lag', 0) + asr.get('queued', 0):.1f} 秒")
    if asr.get("queued", 0) > 5:
        table.add_row("音频暂存", f"{asr['queued']:.1f} 秒音频等待转录")
    if asr.get("warning"):
        table.add_row("收音提示", Text(asr["warning"]))
    table.add_row("DeepSeek", Text("进程已退出，结束时保存待整理原文" if worker_dead else notes.get("status", "等待新增转录")))
    table.add_row("待整理内容", f"{max(0, asr.get('count', 0) - notes.get('cursor', 0))} 个来源片段")
    if notes.get("batch_segments"):
        table.add_row("本批合并", f"{notes['batch_segments']} 个片段 · {notes['batch_chars']} 字符")
    table.add_row("笔记位置", Text(meta["output"]))
    text = "\n".join(filter(None, [asr.get("last", ""), asr.get("pending", ""), asr.get("buffer", "")]))[-900:]
    return Panel(Group(table, Text("\n" + (text or "等待语音…")),
                       Text("\n[P] 暂停/继续    [Q] 结束并整理    Ctrl+C 同样会收尾", style="dim")), title=title)


def demo_capture(directory: Path) -> int:
    from .capture import Transcript
    transcript = Transcript(directory)
    samples = [
        "This is a synthetic demonstration, not an actual lecture. Today we introduce eigenvalues and eigenvectors.",
        "For a square matrix A, a nonzero vector v is an eigenvector if A v equals lambda v. Lambda is the eigenvalue.",
        "The nonzero condition is essential. The zero vector is not called an eigenvector.",
        "For the diagonal matrix with diagonal entries two and three, the coordinate vectors are eigenvectors with eigenvalues two and three.",
        "Look at this equation on the board. We will prove this statement next time. The board is not included in this recording.",
    ]
    for i, text in enumerate(samples):
        if (directory / "stop").exists():
            break
        while (directory / "pause").exists() and not (directory / "stop").exists():
            time.sleep(0.1)
        transcript.append(text, i * 10, (i + 1) * 10)
        write_json(directory / "asr-state.json", {"status": "演示输入（未录音）", "seconds": (i + 1) * 10,
                                                 "last": text, "count": transcript.count})
        time.sleep(0.4)
    return 0


def preserve_tail(directory: Path):
    transcript_path = directory / "transcript.jsonl"
    if transcript_path.exists():
        raw = transcript_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            # A killed writer can leave half a JSON record. Do not append into that record.
            with transcript_path.open("r+b") as f:
                f.truncate(raw.rfind(b"\n") + 1)
    state = read_json(directory / "asr-state.json")
    tail = " ".join(filter(None, [state.get("pending"), state.get("buffer")]))
    if tail:
        records = events(directory)
        from .capture import timestamp
        timecode = timestamp(state.get("seconds", 0))
        record = {"id": len(records) + 1, "start": timecode, "end": timecode,
                  "text": "[中断时的未确认尾部，待核对] " + tail}
        with (directory / "transcript.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        state.update(pending="", buffer="")
        write_json(directory / "asr-state.json", state)


def session(args, config: dict, course: Path) -> int:
    output_dir = course / "LectureNotes"
    if output_dir.is_symlink():
        raise ValueError("LectureNotes 不能是指向其他目录的符号链接")
    output_dir.mkdir(exist_ok=True)
    now = datetime.now().astimezone()
    suffix = "演示笔记" if args.command == "demo" else "课堂笔记"
    output = output_dir / f"{now:%Y-%m-%d_%H%M%S}-{suffix}-{uuid.uuid4().hex[:6]}.md"
    context = ""
    if getattr(args, "context", None):
        context = Path(args.context).expanduser().read_text()
        if len(context) > 12000:
            raise ValueError("课程背景文件过长，请控制在 12000 字符以内")
    meta = dict(config, app="lecture-cli-v1", controller_pid=os.getpid(), course=course.name, output=str(output), started=now.isoformat(timespec="seconds"),
                demo=args.command == "demo",
                context=context, **load_glossary(course, 600 if config.get("asr_backend") == "api" else 1000),
                audio_file=str(Path(args.audio_file).expanduser().resolve()) if getattr(args, "audio_file", None) else None,
                fast=getattr(args, "fast", False))
    meta["refine"] = bool(config.get("refine") and args.command == "start" and config.get("asr_backend") != "api")
    if meta.get("asr_backend") == "api":
        meta["asr_model"] = meta["asr_api_model"]
    if meta["audio_file"] and not Path(meta["audio_file"]).is_file():
        raise ValueError("音频文件不存在")
    # /tmp is explicit: do not let TMPDIR redirect classroom artifacts into a persistent vault.
    directory = Path(tempfile.mkdtemp(prefix=f"lecture-{os.getuid()}-", dir="/tmp"))
    capture = worker = refinement = None
    stop_requested = False
    stop_requests = 0
    logs = []
    handlers = {}
    rc = 0
    has_fallback = False
    detail_incomplete = False
    refinement_failed = False
    persisted = False
    owner_lock = (directory / "owner.lock").open("w")
    fcntl.flock(owner_lock, fcntl.LOCK_EX)

    def request_stop(signum, frame):
        nonlocal stop_requested, stop_requests
        stop_requested = True
        stop_requests += 1
        (directory / "stop").touch()

    def spawn(role):
        log = (directory / f"{role}.log").open("w")
        logs.append(log)
        env = os.environ.copy()
        python = sys.executable
        if role == "_capture" and meta.get("asr_backend") != "api":
            env = capture_environment(meta["asr_model"], env)
            python = capture_python(meta["asr_model"])
        elif role == "_refine":
            from .refinement import MODEL
            env = capture_environment(MODEL, env)
            env["HF_HUB_OFFLINE"] = "1"
            python = capture_python(MODEL)
        if role != "_capture" or meta.get("asr_backend") != "api":
            env.pop("LECTURE_ASR_API_KEY", None)
        if role != "_worker":
            env.pop("DEEPSEEK_API_KEY", None)
        return subprocess.Popen([python, "-m", "lecture_cli", role, str(directory)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, env=env)

    try:
        write_json(directory / "session.json", meta)
        journal = Journal(directory)
        journal.render()
        journal.close()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, request_stop)
        worker = spawn("_worker")
        capture = spawn("_demo" if args.command == "demo" else "_capture")
        meta["children"] = [capture.pid, worker.pid]
        write_json(directory / "session.json", meta)
        console.print(f"笔记将保存到：{output}", markup=False)
        with keyboard() as key, Live(display(directory), console=console, refresh_per_second=4,
                                     auto_refresh=False, transient=not console.is_terminal) as live:
            while capture.poll() is None and not stop_requested:
                pressed = key()
                if pressed == "q":
                    request_stop(None, None)
                elif pressed == "p":
                    pause = directory / "pause"
                    pause.unlink() if pause.exists() else pause.touch()
                live.update(display(directory, worker_dead=worker.poll() is not None), refresh=True)
                time.sleep(0.2)
            (directory / "stop").touch()
            deadline = time.monotonic() + drain_timeout(read_json(directory / "asr-state.json")) + 20
            while capture.poll() is None and time.monotonic() < deadline:
                live.update(display(directory, "正在完成末尾转录…"), refresh=True)
                time.sleep(0.2)
            if capture.poll() is None:
                capture.kill()
                capture.wait()
                state = read_json(directory / "asr-state.json")
                state["error"] = "转录收尾超时；已保留可取得的文字，尚未识别的音频未保存。"
                write_json(directory / "asr-state.json", state)
            if capture.returncode:
                rc = 1
                state = read_json(directory / "asr-state.json")
                if not state.get("error"):
                    state["error"] = "转录进程异常退出；已保留可取得的文字。"
                    write_json(directory / "asr-state.json", state)
                preserve_tail(directory)
            # Keep the notes worker on live batches until offline source is
            # atomically ready. The capture child has exited and freed its GPU.
            if meta["refine"]:
                from .refinement import timeout_seconds, WARNING
                if capture.returncode == 0:
                    refinement = spawn("_refine")
                    meta["children"].append(refinement.pid)
                    write_json(directory / "session.json", meta)
                    deadline = time.monotonic() + timeout_seconds(directory)
                    initial_stop_requests = stop_requests
                    cancelled = False
                    while refinement.poll() is None and time.monotonic() < deadline:
                        if key() == "q" or stop_requests > initial_stop_requests:
                            cancelled = True
                            break
                        live.update(display(directory, "课后离线校正中 · Q / Ctrl+C 跳过并保存实时记录"), refresh=True)
                        time.sleep(0.2)
                    if refinement.poll() is None:
                        state = read_json(directory / "refinement-state.json")
                        state.update(complete=False, stage=state.get("status", "离线校正"),
                                     reason="用户跳过离线校正" if cancelled else "离线校正超时")
                        write_json(directory / "refinement-state.json", state)
                        refinement.terminate()
                        try:
                            refinement.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            refinement.kill()
                            refinement.wait()
                    if refinement.returncode != 0:
                        state = read_json(directory / "refinement-state.json")
                        if not state.get("reason"):
                            state["reason"] = f"离线进程退出，退出码 {refinement.returncode}"
                        state.update(status=WARNING, complete=False)
                        write_json(directory / "refinement-state.json", state)
                else:
                    write_json(directory / "refinement-state.json", {
                        "status": WARNING, "complete": False, "stage": "录音收尾",
                        "reason": "录音进程异常退出，未启动离线校正"})
            (directory / "capture.done").touch()
            from .final_notes import finish_timeout
            deadline = time.monotonic() + finish_timeout(events(directory), read_json(directory / "notes-state.json").get("cursor", 0))
            if meta["refine"]:
                deadline += finish_timeout(final_events(directory), 0)
            while worker.poll() is None and time.monotonic() < deadline:
                live.update(display(directory, "正在保存最后的笔记…"), refresh=True)
                time.sleep(0.2)
    finally:
        # Stop children before deleting their workspace. Also covers Ctrl+C/TERM/HUP and exceptions.
        for process in (capture, refinement, worker):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        try:
            if (directory / "session.json").exists():
                preserve_tail(directory)
                journal = Journal(directory)
                try:
                    state = read_json(directory / "asr-state.json")
                    if state.get("error") or state.get("warning"):
                        warning = " ".join(filter(None, [state.get("error"), state.get("warning")]))
                        journal.add_warning(warning)
                        console.print(warning, style="yellow", markup=False)
                    if meta["refine"] and refined_events(directory) is None:
                        from .refinement import WARNING
                        journal.add_warning(WARNING)
                        refinement_failed = True
                    journal.fallback()
                    journal.preserve_detail_tail()
                    journal.render(finished=True)
                    persisted = True
                    has_fallback = bool(journal.db.execute("SELECT COUNT(*) FROM batches WHERE fallback=1").fetchone()[0])
                    detail_incomplete = dict(journal.db.execute("SELECT key, value FROM info")).get("detail_status") == "incomplete"
                finally:
                    journal.close()
        finally:
            for log in logs:
                log.close()
            try:
                if persisted or not (directory / "session.json").exists():
                    shutil.rmtree(directory)
                else:
                    console.print(f"笔记尚未成功保存，暂存于 {directory}；恢复目标目录可写后再运行 lecture，即可恢复并清理。", style="yellow", markup=False)
            finally:
                owner_lock.close()
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
    console.print(f"已保存：{output}\n本次 /tmp 中间文件已清理。", markup=False)
    if refinement_failed:
        from .refinement import WARNING
        console.print(WARNING, style="yellow", markup=False)
    if has_fallback:
        console.print("部分内容未完成 DeepSeek 整理，已作为“待整理原文”保存在笔记中。", style="yellow")
    if detail_incomplete:
        console.print("详细笔记未全部完成；已完成章节及剩余原文已保存在笔记中。", style="yellow")
    return rc


def setup(config):
    if not sys.stdin.isatty():
        raise ValueError("setup 需要交互终端")
    root = console.input(f"课程目录 [{config['courses_dir']}]：", markup=False).strip()
    if root:
        config["courses_dir"] = str(Path(root).expanduser().resolve())
    course_paths(Path(config["courses_dir"]))
    config["asr_model"] = choose_asr_model(config.get("asr_model", "base.en"))
    devices()
    answer = console.input("麦克风编号或名称 [系统默认]：", markup=False).strip()
    config["device"] = int(answer) if answer.isdigit() else answer or None
    key = getpass.getpass("DeepSeek API key（留空保留现有配置）：").strip()
    save_config(config)
    if key:
        from .storage import atomic_text
        atomic_text(config_dir() / "api-key", key + "\n")
    console.print("配置已保存。运行 lecture start 选择课程并开始。")


def choose_asr_model(current: str) -> str:
    """Interactive ↑↓ picker. Weakest models first. Saves nothing by itself."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("选择语音模型需要交互终端")
    names = asr_models()
    if not names:
        raise ValueError("没有可用的语音模型")
    current = current if current in names else names[0]
    index = names.index(current)
    return pick_asr_model(names, index, current)


def pick_asr_model(names: tuple[str, ...], index: int, current: str) -> str:
    """curses keypad() translates arrows reliably across terminals."""
    import curses

    outcome: dict = {"value": None, "error": None}

    def main(stdscr):
        try:
            outcome["value"] = _curses_model_menu(stdscr, names, index, current)
        except ValueError as exc:
            outcome["error"] = exc

    curses.wrapper(main)
    if outcome["error"] is not None:
        raise outcome["error"]
    if not outcome["value"]:
        raise ValueError("已取消选择语音模型")
    return outcome["value"]


def _curses_model_menu(stdscr, names: tuple[str, ...], index: int, current: str) -> str:
    import curses
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.noutrefresh()
    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, "语音模型  Up/Down 或 j/k 选择 · Enter 确认 · q 取消")
        for i, name in enumerate(names):
            marker = "> " if i == index else "  "
            suffix = "  *" if name == current else ""
            line = f"{marker}{name}{suffix}"
            attr = curses.A_REVERSE if i == index else curses.A_NORMAL
            try:
                stdscr.addstr(i + 2, 0, line, attr)
            except curses.error:
                break
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            raise ValueError("已取消选择语音模型")
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return names[index]
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(names)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(names)
        # Ignore ESC and other CSI leftovers; keypad() already maps real arrows.


def save_config(config) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(directory / "config.json", config)


def devices():
    import sounddevice as sd
    for i, device in enumerate(sd.query_devices()):
        if device["max_input_channels"]:
            console.print(f"{i:>3}  {device['name']}", markup=False)


def doctor(config):
    import importlib.util
    checks = {"课程目录": Path(config["courses_dir"]).is_dir(),
              "DeepSeek key": bool(os.environ.get("DEEPSEEK_API_KEY")),
              "FFmpeg": bool(shutil.which("ffmpeg"))}
    if config.get("asr_backend") == "api":
        import httpx
        from .api_capture import API_TIMEOUT
        checks["转录 key"] = bool(os.environ.get("LECTURE_ASR_API_KEY"))
        checks["转录服务"] = False
        if checks["转录 key"]:
            try:
                with httpx.Client(timeout=API_TIMEOUT) as client:
                    response = client.get(config["asr_api_base"].rstrip("/") + "/models",
                                          headers={"Authorization": "Bearer " + os.environ["LECTURE_ASR_API_KEY"]})
                checks["转录服务"] = response.status_code == 200
            except httpx.RequestError:
                pass
    else:
        checks["WhisperLiveKit"] = importlib.util.find_spec("whisperlivekit") is not None
        try:
            probe = subprocess.run([capture_python(config["asr_model"]), "-m", "lecture_cli.asr", config.get("asr_device", "auto"), config["asr_model"]],
                                   env=capture_environment(config["asr_model"]), capture_output=True, text=True, timeout=30)
            result = json.loads(probe.stdout)
            checks["识别设备检查"] = probe.returncode == 0
            precision = "BF16" if config["asr_model"] in QWEN_MODELS else "FP16"
            console.print("识别设备：" + (f"NVIDIA GPU · {precision}" if result.get("device") == "cuda" else
                                         result.get("error") or result.get("notice") or "CPU"), markup=False)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            checks["识别设备检查"] = False
    try:
        import sounddevice as sd
        sd.check_input_settings(device=config.get("device"), channels=1, samplerate=16000)
        checks["麦克风格式（未开始录音）"] = True
    except Exception:
        checks["麦克风格式（未开始录音）"] = False
    for label, ok in checks.items():
        console.print(f"{'✓' if ok else '✗'} {label}", markup=False)
    return 0 if all(checks.values()) else 1


def main(argv=None):
    os.umask(0o077)
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "completion":
        # Install path dumps the script; keep callable but out of user help.
        if len(argv) != 2 or argv[1] != "zsh":
            print("usage: lecture completion zsh", file=sys.stderr)
            return 2
        sys.stdout.write((Path(__file__).parent / "completions" / "_lecture").read_text())
        return 0
    if argv and argv[0] == "_complete-asr-models":
        for name in asr_models():
            print(name)
        return 0
    if argv and argv[0] == "_complete-courses":
        # Completion must never recover sessions, read credentials or open audio hardware.
        root = Path(argv[1]).expanduser() if len(argv) > 1 else Path(configuration(load_key=False)["courses_dir"]).expanduser()
        try:
            for path in course_paths(root):
                if not any(c in path.name for c in "\n\r\t"):
                    print(path.name)
        except (OSError, ValueError):
            pass
        return 0
    if argv and argv[0] in ("_capture", "_worker", "_demo", "_refine"):
        # On Linux, a killed terminal/controller must not leave microphone or API children running.
        import ctypes
        directory = Path(argv[1])
        expected_parent = read_json(directory / "session.json").get("controller_pid")
        if sys.platform.startswith("linux"):
            ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG
        if os.getppid() != expected_parent:
            return 1
        if argv[0] == "_worker":
            from .worker import run
        elif argv[0] == "_capture":
            if read_json(directory / "session.json").get("asr_backend") == "api":
                from .api_capture import run
            else:
                from .capture import run
        elif argv[0] == "_refine":
            from .refinement import refine as run
        else:
            run = demo_capture
        return run(directory)
    parser = argparse.ArgumentParser(prog="lecture", description="课堂转录与 DeepSeek 中文笔记")
    parser.add_argument("--courses-dir", help="覆盖课程根目录")
    subs = parser.add_subparsers(dest="command")
    for name, help_text in [("courses", "列出课程"), ("models", "选择并保存默认语音模型"),
                            ("setup", "配置目录、语音模型、麦克风和 API key"),
                            ("devices", "列出麦克风"), ("doctor", "检查环境，不录音"),
                            ("prepare", "提前下载语音模型"), ("start", "选择课程并录制"),
                            ("diagnose-asr", "录制同一段音频并对比两个语音模型"),
                            ("demo", "用自造文字演示笔记生成，不录音")]:
        sub = subs.add_parser(name, help=help_text)
        if name in ("start", "demo"):
            sub.add_argument("course", nargs="?")
            sub.add_argument("--interval", type=float, help="笔记检查间隔，默认 60 秒")
            sub.add_argument("--context", help="笔记背景文本（最多 12000 字符）；ASR 使用课程 glossary.json")
        if name in ("start", "prepare", "doctor"):
            sub.add_argument("--asr-model", metavar="NAME",
                             help="语音模型（lecture models 查看全部），覆盖已保存的默认值")
        if name in ("start", "doctor"):
            sub.add_argument("--asr-backend", choices=["local", "api"], help="采集后端，默认 local")
            sub.add_argument("--asr-device", choices=["auto", "cuda", "cpu"], help="识别设备，默认 auto 优先 GPU")
        if name == "start":
            sub.add_argument("--asr-api-model", metavar="NAME", help="云端转录模型，仅影响本次运行")
            sub.add_argument("--refine", action=argparse.BooleanOptionalAction, default=None,
                             help="下课后用 Qwen 1.7B 重转录；临时保存音频，完成后删除")
            sub.add_argument("--device", help="麦克风编号或名称")
            sub.add_argument("--language", help="课堂语言，默认 en")
            sub.add_argument("--audio-file", help="使用已有音频代替麦克风")
            sub.add_argument("--fast", action="store_true", help="尽快处理已有音频")
        if name == "diagnose-asr":
            from .asr_diagnostics import DEFAULT_MODELS
            sub.add_argument("course", nargs="?")
            sub.add_argument("--seconds", type=int, default=30, help="采样时长，30–60 秒；默认 30")
            sub.add_argument("--model-a", default=DEFAULT_MODELS[0], help="第一个语音模型")
            sub.add_argument("--model-b", default=DEFAULT_MODELS[1], help="第二个语音模型")
            sub.add_argument("--asr-device", choices=["auto", "cuda", "cpu"], help="识别设备，默认 auto")
            sub.add_argument("--device", help="麦克风编号或名称")
            sub.add_argument("--language", help="音频语言，默认 en")
            sub.add_argument("--context", help="诊断用 ASR 英文短词表（最多 1000 字符）")
            sub.add_argument("--audio-file", help="使用已有音频，不打开麦克风")
            sub.add_argument("--keep-audio", action="store_true", help="在诊断目录保留截取后的 audio.wav")
            sub.add_argument("--fast", action="store_true", help="尽快重放；默认按实时速度以贴近课堂路径")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    config = configuration(load_key=args.command != "diagnose-asr")
    reap_stale_sessions()
    for field in ("courses_dir", "asr_model", "asr_device", "interval", "device", "language", "refine", "asr_backend", "asr_api_model"):
        if getattr(args, field, None) is not None:
            config[field] = getattr(args, field)
    if isinstance(config["device"], str) and config["device"].isdigit():
        config["device"] = int(config["device"])
    root = Path(config["courses_dir"]).expanduser().resolve()
    config["courses_dir"] = str(root)
    try:
        if not 1 <= config["interval"] <= 3600:
            raise ValueError("间隔必须在 1–3600 秒之间")
        if config["asr_backend"] not in ("local", "api"):
            raise ValueError("采集后端必须为 local 或 api")
        if config["asr_backend"] == "api" and (
                not isinstance(config["asr_api_base"], str) or
                not config["asr_api_base"].startswith(("https://", "http://")) or
                not isinstance(config["asr_api_model"], str) or not config["asr_api_model"].strip()):
            raise ValueError("云端转录须配置 HTTP(S) 地址和非空模型名称")
        if args.command == "prepare" or (args.command in ("start", "doctor", "demo")
                                         and config["asr_backend"] == "local"):
            config["asr_model"] = resolve_asr_model(config["asr_model"])
        if args.command == "courses":
            for p in course_paths(root):
                console.print(p.name, markup=False)
        elif args.command == "models":
            if not sys.stdin.isatty():
                current = config.get("asr_model", "base.en")
                for name in asr_models():
                    mark = " *" if name == current else ""
                    console.print(f"{name}{mark}", markup=False, highlight=False)
            else:
                config["asr_model"] = choose_asr_model(config.get("asr_model", "base.en"))
                save_config(config)
                console.print(f"默认语音模型已设为 {config['asr_model']}", markup=False)
        elif args.command == "devices":
            devices()
        elif args.command == "setup":
            setup(config)
        elif args.command == "doctor":
            return doctor(config)
        elif args.command == "prepare":
            console.print(f"下载模型：{config['asr_model']}", markup=False)
            if config["asr_model"] in QWEN_MODELS:
                from huggingface_hub import snapshot_download
                snapshot_download(QWEN_MODELS[config["asr_model"]])
            else:
                from faster_whisper.utils import download_model
                download_model(config["asr_model"])
            console.print("模型已准备好。")
        elif args.command == "diagnose-asr":
            from .asr_diagnostics import run_diagnostic
            if not 30 <= args.seconds <= 60:
                raise ValueError("诊断时长必须在 30–60 秒之间")
            context = ""
            if args.context:
                context = Path(args.context).expanduser().read_text()
            course = select_course(root, args.course)
            if not args.audio_file:
                console.print(f"将从麦克风录制 {args.seconds} 秒；Ctrl+C 可取消。", markup=False)
            result = run_diagnostic(
                course=course,
                seconds=args.seconds,
                models=(args.model_a, args.model_b),
                language=config["language"],
                device=config.get("device"),
                asr_device=config.get("asr_device", "auto"),
                context=context,
                audio_file=Path(args.audio_file).expanduser() if args.audio_file else None,
                keep_audio=args.keep_audio,
                fast=args.fast,
            )
            console.print(f"ASR 诊断已保存：{result['output']}", markup=False)
            if not args.keep_audio:
                console.print("原始诊断音频已从 /tmp 自动删除。", markup=False)
            return 0 if result["ok"] else 1
        else:
            if args.command == "start" and config["asr_backend"] == "api":
                config["refine"] = False
                if not os.environ.get("LECTURE_ASR_API_KEY"):
                    raise ValueError("缺少转录 key，请设置 LECTURE_ASR_API_KEY 或写入配置目录的 asr-api-key 文件")
            if args.command == "start" and config["asr_backend"] == "local":
                capture_python(config["asr_model"])
                if config.get("refine"):
                    from .refinement import MODEL
                    capture_python(MODEL)
                if config["asr_model"] in QWEN_MODELS and config["language"] == "auto":
                    raise ValueError("Qwen 流式识别需要 --language en 或 zh")
            if not os.environ.get("DEEPSEEK_API_KEY"):
                raise ValueError("缺少 DeepSeek key，请先运行 lecture setup 或设置 DEEPSEEK_API_KEY")
            return session(args, config, select_course(root, args.course))
        return 0
    except (ValueError, OSError) as exc:
        console.print(f"错误：{exc}", style="red", markup=False)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
