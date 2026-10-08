"""Plan N3.1: whether this machine can switch a Chinese lecture to live Qwen right now."""
from __future__ import annotations

import json
import subprocess
import threading

from .. import checks
from ..asr import QWEN_MODELS, capture_environment, capture_python

INSTALL = "./install.sh --with-qwen"
_gpu: dict[str, bool] = {}  # Per Qwen interpreter; a probe imports torch and takes seconds.
_lock = threading.Lock()


def model(config: dict) -> str:
    """The Qwen model to switch to: the refinement model when it is one, so both share weights."""
    chosen = config.get("refine_model")
    return chosen if chosen in QWEN_MODELS else "qwen3-asr-1.7b"


def gpu(python: str, name: str, runner=None) -> bool:
    """Live Qwen on CPU cannot keep up with a lecture, so a ready switch needs CUDA."""
    with _lock:
        if python in _gpu:
            return _gpu[python]
    try:
        probe = (runner or subprocess.run)([python, "-m", "lecture_cli.asr", "auto", name],
                                           env=capture_environment(name), capture_output=True, text=True, timeout=30)
        found = json.loads(probe.stdout).get("device") == "cuda"
    except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
        return False  # Not remembered: a later look may succeed.
    with _lock:
        _gpu[python] = found
    return found


def readiness(config: dict, runner=None) -> dict:
    name = model(config)
    try:
        python = capture_python(name, config.get("qwen_python"))
        env_ready = True
    except ValueError:
        python, env_ready = None, False
    cached = env_ready and checks.weights_cached(name)
    has_gpu = bool(python) and cached and gpu(python, name, runner)
    return {"model": name, "env_ready": env_ready, "cached": cached, "gpu": has_gpu,
            "ready": env_ready and cached and has_gpu, "install": INSTALL}
