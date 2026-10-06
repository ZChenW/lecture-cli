#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v uv >/dev/null || { echo '请先安装 uv。' >&2; exit 1; }
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    uv venv --python 3.12 "$project_dir/.venv"
fi
if [[ "${1:-}" == "--api" ]]; then
    uv pip install --python "$project_dir/.venv/bin/python" -e "$project_dir"
else
    uv pip install --python "$project_dir/.venv/bin/python" --torch-backend cpu -r "$project_dir/requirements.lock"
    uv pip install --python "$project_dir/.venv/bin/python" --no-deps -e "$project_dir"
fi
if [[ "${1:-}" == "--gpu" ]]; then
    uv pip install --python "$project_dir/.venv/bin/python" -r "$project_dir/requirements-gpu.lock"
fi
lecture_bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p "$lecture_bin_dir"
if [[ -e "$lecture_bin_dir/lecture" || -L "$lecture_bin_dir/lecture" ]]; then
    if [[ "$(readlink "$lecture_bin_dir/lecture" || true)" != "$project_dir/.venv/bin/lecture" ]]; then
        echo "已有其他 lecture 命令，未覆盖：$lecture_bin_dir/lecture" >&2
        exit 1
    fi
fi
ln -sfn "$project_dir/.venv/bin/lecture" "$lecture_bin_dir/lecture"
lecture_completion_dir="${XDG_DATA_HOME:-$HOME/.local/share}/zsh/site-functions"
mkdir -p "$lecture_completion_dir"
if [[ -e "$lecture_completion_dir/_lecture" || -L "$lecture_completion_dir/_lecture" ]]; then
    if [[ "$(readlink "$lecture_completion_dir/_lecture" || true)" != "$project_dir/lecture_cli/completions/_lecture" ]]; then
        echo "已有其他 lecture 补全文件，未覆盖：$lecture_completion_dir/_lecture" >&2
        exit 1
    fi
fi
ln -sfn "$project_dir/lecture_cli/completions/_lecture" "$lecture_completion_dir/_lecture"
echo "已安装：$lecture_bin_dir/lecture"
echo "zsh 补全：$lecture_completion_dir/_lecture（需将该目录加入 fpath 后运行 compinit）"
echo '运行 lecture doctor 检查环境，lecture start 选择课程。'
