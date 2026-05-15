# drama_subtitler 技术文档

> 本文档面向希望理解、修改或维护本代码库的开发者。

## 1. 项目概述

`drama_subtitler` 是一个本地运行的字幕生成与翻译管道。输入是用户已有的视频文件，输出是两个字幕文件：

- `<media>.orig.srt`：Whisper 识别出的原始语言字幕
- `<media>.bilingual.srt`：双语字幕（原始语言 + 目标语言翻译，逐条显示）

虽然架构上支持任意源语言和目标语言，但本项目**开发并实际测试的主要场景**是：
- 源语言：**日语**（`ja`）、**韩语**（`ko`）
- 目标语言：**简体中文**（`zh`）

项目中一些针对性的处理（如 mojibake 修复的编码回退、`cp932`/`cp949`/`euc-kr` 等）也是围绕 CJK 环境设计的。

## 2. 架构总览

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   CLI / Web UI  │────▶│   SubtitlePipeline  │────▶│  .orig.srt      │
│                 │     │   (核心管道类)       │     │  .bilingual.srt │
└─────────────────┘     └─────────────────────┘     └─────────────────┘
         │                         │
         ▼                         ▼
  subtitle_pipeline.py      Whisper (语音识别)
  run.py + Flask routes     LLM API (翻译)
  SubtitleJobManager        cost_estimator
```

### 2.1 入口层

| 文件 | 作用 |
|------|------|
| `subtitle_pipeline.py` | CLI 入口。解析命令行参数，构建配置字典，调用 `SubtitlePipeline.process()` |
| `run.py` | Flask Web 服务器启动脚本。加载 `.env`，创建 app，监听端口 |
| `app/routes.py` | Flask 路由定义。提供 REST API 和 HTML 前端 |

### 2.2 核心层

| 文件 | 作用 |
|------|------|
| `app/models/subtitle_pipeline.py` | **核心引擎**。包含 `SubtitlePipeline`（语音识别+翻译）和 `SubtitleJobManager`（Web 端的任务管理） |
| `app/models/cost_estimator.py` | 预运行成本估算。根据已有 `.orig.srt` 或 `ffprobe` 时长估算 Token 数和费用 |
| `config.py` | 环境变量驱动的配置类 `Config`。所有可调参数从这里读取 |

### 2.3 辅助模块

| 文件 | 作用 |
|------|------|
| `drama_subtitler/` | 可 pip 安装的库封装。`pipeline.py` 通过 `importlib` 动态加载核心模块，避免强制依赖 Flask |
| `tests/` | 单元测试。覆盖编码回退、mojibake 修复、翻译分块、错误恢复等逻辑 |

---

## 3. 核心模块详解

### 3.1 `config.py` — 配置中心

所有参数都通过环境变量（或 `.env` / `.env.local`）注入，优先级：

```
shell 环境变量 > .env.local > .env > 代码默认值
```

主要配置分组：

- **Whisper**: `WHISPER_BACKEND`, `WHISPER_MODEL`, `WHISPER_CPP_MODEL_PATH` 等
- **翻译后端**: `TRANSLATION_BACKEND`（`ollama` / `openrouter` / `deepseek`）
- **目标语言**: `TARGET_LANGUAGE`（默认 `zh`）
- **媒体目录**: `MEDIA_DIR`

**注意**: 默认后端在 Apple Silicon 上自动选 `whispercpp`，其他平台用 `faster-whisper`。

### 3.2 `SubtitlePipeline` — 核心管道

#### 3.2.1 主流程 `process()`

```python
def process(self, media_path, ...):
    # 1. 语音识别 (Whisper) → .orig.srt
    # 2. 翻译 (LLM) → .bilingual.srt
    # 3. 返回结果字典
```

**阶段 1：语音识别**

根据 `WHISPER_BACKEND` 分发到两个后端：

- **`_transcribe_with_faster_whisper()`**: 调用 `faster-whisper` Python 库。支持 `language_hint` 参数，会自动探测语言并返回 `(segments, detected_language)`。
- **`_transcribe_with_whispercpp()`**: 调用外部 `whisper-cli` 命令行工具。先用 `ffmpeg` 提取 16kHz mono WAV，再执行 whisper.cpp。解析其 JSON 输出生成 segments。

**关键处理：编码回退与 mojibake 修复**

whisper.cpp 在 macOS / 日语 locale 下可能输出非 UTF-8 的 JSON。`_load_json_with_fallback()` 按以下顺序尝试解码：

1. 系统默认编码
2. `utf-8`
3. `cp932`（日文的 Shift-JIS）
4. `shift_jis`
5. `cp949`（韩文）
6. `euc-kr`
7. `latin-1`

此外，`_repair_mojibake_text()` 检测一种常见损坏模式：UTF-8 文本被错误地按 `latin-1` 解码后再按 `utf-8` 编码。通过统计 CJK 字符数来判断是否需要修复。

**阶段 2：翻译**

调用 `_translate_segments()`，将识别出的文本分批送入 LLM：

1. 将 `segments` 按 `TRANSLATION_CHUNK_SIZE`（默认 20）分块
2. 对每个 chunk 调用 `_translate_with_recovery()`
3. 结果写入 `.bilingual.srt`，格式为 `原文\n译文`

#### 3.2.2 翻译容错 `_translate_with_recovery()`

这是项目中最复杂、最重要的容错逻辑：

```
翻译 chunk:
  ├─ 成功（返回的 item 数量与输入一致）→ 返回
  ├─ 数量不匹配 → 拆分为两半，递归重试
  ├─ HTTP 错误:
  │   ├─ 400 + "json mode is not supported" → 禁用 JSON mode，重试一次
  │   ├─ 401/403/404 → FatalTranslationError，立即终止
  │   └─ 其他 → 记录错误，拆分重试
  ├─ 网络错误 / JSON 解析失败 → 记录错误，拆分重试
  └─ 拆分至单条仍失败 → _fallback_translations() 逐条翻译
```

**核心思想**：二分拆分 + 错误预算（`_translation_error_budget`，默认 10）。只要错误不超过预算，就不断把失败 chunk 拆小重试，直到逐条翻译。

**自动降级 JSON mode**

部分免费/廉价模型不支持 `response_format: {type: "json_object"}`。当收到 `"json mode is not supported"` 时，`_json_mode_enabled` 被设为 `False`，后续所有请求改用纯文本 prompt + JSON 提取。这个状态是**每轮 `process()` 独立**的。

#### 3.2.3 Prompt 设计

Chunk 翻译的 prompt 结构：

```
System: 你是一个专业字幕翻译。源语言: <source>。目标语言: <target>。输出必须是合法 JSON。

User: 
  你正在将 <source> 电视剧对白翻译成 <target>。
  返回严格 JSON: {"items": [{"target": "..."}]}
  每条对应一句输入字幕，保持顺序。
  要求：自然、有情感、符合口语习惯；保留专有名词；不加注释、说话人标签或引号。

  Input JSON:
  {"items": ["原文1", "原文2", ...]}
```

System prompt 和 User prompt 分离的设计，是为了让 LLM 更好地理解角色定位。

**输出解析的兼容性**

`_translate_chunk()` 返回的 JSON 中，每条 item 会按以下优先级取译文：
1. `item["target"]`
2. `item["<target_lang_code>"]`（如 `"zh"`）
3. `item["translation"]`

这提高了对廉价模型/不规范输出的兼容性。

### 3.3 `SubtitleJobManager` — Web 任务管理

Web UI 需要异步执行耗时任务（语音识别+翻译），`SubtitleJobManager` 提供了基于线程的任务调度：

```python
class SubtitleJobManager:
    def start_job(...):  # 创建线程，在线程中执行 pipeline.process()
    def get_job(job_id): # 查询任务状态
    def cancel_job(job_id): # 设置 cancel_event，中断翻译循环
```

任务状态存储在内存字典中（`{job_id: {status, progress, message, ...}}`），由锁保护。**重启 Flask 进程会丢失任务状态**，但已生成的 SRT 文件保留在磁盘上。

两阶段工作流：
1. **先语音识别**: `process(stop_after_transcription=True)` 只生成 `.orig.srt`
2. **后翻译**: `start_translation()` 复用已有的 `.orig.srt`，执行翻译阶段

### 3.4 `cost_estimator.py` — 费用估算

提供预运行成本预估，无需真正调用 API：

- **如果有 `.orig.srt`**: 读取真实字幕文本，按字符数估算 Token
- **如果没有**: 用 `ffprobe` 获取视频时长，按经验值（~6 行/分钟，~18 字符/行）估算

Token 估算公式：

```
input_tokens  ≈ text_tokens + line_overhead + chunk_overhead
output_tokens ≈ text_tokens + line_count * 4

其中:
  text_tokens  = ceil(总字符数 / 2.0)    # CJK 混合文本保守估计
  line_overhead = 行数 * 6              # 每行 JSON 包装开销
  chunk_overhead = chunk数 * 220        # 每 chunk 的 system + user prompt 开销
```

## 4. 数据流

### 4.1 CLI 完整流程

```
subtitle_pipeline.py
  │
  ├─ load_env_files()          # .env / .env.local
  ├─ build_config()            # config.py → dict
  ├─ resolve_input_path()      # 解析输入文件路径
  │
  ▼
SubtitlePipeline.process()
  │
  ├─ _transcribe()
  │   ├─ faster-whisper / whisper.cpp
  │   ├─ _dedupe_repeated_segments()  # 消除 Whisper 幻觉重复
  │   └─ write_srt() → .orig.srt
  │
  ├─ _translate_segments()
  │   ├─ _translate_with_recovery()     # 二分拆分容错
  │   │   └─ _translate_chunk()         # LLM API 调用
  │   └─ write_srt() → .bilingual.srt
  │
  └─ 返回 result dict
```

### 4.2 Web API 流程

```
POST /api/jobs
  │
  ├─ 上传文件 → media/uploads/
  │  或选择已有文件
  │
  ▼
SubtitleJobManager.start_job()
  │
  ├─ 在线程中执行 pipeline.process()
  ├─ 通过 progress_cb 更新进度到 jobs[job_id]
  │
GET /api/jobs/<id>          # 轮询进度
GET /api/jobs/<id>/download/original|bilingual  # 下载 SRT
```

进度百分比分配（大致）：
- 0% → 语音识别开始
- 0-60% → Whisper 语音识别进度
- 60-95% → 翻译进度
- 100% → 完成

## 5. 关键设计决策

### 5.1 为什么不统一用一种 Whisper 后端？

- `faster-whisper`：纯 Python，安装简单，跨平台，但 Apple Silicon 上性能一般
- `whisper.cpp`：C++ 实现，Apple Silicon 上利用 Metal/GPU 加速显著更快

默认策略：Apple Silicon 自动选 `whispercpp`，其他平台用 `faster-whisper`。用户可通过环境变量覆盖。

### 5.2 为什么用二分拆分而不是固定大小重试？

翻译失败通常由两类原因导致：
1. **全局原因**（API 密钥错误、模型不支持）→ 不应重试，应立即终止
2. **局部原因**（某一行过长、某一组语义太复杂）→ 拆分该组即可

二分拆分能精确定位到 problematic 的最小子集，同时保留其他已成功部分的上下文。配合错误预算（默认 10 次），避免无限递归。

### 5.3 为什么保留原始语言行而不是只输出译文？

双语字幕的格式是 `原文\n译文`，而非纯译文。这是因为：
- 学习者可以对照原文
- 翻译错误时可以通过原文校对
- 播放器/字幕组通常需要双语版本

### 5.4 为什么翻译 prompt 强调"电视剧对白"？

与通用翻译不同，电视剧字幕有独特要求：
- 口语化（不能书面化）
- 敬语/平语对应（韩语 반말 vs 존댓말）
- 情感节奏匹配（感叹号、省略号的处理）
- 专有名词保留（人名、地名不翻译）

Prompt 中明确加入这些约束，显著提高了翻译质量。

## 6. 测试

```bash
source .venv/bin/activate
pytest -v
```

当前测试覆盖：

| 测试 | 说明 |
|------|------|
| `test_load_json_with_fallback_*` | `cp932` / `cp949` 编码回退 |
| `test_count_cjk_chars_includes_hangul` | CJK + 韩文字符统计 |
| `test_repair_mojibake_text_*` | mojibake 检测与修复 |
| `test_translate_with_recovery_splits_*` | 二分拆分的调用次数验证 |
| `test_translate_chunk_accepts_zh_key` | 输出兼容 `zh` / `target` / `translation` 三种 key |
| `test_translate_segments_emits_*` | 双语输出格式验证 |
| `test_process_skip_transcription_*` | 复用已有 `.orig.srt` 和 source_language_hint |
| `test_estimate_*` | 费用估算逻辑 |

**未覆盖（设计如此）**：
- OpenRouter SSE 解析器：纯 I/O 包装，与 proven 版本一致
- Whisper 子进程路径：依赖外部二进制和真实音频

## 7. 扩展与定制

### 7.1 添加新的翻译后端

在 `SubtitlePipeline` 中：
1. 在 `__init__` 中新增配置参数
2. 在 `_chat_completion()` 中新增分支
3. 实现 `_chat_completion_<backend>()`，返回模型生成的字符串
4. 更新 `_record_usage()` 以统计 Token 用量

### 7.2 修改翻译 prompt

编辑 `_translate_chunk()` 和 `_translate_single()` 中的 `messages` 列表。**注意**：prompt 变更应在真实 30-60 秒片段上测试后再提交，廉价模型对 prompt 非常敏感。

### 7.3 添加持久化任务状态

当前 `SubtitleJobManager.jobs` 是内存字典。要支持跨重启保留，可在 `start_job()` 中将状态写入 SQLite/JSON 文件，在 `get_job()` 中读取。

---

## 8. 技术栈

- **Python**: >= 3.10
- **语音识别**: OpenAI Whisper（`faster-whisper` 或 `whisper.cpp`）
- **翻译**: Ollama / OpenRouter / DeepSeek（OpenAI-compatible API）
- **Web**: Flask
- **测试**: pytest + pytest-mock
- **构建**: setuptools（`pyproject.toml`）
