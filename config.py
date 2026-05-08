import os
import platform
import sys


IS_APPLE_SILICON = sys.platform == "darwin" and platform.machine() in ("arm64", "aarch64")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-key-drama-subtitler"

    # Media library directory.
    MEDIA_DIR = os.environ.get("MEDIA_DIR") or "media"

    # --- Whisper ---
    WHISPER_BACKEND = os.environ.get("WHISPER_BACKEND") or (
        "whispercpp" if IS_APPLE_SILICON else "faster-whisper"
    )
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL") or "large-v3"
    WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE") or "auto"
    WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE") or (
        "int8" if IS_APPLE_SILICON else "auto"
    )
    WHISPER_CPP_COMMAND = os.environ.get("WHISPER_CPP_COMMAND") or "whisper-cli"
    WHISPER_CPP_MODEL_PATH = os.environ.get("WHISPER_CPP_MODEL_PATH") or ""
    WHISPER_CPP_THREADS = int(os.environ.get("WHISPER_CPP_THREADS") or 0)

    # --- Translation ---
    TRANSLATION_BACKEND = (os.environ.get("TRANSLATION_BACKEND") or "openrouter").strip().lower()
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
    OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or ""
    OPENROUTER_REFERER = os.environ.get("OPENROUTER_REFERER") or ""
    OPENROUTER_APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE") or "drama_subtitler"

    DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or ""

    if TRANSLATION_BACKEND == "openrouter":
        _DEFAULT_TRANSLATION_MODEL = "deepseek/deepseek-v4-flash"
    elif TRANSLATION_BACKEND == "deepseek":
        _DEFAULT_TRANSLATION_MODEL = "deepseek-v4-flash"
    else:
        _DEFAULT_TRANSLATION_MODEL = "qwen2.5:14b"
    TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL") or _DEFAULT_TRANSLATION_MODEL
    TRANSLATION_CHUNK_SIZE = int(
        os.environ.get("TRANSLATION_CHUNK_SIZE") or (16 if IS_APPLE_SILICON else 20)
    )
    TRANSLATION_TIMEOUT = int(os.environ.get("TRANSLATION_TIMEOUT") or 120)

    # --- Languages ---
    TARGET_LANGUAGE = (os.environ.get("TARGET_LANGUAGE") or "zh").strip().lower()
