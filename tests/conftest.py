"""Never let tests change the host's PipeWire volume or write to its run registry."""
import subprocess
import threading
from types import SimpleNamespace

import pytest

from lecture_cli import mic_gain


@pytest.fixture(autouse=True)
def fake_wpctl(monkeypatch):
    fake = SimpleNamespace(volume=0.85, muted=False, calls=[], node="alsa_input.fake-test-mic")

    def run(command, **kwargs):
        assert threading.current_thread() is threading.main_thread(), "wpctl ran in an audio callback"
        assert command[:1] == ["wpctl"]
        assert command[2] == mic_gain.SOURCE
        fake.calls.append(command)
        if command[1] == "set-volume":
            fake.volume = float(command[3])
        elif command[1] == "inspect":  # PLAN-GUI-5 R2.4: the source's node.name keys mic-levels.json.
            return SimpleNamespace(stdout=f'id 42, type PipeWire:Interface:Node\n  * node.name = "{fake.node}"\n')
        else:
            assert command[1] == "get-volume"
        return SimpleNamespace(stdout=f"Volume: {fake.volume}" + (" [MUTED]" if fake.muted else ""))

    # Replace only this module's subprocess reference, leaving other probes intact.
    monkeypatch.setattr(mic_gain, "subprocess", SimpleNamespace(
        run=run, CalledProcessError=subprocess.CalledProcessError,
        TimeoutExpired=subprocess.TimeoutExpired))
    return fake


@pytest.fixture(autouse=True)
def isolated_run_registry(tmp_path, monkeypatch):
    # Controllers started by tests, in-process or as children, record runs here.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state" / "lecture-cli" / "runs"
