#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Flask launcher for drama_subtitler."""

import argparse
import sys
import threading
import webbrowser

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(dotenv_path=".env", override=False)
    load_dotenv(dotenv_path=".env.local", override=False)

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

from app import create_app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drama Subtitler web UI")
    parser.add_argument("--port", type=int, default=5050, help="Web server port")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--media-dir", help="Override MEDIA_DIR")
    parser.add_argument("--browser", action="store_true", help="Open browser automatically")
    args = parser.parse_args()

    if args.media_dir:
        app.config["MEDIA_DIR"] = args.media_dir

    url = f"http://{args.host}:{args.port}"
    if args.browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"Starting drama_subtitler at {url}")
    print(f"MEDIA_DIR: {app.config['MEDIA_DIR']}")
    app.run(host=args.host, port=args.port, debug=False)
