# drama_subtitler

用 Whisper 转录视频对白，再用 LLM 翻译成双语字幕（原文 + 译文）。

你可以使用任意 Whisper 支持的源语言和任意目标语言，但本项目**开发并实测的主要场景**是：
- 源语言：**日语**（`ja`）、**韩语**（`ko`）
- 目标语言：**简体中文**（`zh`）

项目中针对性的处理（如 mojibake 修复的编码回退 `cp932` / `cp949` / `euc-kr`）也是围绕 CJK 环境设计的。

## 功能

- **Whisper 转录**：支持 `faster-whisper` 和 `whisper.cpp`（Apple Silicon 上自动选用后者）。
- **自动语言识别**：支持 Whisper 能识别的任意源语言。
- **多翻译后端**：
  - `ollama` — 本地 `/api/chat` 端点。
  - `openrouter` — 云端 OpenAI-compatible API，支持 SSE 流式输出。
  - `deepseek` — DeepSeek 官方 API。
- **输出**：
  - `<media>.orig.srt` — 源语言转录字幕。
  - `<media>.bilingual.srt` — 双语字幕（原文 + 译文，逐条显示）。
- **可配置目标语言**：通过 `TARGET_LANGUAGE` 环境变量或 `--target-language` 参数切换（默认 `zh`）。
- **断点续跑**：`--skip-transcription` 可复用已有的 `.orig.srt` 重新翻译。
- **Web UI**：小型 Flask 界面，支持上传、进度跟踪、下载。

## 安装

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

系统依赖：

- `ffmpeg` 必须在 `PATH` 中（两个 Whisper 后端都需要）。
- 使用 `whisper.cpp` 时：安装 `whisper-cli`，并将 ggml 模型放到 `models/ggml-<MODEL>.bin`（或设置 `WHISPER_CPP_MODEL_PATH`）。
- 使用 `ollama` 时：需要运行中的 Ollama 守护进程（默认 `http://127.0.0.1:11434`）。
- 使用 `openrouter` 或 `deepseek` 时：在 `.env` 中填入对应的 API Key。

复制 `.env.example` 为 `.env` 并按需编辑。

## 命令行用法

```bash
# 转录 + 翻译（自动识别源语言）
python subtitle_pipeline.py path/to/episode.mkv

# 相对 MEDIA_DIR 解析文件路径
python subtitle_pipeline.py episode.mkv

# 复用已有转录重新翻译
python subtitle_pipeline.py episode.mkv --skip-transcription

# 强制指定源语言
python subtitle_pipeline.py episode.mkv --source-language ko

# 切换目标语言
python subtitle_pipeline.py episode.mkv --target-language en

# 调试时实时查看模型流式输出
python subtitle_pipeline.py episode.mkv --show-translation-stream
```

## Web UI

```bash
python run.py --port 5050
```

打开 http://localhost:5050。界面会列出 `MEDIA_DIR` 下的媒体文件，支持上传和实时进度查看。

## 项目结构

```
drama_subtitler/
├── subtitle_pipeline.py        # CLI 入口
├── run.py                      # Flask 启动脚本
├── config.py                   # 环境变量配置
├── app/
│   ├── __init__.py             # Flask create_app 工厂
│   ├── routes.py               # REST API 与前端路由
│   ├── models/
│   │   ├── subtitle_pipeline.py   # 核心引擎：转录 + 翻译
│   │   └── cost_estimator.py      # 费用估算
│   ├── templates/index.html
│   └── static/{css,js}/...
├── tests/                      # 单元测试
└── media/                      # 默认 MEDIA_DIR（按需创建）
```

## 技术文档

详见 [TECHNICAL.md](TECHNICAL.md)，包含架构设计、核心模块详解、数据流和扩展指南。

## 法律声明 / 免责声明

`drama_subtitler` 仅从你已有的本地视频文件生成字幕文件（SRT）。它**不会**下载、托管、流媒体传输或分发任何受版权保护的内容，也不会绕过 DRM 或其他版权保护技术。你有责任确保你处理的媒体符合所在司法管辖区的版权法和许可协议。完整声明请见 [DISCLAIMER.md](DISCLAIMER.md)。

## 开源许可

MIT — 详见 [LICENSE](LICENSE)。

欢迎提交 PR，提交前请运行 `pytest -v` 确保测试通过。
