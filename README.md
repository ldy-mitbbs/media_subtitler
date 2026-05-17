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
  - `openrouter` — 云端 OpenAI-compatible API，支持 SSE 流式输出。
  - `deepseek` — DeepSeek 官方 API。
- **Qwen3-ASR 可选后端**：可安装额外依赖后使用本地 Qwen3-ASR 做语音识别。
- **远程 GPU 支持**：可把 Whisper 语音转文字和 Ollama 翻译跑在另一台局域网电脑（例如 Windows + NVIDIA 游戏 PC）上，本机只负责抽取音频、上传、调度和写字幕。
- **输出**：
  - `<media>.orig.srt` — 源语言字幕。
  - `<media>.bilingual.srt` — 双语字幕（原文 + 译文，逐条显示）。
  - `<media>.bilingual.ass` — 带样式的双语字幕（原文和译文使用不同字体/颜色，并按视频画面比例设置布局）。
- **可配置目标语言**：通过 `TARGET_LANGUAGE` 环境变量或 `--target-language` 参数切换（默认 `zh`）。
- **断点续跑**：`--skip-transcription` 可复用已有的 `.orig.srt` 重新翻译。
- **网页界面与 macOS 桌面应用**：可在浏览器中运行，也可打包成 macOS `.app`。桌面版支持拖放文件、原生窗口、设置持久化和独立 Finder 右键入口。
- **内置排障样片**：桌面版提供机器生成的日语测试视频，可一键填入路径，用来确认本机语音识别链路是否跑通。

## 安装

### macOS / Linux

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
- 如果要直接复用合法来源的 TS 文件里的 `[字]` 字幕，`ffmpeg` 还需要支持
  `arib_caption` 解码。普通 Homebrew `ffmpeg` 可能没有这个解码器；macOS
  可使用支持 `--with-libaribcaption` 的 ffmpeg 构建，或自行编译带
  `libaribcaption` 的 ffmpeg。
- 使用 `whisper.cpp` 时：安装 `whisper-cli`，并将 ggml 模型放到 `models/ggml-<MODEL>.bin`、`~/.cache/media_subtitler/models/ggml-<MODEL>.bin`，或在设置里填写 `WHISPER_CPP_MODEL_PATH`。
- 使用 `ollama` 时：需要运行中的 Ollama 守护进程（默认 `http://127.0.0.1:11434`，或由 `GPU_BASE_URL` 派生为 `<GPU_BASE_URL>:11434`）。
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
