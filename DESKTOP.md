# macOS 桌面应用

桌面版复用现有 Flask 界面和字幕处理管道，只是在外层包了一层原生 macOS 窗口。启动器会在随机本地端口启动 Flask 应用，再用 `pywebview` 打开窗口。

## 本地运行

```bash
python3.12 -m venv .venv-desktop
source .venv-desktop/bin/activate
pip install -r requirements-desktop.txt
python desktop_launcher.py
```

如果本地开发环境缺少 `pywebview`，启动器会退回到默认浏览器打开界面。

## 构建 macOS `.app`

```bash
./scripts/build-macos-app.sh
```

构建结果位于：

```text
dist/Media Subtitler.app
```

发布给其他机器前，需要对 `.app` 做代码签名和公证。上架 App Store 还需要额外处理沙盒限制，因此第一版更适合直接分发。

## 桌面版功能

- 使用原生 macOS 窗口承载现有界面。
- 支持把媒体文件拖进窗口，自动填入本地路径并刷新费用估算。
- 内置一段机器生成的日语测试视频，可一键填入路径，用来排查本机 `ffmpeg`、`whisper.cpp` 和模型配置。
- 设置保存在 Application Support，不写入应用包内部。
- 与 Web 版使用不同的 Finder 入口，互不干扰。

## 排障测试

桌面版界面的「本地文件路径」旁边有「使用测试视频」按钮。点击后，应用会把内置日语测试视频复制到：

```text
~/Library/Application Support/Media Subtitler/Media/media-subtitler-japanese-test.mp4
```

然后自动填入路径。点击「直接运行」即可验证本地语音识别链路是否可用。正常情况下，任务会先停在「等待翻译」状态，并生成：

```text
~/Library/Application Support/Media Subtitler/Media/media-subtitler-japanese-test.orig.srt
```

如果这个测试视频能成功转写，但你自己的媒体文件失败，问题通常在媒体文件本身、内嵌字幕、文件路径或对应输出目录权限上。

## Finder 入口

Web 版和桌面版使用两个独立的 Finder 辅助应用：

```text
~/Applications/Media Subtitler 网页版启动任务.app
~/Applications/Media Subtitler 桌面版启动任务.app
```

Web 入口会提交任务到固定的 `http://127.0.0.1:5050` 服务，并在需要时启动 `run.py`。桌面入口会提交任务到当前运行中的桌面应用；如果桌面应用没有运行，会先尝试打开 `Media Subtitler.app`。

在桌面应用里点击「安装 / 更新 Finder 入口」会安装桌面版入口。

## 运行时数据

桌面版设置保存在应用包外：

```text
~/Library/Application Support/Media Subtitler/settings.json
```

如果没有配置 `MEDIA_DIR`，上传文件和默认媒体输出目录为：

```text
~/Library/Application Support/Media Subtitler/Media
```

通过本地路径创建的任务仍然会把字幕写到源视频旁边，和 Web 版行为一致。
