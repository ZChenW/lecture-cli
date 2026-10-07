import os
from pathlib import Path
import select
import shlex
import subprocess
import time

import pytest

from lecture_cli import cli
from lecture_cli.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def test_completion_does_not_read_key_or_recover_sessions(tmp_path, monkeypatch, capsys):
    (tmp_path / "CS590OP_HW").mkdir()
    monkeypatch.setattr(cli, "reap_stale_sessions", lambda: pytest.fail("completion must not mutate sessions"))
    def config(load_key=True):
        assert not load_key
        return {"courses_dir": str(tmp_path)}
    monkeypatch.setattr(cli, "configuration", config)
    assert cli.main(["_complete-courses"]) == 0
    assert capsys.readouterr().out == "CS590OP_HW\n"
    assert cli.main(["completion", "zsh"]) == 0
    assert capsys.readouterr().out.startswith("#compdef lecture")
    assert cli.main(["_complete-asr-models"]) == 0
    models = capsys.readouterr().out.splitlines()
    assert "base.en" in models and "medium.en" in models and "large-v3-turbo" in models


def test_real_zsh_tab_completes_commands_courses_and_gpu_options(tmp_path):
    courses = tmp_path / "courses with spaces"
    for name in ("CS590OP_HW", "MATH421"):
        (courses / name).mkdir(parents=True)
    config = tmp_path / "config"
    (config / "lecture-cli").mkdir(parents=True)
    write_json(config / "lecture-cli" / "config.json", {"courses_dir": str(courses)})
    env = dict(os.environ, TERM="xterm", XDG_CONFIG_HOME=str(config),
               PATH=str(ROOT / ".venv/bin") + os.pathsep + os.environ["PATH"])
    master, slave = os.openpty()
    proc = subprocess.Popen(["zsh", "-f"], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    def read_until(expected, timeout=5):
        output = b""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 65536)
                if all(word.encode() in output for word in expected):
                    return output.decode(errors="replace")
        pytest.fail(f"missing {expected!r} in terminal output: {output!r}")
    try:
        setup = (f"fpath=({shlex.quote(str(ROOT / 'lecture_cli/completions'))} $fpath); "
                 f"autoload -Uz compinit; compinit -d {shlex.quote(str(tmp_path / 'zcompdump'))}; "
                 "PS1='READY> '; bindkey '^I' complete-word; bindkey '^G' send-break; print SETUP_DONE\n")
        os.write(master, setup.encode())
        read_until(["\r\nSETUP_DONE\r\n"])
        for command, expected in [
            ("lecture ", ["start", "doctor", "models", "diagnose-asr"]),
            ("lecture start --asr-model ", ["base.en", "medium.en", "qwen3-asr-1.7b", "qwen3-asr-0.6b"]),
            ("lecture doctor --asr-model ", ["qwen3-asr-1.7b", "qwen3-asr-0.6b"]),
            ("lecture start ", ["CS590OP_HW", "MATH421"]),
            ("lecture start --asr-device ", ["auto", "cuda", "cpu"]),
            ("lecture start CS590OP_HW --asr-device ", ["auto", "cuda", "cpu"]),
            ("lecture diagnose-asr ", ["CS590OP_HW", "MATH421"]),
            ("lecture diagnose-asr MATH421 --model-a ", ["qwen3-asr-1.7b", "large-v3-turbo"]),
            ("lecture start --asr-", ["--asr-device", "--asr-model"]),
            ("lecture start --", ["--refine", "--no-refine", "--auto-gain", "--no-auto-gain"]),
            (f"lecture --courses-dir {shlex.quote(str(courses))} start ", ["CS590OP_HW", "MATH421"]),
        ]:
            os.write(master, command.encode() + b"\t")
            read_until(expected)
            os.write(master, b"\x07")  # ZLE send-break; never execute the completed line.
            read_until(["READY> "])
    finally:
        proc.kill()
        proc.wait(timeout=5)
        os.close(master)
