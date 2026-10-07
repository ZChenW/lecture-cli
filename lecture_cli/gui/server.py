"""Starlette application: loopback-only JSON API, SSE status stream and static frontend."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import config as settings
from ..providers import ASR_PRESETS, NOTES_PRESETS, test_asr, test_notes
from . import library
from .sessions import Busy, Sessions, StartError

COOKIE = "lecture_session"
# Everything the frontend needs comes from this origin: bundled scripts, styles and fonts,
# fetch/SSE to the API. No inline code, no data: URLs, no framing, no native form posts.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
STATIC = Path(__file__).parent / "static"
KINDS = ("notes", "asr")
SSE_INTERVAL = 0.25
KEEPALIVE_SECONDS = 15
OVERRIDES = {"language", "interval", "refine", "auto_gain", "context_path", "demo"}
SENTINELS = {"pause": ("pause", True), "resume": ("pause", False), "stop": ("stop", True),
             "skip-refine": ("skip-refine", True)}
DEVICE_NOTES = {
    "default": "不选择设备时使用系统默认输入，推荐。",
    "pipewire": "系统默认以及名为 pipewire、default、pulse 的设备经由 PipeWire 默认源录音，可自动调节音量；其他设备不自动调节。",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.field, self.extra = status, code, message, field, extra


def error_response(status, code, message, field=None, **extra) -> JSONResponse:
    body = {"code": code, "message": message, **({"field": field} if field else {}), **extra}
    return JSONResponse({"error": body}, status_code=status)


class Guard:
    """Host, token cookie and Origin checks in front of everything else."""

    def __init__(self, app, port: int, token: str):
        self.app = app
        self.token = token.encode()
        # DNS rebinding sends another host name to the same loopback port.
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def same(self, value: str) -> bool:
        return hmac.compare_digest(value.encode(), self.token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_policy(message):
            # Every response, including this guard's own errors and redirects.
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"content-security-policy", CSP.encode())]}
            await send(message)

        await self.check(scope, receive, send_with_policy)

    async def check(self, scope, receive, send):
        request = Request(scope)
        host = request.headers.get("host", "")
        if host not in self.hosts:
            return await error_response(400, "bad_host", "不接受该 Host")(scope, receive, send)
        if scope["path"] == "/" and self.same(request.query_params.get("t", "")):
            response = RedirectResponse("/", status_code=302)
            response.set_cookie(COOKIE, self.token.decode(), httponly=True, samesite="strict", path="/")
            return await response(scope, receive, send)
        if scope["method"] not in ("GET", "HEAD") and request.headers.get("origin") != f"http://{host}":
            return await error_response(403, "bad_origin", "跨源请求被拒绝")(scope, receive, send)
        if scope["path"].startswith("/api") and not self.same(request.cookies.get(COOKIE, "")):
            return await error_response(401, "unauthorized", "缺少有效的会话令牌，请从 lecture gui 打开的地址进入")(
                scope, receive, send)
        await self.app(scope, receive, send)


class Tasks:
    """Long CLI subprocesses (model downloads) whose output the GUI polls."""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def start(self, args: list[str]) -> str:
        task_id = secrets.token_hex(6)
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace")
        item = {"id": task_id, "status": "running", "exit_code": None, "lines": deque(maxlen=200)}
        self.items[task_id] = item

        def follow():
            # Universal newlines also split progress bars that redraw with \r.
            for line in process.stdout:
                if line.strip():
                    item["lines"].append(line.rstrip())
            item["exit_code"] = process.wait()
            item["status"] = "done" if item["exit_code"] == 0 else "failed"

        threading.Thread(target=follow, daemon=True).start()
        return task_id

    def view(self, task_id: str, lines: int = 20) -> dict:
        item = self.items.get(task_id)
        if item is None:
            raise ApiError(404, "not_found", "任务不存在")
        return {"id": item["id"], "status": item["status"], "exit_code": item["exit_code"],
                "output": list(item["lines"])[-lines:]}


def key_environment() -> dict:
    """Environment for run_checks; this process itself never loads key files into os.environ,
    so key_status keeps telling environment keys from stored ones."""
    environ = dict(os.environ)
    for kind, variable in settings.KEY_VARIABLES.items():
        value, _ = settings.read_key(kind)
        if value:
            environ[variable] = value
    return environ


def courses_root(config: dict) -> Path:
    root = config.get("courses_dir")
    if not root or not Path(root).expanduser().is_dir():
        raise ApiError(409, "no_courses_dir", "尚未设置有效的课程目录", "courses_dir")
    return Path(root).expanduser().resolve()


async def body_of(request: Request, required=True) -> dict:
    raw = await request.body()
    if not raw and not required:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        raise ApiError(400, "bad_request", "请求体必须是 JSON 对象")
    if not isinstance(value, dict):
        raise ApiError(400, "bad_request", "请求体必须是 JSON 对象")
    return value


def same_base(left: str, right: str) -> bool:
    return left.strip().rstrip("/") == right.strip().rstrip("/")


def problems_of(config: dict) -> list[dict]:
    return [{"field": p.field, "message": p.message} for p in settings.validate(config)]


def version() -> str:
    from importlib.metadata import PackageNotFoundError, version as package_version
    try:
        return package_version("lecture-cli")
    except PackageNotFoundError:
        return "unknown"


def check_overrides(overrides, root: Path) -> tuple[dict, Path | None]:
    if not isinstance(overrides, dict):
        raise ApiError(422, "invalid_overrides", "overrides 必须是对象", "overrides")
    for key, value in overrides.items():
        if key not in OVERRIDES:
            raise ApiError(422, "invalid_overrides", f"不允许覆盖 {key}", key)
        valid = {"language": isinstance(value, str) and value.strip() != "",
                 "interval": isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value <= 3600,
                 "context_path": isinstance(value, str) and value != ""}.get(key, isinstance(value, bool))
        if not valid:
            raise ApiError(422, "invalid_overrides", f"{key} 的取值无效", key)
    context = None
    if "context_path" in overrides:
        context = library.inside(root, overrides["context_path"])
        if not context.is_file():
            raise ApiError(422, "invalid_overrides", "课程背景文件不存在", "context_path")
    return overrides, context


def create_app(port: int, token: str, *, on_quit=None, sessions: Sessions | None = None,
               transport=None, opener=None) -> Starlette:
    sessions = sessions or Sessions()
    tasks = Tasks()
    system_opener = opener is None
    opener = opener or (lambda target: subprocess.Popen(
        ["xdg-open", str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True))

    def keys() -> dict:
        return {kind: settings.key_status(kind) for kind in KINDS}

    def kind_of(request) -> str:
        kind = request.path_params["kind"]
        if kind not in KINDS:
            raise ApiError(404, "not_found", "未知的 key 类型")
        return kind

    async def active_snapshot():
        active = await run_in_threadpool(sessions.active)
        return (await run_in_threadpool(sessions.snapshot, active)) if active else None

    async def bootstrap(request):
        config = settings.load()
        key_state = keys()
        return JSONResponse({"version": version(), "configured": settings.is_configured(config, key_state),
                             "problems": problems_of(config), "config": config, "keys": key_state,
                             "active_run": await active_snapshot(),
                             "presets": {"notes": NOTES_PRESETS, "asr": ASR_PRESETS}})

    async def put_config(request):
        changes = await body_of(request)
        for field in changes:
            if field not in settings.DEFAULTS or field == "config_version":
                raise ApiError(422, "invalid_config", f"未知配置项：{field}", field)
        current = settings.load()
        merged = {**current, **changes}
        if isinstance(changes.get("courses_dir"), str) and changes["courses_dir"].strip():
            merged["courses_dir"] = str(Path(changes["courses_dir"].strip()).expanduser().resolve())
        before = {(p.field, p.message) for p in settings.validate(current)}
        # Reject what this request breaks or submits wrongly; older unrelated problems stay listed.
        found = [p for p in settings.validate(merged) if p.field in changes or (p.field, p.message) not in before]
        if found:
            return error_response(422, "invalid_config", found[0].message, found[0].field,
                                  problems=[{"field": p.field, "message": p.message} for p in found])
        settings.save(merged)
        return JSONResponse({"config": merged, "problems": problems_of(merged),
                             "configured": settings.is_configured(merged, keys())})

    async def put_key(request):
        kind = kind_of(request)
        value = (await body_of(request)).get("value")
        if not isinstance(value, str):
            raise ApiError(422, "invalid_key", "key 必须是字符串", "value")
        try:
            return JSONResponse(settings.write_key(kind, value))
        except ValueError as exc:
            raise ApiError(422, "invalid_key", str(exc), "value")

    async def delete_key(request):
        kind = kind_of(request)
        if settings.key_status(kind)["source"] == "env":
            raise ApiError(409, "key_from_env", "该 key 来自环境变量，无法在此删除")
        return JSONResponse(settings.delete_key(kind))

    async def test_service(request):
        kind = kind_of(request)
        changes = await body_of(request, required=False)
        for field in ("api_base", "model", "key", "provider"):
            if field in changes and not isinstance(changes[field], str):
                raise ApiError(422, "invalid_request", f"{field} 必须是字符串", field)
        config = settings.load()
        key = changes.get("key", "").strip()
        saved_base = config.get(f"{kind}_api_base") or ""
        if "api_base" in changes and not same_base(changes["api_base"], saved_base) and not key:
            # The saved key belongs to the saved service; never hand it to another host.
            raise ApiError(422, "key_required", "测试其他服务地址时必须同时填写 key", "key")
        if "api_base" in changes:
            config[f"{kind}_api_base"] = changes["api_base"]
        if "model" in changes:
            config["notes_model" if kind == "notes" else "asr_api_model"] = changes["model"]
        if "provider" in changes:  # Only names the service in the result message.
            config[f"{kind}_provider"] = changes["provider"]
        key = key or settings.read_key(kind)[0]
        test = test_notes if kind == "notes" else test_asr
        return JSONResponse(asdict(await run_in_threadpool(test, config, key, transport)))

    async def list_courses(request):
        root = courses_root(settings.load())
        return JSONResponse(await run_in_threadpool(library.courses, root))

    async def add_course(request):
        root = courses_root(settings.load())
        try:
            course = library.create_course(root, (await body_of(request)).get("name"))
        except FileExistsError:
            raise ApiError(409, "exists", "同名课程已存在", "name")
        except ValueError as exc:
            if isinstance(exc, library.Forbidden):
                raise
            raise ApiError(422, "invalid_name", str(exc), "name")
        return JSONResponse(course, status_code=201)

    async def devices(request):
        def query():
            import sounddevice as sd
            default = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
            return [{"index": i, "name": d["name"], "channels": d["max_input_channels"], "default": i == default}
                    for i, d in enumerate(sd.query_devices()) if d["max_input_channels"]]
        try:
            listed = await run_in_threadpool(query)
        except Exception as exc:  # PortAudio missing or broken: the GUI shows the reason.
            raise ApiError(503, "devices_unavailable", f"无法列出麦克风：{exc}")
        return JSONResponse({"devices": listed, "selected": settings.load().get("device"), "notes": DEVICE_NOTES})

    async def asr_models(request):
        from .. import checks
        from ..asr import QWEN_MODELS, asr_models as names, capture_python
        config = settings.load()

        def ready(name):
            if name not in QWEN_MODELS:
                return importlib.util.find_spec("whisperlivekit") is not None
            try:
                capture_python(name, config.get("qwen_python"))
                return True
            except ValueError:
                return False

        def describe():
            return [{"name": name, "family": "qwen" if name in QWEN_MODELS else "whisper",
                     "cached": checks.weights_cached(name), "env_ready": ready(name)} for name in names()]
        return JSONResponse(await run_in_threadpool(describe))

    async def prepare(request):
        from ..asr import asr_models as names
        name = request.path_params["name"]
        if name not in names():
            raise ApiError(404, "not_found", "未知的语音模型")
        task_id = tasks.start([sys.executable, "-m", "lecture_cli", "prepare", f"--asr-model={name}"])
        return JSONResponse({"task": task_id}, status_code=202)

    async def task(request):
        return JSONResponse(tasks.view(request.path_params["id"]))

    async def run_checks(request):
        from .. import checks
        config = settings.load()
        results = await run_in_threadpool(lambda: checks.run_checks(config, environ=key_environment(),
                                                                    transport=transport))
        return JSONResponse([asdict(item) for item in results])

    async def start_run(request):
        body = await body_of(request)
        root = courses_root(settings.load())
        course = library.course_dir(root, body.get("course"))
        overrides, context = check_overrides(body.get("overrides", {}), root)
        try:
            active = await run_in_threadpool(sessions.start, course.name, overrides, context)
        except Busy:
            raise ApiError(409, "busy", "已有进行中的课堂")
        except StartError as exc:
            raise ApiError(422, "start_failed", str(exc), log=exc.log)
        return JSONResponse(await run_in_threadpool(sessions.snapshot, active), status_code=201)

    async def get_active(request):
        return JSONResponse(await active_snapshot())

    async def control(request):
        action = request.path_params["action"]
        if action not in SENTINELS:
            raise ApiError(404, "not_found", "未知操作")
        active = await run_in_threadpool(sessions.active)
        if not active:
            raise ApiError(404, "no_active_run", "没有进行中的课堂")
        name, present = SENTINELS[action]
        sentinel = active["directory"] / name
        sentinel.touch() if present else sentinel.unlink(missing_ok=True)
        return JSONResponse(await run_in_threadpool(sessions.snapshot, active))

    async def events(request):
        active = await run_in_threadpool(sessions.active)
        if not active:
            raise ApiError(404, "no_active_run", "没有进行中的课堂")

        async def stream():
            last, quiet_since = None, time.monotonic()
            while not await request.is_disconnected():
                # A vanished session.json means the workspace is being removed: no empty snapshot.
                if (active["directory"] / "session.json").exists():
                    current = await run_in_threadpool(sessions.snapshot, active)
                    if current != last:
                        last, quiet_since = current, time.monotonic()
                        yield f"event: snapshot\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
                record = await run_in_threadpool(sessions.final_record, active)
                if record is not None:
                    yield f"event: finished\ndata: {json.dumps(record, ensure_ascii=False)}\n\n"
                    return
                if time.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                    quiet_since = time.monotonic()
                    yield ": keepalive\n\n"
                await asyncio.sleep(SSE_INTERVAL)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def list_runs(request):
        from .sessions import records
        return JSONResponse(await run_in_threadpool(records))

    async def list_notes(request):
        root = courses_root(settings.load())
        course = library.course_dir(root, request.query_params.get("course"))
        return JSONResponse(await run_in_threadpool(library.notes, root, course))

    async def note_content(request):
        root = courses_root(settings.load())
        return JSONResponse(await run_in_threadpool(library.content, root, request.query_params.get("path", "")))

    async def open_path(request):
        body = await body_of(request)
        root = courses_root(settings.load())
        if body.get("mode") not in ("file", "folder") or not isinstance(body.get("path"), str):
            raise ApiError(422, "invalid_request", "需要 path 和 mode（file 或 folder）")
        path = library.inside(root, body["path"])
        if not path.exists() or (body["mode"] == "file" and not path.is_file()):
            raise ApiError(404, "not_found", "文件不存在", "path")
        if system_opener and not shutil.which("xdg-open"):
            raise ApiError(503, "unavailable", "未找到 xdg-open")
        opener(path if body["mode"] == "file" or path.is_dir() else path.parent)
        return JSONResponse({"ok": True})

    async def quit_app(request):
        if on_quit:
            # After this response is sent; sessions keep running in their own processes.
            asyncio.get_running_loop().call_later(0.1, on_quit)
        return JSONResponse({"ok": True})

    async def unknown_api(request):
        raise ApiError(404, "not_found", "接口不存在")

    def handle_api_error(request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.field, **exc.extra)

    routes = [
        Route("/api/bootstrap", bootstrap),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/keys/{kind}", put_key, methods=["PUT"]),
        Route("/api/keys/{kind}", delete_key, methods=["DELETE"]),
        Route("/api/test/{kind}", test_service, methods=["POST"]),
        Route("/api/courses", list_courses),
        Route("/api/courses", add_course, methods=["POST"]),
        Route("/api/devices", devices),
        Route("/api/asr-models", asr_models),
        Route("/api/asr-models/{name}/prepare", prepare, methods=["POST"]),
        Route("/api/tasks/{id}", task),
        Route("/api/checks", run_checks),
        Route("/api/runs", start_run, methods=["POST"]),
        Route("/api/runs", list_runs),
        Route("/api/runs/active", get_active),
        Route("/api/runs/active/events", events),
        Route("/api/runs/active/{action}", control, methods=["POST"]),
        Route("/api/notes", list_notes),
        Route("/api/notes/content", note_content),
        Route("/api/open", open_path, methods=["POST"]),
        Route("/api/quit", quit_app, methods=["POST"]),
        Route("/api/{rest:path}", unknown_api, methods=["GET", "POST", "PUT", "DELETE"]),
        Mount("/", StaticFiles(directory=STATIC, html=True)),
    ]
    handlers = {
        ApiError: handle_api_error,
        library.Forbidden: lambda request, exc: error_response(403, "forbidden", str(exc)),
        library.NotFound: lambda request, exc: error_response(404, "not_found", str(exc)),
    }
    app = Starlette(routes=routes, exception_handlers=handlers)
    return Guard(app, port, token)
