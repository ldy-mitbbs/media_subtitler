# media_subtitler

用 Whisper 将视频对白转成文字，再用 LLM 翻译成双语字幕（原文 + 译文）。

你可以使用任意 Whisper 支持的源语言和任意目标语言，但本项目**开发并实测的主要场景**是：
- 源语言：**日语**（`ja`）、**韩语**（`ko`）
- 目标语言：**简体中文**（`zh`）

项目中针对性的处理（如 mojibake 修复的编码回退 `cp932` / `cp949` / `euc-kr`）也是围绕 CJK 环境设计的。

## 功能

- **Whisper 语音转文字**：支持 `faster-whisper`、远程 GPU `faster-whisper`、`whisper.cpp`（Apple Silicon 上自动选用后者）和 OpenAI Whisper API。
- **优先复用已有字幕**：视频内嵌字幕或同目录外挂字幕可直接提取/复用后翻译；没有可用字幕时才进行语音识别。
- **自动语言识别**：支持 Whisper 能识别的任意源语言。
- **多翻译后端**：
  - `ollama` — 本地 `/api/chat` 端点。
  - `lmstudio` — 本地/局域网 LM Studio 的 OpenAI-compatible 端点（默认 `:1234/v1`），适合用一台带 GPU 的台式机跑 14B 级模型。
  - `openrouter` — 云端 OpenAI-compatible API，支持 SSE 流式输出。
  - `deepseek` — DeepSeek 官方 API。
- **Qwen3-ASR 可选后端**：可安装额外依赖后使用本地 Qwen3-ASR 做语音识别。
- **远程 GPU 支持**：可把 Whisper 语音转文字和 Ollama 翻译跑在另一台局域网电脑（例如 Windows + NVIDIA 游戏 PC）上，本机只负责抽取音频、上传、调度和写字幕。
- **输出**：
  - `<media>.orig.srt` — 源语言字幕。
  - `<media>.orig.ass` — ARIB 字幕的原始布局缓存，保留解码后的日文位置、样式和时间。
  - `<media>.translation.ass` — ARIB 的纯中文/目标语言定位字幕，在 mpv 中作为第二轨叠加。
  - `<media>.bilingual.srt` — 双语字幕（原文 + 译文，逐条显示）。
  - `<media>.bilingual.ass` — 带样式的双语字幕（原文和译文使用不同字体/颜色，并按视频画面比例设置布局）。
- **可配置目标语言**：通过 `TARGET_LANGUAGE` 环境变量或 `--target-language` 参数切换（默认 `zh`）。
- **断点续跑**：`--skip-transcription` 可复用已有的 `.orig.srt` 重新翻译。
- **网页界面与 macOS 桌面应用**：可在浏览器中运行，也可打包成 macOS `.app`。桌面版支持拖放文件、原生窗口、设置持久化和独立 Finder 右键入口。
- **内置排障样片**：桌面版提供机器生成的日语测试视频，可一键填入路径，用来确认本机语音识别链路是否跑通。

### 日本电视 ARIB 字幕：在每行日文下面加中文

生成的 ASS 同时写入 `LayoutResX/Y`，明确使用 ARIB 显示画布比例；避免 1440×1080 等非方形像素 TS 将中日文字形再次横向拉宽。同时修复 FFmpeg/libaribcaption 导出 ASS 时将默认半透明黑底写成不透明黑底的问题：默认背景使用 ASS alpha `7F`（约 50% 不透明度），逐事件显式透明度仍优先。原始字幕缓存与事件保持不变。

检测到内嵌 `arib_caption` 时，程序使用 FFmpeg 的 **libaribcaption** 解码器
提取带定位信息的 ASS，而不是先丢弃布局转成 SRT。生成的 `.bilingual.ass`
保留日文的坐标、颜色、字号及时间，在每个日文正文行下面添加较小的中文译文。
双行日文会显示为「日文第一行 / 中文第一行 / 日文第二行 / 中文第二行」。
小号注音保留在原处，不重复翻译；同一行的不同颜色片段一起翻译。

```bash
.venv/bin/python subtitle_pipeline.py '/path/to/recording.ts' --target-language zh
```

中文根据原行间距和剩余画面宽度缩小，禁止自动换行，以免盖住下一行日文。
空间不足或遇到暂不支持的动态排版时会明确报错，不会悄悄挪动原字幕。
SRT 无法保存这些坐标。macOS 网页/桌面版的「打开视频」会优先用 mpv 加载
`.translation.ass` 为第二轨，第一轨仍是内嵌 ARIB 日文，保持 mpv 现有的日文显示。
也可以在仓库根目录手动运行：

```bash
/Applications/mpv.app/Contents/MacOS/mpv --secondary-sub-ass-override=no \
  --script="$PWD/contrib/mpv/arib-translation.lua" '/path/to/recording.ts'
```

脚本读取视频旁边的 `.translation.ass`，按保存的 FFmpeg 流索引选择对应的内嵌
ARIB 轨。无需修改全局 mpv 字体/样式设置，也无需安装脚本到全局配置目录。

其他播放器可选择 **`.bilingual.ass` 单个字幕轨**，其中已包含日文，不要再同时叠加
内嵌日文轨。注意 mpv 对内嵌 ARIB 和外挂 ASS 应用的默认样式不同：直接播放
合并 ASS 可能显示更大的日文或黑色背景。输出会修正上述画布与默认背景透明度，但 ASS 并不等同于原始广播渲染；
如果要保持 mpv 原来看到的日文效果，请用上述纯译文叠加方式。ASS 转换不能
恢复 libaribcaption 未识别的 DRCS 自定义字形。

`.orig.ass` 与 `.orig.srt` 一起保存，因此分阶段翻译及 `--skip-transcription`
重译都能恢复布局。如果手动编辑 SRT 导致两者不一致，需要重新提取。
旧版仅有 `.orig.srt` 的结果必须先不带 `--skip-transcription` 运行一次，才能获得布局。
没有 ARIB 定位信息的 SRT / Whisper 字幕继续使用原来的双语排版。

## 安装

### macOS（推荐路径）

全新的 Mac 上只需要一条命令：

```bash
git clone <repo-url> media_subtitler
cd media_subtitler
./scripts/setup-macos.sh
```

脚本会自动完成：

- 用 Homebrew 装好 `ffmpeg` 和 `whisper-cpp`（已经装过、或已有自己编译的
  `whisper.cpp` 会被自动探测并复用，不会重复安装）；
- 创建 `.venv` 并安装依赖；
- 下载 `ggml-large-v3-turbo` 模型到 `~/.cache/media_subtitler/models/`
  （约 1.5GB，Apple Silicon 上的默认模型）；
- 生成 `settings.json`（已在 `.gitignore` 里），并提示填入 DeepSeek API key；
- 最后逐项自检，把每一项配置是否可用打印出来。

脚本可以**重复运行**：已装好的工具、已下载的模型和已填的 API key 都会保留。

常用参数：

```bash
./scripts/setup-macos.sh --skip-model      # 不下载模型
./scripts/setup-macos.sh --model large-v3  # 换用 large-v3（3.1GB，慢但更稳）
./scripts/setup-macos.sh --skip-brew       # 只检查，不通过 Homebrew 安装
```

想进一步压榨 Apple Silicon 的性能，见 [docs/macos-coreml.md](docs/macos-coreml.md)
（CoreML 编码器，encoder 约快 2-3 倍）。

### Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows fresh clone

在 PowerShell 里：

```powershell
git clone <repo-url> media_subtitler
cd media_subtitler
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

推荐先装好系统工具：

```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install Gyan.FFmpeg
winget install Ollama.Ollama
```

如果你要在 Windows/NVIDIA 机器上本机跑语音转文字，确认 NVIDIA 驱动可用，并优先使用：

```powershell
ASR_BACKEND=faster-whisper
ASR_DEVICE=auto
ASR_COMPUTE_TYPE=auto
```

`faster-whisper` 第一次运行会下载模型到 Hugging Face cache。`large-v3` 约 3GB；想先试通流程可以用 `small` 或 `medium`。

也可以选择本地 Qwen3-ASR：

```bash
pip install '.[qwen-asr]'
ASR_BACKEND=qwen3-asr
ASR_MODEL=Qwen/Qwen3-ASR-1.7B
```

Qwen3-ASR 速度较快，但当前时间轴是按分句近似生成；如果视频本身有内嵌字幕，程序仍会优先提取内嵌字幕。

如果本机 GPU 语音转文字报 `cublas64_12.dll` / `cudnn*.dll` 找不到，通常是 CUDA/cuDNN runtime 没在 Windows `PATH` 里。先确认 NVIDIA 驱动正常，再按 NVIDIA cuDNN Windows 文档安装运行时。

Windows 自检：

```powershell
.\scripts\check-windows.ps1
```

系统依赖：

- `ffmpeg` 必须在 `PATH` 中（本地/远程 ASR 和内嵌字幕提取都需要）。
- 如果要直接复用 TS 文件里的 `[字]` 字幕并保留布局，FFmpeg 必须包含
  **libaribcaption**。先检查现有构建，已经支持时无需替换：

  ```bash
  ffmpeg -hide_banner -decoders | grep libaribcaption
  ```

  如果没有结果，请安装启用 `--enable-libaribcaption` 的 FFmpeg 构建，再确认解码器列表。

  Linux/CI 可直接用 BtbN 的静态构建（见 `.github/workflows/ci.yml`）。
- 使用 `whisper.cpp` 时：安装 `whisper-cli`，并将 ggml 模型放到 `models/ggml-<MODEL>.bin`、`~/.cache/media_subtitler/models/ggml-<MODEL>.bin`，或在设置里填写 `WHISPER_CPP_MODEL_PATH`。
- 使用 `ollama` 时：需要运行中的 Ollama 守护进程（默认 `http://127.0.0.1:11434`，或由 `GPU_BASE_URL` 派生为 `<GPU_BASE_URL>:11434`）。
- 使用 `lmstudio` 时：需要在目标机器上运行 LM Studio 并开启本地服务器（默认 `http://127.0.0.1:1234/v1`，或由 `GPU_BASE_URL` 派生为 `<GPU_BASE_URL>:1234/v1`），并在 LM Studio 里加载好对应模型（如 `qwen2.5-14b-instruct`）。无需 API Key。
- 使用 `openrouter` 或 `deepseek` 时：在 `.env` 中填入对应的 API Key。

复制 `.env.example` 为 `.env` 并按需编辑。

## 命令行用法

```bash
# 语音转文字 + 翻译（自动识别源语言）
python subtitle_pipeline.py path/to/sample.mkv

# 也可以传入文件名，程序会相对 MEDIA_DIR 解析
python subtitle_pipeline.py sample.mkv

# 复用已有转文字结果重新翻译
python subtitle_pipeline.py sample.mkv --skip-transcription

# 强制指定源语言
python subtitle_pipeline.py sample.mkv --source-language ko

# 切换目标语言
python subtitle_pipeline.py sample.mkv --target-language en

# 调试时实时查看模型流式输出
python subtitle_pipeline.py sample.mkv --show-translation-stream

# 使用局域网 Windows/NVIDIA 机器转文字，且用同一台机器的 Ollama 翻译
python subtitle_pipeline.py sample.mkv \
  --asr-backend remote-faster-whisper \
  --translation-backend ollama \
  --gpu-base-url http://192.168.1.42

# 用局域网带 GPU 的台式机上的 LM Studio 翻译（OpenAI-compatible :1234/v1）
python subtitle_pipeline.py sample.mkv \
  --translation-backend lmstudio \
  --translation-model qwen2.5-14b-instruct \
  --lmstudio-base-url http://192.168.0.209:1234/v1
```

Windows PowerShell 示例：

```powershell
.\.venv\Scripts\python.exe .\subtitle_pipeline.py "D:\Videos\sample01.mkv" `
  --whisper-backend faster-whisper `
  --whisper-model large-v3 `
  --translation-backend ollama `
  --translation-model qwen2.5:14b `
  --target-language zh
```

## 远程 GPU 机器设置

`contrib/` 里包含独立运行所需的辅助脚本：

```bash
contrib/whisper-server.py              # 在 GPU 机器上运行的 faster-whisper HTTP 服务
contrib/check-gpu-services.sh          # 在本机检查 Whisper :5051 和 Ollama :11434
contrib/start-media-subtitler-gpu.ps1  # Windows PowerShell 启动/检查脚本
```

Windows GPU 机器上的常见流程。如果已经在仓库根目录运行过 `.\scripts\setup-windows.ps1`，可以跳过创建虚拟环境和安装依赖，直接运行 `.\contrib\start-media-subtitler-gpu.ps1`。GPU helper 会优先使用 `.venv`，也兼容旧的 `venv` 目录。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install faster-whisper flask
.\contrib\start-media-subtitler-gpu.ps1 -OllamaModel qwen2.5:14b -WhisperModel large-v3
```

如果是通过 pip 安装包，也可以直接运行：

```powershell
media-subtitler-whisper-server --host 0.0.0.0 --port 5051 --model large-v3
```

然后在运行 `media_subtitler` 的机器上设置：

```bash
GPU_BASE_URL=http://192.168.1.42
WHISPER_BACKEND=remote-faster-whisper
TRANSLATION_BACKEND=ollama
TRANSLATION_MODEL=qwen2.5:14b
```

也可以在网页界面里为单个任务选择「远程 GPU faster-whisper」、填写 `GPU_BASE_URL`，并把翻译后端切到 Ollama。

## 网页界面

```bash
python run.py --port 5050
```

打开 http://localhost:5050。在「本地文件路径」输入视频的绝对路径，程序会直接处理源文件，并把 `.orig.srt`、`.bilingual.srt`、`.bilingual.ass` 写在视频旁边。

也可以点击「选择文件」使用系统文件选择器；任务完成后，网页界面会提供原始字幕、双语 SRT、双语 ASS 的下载入口，并可尝试用系统默认播放器直接打开视频和字幕。

macOS Finder 右键启动任务：

```bash
./scripts/install-macos-finder-shortcut.sh
```

安装后，在 Finder 里选中媒体文件，右键选择「打开方式」-> `Media Subtitler 网页版启动任务`。这个入口会把文件路径提交到本地网页服务；如果 `http://127.0.0.1:5050` 没有运行，会尝试自动启动 `run.py`。

桌面应用版本会安装独立的 `Media Subtitler 桌面版启动任务`，它会把文件提交给正在运行的桌面应用；如果桌面应用未运行，会先尝试打开桌面应用。

Windows:

```powershell
.\scripts\run-web-windows.ps1 -Browser -MediaDir "D:\Videos"
```

## macOS 桌面应用

如果你希望用普通桌面应用的方式运行，可以构建 macOS `.app`：

```bash
./scripts/build-macos-app.sh
```

构建结果位于：

```text
dist/Media Subtitler.app
```

桌面版仍然复用同一套 Flask 界面和字幕处理管道，但会在原生窗口中打开，并使用随机本地端口，不占用固定的 `5050`。桌面设置会保存到：

```text
~/Library/Application Support/Media Subtitler/settings.json
```

桌面版支持把媒体文件直接拖进窗口：拖放后会自动填入「本地文件路径」并刷新费用估算，但不会自动开始任务，需要你再点击「直接运行」。

### 桌面版排障测试

如果不确定本机 `ffmpeg`、`whisper.cpp` 或模型路径是否配置正确，可以点击「本地文件路径」旁边的「使用测试视频」。应用会把内置日语测试视频复制到：

```text
~/Library/Application Support/Media Subtitler/Media/media-subtitler-japanese-test.mp4
```

然后自动填入路径。点击「直接运行」后，正常结果会先停在「等待翻译」状态，并生成：

```text
~/Library/Application Support/Media Subtitler/Media/media-subtitler-japanese-test.orig.srt
```

### 桌面版 Finder 入口

在桌面应用里点击「安装 / 更新 Finder 入口」会安装：

```text
~/Applications/Media Subtitler 桌面版启动任务.app
```

之后可以在 Finder 里选中媒体文件，右键选择「打开方式」-> `Media Subtitler 桌面版启动任务`。这个入口会把文件提交给正在运行的桌面应用；如果桌面应用未运行，会先尝试打开 `Media Subtitler.app`。

网页界面的 Finder 入口是另一个独立应用：

```text
~/Applications/Media Subtitler 网页版启动任务.app
```

两者可以并存：网页入口面向固定的 `http://127.0.0.1:5050` 服务，桌面入口面向当前运行的桌面应用。

发布给其他机器前，需要对 `.app` 做代码签名和公证；当前构建脚本生成的是本机可试用的开发包。

## 项目结构

```
media_subtitler/
├── subtitle_pipeline.py        # CLI 入口
├── run.py                      # Flask 启动脚本
├── config.py                   # 环境变量配置
├── app/
│   ├── __init__.py             # Flask create_app 工厂
│   ├── routes.py               # REST API 与前端路由
│   ├── models/
│   │   ├── subtitle_pipeline.py   # 核心引擎：语音转文字 + 翻译
│   │   └── cost_estimator.py      # 费用估算
│   ├── templates/index.html
│   └── static/{css,js}/...
├── contrib/                     # 远程 GPU Whisper/Ollama helper
├── scripts/                     # Windows setup/run/check helper
├── tests/                      # 单元测试
└── media/                      # CLI 相对路径解析用的默认 MEDIA_DIR（按需创建）
```

## 技术文档

详见 [TECHNICAL.md](TECHNICAL.md)，包含架构设计、核心模块详解、数据流和扩展指南。

## 法律声明 / 免责声明

`media_subtitler` 仅从你已有的本地视频文件生成字幕文件（SRT / ASS）。它**不会**下载、托管、流媒体传输或分发任何受版权保护的内容，也不会绕过 DRM 或其他技术保护措施。

你在使用本工具时完全对自己的行为负责。

## 开源许可

MIT — 详见 [LICENSE](LICENSE)。

欢迎提交 PR，提交前请运行 `pytest -v` 确保测试通过。
