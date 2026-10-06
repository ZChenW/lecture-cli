#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v uv >/dev/null || { echo '请先安装 uv。' >&2; exit 1; }
if [[ ! -x "$project_dir/.venv-qwen/bin/python" ]]; then
    uv venv --python 3.12 "$project_dir/.venv-qwen"
fi
uv pip install --python "$project_dir/.venv-qwen/bin/python" --torch-backend cu128 -r "$project_dir/requirements-qwen.lock"
uv pip install --python "$project_dir/.venv-qwen/bin/python" --no-deps -e "$project_dir"
uv pip check --python "$project_dir/.venv-qwen/bin/python"
echo 'Qwen GPU 环境已安装。运行 lecture prepare --asr-model qwen3-asr-1.7b 下载模型。'
echo '运行 lecture start --asr-model qwen3-asr-1.7b --asr-device cuda 使用 Qwen。'
