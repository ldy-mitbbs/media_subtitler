#!/usr/bin/env python3
"""Media Subtitler 桌面启动器。

保留现有 Flask 界面和处理管道，只在外层提供一个 macOS 原生窗口。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
import json
from pathlib import Path

from werkzeug.serving import make_server


APP_NAME = "Media Subtitler"


def _app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "media-subtitler"


def _prepare_desktop_environment() -> Path:
    support_dir = _app_support_dir()
    support_dir.mkdir(parents=True, exist_ok=True)

    current_path = os.environ.get("PATH", "")
    gui_safe_paths = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    path_parts = [p for p in current_path.split(os.pathsep) if p]
    for path in reversed(gui_safe_paths):
        if path not in path_parts:
            path_parts.insert(0, path)
    os.environ["PATH"] = os.pathsep.join(path_parts)

    os.environ.setdefault(
        "MEDIA_SUBTITLER_SETTINGS_PATH",
        str(support_dir / "settings.json"),
    )
    os.environ.setdefault("MEDIA_SUBTITLER_DESKTOP", "1")
    return support_dir


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FlaskServerThread(threading.Thread):
    def __init__(self, flask_app, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.server = make_server(host, port, flask_app, threaded=True)
        self.context = flask_app.app_context()

    def run(self) -> None:
        self.context.push()
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()


def _create_flask_app(support_dir: Path):
    from app import create_app

    flask_app = create_app()
    configured_media_dir = str(flask_app.config.get("MEDIA_DIR") or "").strip()
    if not configured_media_dir or configured_media_dir == "media":
        media_dir = support_dir / "Media"
        media_dir.mkdir(parents=True, exist_ok=True)
        flask_app.config["MEDIA_DIR"] = str(media_dir)
    return flask_app


def _server_info_path(support_dir: Path) -> Path:
    return support_dir / "server.json"


def _write_server_info(support_dir: Path, url: str) -> None:
    payload = {"url": url, "pid": os.getpid(), "app": APP_NAME}
    _server_info_path(support_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_server_info(support_dir: Path) -> None:
    try:
        _server_info_path(support_dir).unlink()
    except FileNotFoundError:
        pass


def _bind_desktop_file_drop(window) -> None:
    def on_drop(event):
        files = (event or {}).get("dataTransfer", {}).get("files", [])
        if not files:
            return
        path = files[0].get("pywebviewFullPath") or files[0].get("path") or files[0].get("name")
        if path:
            window.evaluate_js(f"window.mediaSubtitlerSetDroppedPath({json.dumps(path)});")

    def on_loaded():
        try:
            window.dom.document.events.drop += on_drop
        except Exception as exc:
            print(f"无法启用桌面拖放文件功能: {exc}", file=sys.stderr)

    window.events.loaded += on_loaded


def main() -> int:
    support_dir = _prepare_desktop_environment()
    flask_app = _create_flask_app(support_dir)

    host = "127.0.0.1"
    port = _find_free_port()
    server = FlaskServerThread(flask_app, host, port)
    server.start()

    url = f"http://{host}:{port}"
    _write_server_info(support_dir, url)
    time.sleep(0.25)

    try:
        import webview
    except ImportError:
        print("未安装 pywebview；将使用默认浏览器打开桌面界面。")
        print(url)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()
            return 0

    window = webview.create_window(
        APP_NAME,
        url,
        width=1180,
        height=840,
        min_size=(900, 640),
        text_select=True,
    )
    _bind_desktop_file_drop(window)

    try:
        webview.start()
    finally:
        _remove_server_info(support_dir)
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
