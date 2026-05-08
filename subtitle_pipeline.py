#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLI entry point for the drama subtitle pipeline."""

import argparse
import shutil
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def load_env_files():
    if load_dotenv is None:
        return
    # Keep shell-exported vars as highest priority.
    load_dotenv(dotenv_path=".env", override=False)
    load_dotenv(dotenv_path=".env.local", override=False)


def build_config(overrides):
    from config import Config

    cfg = {
        "MEDIA_DIR": Config.MEDIA_DIR,
        "WHISPER_BACKEND": Config.WHISPER_BACKEND,
        "WHISPER_MODEL": Config.WHISPER_MODEL,
        "WHISPER_DEVICE": Config.WHISPER_DEVICE,
        "WHISPER_COMPUTE_TYPE": Config.WHISPER_COMPUTE_TYPE,
        "WHISPER_CPP_COMMAND": Config.WHISPER_CPP_COMMAND,
        "WHISPER_CPP_MODEL_PATH": Config.WHISPER_CPP_MODEL_PATH,
        "WHISPER_CPP_THREADS": Config.WHISPER_CPP_THREADS,
        "TRANSLATION_BACKEND": Config.TRANSLATION_BACKEND,
        "OLLAMA_BASE_URL": Config.OLLAMA_BASE_URL,
        "OPENROUTER_BASE_URL": Config.OPENROUTER_BASE_URL,
        "OPENROUTER_API_KEY": Config.OPENROUTER_API_KEY,
        "OPENROUTER_REFERER": Config.OPENROUTER_REFERER,
        "OPENROUTER_APP_TITLE": Config.OPENROUTER_APP_TITLE,
        "DEEPSEEK_BASE_URL": Config.DEEPSEEK_BASE_URL,
        "DEEPSEEK_API_KEY": Config.DEEPSEEK_API_KEY,
        "TRANSLATION_MODEL": Config.TRANSLATION_MODEL,
        "TRANSLATION_CHUNK_SIZE": Config.TRANSLATION_CHUNK_SIZE,
        "TRANSLATION_TIMEOUT": Config.TRANSLATION_TIMEOUT,
        "TARGET_LANGUAGE": Config.TARGET_LANGUAGE,
    }
    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value
    return cfg


def resolve_input_path(input_arg, media_dir):
    input_path = Path(input_arg)
    if input_path.exists():
        return input_path.resolve()

    candidate = (Path(media_dir) / input_arg).resolve()
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"Input file not found: {input_arg} (also checked {candidate})"
    )


def main():
    load_env_files()

    parser = argparse.ArgumentParser(
        description="Transcribe Japanese / Korean drama videos and emit bilingual SRT subtitles."
    )
    parser.add_argument("input", help="Input media path or filename inside MEDIA_DIR")
    parser.add_argument(
        "--output-dir",
        help="Optional output directory for SRT files (default: next to input media)",
    )
    parser.add_argument(
        "--source-language",
        help="Force source language (e.g. ja, ko). Default: auto-detect.",
    )
    parser.add_argument("--whisper-backend", help="Whisper backend (faster-whisper/whispercpp/auto)")
    parser.add_argument("--whisper-model", help="Whisper model name")
    parser.add_argument("--whisper-device", help="Whisper device (auto/cpu/cuda)")
    parser.add_argument("--whisper-compute-type", help="Whisper compute type")
    parser.add_argument("--whispercpp-command", help="whisper.cpp CLI command name/path")
    parser.add_argument("--whispercpp-model-path", help="Path to whisper.cpp ggml model file")
    parser.add_argument("--whispercpp-threads", type=int, help="whisper.cpp thread count")
    parser.add_argument("--translation-backend", help="Translation backend ('ollama', 'openrouter', or 'deepseek')")
    parser.add_argument("--translation-model", help="Translation model name (Ollama tag, OpenRouter slug, or DeepSeek model)")
    parser.add_argument("--translation-chunk-size", type=int, help="Subtitle lines per translation chunk")
    parser.add_argument("--translation-timeout", type=int, help="Translation request timeout seconds")
    parser.add_argument("--ollama-base-url", help="Ollama base URL")
    parser.add_argument("--openrouter-base-url", help="OpenRouter base URL")
    parser.add_argument("--openrouter-api-key", help="OpenRouter API key (overrides env)")
    parser.add_argument("--deepseek-base-url", help="DeepSeek base URL")
    parser.add_argument("--deepseek-api-key", help="DeepSeek API key (overrides env)")
    parser.add_argument("--target-language", help="Target language code (default: zh)")
    parser.add_argument(
        "--skip-transcription",
        action="store_true",
        help="Skip transcription and reuse existing .orig.srt next to input media",
    )
    parser.add_argument(
        "--show-translation-stream",
        action="store_true",
        help="Show live translation model output while translating each chunk",
    )

    args = parser.parse_args()

    overrides = {
        "WHISPER_BACKEND": args.whisper_backend,
        "WHISPER_MODEL": args.whisper_model,
        "WHISPER_DEVICE": args.whisper_device,
        "WHISPER_COMPUTE_TYPE": args.whisper_compute_type,
        "WHISPER_CPP_COMMAND": args.whispercpp_command,
        "WHISPER_CPP_MODEL_PATH": args.whispercpp_model_path,
        "WHISPER_CPP_THREADS": args.whispercpp_threads,
        "TRANSLATION_BACKEND": args.translation_backend,
        "TRANSLATION_MODEL": args.translation_model,
        "TRANSLATION_CHUNK_SIZE": args.translation_chunk_size,
        "TRANSLATION_TIMEOUT": args.translation_timeout,
        "OLLAMA_BASE_URL": args.ollama_base_url,
        "OPENROUTER_BASE_URL": args.openrouter_base_url,
        "OPENROUTER_API_KEY": args.openrouter_api_key,
        "DEEPSEEK_BASE_URL": args.deepseek_base_url,
        "DEEPSEEK_API_KEY": args.deepseek_api_key,
        "TARGET_LANGUAGE": args.target_language,
    }
    cfg = build_config(overrides)

    media_path = resolve_input_path(args.input, cfg["MEDIA_DIR"])

    from app.models.subtitle_pipeline import SubtitlePipeline

    pipeline = SubtitlePipeline(cfg)

    def progress_cb(progress, message):
        print(f"\r[{progress:3d}%] {message}", end="", flush=True)

    def translation_stream_cb(text):
        if not args.show_translation_stream:
            return
        print(text, end="", flush=True)

    print(f"Input: {media_path}")
    print(
        f"Whisper: {cfg['WHISPER_MODEL']} via {cfg['WHISPER_BACKEND']} "
        f"({cfg['WHISPER_DEVICE']}/{cfg['WHISPER_COMPUTE_TYPE']})"
    )
    print(f"Translator: {cfg['TRANSLATION_MODEL']} (backend={cfg['TRANSLATION_BACKEND']})")
    print(f"Target language: {cfg['TARGET_LANGUAGE']}")

    try:
        result = pipeline.process(
            media_path,
            progress_cb=progress_cb,
            skip_transcription=args.skip_transcription,
            translation_stream_cb=translation_stream_cb if args.show_translation_stream else None,
            source_language_hint=args.source_language,
        )
        print("\nDone")
    except Exception as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    original_srt = Path(result["original_srt"])
    bilingual_srt = Path(result["bilingual_srt"])

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target_original = out_dir / original_srt.name
        target_bilingual = out_dir / bilingual_srt.name
        shutil.copy2(original_srt, target_original)
        shutil.copy2(bilingual_srt, target_bilingual)
        original_srt = target_original
        bilingual_srt = target_bilingual

    print(f"Source language: {result['source_language']}")
    print(f"Segments: {result['segment_count']}")
    print(f"Original SRT: {original_srt}")
    print(f"Bilingual SRT: {bilingual_srt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
