"""install.sh / uninstall.sh against a stub uv: profiles, aliases, ownership checks, idempotency."""
import os
from pathlib import Path
import pty
import re
import shutil
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = ["install.sh", "install-qwen.sh", "uninstall.sh", "pyproject.toml", "requirements.lock",
         "requirements-gpu.lock", "requirements-gui.lock", "requirements-qwen.lock",
         "lecture_cli/completions/_lecture", "lecture_cli/gui/lecture-cli.svg"]
TOOLS = ["bash", "dirname", "sed", "grep", "readlink", "ln", "mkdir", "cat", "mv", "rm", "env", "chmod"]
# Records every call; `uv venv` makes the interpreter, `uv pip install -e` records the editable install.
STUB_UV = r"""#!/bin/bash
printf '%s\n' "$*" >> "$UV_LOG"
if [[ $1 == venv ]]; then
    mkdir -p "${@: -1}/bin" && printf '#!/bin/bash\n' > "${@: -1}/bin/python" && chmod +x "${@: -1}/bin/python"
    : > "${@: -1}/bin/lecture"
elif [[ $1 == pip && $2 == show ]]; then
    [[ -f $3.editable || -f ${4%/bin/python}.editable ]] && cat "${4%/bin/python}.editable"
    [[ -f ${4%/bin/python}.editable ]]
elif [[ $1 == pip && $2 == install && " $* " == *" -e "* ]]; then
    project="${@: -1}"; venv="${4%/bin/python}"
    printf 'Name: lecture-cli\nVersion: %s\nEditable project location: %s\n' \
        "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$project/pyproject.toml")" "$project" > "$venv.editable"
fi
"""


@pytest.fixture
def env(tmp_path):
    project = tmp_path / "project dir"
    for name in FILES:
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, project / name)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        (tools / name).symlink_to(shutil.which(name))
    (tools / "uv").write_text(STUB_UV)
    (tools / "uv").chmod(0o755)
    home = tmp_path / "home"
    xdg = {name: tmp_path / "xdg" / name.lower() for name in ("CONFIG", "DATA", "STATE", "CACHE")}
    for path in [home, *xdg.values()]:
        path.mkdir(parents=True)
    variables = {"HOME": str(home), "PATH": str(tools), "UV_LOG": str(tmp_path / "uv.log"), "LANG": "C.UTF-8",
                 **{f"XDG_{name}_HOME": str(path) for name, path in xdg.items()}}
    return {"project": project, "home": home, "data": xdg["DATA"], "config": xdg["CONFIG"],
            "tools": tools, "log": tmp_path / "uv.log", "env": variables}


def run(env, script, *args, stdin=subprocess.DEVNULL):
    result = subprocess.run([str(env["project"] / script), *args], env=env["env"], stdin=stdin,
                            capture_output=True, text=True, timeout=30)
    return result


def calls(env):
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def targets(env):
    data = env["data"]
    return {"bin": env["home"] / ".local/bin/lecture", "completion": data / "zsh/site-functions/_lecture",
            "desktop": data / "applications/lecture.desktop", "icon": data / "icons/hicolor/scalable/apps/lecture-cli.svg"}


def test_api_profile_installs_the_gui_lock_and_desktop_entry(env):
    result = run(env, "install.sh", "--profile", "api")
    assert result.returncode == 0, result.stderr
    project, python = env["project"], f"{env['project']}/.venv/bin/python"
    assert calls(env) == [f"venv --python 3.12 {project}/.venv",
                          f"pip show --python {python} lecture-cli",
                          f"pip install --python {python} -e {project}",
                          f"pip install --python {python} -r {project}/requirements-gui.lock"]
    paths = targets(env)
    assert os.readlink(paths["bin"]) == f"{project}/.venv/bin/lecture"
    assert os.readlink(paths["completion"]) == f"{project}/lecture_cli/completions/_lecture"
    assert os.readlink(paths["icon"]) == f"{project}/lecture_cli/gui/lecture-cli.svg"
    entry = paths["desktop"].read_text()
    assert f'Exec="{project}/.venv/bin/lecture" gui\n' in entry and "Icon=lecture-cli\n" in entry
    assert "桌面入口" in result.stdout and "lecture gui" in result.stdout


@pytest.mark.parametrize("old, new", [(["--api"], ["--profile", "api"]), (["--gpu"], ["--profile=gpu"])])
def test_old_flags_are_aliases(env, tmp_path, old, new):
    assert run(env, "install.sh", *old).returncode == 0
    first = calls(env)
    for path in [env["project"] / ".venv", env["log"], *targets(env).values()]:
        shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    (env["project"] / ".venv.editable").unlink()
    assert run(env, "install.sh", *new).returncode == 0
    assert calls(env) == first


def test_cpu_and_gpu_profiles_keep_the_locked_local_install(env):
    assert run(env, "install.sh", "--gpu", "--no-gui").returncode == 0
    project, python = env["project"], f"{env['project']}/.venv/bin/python"
    assert calls(env)[1:] == [f"pip install --python {python} --torch-backend cpu -r {project}/requirements.lock",
                              f"pip show --python {python} lecture-cli",
                              f"pip install --python {python} --no-deps -e {project}",
                              f"pip install --python {python} -r {project}/requirements-gpu.lock"]
    assert not targets(env)["desktop"].exists() and not targets(env)["icon"].exists()


def test_window_installs_the_same_pywebview_as_the_extra(env):
    extra = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["gui-window"]
    assert run(env, "install.sh", "--profile", "api", "--with-window").returncode == 0
    assert calls(env)[-1] == f"pip install --python {env['project']}/.venv/bin/python {extra[0]}"
    result = run(env, "install.sh", "--profile", "api", "--with-window", "--no-gui")
    assert result.returncode == 2 and "--no-gui" in result.stderr


def test_with_qwen_runs_install_qwen(env):
    assert run(env, "install.sh", "--profile", "cpu", "--with-qwen").returncode == 0
    qwen = f"{env['project']}/.venv-qwen"
    assert f"venv --python 3.12 {qwen}" in calls(env)
    assert f"pip check --python {qwen}/bin/python" in calls(env)


def test_second_run_changes_nothing(env):
    assert run(env, "install.sh", "--profile", "api").returncode == 0
    paths = targets(env)
    entry = env["project"] / ".venv/share/applications/lecture.desktop"
    before = {path: os.lstat(path) for path in [*paths.values(), entry]}
    env["log"].unlink()
    second = run(env, "install.sh", "--profile", "api")
    assert second.returncode == 0, second.stderr
    assert not any(" -e " in call or call.startswith("venv") for call in calls(env)), calls(env)
    # Without the editable rebuild the project's dependencies are still brought up to date.
    project, python = env["project"], f"{env['project']}/.venv/bin/python"
    assert f"pip install --python {python} -r {project}/pyproject.toml" in calls(env)
    for path, stat in before.items():
        assert (os.lstat(path).st_ino, os.lstat(path).st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns), path


def test_foreign_files_are_never_overwritten(env):
    paths = targets(env)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("someone else's\n")
    paths["icon"].unlink()
    paths["icon"].symlink_to("/usr/share/icons/other.svg")
    result = run(env, "install.sh", "--profile", "api")
    assert result.returncode == 1
    for label, path in [("lecture 命令", paths["bin"]), ("lecture 补全文件", paths["completion"]),
                        ("lecture 桌面入口", paths["desktop"]), ("lecture 图标", paths["icon"])]:
        assert f"已有其他 {label}，未覆盖：{path}" in result.stderr
    assert "没有做任何改动" in result.stderr and calls(env) == []
    assert all(path.read_text() == "someone else's\n" for name, path in paths.items() if name != "icon")
    assert os.readlink(paths["icon"]) == "/usr/share/icons/other.svg"
    # Without the GUI the desktop entry and icon are not this install's business.
    for name in ("bin", "completion"):
        paths[name].unlink()
    assert run(env, "install.sh", "--profile", "api", "--no-gui").returncode == 0


def test_profile_is_never_chosen_silently(env):
    result = run(env, "install.sh")
    assert result.returncode == 2 and "--profile cpu" in result.stderr and calls(env) == []
    (env["tools"] / "nvidia-smi").write_text("#!/bin/bash\nexit 0\n")
    (env["tools"] / "nvidia-smi").chmod(0o755)
    result = run(env, "install.sh")
    assert result.returncode == 2 and "检测到 NVIDIA GPU" in result.stderr and "--profile gpu" in result.stderr
    assert run(env, "install.sh", "--profile", "tpu").returncode == 2


@pytest.mark.parametrize("answer, local", [(b"\n", True), (b"api\n", False)])
def test_interactive_prompt_confirms_the_suggestion(env, answer, local):
    master, slave = pty.openpty()
    proc = subprocess.Popen([str(env["project"] / "install.sh")], env=env["env"], stdin=slave,
                            stdout=slave, stderr=slave)
    os.close(slave)
    output = b""
    while "建议安装 cpu".encode() not in output:
        output += os.read(master, 4096)
    os.write(master, answer)
    assert proc.wait(timeout=30) == 0
    os.close(master)
    assert any("--torch-backend cpu" in call for call in calls(env)) == local


def test_desktop_entry_quotes_unusual_paths(env, tmp_path):
    odd = tmp_path / 'odd $dir "x" 100%'
    env["project"].rename(odd)
    env["project"] = odd
    assert run(env, "install.sh", "--profile", "api").returncode == 0
    exec_line = re.search(r"^Exec=(.*)$", targets(env)["desktop"].read_text(), re.M)[1]
    assert exec_line == f'"{tmp_path}/odd \\\\$dir \\\\"x\\\\" 100%%/.venv/bin/lecture" gui'


def test_uninstall_removes_only_our_links_and_keeps_user_data(env):
    assert run(env, "install.sh", "--profile", "api").returncode == 0
    keep = [env["config"] / "lecture-cli/config.json", env["config"] / "lecture-cli/notes-api-key",
            env["home"] / "courses/MATH421/LectureNotes/note.md",
            Path(env["env"]["XDG_CACHE_HOME"]) / "huggingface/hub/models--x/blob"]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("kept\n")
    paths = targets(env)
    paths["icon"].unlink()
    paths["icon"].write_text("someone else's\n")
    result = run(env, "uninstall.sh")
    assert result.returncode == 0, result.stderr
    assert not any(os.path.lexists(paths[name]) for name in ("bin", "completion", "desktop"))
    assert paths["icon"].read_text() == "someone else's\n" and f"未删除：{paths['icon']}" in result.stdout
    assert all(path.read_text() == "kept\n" for path in keep)
    assert (env["project"] / ".venv/bin/python").exists()
    for location in (f"{env['config']}/lecture-cli", f"{env['env']['XDG_CACHE_HOME']}/huggingface/hub"):
        assert location in result.stdout
    assert run(env, "uninstall.sh").returncode == 0  # Nothing left to remove is not an error.
