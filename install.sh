#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
usage() {
    cat <<'USAGE'
用法：./install.sh [--profile api|cpu|gpu] [--with-qwen] [--no-gui] [--with-window]
  --profile api   云端转录：只装基础依赖，不装本地模型和 torch
  --profile cpu   本地转录，在 CPU 上运行
  --profile gpu   本地转录，另装项目内的 NVIDIA 运行库
  --with-qwen     另装 Qwen GPU 环境（调用 ./install-qwen.sh）
  --with-gui      图形界面依赖和桌面入口（默认安装）
  --no-gui        不装图形界面
  --with-window   另装 pywebview，lecture gui 在独立窗口中打开
旧用法 --api、--gpu 分别等同于 --profile api、--profile gpu。
USAGE
}
profile="" qwen=0 gui=1 window=0
while (($#)); do
    case $1 in
        --profile) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; profile=$2; shift ;;
        --profile=*) profile=${1#*=} ;;
        --api) profile=api ;;
        --gpu) profile=gpu ;;
        --with-qwen) qwen=1 ;;
        --with-gui) gui=1 ;;
        --no-gui) gui=0 ;;
        --with-window) window=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done
if ((window && !gui)); then
    echo '--with-window 用于图形界面，不能与 --no-gui 同时使用。' >&2
    exit 2
fi
command -v uv >/dev/null || { echo '请先安装 uv。' >&2; exit 1; }
if [[ -z $profile ]]; then
    # Suggest, never pick silently: a wrong profile costs gigabytes or leaves transcription on the CPU.
    if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
        suggested=gpu found='检测到 NVIDIA GPU'
    else
        suggested=cpu found='未检测到可用的 NVIDIA GPU'
    fi
    if [[ ! -t 0 ]]; then
        echo "${found}，建议运行 ./install.sh --profile ${suggested}；只用云端转录时用 --profile api。" >&2
        exit 2
    fi
    read -r -p "${found}，建议安装 ${suggested}。按回车确认，或输入 api、cpu、gpu：" answer
    profile=${answer:-$suggested}
fi
case $profile in
    api|cpu|gpu) ;;
    *) echo "未知安装配置：${profile}（可选 api、cpu、gpu）" >&2; exit 2 ;;
esac

python="$project_dir/.venv/bin/python"
lecture_bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
lecture_completion_dir="$data_dir/zsh/site-functions"
applications_dir="$data_dir/applications"
icon_dir="$data_dir/icons/hicolor/scalable/apps"
desktop_source="$project_dir/.venv/share/applications/lecture.desktop"
icon_source="$project_dir/lecture_cli/gui/lecture-cli.svg"

# Each entry: link path, the file it must point to, and what it is. A path that exists and does
# not point there belongs to someone else; check them all before changing anything.
targets=("$lecture_bin_dir/lecture" "$project_dir/.venv/bin/lecture" "lecture 命令"
         "$lecture_completion_dir/_lecture" "$project_dir/lecture_cli/completions/_lecture" "lecture 补全文件")
if ((gui)); then
    targets+=("$applications_dir/lecture.desktop" "$desktop_source" "lecture 桌面入口"
              "$icon_dir/lecture-cli.svg" "$icon_source" "lecture 图标")
fi
conflict=0
for ((i = 0; i < ${#targets[@]}; i += 3)); do
    if [[ -e ${targets[i]} || -L ${targets[i]} ]]; then
        if [[ "$(readlink "${targets[i]}" || true)" != "${targets[i + 1]}" ]]; then
            echo "已有其他 ${targets[i + 2]}，未覆盖：${targets[i]}" >&2
            conflict=1
        fi
    fi
done
if ((conflict)); then
    echo '没有做任何改动。请移走或改名上面的文件后重新运行。' >&2
    exit 1
fi

if [[ ! -x $python ]]; then
    uv venv --python 3.12 "$project_dir/.venv"
fi
project_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$project_dir/pyproject.toml")"
editable_installed() {
    # uv rebuilds editable projects on every install; skip that when this version is already linked here.
    local info
    info="$(uv pip show --python "$python" lecture-cli 2>/dev/null)" || return 1
    grep -qxF "Version: $project_version" <<<"$info" &&
        grep -qxF "Editable project location: $project_dir" <<<"$info"
}
if [[ $profile == api ]]; then
    if editable_installed; then
        # Dependencies only, so ones added since the last run still arrive without rebuilding the project.
        uv pip install --python "$python" -r "$project_dir/pyproject.toml"
    else
        uv pip install --python "$python" -e "$project_dir"
    fi
else
    uv pip install --python "$python" --torch-backend cpu -r "$project_dir/requirements.lock"
    editable_installed || uv pip install --python "$python" --no-deps -e "$project_dir"
fi
if [[ $profile == gpu ]]; then
    uv pip install --python "$python" -r "$project_dir/requirements-gpu.lock"
fi
if ((gui)); then
    uv pip install --python "$python" -r "$project_dir/requirements-gui.lock"
fi
if ((window)); then
    # Same requirement as the gui-window extra in pyproject.toml.
    uv pip install --python "$python" "pywebview[qt]>=6,<7"
fi

link() {
    [[ "$(readlink "$2" || true)" == "$1" ]] || ln -sfn "$1" "$2"
}
mkdir -p "$lecture_bin_dir" "$lecture_completion_dir"
link "$project_dir/.venv/bin/lecture" "$lecture_bin_dir/lecture"
link "$project_dir/lecture_cli/completions/_lecture" "$lecture_completion_dir/_lecture"
echo "已安装：$lecture_bin_dir/lecture"
echo "zsh 补全：$lecture_completion_dir/_lecture（需将该目录加入 fpath 后运行 compinit）"
if ((gui)); then
    # Desktop Entry quoting: \ " ` $ are escaped inside quotes, then every backslash once more; % doubles.
    exec_path="$(printf '%s' "$project_dir/.venv/bin/lecture" | sed -e 's/\\/\\\\\\\\/g' -e 's/["`$]/\\\\&/g' -e 's/%/%%/g')"
    desktop="[Desktop Entry]
Type=Application
Name=Lecture
Comment=课堂转录与中文笔记
Exec=\"$exec_path\" gui
Icon=lecture-cli
Terminal=false
Categories=Education;
StartupNotify=true"
    mkdir -p "${desktop_source%/*}" "$applications_dir" "$icon_dir"
    if [[ "$(cat "$desktop_source" 2>/dev/null || true)" != "$desktop" ]]; then
        printf '%s\n' "$desktop" > "$desktop_source.tmp"
        mv "$desktop_source.tmp" "$desktop_source"
    fi
    link "$desktop_source" "$applications_dir/lecture.desktop"
    link "$icon_source" "$icon_dir/lecture-cli.svg"
    echo "桌面入口：$applications_dir/lecture.desktop"
fi
if ((qwen)); then
    "$project_dir/install-qwen.sh"
fi
if ((gui)); then
    echo '运行 lecture gui 打开图形界面并完成首次设置；也可以用 lecture doctor 检查环境，lecture start 选择课程。'
else
    echo '运行 lecture doctor 检查环境，lecture start 选择课程。'
fi
