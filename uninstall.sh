#!/usr/bin/env bash
# Removes only what install.sh linked into your home directory. Configuration, keys, model caches,
# notes and the project's own virtual environments stay.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
remove() {
    if [[ -L $1 && "$(readlink "$1")" == "$2" ]]; then
        rm -- "$1"
        echo "已移除$3：$1"
    elif [[ -e $1 || -L $1 ]]; then
        echo "不是本项目安装的$3，未删除：$1"
    fi
}
remove "${XDG_BIN_HOME:-$HOME/.local/bin}/lecture" "$project_dir/.venv/bin/lecture" " lecture 命令"
remove "$data_dir/zsh/site-functions/_lecture" "$project_dir/lecture_cli/completions/_lecture" "补全文件"
remove "$data_dir/applications/lecture.desktop" "$project_dir/.venv/share/applications/lecture.desktop" "桌面入口"
remove "$data_dir/icons/hicolor/scalable/apps/lecture-cli.svg" "$project_dir/lecture_cli/gui/lecture-cli.svg" "图标"

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/lecture-cli"
courses_dir=""
# Read the configuration without load(): that would migrate and rewrite an old file.
for python in "$project_dir/.venv/bin/python" "$project_dir/.venv-qwen/bin/python"; do
    if [[ -x $python ]]; then
        courses_dir="$(cd "$project_dir" && "$python" -c '
import json
from lecture_cli.config import config_path, migrate
print(migrate(json.loads(config_path().read_text())).get("courses_dir") or "")' 2>/dev/null || true)"
        break
    fi
done
echo
echo '以下内容没有删除，不再需要时可自行处理：'
echo "  配置与 key：$config_dir"
echo "  模型缓存：${HF_HUB_CACHE:-${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}/hub}"
echo "  笔记：${courses_dir:-见配置里的 courses_dir}（各课程的 LectureNotes 文件夹）"
echo "  运行记录：${XDG_STATE_HOME:-$HOME/.local/state}/lecture-cli"
echo "  程序与虚拟环境：$project_dir"
