#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
command -v uv >/dev/null || { echo '请先安装 uv。' >&2; exit 1; }
python="$project_dir/.venv-qwen/bin/python"
if [[ ! -x $python ]]; then
    uv venv --python 3.12 "$project_dir/.venv-qwen"
fi
uv pip install --python "$python" --torch-backend cu128 -r "$project_dir/requirements-qwen.lock"
project_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$project_dir/pyproject.toml")"
# uv rebuilds editable projects on every install; skip that when this version is already linked here.
info="$(uv pip show --python "$python" lecture-cli 2>/dev/null || true)"
if ! grep -qxF "Version: $project_version" <<<"$info" ||
        ! grep -qxF "Editable project location: $project_dir" <<<"$info"; then
    uv pip install --python "$python" --no-deps -e "$project_dir"
fi
uv pip check --python "$python"
echo 'Qwen GPU 环境已安装。运行 lecture prepare --asr-model qwen3-asr-1.7b 下载模型。'
echo '运行 lecture start --asr-model qwen3-asr-1.7b --asr-device cuda 使用 Qwen。'
