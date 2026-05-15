#!/usr/bin/env python3
"""Minimal remote whisper inference server for GPU offload.

Run this on your gaming PC (the one with the NVIDIA GPU):

    pip install faster-whisper flask
    python whisper-server.py --host 0.0.0.0 --port 5051

Then set drama_subtitler's GPU_BASE_URL to http://<pc-ip> (whisper is reached at port 5051).
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time

from flask import Flask, jsonify, request

try:
    from faster_whisper import WhisperModel
except ImportError as exc:
    raise SystemExit(
        "faster-whisper is required: pip install faster-whisper"
    ) from exc

app = Flask(__name__)
_model: WhisperModel | None = None
_model_name: str = ""


def load_model(name: str):
    global _model, _model_name
    print(f"Loading whisper model: {name}...", flush=True)
    start = time.time()
    _model = WhisperModel(name, device="cuda", compute_type="float16")
    _model_name = name
    print(f"Model loaded in {time.time() - start:.1f}s", flush=True)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "model": _model_name,
        "device": "cuda",
    })


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "missing 'audio' file in multipart body"}), 400

    audio = request.files["audio"]
    lang_hint = (request.form.get("language") or "").strip().lower() or None
    file_size_mb = len(audio.read()) / (1024 * 1024)
    audio.seek(0)
    client = request.remote_addr or "unknown"
    print(f"[{time.strftime('%H:%M:%S')}] Transcribe request from {client} - {file_size_mb:.1f} MB", flush=True)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        audio.save(tmp_path)

    try:
        start = time.time()
        print(f"  -> transcribing with {_model_name}...", flush=True)
        segments, info = _model.transcribe(
            tmp_path,
            language=lang_hint,
            condition_on_previous_text=False,
            vad_filter=True,
            no_repeat_ngram_size=3,
            repetition_penalty=1.05,
            compression_ratio_threshold=2.4,
        )
        segs = [
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
            }
            for s in segments
        ]
        elapsed = time.time() - start
        print(f"  -> done in {elapsed:.1f}s, {len(segs)} segments, detected lang={info.language}", flush=True)
        return jsonify({
            "language": info.language,
            "duration": round(info.duration, 3),
            "segments": segs,
            "elapsed_seconds": round(elapsed, 2),
        })
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Remote faster-whisper server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5051)
    parser.add_argument("--model", default="large-v3")
    args = parser.parse_args()

    load_model(args.model)
    print(f"Listening on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
