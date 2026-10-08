# Lecture CLI

课堂录音实时转成文字（本机 WhisperLiveKit，或云端转录服务），可在下课后用 Qwen 离线重转录，再由独立进程调用笔记服务（默认 DeepSeek，可换成任何 OpenAI 兼容服务）生成中文课堂笔记。图形界面或终端显示转录及状态，Markdown 自动保存到课程文件夹，可用 Obsidian 查看，也可以在图形界面里阅读。

## 需要什么

- **Linux**（x86_64）。
- **[uv](https://docs.astral.sh/uv/)**：安装脚本用它建立项目自己的 Python 3.12 环境；本机没有 Python 3.12 时 uv 会自动下载一份。
- **FFmpeg** 和 **PortAudio**：Arch 系统包为 `uv ffmpeg portaudio`；Debian/Ubuntu 为 `ffmpeg libportaudio2`（uv 按其官方说明安装）。
- **中文字体**：Noto CJK，含衬线的 Noto Serif CJK（笔记阅读界面的标题和正文用它）。Arch 为 `noto-fonts-cjk`，Debian/Ubuntu 为 `fonts-noto-cjk`。
- 可选 **NVIDIA GPU** 和正常工作的驱动：本地转录更快；Qwen 识别与课后校正需要它。
- 可选 **WirePlumber**（`wpctl`）：自动降低削波的麦克风音量。
- 图形界面默认用浏览器的应用窗口打开（Chromium、Google Chrome、Brave 或 Edge 之一，都没有时用默认浏览器）；也可以安装独立窗口（见下）。

项目使用独立 Python 3.12 环境。本地安装的 PyTorch 用于 CPU 上的语音活动检测；Whisper 的 GPU 推理由 CTranslate2 执行，无需替换为 GPU PyTorch。

## 安装

```sh
git clone https://github.com/ZChenW/lecture-cli.git && cd lecture-cli
./install.sh --profile cpu     # 选一个：api、cpu 或 gpu，见下表
lecture gui                    # 打开图形界面，按向导完成设置
```

| 安装配置 | 适合 | 装什么 |
|---|---|---|
| `--profile api` | 没有 GPU、CPU 较弱，用云端转录 | 只装基础依赖，不装本地模型和 torch |
| `--profile cpu` | 本地转录，在 CPU 上运行 | `requirements.lock` 里的全部本地依赖（CPU 版 PyTorch） |
| `--profile gpu` | 有 NVIDIA GPU 的本地转录 | 同 cpu，另装 `requirements-gpu.lock` 里的 CUDA 运行库 |

不写 `--profile` 时，脚本会检测 `nvidia-smi`，给出建议（有可用 GPU 建议 `gpu`，否则 `cpu`），在终端里等你按回车确认或改选；不在终端里运行时直接退出并打印建议，不会替你选。

其他选项：

- `--with-gui`（默认开启）：图形界面依赖（`requirements-gui.lock`），并写入桌面入口 `~/.local/share/applications/lecture.desktop` 和图标；`--no-gui` 关闭。
- `--with-window`：另装 pywebview，`lecture gui` 在独立窗口中打开，而不是浏览器的应用窗口。
- `--with-qwen`：另装 Qwen GPU 环境，等同于再运行 `./install-qwen.sh`（见下文“Qwen 语音识别”）。
- 旧用法 `./install.sh --api`、`./install.sh --gpu` 仍然可用，分别等同于 `--profile api`、`--profile gpu`。

安装脚本把 `lecture` 链接到 `~/.local/bin`（设置了 `XDG_BIN_HOME` 时用它），把 zsh 补全链接到 `${XDG_DATA_HOME:-$HOME/.local/share}/zsh/site-functions`。这些位置已有不属于本项目的同名文件时，脚本列出它们并退出，不覆盖、不做任何改动。重复运行是安全的：已装好的依赖不会重装，已有的链接和桌面入口不会重写。

代码固定 WhisperLiveKit 提交 `363e4f6d029694d9c81ae548beddd9d3c88a3637`，安装版本记录在 `requirements.lock`。项目可移动，但移动后需重新运行安装脚本以更新虚拟环境入口。

GPU 运行库版本记录在 `requirements-gpu.lock`，放在项目虚拟环境中；程序只为转录子进程设置库搜索路径，不修改全局环境或系统驱动。GPU 模式需要正常工作的 NVIDIA 驱动。`CUDA_VISIBLE_DEVICES` 的用户设置会保留。

上课前可先下载语音模型并检查环境：

```sh
lecture prepare          # 上课前下载 base.en 模型
lecture doctor
```

卸载：`./uninstall.sh` 只移除上面的命令链接、补全文件、桌面入口和图标。配置与 key、模型缓存、笔记都不删除，结束时会打印它们的位置；项目目录（含虚拟环境）也保留，不再需要时可自行删除。

## 图形界面

```sh
lecture gui              # 打开图形界面；界面已在运行时再打开一个指向它的窗口
lecture gui --no-window  # 只启动本机界面服务并打印地址
```

第一次打开时有五步向导：课程目录 → 转录方式（本地或云端）→ 笔记服务 → 麦克风 → 检查。检查页列出环境检查的结果，失败项会指向能修复它的那一步；也可以在这一步试运行一次演示（自造文字，不录音，会消耗少量笔记服务额度）。之后的首页列出课程和笔记，可以开始录制、阅读笔记、修改设置。

界面服务只监听本机回环地址，并用一次性令牌保护；录制在独立的后台进程里进行，关闭窗口不会中断录制，再次运行 `lecture gui` 会回到正在进行的课。安装了 pywebview（`--with-window`）时使用独立窗口，否则依次尝试 Chromium、Google Chrome、Brave、Edge 的应用窗口模式，都没有时用默认浏览器打开。

## 命令行用法

本机安装完成后：

```sh
lecture courses           # 查看已有课程
lecture doctor            # 检查本地环境与服务连接（只请求 /models），不录音、不生成笔记
lecture diagnose-asr MATH421 --seconds 30  # 同一段音频对比两个 ASR，不调用 DeepSeek
lecture start             # 选择课程，开始录制
lecture start math421     # 直接选择 MATH421
lecture start cs687       # 自动匹配 CS687_HW
```

默认优先使用 NVIDIA GPU，录制界面会显示实际的“识别设备”。也可以明确指定：

```sh
lecture start cs590op --asr-device cuda  # 强制 GPU，不可用时明确报错
lecture start cs590op --asr-device cpu   # 使用 CPU
lecture doctor --asr-device cuda        # 检查 GPU 可见性与运行库，不录音
```

`--device` 仍然表示麦克风；`--asr-device` 表示语音模型的计算设备。`auto` 在没有可见 GPU 或缺少运行库时回退 CPU 并显示提示；模型加载或推理失败会报错，不在录制中静默切换设备。

终端按 **P** 暂停/继续，按 **Q** 或 **Ctrl+C** 结束。结束后会等待转录收尾，再根据完整转录编写详细课堂笔记，然后清理本次临时目录。长课可能需要数分钟整理，界面显示章节进度。关闭终端或收到 SIGTERM/SIGHUP 也会尝试收尾；终端关闭时需要给后台进程留出退出时间。

`start` 和 `demo` 的 `--headless` 不使用终端界面运行，需要指定课程；图形界面就是这样启动录制的。

首次配置或更换麦克风：

```sh
lecture setup
lecture devices
lecture start math421 --device pipewire
```

`setup` 隐藏输入 API key，存入权限为 600 的配置文件 `notes-api-key`；环境变量 `LECTURE_NOTES_API_KEY` 优先。旧的变量 `DEEPSEEK_API_KEY` 和旧文件 `api-key` 仍可使用，优先级排在新名称之后。已有可用环境变量时不必运行 setup。不要把 key 写进命令参数。实际课堂文字会发送到 DeepSeek，使用 API 额度。

可选参数：

```sh
lecture start math421 --interval 60 --context /path/to/context.md
lecture start math421 --asr-model small.en
lecture start math421 --language zh --asr-model small
lecture --courses-dir /path/to/courses start course-name
```

`start/demo --context` 提供笔记生成背景（不超过 12,000 字符），不再传给 ASR。程序自动读取所选课程目录的 `glossary.json`，将英文术语给实时/离线 ASR，中英对应给笔记模型。例如：

```json
[
  {"term": "planar isotopy", "translation": "平面同痕", "source": "讲义 p.31"},
  {"term": "linking number", "translation": "链接数", "source": "讲义 p.36"}
]
```

`source` 可省略。英文术语合并后本地模式最多 1,000 字符、API 模式最多 600 字符，超出会在录音启动前提示缩减；不会静默截断。没有词表也可正常录音。程序不会自动读取课程目录里的作业或 PDF，词表注明来源不代表已经加载讲义。

### 自动降低麦克风削波音量

`lecture start` 默认启用 `auto_gain`，本地与云端后端都适用。仅调节 PipeWire 默认源（未指定 `--device`，或使用 `pipewire`、`default`、`pulse`）；数字编号、具体硬件名称、`--audio-file` 和 demo 不调节。每累计约 2 秒收音，若至少 0.1% 采样达到削波阈值（绝对值 ≥ 0.999），就降低 12 dB：按 PipeWire 立方刻度将音量乘以约 0.631，最低 10%。每次调整后跳过一个窗口等待生效，暂停期间不评估；到下限仍削波时提示检查硬件增益（Mic Boost）。

第一版**只降不升**，避免安静教室里误升音量再次削波。结束后**不恢复音量**，由 WirePlumber 记住调整后的值。界面的“麦克风音量”和结束后的控制台显示最后一次提示。缺少 `wpctl` 或无法读取音量时给出说明，录制照常；已静音时只提示，不自动取消静音。`lecture doctor` 只读检查默认源音量，不修改它。

本次关闭：`lecture start MATH421 --no-auto-gain`；长期关闭：在 `config.json` 中设置 `"auto_gain": false`。可用 `--auto-gain` 覆盖配置重新启用。

### 云端 API 转录（无 GPU / 弱 CPU）

```sh
./install.sh --profile api              # 只装基础依赖，不装本地模型和 torch
export LECTURE_ASR_API_KEY='你的转录服务 key'
export LECTURE_NOTES_API_KEY='你的笔记服务 key'
lecture doctor --asr-backend api         # 验证转录服务，不录音
lecture start MATH421 --asr-backend api
lecture start MATH421 --asr-backend api --asr-api-model whisper-large-v3-turbo
lecture start MATH421 --asr-backend api --audio-file /path/to/lecture.wav --fast
```

转录 key 优先读取 `LECTURE_ASR_API_KEY`，否则读取配置目录的 `asr-api-key` 文件（默认 `~/.config/lecture-cli/asr-api-key`，建议权限 600）。DeepSeek 仍使用自己的 key；两个 key 分别只传给转录进程和笔记进程，不写入会话记录。轻量安装无需运行 `prepare`；已有环境里的本地依赖不会被卸载。

默认仍为 `local`。可在配置目录的 `config.json` 中设置以下字段；命令行的后端和云端模型只覆盖本次运行：

```json
{
  "asr_backend": "api",
  "asr_api_base": "https://api.groq.com/openai/v1",
  "asr_api_model": "whisper-large-v3-turbo"
}
```

服务须兼容 `POST {asr_api_base}/audio/transcriptions`，并提供 `GET {asr_api_base}/models`。默认使用 Groq；开始录音前会验证服务与 key，`doctor --asr-backend api` 也会实际请求 `/models`。课堂音频会上传至转录服务，识别文字随后发送到 DeepSeek，分别消耗服务额度。

音频攒满 30 秒后，在 20–30 秒内的低音量位置切分，按序上传 WAV；不重叠，结束时处理剩余音频。近静音片段不上传。`--language auto` 不传语言参数，其他值直接传入。课程 `glossary.json` 产生的英文词表最多 **600 字符**，超出会在启动入口报错；每次请求追加上一条转录的末尾，最多 200 字符。高重复率文字标记为“疑似重复，待核对”。

网络错误、超时、429 和 5xx 会以 5 秒起、最多 60 秒的退避间隔重试，麦克风继续写入已有的 64 MiB 暂存；其他 4xx 直接报错。停止后的处理受现有收尾时限约束。API 模式强制关闭课后校正，不建立音频归档；转录、中文笔记和附件仍沿用现有保存与清理流程，界面“识别设备”显示“云端 API”。

本阶段仅完成假 HTTP 服务和定向测试；真实服务兼容性、课堂准确率、麦克风收音及 X230 性能须实机验证。

### Qwen 语音识别

支持 `qwen3-asr-1.7b` 和 `qwen3-asr-0.6b`。Qwen 在本机 GPU 上做转录，笔记仍使用现有 DeepSeek key 和模型，不需要 Qwen API key。

```sh
./install-qwen.sh                            # 安装独立 .venv-qwen GPU 环境
lecture prepare --asr-model qwen3-asr-1.7b    # 提前下载权重
lecture doctor --asr-model qwen3-asr-1.7b --asr-device cuda
lecture start --asr-model qwen3-asr-1.7b --asr-device cuda
lecture models                              # 选择并保存默认语音模型
```

需要减小模型时，先 `lecture prepare --asr-model qwen3-asr-0.6b`，再在 `lecture models` 中选择它。命令行的 `--asr-model` 只影响本次运行；`models` 会保存默认选择。两种 Qwen 名称也支持 Tab 补全。原有 Whisper 模型仍可选择。

Qwen 使用当前固定版本 WhisperLiveKit 的 `qwen3-streaming` 后端，通过 Transformers 维护流式音频上下文并确认稳定文字；GPU 精度为 BF16。该路径需要 GPU PyTorch，独立安装以保留原来的 Whisper 环境。这里只替换转录进程，录音缓冲、暂停、收尾和 `/tmp` 清理共用原有流程。

必须指定课堂语言（默认 `en`，中文可用 `--language zh`），这一流式后端不使用 `--language auto`。时间戳是估算值。模型初始化和首次确认文字需要时间；实时积压、文字确认延迟和模型加载耗时是不同指标。

### 实时 Whisper + 课后离线 Qwen

本机已配置 `large-v3-turbo` 实时转录、`pipewire` 麦克风和课后校正，直接运行 `lecture start MATH421` 即可。DeepSeek 仍使用原有模型与 key。

```sh
lecture start MATH421                         # 使用本机已保存的双阶段配置
lecture start MATH421 --no-refine             # 本次跳过课后重转录，也不建立整堂音频暂存
lecture start MATH421 --asr-model large-v3-turbo --refine  # 显式启用
```

其他安装默认不启用校正；需要先安装 `.venv-qwen`，并用 `lecture prepare --asr-model qwen3-asr-1.7b` 缓存权重。课后阶段禁止自动联网下载权重，模型不可用时回退实时转录。

按 Q 或 Ctrl+C 下课后，程序先完成实时转录并退出 Whisper 进程、释放显存，然后只加载一次官方 Qwen 离线模型。按 20–30 秒分段，优先在低音量位置切分；不重叠、不跳过末尾音频。详细笔记读取整套离线结果，随堂记录保留原版。引用以 `live-L…` / `refined-L…` 区分版本，链接到完整原文；离线时间戳是音频段范围，不是词级对齐。

离线阶段有进度显示，会增加下课后的等待时间；再次按 Q 或 Ctrl+C 可跳过校正，随后仍会整理并保存实时文字。任一分段失败、有声片段识别为空、超时、暂存不完整或被取消时，整份最终笔记回退实时原文并标明回退，不混用两套编号。离线模型也可能识别错误，数学公式仍须核对。

启用时额外暂存本次已输入 ASR 的 16 kHz 单声道 PCM，约 **110 MiB/小时**，上限 **512 MiB（约 4 小时 40 分钟）**，暂停期间不录入。达到上限或暂存写入失败不打断实时识别，但本次不再采用离线结果。文件仅位于私有 `/tmp`；若 `/tmp` 是 tmpfs，这会占用内存/交换空间。笔记成功保存后自动清理；目标目录不可写时保留暂存等待恢复。

### zsh Tab 补全

本机已接入用户补全目录。打开新终端后，输入 `lecture ` 按 Tab 可列出子命令；`lecture start ` 后按 Tab 可列出课程；`lecture start --asr-device ` 后按 Tab 可选择 `auto`、`cuda`、`cpu`。课程列表动态读取配置中的课程目录，支持 `--courses-dir` 指定其他目录。

已有终端可执行一次以下命令立即启用：

```zsh
autoload -Uz _lecture
compdef _lecture lecture
```

安装脚本将 `_lecture` 链接到 `${XDG_DATA_HOME:-$HOME/.local/share}/zsh/site-functions`。其他电脑若未配置该目录，需要在 `.zshrc` 的 `compinit` **之前**添加：

```zsh
fpath=("${XDG_DATA_HOME:-$HOME/.local/share}/zsh/site-functions" $fpath)
autoload -Uz compinit
compinit
```

补全也覆盖 `lecture gui` 及其 `--no-window`，以及 `start`、`demo` 的 `--headless`。补全不会启动录制、调用 API 或恢复旧会话。

### 演示与已有音频

```sh
lecture demo math421
```

使用自造的英文课堂文字调用真实 DeepSeek，**不会打开麦克风**，在对应课程中生成带“演示”标记的笔记。

```sh
lecture start math421 --audio-file /path/to/lecture.wav
lecture start math421 --audio-file /path/to/lecture.wav --fast
```

已有音频默认按实时速度输入，`--fast` 尽快处理。用户提供的原始文件不会被删除。音频输入与麦克风复用同一 WhisperLiveKit 流式处理和笔记链路。

### 课堂收音与 ASR 对比诊断

课堂转录出现乱码或漏句时，先让两个语音模型识别同一段实际环境音频：

```bash
lecture diagnose-asr MATH421 --seconds 30
```

默认依次运行 `qwen3-asr-1.7b` 和 `large-v3-turbo`，不会调用 DeepSeek，也不会生成或修改 `LectureNotes`。结果保存在课程目录的 `ASRDiagnostics/时间戳-随机码/`：

- `report.md`：音频电平、近静音与削波比例，以及模型运行状态；
- `metrics.json`：机器可读指标；
- `qwen3-asr-1.7b.txt`、`large-v3-turbo.txt`：两份带时间戳的原始转录。

诊断 WAV 默认只存在于权限受限的 `/tmp`，两个模型处理结束后自动删除。需要人工复听或以后重跑时，显式使用 `--keep-audio`，此时截取后的 `audio.wav` 会保存在诊断目录：

```bash
lecture diagnose-asr MATH421 --seconds 45 --keep-audio
```

也可以比较已有录音，不打开麦克风：

```bash
lecture diagnose-asr MATH421 --audio-file /path/to/sample.wav --seconds 60
```

默认按实时速度重放，以贴近课堂路径；对已有音频可加 `--fast` 缩短等待。可用 `--model-a`、`--model-b` 更换任一模型。诊断命令的 `--context` 是 ASR 短词表，最多 1,000 字符，与 `start --context` 的笔记背景用途不同。若两份转录都很差且报告显示大面积近静音，应先改善麦克风距离或输入设备；模型结果不同则用完整句、术语和漏句情况做人工比较。

## 配置项参考

配置文件是配置目录（默认 `~/.config/lecture-cli/`，遵循 `XDG_CONFIG_HOME`）里的 `config.json`。图形界面的向导和设置页、`lecture setup`、`lecture models` 都会写它，也可以手工编辑；没写的键取默认值，未知键原样保留。旧版（没有 `config_version`）的文件首次读取时自动升级，原文件另存为 `config.json.v1.bak`。

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `config_version` | int | `2` | 配置格式版本 |
| `courses_dir` | str 或 null | `null` | 课程根目录，没有默认值；其下每个文件夹是一门课 |
| `notes_provider` | str | `"deepseek"` | 笔记服务预设：`deepseek`、`openai`、`custom` |
| `notes_api_base` | str | `"https://api.deepseek.com"` | 笔记服务地址（OpenAI 兼容，须以 `http://` 或 `https://` 开头） |
| `notes_model` | str | `"deepseek-flash"` | 笔记模型名称，不能为空 |
| `notes_extra_body` | object | `{"thinking": {"type": "disabled"}}` | 追加到每个笔记请求体里的服务专有字段 |
| `asr_backend` | `"local"` 或 `"api"` | `"local"` | 本地转录或云端转录 |
| `asr_model` | str | `"base.en"` | 本地语音模型（`lecture models` 查看全部） |
| `asr_device` | `"auto"`/`"cuda"`/`"cpu"` | `"auto"` | 本地语音模型的计算设备 |
| `asr_provider` | str | `"groq"` | 云端转录预设：`groq`、`openai`、`custom` |
| `asr_api_base` | str | `"https://api.groq.com/openai/v1"` | 云端转录服务地址 |
| `asr_api_model` | str | `"whisper-large-v3-turbo"` | 云端转录模型；`asr_backend` 为 `api` 时不能为空 |
| `language` | str | `"en"` | 课堂语言，如 `en`、`zh`、`auto` |
| `interval` | number | `60` | 随堂笔记检查间隔（秒，1–3600） |
| `device` | int/str/null | `null` | 麦克风编号或名称；`null` 为系统默认 |
| `refine` | bool | `false` | 下课后用 Qwen 离线重转录 |
| `refine_model` | str | `"qwen3-asr-1.7b"` | 课后校正模型：`qwen3-asr-1.7b` 或 `qwen3-asr-0.6b` |
| `auto_gain` | bool | `true` | 自动降低削波的默认麦克风音量 |
| `qwen_python` | str 或 null | `null` | Qwen 环境的 Python 路径；`null` 时用项目里的 `.venv-qwen` |
| `file_manager` | str 或 null | `null` | 图形界面“在文件夹中显示”的回退文件管理器：`nautilus`、`dolphin`、`thunar`、`nemo`、`caja`、`pcmanfm`；`null` 为按此顺序第一个已安装的 |
| `terminal` | str 或 null | `null` | “在终端打开”所用终端：`kitty`、`ghostty`、`alacritty`、`foot`、`wezterm`、`gnome-terminal`、`konsole`、`xfce4-terminal`；`null` 同上 |
| `editor` | str 或 null | `null` | “用编辑器打开”所用编辑器：`code`、`codium`、`zed`、`obsidian`、`gnome-text-editor`、`kate`、`gedit`、`mousepad`；`null` 同上 |
| `course_settings` | object | `{}` | 每门课记住的设置，目前只有语言：`{"MATH421": {"language": "zh"}}` |

`lecture start` 的课堂语言依次取：命令行 `--language`、该课程在 `course_settings` 里记住的语言、全局 `language`。从图形界面开始上课时，所选语言会写回该课程。课程名不在 `course_settings` 里时直接用全局值。

**在其他程序中打开**：图形界面只提供三种打开方式，“在文件夹中显示”（先通过 D-Bus 的 `org.freedesktop.FileManager1.ShowItems` 让桌面文件管理器选中该文件，失败时由 `file_manager` 打开所在文件夹）、“在终端打开”（`terminal`，工作目录为所在文件夹）和“用编辑器打开”（`editor`）。程序只能从上表的固定名单中选；界面和接口都不接受任意命令，也不经过 shell。名单外的程序只能手工在 `config.json` 中写 `file_manager_command`、`terminal_command` 或 `editor_command`，值为参数数组，每项一个参数，`{path}` 换成文件的完整路径、`{dir}` 换成所在文件夹；两者都没写时把目标追加在末尾。写了 `*_command` 时它优先于同名选项。例如：

```json
{
  "terminal_command": ["footclient", "--working-directory={dir}"],
  "editor_command": ["emacsclient", "-c", "-n", "{path}"]
}
```

key 不写进 `config.json`。笔记 key 依次读取环境变量 `LECTURE_NOTES_API_KEY`、旧变量 `DEEPSEEK_API_KEY`、配置目录里的 `notes-api-key` 文件、旧文件 `api-key`；转录 key 依次读取 `LECTURE_ASR_API_KEY`、`asr-api-key` 文件。图形界面和 `lecture setup` 写入的 key 文件权限为 600。两个 key 分别只传给笔记进程和转录进程。

**全本地**：本机转录，笔记交给本机运行的 OpenAI 兼容服务（下例地址为占位，换成你的服务；这类服务通常接受任意非空 key，但仍须设置一个）。

```json
{
  "config_version": 2,
  "courses_dir": "/home/you/Courses",
  "asr_backend": "local",
  "asr_model": "large-v3-turbo",
  "asr_device": "auto",
  "language": "en",
  "refine": false,
  "auto_gain": true,
  "notes_provider": "custom",
  "notes_api_base": "http://127.0.0.1:8080/v1",
  "notes_model": "your-local-model",
  "notes_extra_body": {}
}
```

**本地转录 + 云端笔记**（默认组合，笔记用 DeepSeek）：

```json
{
  "config_version": 2,
  "courses_dir": "/home/you/Courses",
  "asr_backend": "local",
  "asr_model": "large-v3-turbo",
  "asr_device": "auto",
  "language": "en",
  "refine": true,
  "refine_model": "qwen3-asr-1.7b",
  "notes_provider": "deepseek",
  "notes_api_base": "https://api.deepseek.com",
  "notes_model": "deepseek-flash",
  "notes_extra_body": {"thinking": {"type": "disabled"}}
}
```

**全云端**（转录用 Groq，笔记用 OpenAI；`notes_model` 须自己填写）：

```json
{
  "config_version": 2,
  "courses_dir": "/home/you/Courses",
  "asr_backend": "api",
  "asr_provider": "groq",
  "asr_api_base": "https://api.groq.com/openai/v1",
  "asr_api_model": "whisper-large-v3-turbo",
  "language": "en",
  "notes_provider": "openai",
  "notes_api_base": "https://api.openai.com/v1",
  "notes_model": "填写你要用的模型名",
  "notes_extra_body": {}
}
```

## 更换服务

笔记服务和云端转录服务都可以在图形界面的设置页更换（选预设或“自定义”，填地址、模型和 key，可当场“测试连接”），也可以直接改上面的配置键。预设里模型名为空的（如 OpenAI 笔记）需要自己填写，程序不替你猜模型名。

笔记服务须满足：

- OpenAI 兼容的 `POST {notes_api_base}/chat/completions`，请求体为 `model`、`messages`、`stream: false`、`max_tokens`，再合并 `notes_extra_body`；必须支持 `max_tokens`。
- 正常结束时 `finish_reason` 为 `"stop"`；其他取值按“输出不完整”处理。
- 提供 `GET {notes_api_base}/models`：连接测试和 `lecture doctor` 用它验证地址与 key（列表里没有所配模型时只给警告，有些服务不列全）。
- 鉴权为 `Authorization: Bearer <key>`。

云端转录服务须兼容 `POST {asr_api_base}/audio/transcriptions`，并提供 `GET {asr_api_base}/models`（详见上文“云端 API 转录”）。

课堂文字会发送给笔记服务，云端转录时课堂音频会上传给转录服务，分别消耗各自的额度。

## 保存规则

课程根目录由 `lecture setup` 设置（配置项 `courses_dir`，没有默认值；未设置时需要课程的命令会提示先运行 setup。从旧版配置升级时，若 `~/Downloads/Umass_CS_Class` 存在则沿用），自动列出其下非隐藏、非符号链接的文件夹。旧版 `config.json` 首次读取时会自动升级，原文件另存为 `config.json.v1.bak`。模型默认 `base.en`，优先 NVIDIA GPU / FP16，CPU 备用；笔记默认 `deepseek-flash`，关闭 thinking，约每 60 秒检查新增转录。

界面中的“转录积压”包括正在识别的音频和等待识别的音频；录制时长按实际接收的音频计算。缓存修复不会提高模型本身的识别速度；若积压持续增长，下课后的收尾也会更久。

随堂整理会把相邻短片段合并为段落再发送，保留每个来源编号。到检查时间后优先等到句末或明显停顿，最多再等 30 秒（检查间隔小于 30 秒时最多等一个间隔）；结束录制时立即处理剩余文字。每批通常上限 6,000 字符，大片积压分批连续处理。界面的“待整理内容”是原始片段数量，不是 API 请求数；“本批合并”显示当前请求合并了多少片段与字符。合并依据标点、时间间隔和长度，不保证与知识点边界完全一致。

```text
<课程目录>/MATH421/LectureNotes/
├── 2026-09-14_143000-课堂笔记-a1b2c3.md
└── 原文与记录/
    ├── 2026-09-14_143000-课堂笔记-a1b2c3.transcript.md
    ├── 2026-09-14_143000-课堂笔记-a1b2c3.live.md
    └── 2026-09-14_143000-课堂笔记-a1b2c3.review.md
```

每次录制生成唯一主文件，同名附件放在 `LectureNotes/原文与记录/` 子文件夹（不以点开头，Obsidian 等工具能看到）；主文件里指向附件的链接都带这一层路径并做 URL 编码，附件之间的链接指向同一文件夹。录制中主文件显示随堂预览；结束后阅读入口是学习路线与主题正文，随堂记录移到 `.live.md`。`.transcript.md` 保存完整实时原文，以及成功时的离线版本；`.review.md` 仅在有疑点或处理失败时生成。没有随堂内容时不生成 `.live.md`。**请在其他文件里写自己的补充**，这些程序管理的文件会自动重写。

旧版本保存的笔记附件与主文件并列（`LectureNotes/<名称>.transcript.md` 等），不会被移动或改名；图形界面先在 `原文与记录/` 找附件，找不到再找旧位置。旧版本中断、升级后才恢复的会话按新布局写附件。

录制中可以放弃本次记录（会话目录中出现 `discard` 标记；图形界面通过 `POST /api/runs/active/discard`）：立即结束子进程，不做收尾、不生成笔记，只删除本次的主文件和三个附件（按完整文件名精确匹配，以及原子写入中断时留下的同名临时文件），清理 `/tmp` 会话，运行登记记为 `discarded`，退出码 0。本次新建且已空的 `LectureNotes/`、`原文与记录/` 文件夹一并删除。放弃只在录制阶段有效；下课收尾开始后的放弃请求会被忽略，照常保存。

图形界面删除笔记时，主文件及其附件（新旧两种位置）通过 `gio trash` 移到回收站，从不直接永久删除；没有 `gio` 时拒绝删除。进行中课堂的笔记不能删除。

详细笔记重新读取本次完整原始转录（课后校正成功时使用离线版本，否则使用实时版本），保留定义与条件、老师讲出的推导步骤、例题、反例、问答、重点及作业安排，按知识结构组织中文 Markdown 并保留英文术语、公式和来源编号。不会仅对随堂摘要再次压缩，也不会补造缺失板书或课外推导。

先按每批约 18,000 字符规划连续主题，每批最多四个主题，并合并跨批次延续的同一主题。正文按主题回读原始转录；超长主题分为同一主题的续篇，每次最多生成 8,000 tokens。各章最多三个请求同时进行，仍按课堂顺序保存。提示词要求统一术语、保留条件和去重；不保证模型绝不遗漏或误解。无法可靠理解的残句进入 review，影响结论的疑点仍在正文旁提示。

详细笔记每完成一部分就保存一次。规划格式错误、API 失败、输出截断或进程收尾超时时，保留已完成正文，主文件标明“未全部完成”，原文和失败说明位于附件。规划与主题生成会增加 DeepSeek 用量；规划覆盖、来源引用和文件写入检查不等于数学正确性验证。

转录处理状态、日志、进度数据库放在私有 `/tmp/lecture-<uid>-<随机值>/`。麦克风 PCM 使用该目录内的匿名循环暂存文件，上限 64 MiB（约 35 分钟未处理音频），用于吸收转录拥堵，关闭时自动释放；启用课后校正时另有上述 512 MiB 音频暂存。**主文件及所需附件均成功保存后才清理会话**；完整文字转录作为最终附件保留，不长期保存音频。Markdown 使用同目录临时写入再原子替换；多文件并非一次文件系统事务，失败时保留会话以便重写。

声卡偶发丢帧会显示警告并写入笔记，不再直接结束录制。转录跟不上时继续暂存并处理；结束会按积压量延长收尾时间。若暂存达到上限或 `/tmp` 无法写入，会停止采集、尽量处理已接收音频并明确报告错误。暂存只能缓解短时拥堵，不能使持续慢于实时的模型无限运行。

实时 API 失败时按现有退避机制重试，进度不会跳过内容；结束时仍未整理的文字保存在原文和 review 附件。下课后的主题规划与详细笔记请求遇到网络错误、超时、429 或 5xx 时，间隔 5 秒和 20 秒各重试一次；仍失败或返回格式无效时保存原文与原因，不增加新的自动修复循环。

若目标目录不可写或磁盘写入失败，程序不会删除尚未成功保存的内容：暂存在 `/tmp` 并明确报错。恢复目标目录后再次运行 `lecture`，恢复笔记并清理。此异常情况下不要先清空 `/tmp`。

SIGKILL、断电无法执行即时清理；下次运行 `lecture` 的任一子命令会恢复仍存在的会话文字，并清理本程序遗留的临时目录。Linux 上控制进程异常死亡会终止其转录和笔记子进程。`/tmp` 被系统清空后无法恢复未保存的内容。普通结束保留 Markdown 原文附件，但不提供会话 `resume`；需要重新录音时再次 `start`。

软件环境、语音模型缓存和用户配置长期保留，属于运行所需资源；每节课产生的中间数据按上述规则清理。默认模型缓存遵循 Hugging Face 配置。

第一版的时间戳是模型估计的音频相对时间，不含暂停时长。L 编号表示本次转录片段，最终笔记附每批的时间范围；完整转录按要求不长期保存。未提供的板书与听辨不清的公式保留“待核对”。

## 故障排查

- **找不到 `lecture` 命令**：确认 `~/.local/bin`（或 `XDG_BIN_HOME`）在 `PATH` 里，然后打开新终端。
- **安装脚本提示“已有其他 lecture 命令，未覆盖”**（或补全文件、桌面入口、图标）：那个位置已有别的程序的文件。确认后移走或改名，再重新运行安装脚本；脚本不会覆盖它。
- **先跑一遍检查**：`lecture doctor`，或图形界面设置页的“环境检查”。它只请求两个服务的 `/models`，不录音、不生成笔记；每一项失败都给出修复建议。
- **`lecture gui` 提示缺少界面依赖**：用 `--profile api` 安装时加了 `--no-gui`。不加 `--no-gui` 重新运行 `./install.sh` 即可。
- **独立窗口在 Wayland 下显示异常**：pywebview 只是可选项。卸掉它（`uv pip uninstall --python .venv/bin/python pywebview`）后，`lecture gui` 会改用浏览器的应用窗口，这是有保障的方式。
- **阅读界面的中文标题显示为黑体**：系统缺少中文衬线字体，`lecture doctor` 的“中文字体”一项会给出警告。安装 Noto Serif CJK（见“需要什么”）。
- **GPU 没被使用**：`lecture doctor --asr-device cuda` 检查 GPU 可见性与运行库；用 `--profile gpu` 安装才会装项目内的 CUDA 运行库。需要正常工作的 NVIDIA 驱动。
- **笔记没保存、提示目标目录不可写**：内容暂存在 `/tmp`，修复目录后再次运行 `lecture` 的任一子命令即可恢复（见“保存规则”）；这时不要先清空 `/tmp`。

## 开发

```sh
.venv/bin/python -m pytest -q
uv pip check --python .venv/bin/python
```

测试覆盖增量处理、事务恢复、输出截断、时间顺序、行修订、收尾、API 故障、真实子进程信号退出、异常死亡恢复、临时目录清理及已有笔记保护。

图形界面的前端源码在 `frontend/`（Svelte 5 + Vite + TypeScript），构建产物 `lecture_cli/gui/static/` 提交在仓库中，所以安装和使用都不需要 Node。改了前端要重新构建并提交产物：

```sh
./scripts/build-frontend.sh   # 需要 Node 22.12 以上；依次 npm ci、类型检查、前端测试、构建
```

脚本把前端源码的哈希写入 `lecture_cli/gui/static/build-hash.txt`；产物与源码不一致时 `pytest` 会失败。界面后端对所有响应施加严格的内容安全策略，前端不得使用内联脚本、内联样式或任何外部资源（包括字体和 CDN）。

资料：[WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)、[DeepSeek 接口](https://api-docs.deepseek.com/)。
