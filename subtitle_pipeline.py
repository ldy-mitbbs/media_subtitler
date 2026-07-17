#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLI entry point for the subtitle pipeline."""

import argparse
import os
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
    root = Path(__file__).resolve().parent
    # Keep shell-exported vars as highest priority.
    load_dotenv(dotenv_path=root / ".env", override=False)
    load_dotenv(dotenv_path=root / ".env.local", override=False)


def configure_windows_console():
    if sys.platform != "win32":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_config(overrides):
    from config import Config

    cfg = {
        "MEDIA_DIR": Config.MEDIA_DIR,
        "ASR_BACKEND": Config.ASR_BACKEND,
        "ASR_MODEL": Config.ASR_MODEL,
        "ASR_DEVICE": Config.ASR_DEVICE,
        "ASR_COMPUTE_TYPE": Config.ASR_COMPUTE_TYPE,
        "QWEN_ASR_CHUNK_SECONDS": Config.QWEN_ASR_CHUNK_SECONDS,
        "WHISPER_CPP_COMMAND": Config.WHISPER_CPP_COMMAND,
        "WHISPER_CPP_MODEL_PATH": Config.WHISPER_CPP_MODEL_PATH,
        "WHISPER_CPP_THREADS": Config.WHISPER_CPP_THREADS,
        "GPU_BASE_URL": Config.GPU_BASE_URL,
        "REMOTE_WHISPER_BASE_URL": Config.REMOTE_WHISPER_BASE_URL,
        "TRANSLATION_BACKEND": Config.TRANSLATION_BACKEND,
        "OLLAMA_BASE_URL": Config.OLLAMA_BASE_URL,
        "LMSTUDIO_BASE_URL": Config.LMSTUDIO_BASE_URL,
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
    gpu_base_url = str(cfg.get("GPU_BASE_URL") or "").rstrip("/").rstrip(":")
    if gpu_base_url:
        cfg["GPU_BASE_URL"] = gpu_base_url
        if not cfg.get("REMOTE_WHISPER_BASE_URL"):
            cfg["REMOTE_WHISPER_BASE_URL"] = f"{gpu_base_url}:5051"
        if not overrides.get("OLLAMA_BASE_URL") and not os.environ.get("OLLAMA_BASE_URL"):
            cfg["OLLAMA_BASE_URL"] = f"{gpu_base_url}:11434"
        if not overrides.get("LMSTUDIO_BASE_URL") and not os.environ.get("LMSTUDIO_BASE_URL"):
            cfg["LMSTUDIO_BASE_URL"] = f"{gpu_base_url}:1234/v1"
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
    configure_windows_console()
    load_env_files()

    parser = argparse.ArgumentParser(
        description="Transcribe Japanese / Korean media files and emit bilingual SRT subtitles."
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
    parser.add_argument("--asr-backend", help="ASR backend (auto/faster-whisper/remote-faster-whisper/whispercpp/openai/qwen3-asr/openrouter)")
    parser.add_argument("--asr-model", help="ASR model name")
    parser.add_argument("--asr-device", help="ASR device (auto/cpu/cuda)")
    parser.add_argument("--asr-compute-type", help="ASR compute type")
    parser.add_argument("--qwen-asr-chunk-seconds", type=int, help="Qwen3-ASR audio chunk length")
    parser.add_argument("--whisper-backend", help=argparse.SUPPRESS)
    parser.add_argument("--whisper-model", help=argparse.SUPPRESS)
    parser.add_argument("--whisper-device", help=argparse.SUPPRESS)
    parser.add_argument("--whisper-compute-type", help=argparse.SUPPRESS)
    parser.add_argument("--whispercpp-command", help="whisper.cpp CLI command name/path")
    parser.add_argument("--whispercpp-model-path", help="Path to whisper.cpp ggml model file")
    parser.add_argument("--whispercpp-threads", type=int, help="whisper.cpp thread count")
    parser.add_argument("--gpu-base-url", help="Remote GPU base URL; derives Ollama :11434, LM Studio :1234 and Whisper :5051")
    parser.add_argument("--remote-whisper-base-url", help="Remote faster-whisper server URL")
    parser.add_argument("--translation-backend", help="Translation backend ('ollama', 'lmstudio', 'openrouter', or 'deepseek')")
    parser.add_argument("--translation-model", help="Translation model name (Ollama tag, LM Studio model key, OpenRouter slug, or DeepSeek model)")
    parser.add_argument("--translation-chunk-size", type=int, help="Subtitle lines per translation chunk")
    parser.add_argument("--translation-timeout", type=int, help="Translation request timeout seconds")
    parser.add_argument("--ollama-base-url", help="Ollama base URL")
    parser.add_argument("--lmstudio-base-url", help="LM Studio OpenAI-compatible base URL (e.g. http://host:1234/v1)")
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
        "ASR_BACKEND": args.asr_backend or args.whisper_backend,
        "ASR_MODEL": args.asr_model or args.whisper_model,
        "ASR_DEVICE": args.asr_device or args.whisper_device,
        "ASR_COMPUTE_TYPE": args.asr_compute_type or args.whisper_compute_type,
        "QWEN_ASR_CHUNK_SECONDS": args.qwen_asr_chunk_seconds,
        "WHISPER_CPP_COMMAND": args.whispercpp_command,
        "WHISPER_CPP_MODEL_PATH": args.whispercpp_model_path,
        "WHISPER_CPP_THREADS": args.whispercpp_threads,
        "GPU_BASE_URL": args.gpu_base_url,
        "REMOTE_WHISPER_BASE_URL": args.remote_whisper_base_url,
        "TRANSLATION_BACKEND": args.translation_backend,
        "TRANSLATION_MODEL": args.translation_model,
        "TRANSLATION_CHUNK_SIZE": args.translation_chunk_size,
        "TRANSLATION_TIMEOUT": args.translation_timeout,
        "OLLAMA_BASE_URL": args.ollama_base_url,
        "LMSTUDIO_BASE_URL": args.lmstudio_base_url,
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
        f"ASR: {cfg['ASR_MODEL']} via {cfg['ASR_BACKEND']} "
        f"({cfg['ASR_DEVICE']}/{cfg['ASR_COMPUTE_TYPE']})"
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
    bilingual_ass = Path(result["bilingual_ass"])

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target_original = out_dir / original_srt.name
        target_bilingual = out_dir / bilingual_srt.name
        target_bilingual_ass = out_dir / bilingual_ass.name
        shutil.copy2(original_srt, target_original)
        shutil.copy2(bilingual_srt, target_bilingual)
        shutil.copy2(bilingual_ass, target_bilingual_ass)
        original_srt = target_original
        bilingual_srt = target_bilingual
        bilingual_ass = target_bilingual_ass

    print(f"Source language: {result['source_language']}")
    print(f"Segments: {result['segment_count']}")
    print(f"Original SRT: {original_srt}")
    print(f"Bilingual SRT: {bilingual_srt}")
    print(f"Styled bilingual ASS: {bilingual_ass}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
