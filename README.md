# Lecture CLI · 第一版

本地 WhisperLiveKit 持续转录，可在下课后用 Qwen 离线重转录，再由独立进程调用 DeepSeek 生成中文课堂笔记。终端显示转录及状态，Markdown 自动保存到课程文件夹，可用 Obsidian 查看。

## 使用

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

### 自动降低麦克风削波音量

`lecture start` 默认启用 `auto_gain`，本地与云端后端都适用。仅调节 PipeWire 默认源（未指定 `--device`，或使用 `pipewire`、`default`、`pulse`）；数字编号、具体硬件名称、`--audio-file` 和 demo 不调节。每累计约 2 秒收音，若至少 0.1% 采样达到削波阈值（绝对值 ≥ 0.999），就降低 12 dB：按 PipeWire 立方刻度将音量乘以约 0.631，最低 10%。每次调整后跳过一个窗口等待生效，暂停期间不评估；到下限仍削波时提示检查硬件增益（Mic Boost）。

第一版**只降不升**，避免安静教室里误升音量再次削波。结束后**不恢复音量**，由 WirePlumber 记住调整后的值。界面的“麦克风音量”和结束后的控制台显示最后一次提示。缺少 `wpctl` 或无法读取音量时给出说明，录制照常；已静音时只提示，不自动取消静音。`lecture doctor` 只读检查默认源音量，不修改它。

本次关闭：`lecture start MATH421 --no-auto-gain`；长期关闭：在 `config.json` 中设置 `"auto_gain": false`。可用 `--auto-gain` 覆盖配置重新启用。

### 云端 API 转录（无 GPU / 弱 CPU）

```sh
./install.sh --api                       # 只装基础依赖，不装本地模型和 torch
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

补全不会启动录制、调用 API 或恢复旧会话。

终端按 **P** 暂停/继续，按 **Q** 或 **Ctrl+C** 结束。结束后会等待转录收尾，再根据完整转录编写详细课堂笔记，然后清理本次临时目录。长课可能需要数分钟整理，界面显示章节进度。关闭终端或收到 SIGTERM/SIGHUP 也会尝试收尾；终端关闭时需要给后台进程留出退出时间。

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

## 课堂收音与 ASR 对比诊断

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

## 保存规则

课程根目录由 `lecture setup` 设置（配置项 `courses_dir`，没有默认值；未设置时需要课程的命令会提示先运行 setup。从旧版配置升级时，若 `~/Downloads/Umass_CS_Class` 存在则沿用），自动列出其下非隐藏、非符号链接的文件夹。旧版 `config.json` 首次读取时会自动升级，原文件另存为 `config.json.v1.bak`。模型默认 `base.en`，优先 NVIDIA GPU / FP16，CPU 备用；笔记默认 `deepseek-flash`，关闭 thinking，约每 60 秒检查新增转录。

界面中的“转录积压”包括正在识别的音频和等待识别的音频；录制时长按实际接收的音频计算。缓存修复不会提高模型本身的识别速度；若积压持续增长，下课后的收尾也会更久。

随堂整理会把相邻短片段合并为段落再发送，保留每个来源编号。到检查时间后优先等到句末或明显停顿，最多再等 30 秒（检查间隔小于 30 秒时最多等一个间隔）；结束录制时立即处理剩余文字。每批通常上限 6,000 字符，大片积压分批连续处理。界面的“待整理内容”是原始片段数量，不是 API 请求数；“本批合并”显示当前请求合并了多少片段与字符。合并依据标点、时间间隔和长度，不保证与知识点边界完全一致。

```text
<课程目录>/MATH421/LectureNotes/
├── 2026-09-14_143000-课堂笔记-a1b2c3.md
├── 2026-09-14_143000-课堂笔记-a1b2c3.transcript.md
├── 2026-09-14_143000-课堂笔记-a1b2c3.live.md
└── 2026-09-14_143000-课堂笔记-a1b2c3.review.md
```

每次录制生成唯一主文件及同名附件。录制中主文件显示随堂预览；结束后阅读入口是学习路线与主题正文，随堂记录移到 `.live.md`。`.transcript.md` 保存完整实时原文，以及成功时的离线版本；`.review.md` 仅在有疑点或处理失败时生成。没有随堂内容时不生成 `.live.md`。**请在其他文件里写自己的补充**，这些程序管理的文件会自动重写。

详细笔记重新读取本次完整原始转录（课后校正成功时使用离线版本，否则使用实时版本），保留定义与条件、老师讲出的推导步骤、例题、反例、问答、重点及作业安排，按知识结构组织中文 Markdown 并保留英文术语、公式和来源编号。不会仅对随堂摘要再次压缩，也不会补造缺失板书或课外推导。

先按每批约 18,000 字符规划连续主题，每批最多四个主题，并合并跨批次延续的同一主题。正文按主题回读原始转录；超长主题分为同一主题的续篇，每次最多生成 8,000 tokens。各章最多三个请求同时进行，仍按课堂顺序保存。提示词要求统一术语、保留条件和去重；不保证模型绝不遗漏或误解。无法可靠理解的残句进入 review，影响结论的疑点仍在正文旁提示。

详细笔记每完成一部分就保存一次。规划格式错误、API 失败、输出截断或进程收尾超时时，保留已完成正文，主文件标明“未全部完成”，原文和失败说明位于附件。规划与主题生成会增加 DeepSeek 用量；规划覆盖、来源引用和文件写入检查不等于数学正确性验证。

转录处理状态、日志、进度数据库放在私有 `/tmp/lecture-<uid>-<随机值>/`。麦克风 PCM 使用该目录内的匿名循环暂存文件，上限 64 MiB（约 35 分钟未处理音频），用于吸收转录拥堵，关闭时自动释放；启用课后校正时另有上述 512 MiB 音频暂存。**主文件及所需附件均成功保存后才清理会话**；完整文字转录作为最终附件保留，不长期保存音频。Markdown 使用同目录临时写入再原子替换；多文件并非一次文件系统事务，失败时保留会话以便重写。

声卡偶发丢帧会显示警告并写入笔记，不再直接结束录制。转录跟不上时继续暂存并处理；结束会按积压量延长收尾时间。若暂存达到上限或 `/tmp` 无法写入，会停止采集、尽量处理已接收音频并明确报告错误。暂存只能缓解短时拥堵，不能使持续慢于实时的模型无限运行。

实时 API 失败时按现有退避机制重试，进度不会跳过内容；结束时仍未整理的文字保存在原文和 review 附件。下课后的主题规划与详细笔记请求遇到网络错误、超时、429 或 5xx 时，间隔 5 秒和 20 秒各重试一次；仍失败或返回格式无效时保存原文与原因，不增加新的自动修复循环。

若目标目录不可写或磁盘写入失败，程序不会删除尚未成功保存的内容：暂存在 `/tmp` 并明确报错。恢复目标目录后再次运行 `lecture`，恢复笔记并清理。此异常情况下不要先清空 `/tmp`。

SIGKILL、断电无法执行即时清理；下次运行 `lecture` 的任一子命令会恢复仍存在的会话文字，并清理本程序遗留的临时目录。Linux 上控制进程异常死亡会终止其转录和笔记子进程。`/tmp` 被系统清空后无法恢复未保存的内容。普通结束保留 Markdown 原文附件，但不提供会话 `resume`；需要重新录音时再次 `start`。

软件环境、语音模型缓存和用户配置长期保留，属于运行所需资源；每节课产生的中间数据按上述规则清理。默认模型缓存遵循 Hugging Face 配置。

## 演示与已有音频

```sh
lecture demo math421
```

使用自造的英文课堂文字调用真实 DeepSeek，**不会打开麦克风**，在对应课程中生成带“演示”标记的笔记。

```sh
lecture start math421 --audio-file /path/to/lecture.wav
lecture start math421 --audio-file /path/to/lecture.wav --fast
```

已有音频默认按实时速度输入，`--fast` 尽快处理。用户提供的原始文件不会被删除。音频输入与麦克风复用同一 WhisperLiveKit 流式处理和笔记链路。

## 安装与检查

需要 Linux、uv、FFmpeg、PortAudio；Arch 系统包为 `uv ffmpeg portaudio`。项目使用独立 Python 3.12 环境。本地安装的 PyTorch 用于 CPU 上的语音活动检测；Whisper 的 GPU 推理由 CTranslate2 执行，无需替换为 GPU PyTorch。

```sh
./install.sh
./install.sh --gpu        # NVIDIA 机器：同时安装项目内的 CUDA 运行库
lecture prepare          # 上课前下载 base.en 模型
lecture doctor
```

代码固定 WhisperLiveKit 提交 `363e4f6d029694d9c81ae548beddd9d3c88a3637`，安装版本记录在 `requirements.lock`。项目可移动，但移动后需重新运行安装脚本以更新虚拟环境入口。

GPU 运行库版本记录在 `requirements-gpu.lock`，放在项目虚拟环境中；程序只为转录子进程设置库搜索路径，不修改全局环境或系统驱动。GPU 模式需要正常工作的 NVIDIA 驱动。`CUDA_VISIBLE_DEVICES` 的用户设置会保留。

```sh
.venv/bin/python -m pytest -q
uv pip check --python .venv/bin/python
```

测试覆盖增量处理、事务恢复、输出截断、时间顺序、行修订、收尾、API 故障、真实子进程信号退出、异常死亡恢复、临时目录清理及已有笔记保护。

第一版的时间戳是模型估计的音频相对时间，不含暂停时长。L 编号表示本次转录片段，最终笔记附每批的时间范围；完整转录按要求不长期保存。未提供的板书与听辨不清的公式保留“待核对”。

## 开发

图形界面的前端源码在 `frontend/`（Svelte 5 + Vite + TypeScript），构建产物 `lecture_cli/gui/static/` 提交在仓库中，所以安装和使用都不需要 Node。改了前端要重新构建并提交产物：

```sh
./scripts/build-frontend.sh   # 需要 Node 22.12 以上；依次 npm ci、类型检查、前端测试、构建
```

脚本把前端源码的哈希写入 `lecture_cli/gui/static/build-hash.txt`；产物与源码不一致时 `pytest` 会失败。界面后端对所有响应施加严格的内容安全策略，前端不得使用内联脚本、内联样式或任何外部资源（包括字体和 CDN）。

资料：[WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)、[DeepSeek 接口](https://api-docs.deepseek.com/)。
