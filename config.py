import json
import os
import platform
import sys
from pathlib import Path


IS_APPLE_SILICON = sys.platform == "darwin" and platform.machine() in ("arm64", "aarch64")


def _settings_path() -> Path:
    override = os.environ.get("MEDIA_SUBTITLER_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "settings.json"


def _load_settings():
    path = _settings_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _get_setting(settings, key, default=""):
    """Read a single key from settings dict, falling back to a default."""
    return settings.get(key, default)


def _get_nonempty_setting(settings, key, default=""):
    """Read a setting, treating blank strings as missing."""
    val = settings.get(key)
    if isinstance(val, str) and not val.strip():
        return default
    if val is None:
        return default
    return val


def _get_setting_int(settings, key, default=0):
    raw = _get_setting(settings, key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _seed_settings_from_env():
    """On first run (no settings.json), copy known env vars into settings.json.

    This allows existing users who configured via .env to keep their settings
    when they upgrade to the UI-managed settings.json model. After seeding,
    settings.json is the single source of truth.
    """
    known_keys = {
        "SECRET_KEY",
        "MEDIA_DIR",
        "ASR_BACKEND",
        "ASR_MODEL",
        "ASR_DEVICE",
        "ASR_COMPUTE_TYPE",
        "QWEN_ASR_CHUNK_SECONDS",
        "WHISPER_BACKEND",
        "WHISPER_MODEL",
        "WHISPER_DEVICE",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_CPP_COMMAND",
        "WHISPER_CPP_MODEL_PATH",
        "WHISPER_CPP_THREADS",
        "GPU_BASE_URL",
        "REMOTE_WHISPER_BASE_URL",
        "OPENAI_API_KEY",
        "TRANSLATION_BACKEND",
        "TRANSLATION_MODEL",
        "TRANSLATION_CHUNK_SIZE",
        "TRANSLATION_TIMEOUT",
        "OLLAMA_BASE_URL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_REFERER",
        "OPENROUTER_APP_TITLE",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "TARGET_LANGUAGE",
    }
    seeded = {}
    for key in known_keys:
        val = os.environ.get(key)
        if val is not None and val != "":
            seeded[key] = val
    if seeded:
        path = _settings_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seeded, f, ensure_ascii=False, indent=2)
    return seeded


def save_settings(updates: dict) -> dict:
    """Persist updates to settings.json and return the merged dict."""
    path = _settings_path()
    current = _load_settings()
    current.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return current


# Load settings. If the file is missing, seed it once from env vars (which may
# have been populated by python-dotenv from .env / .env.local). After that
# settings.json is the single source of truth.
SETTINGS = _load_settings()
if not SETTINGS:
    SETTINGS = _seed_settings_from_env()


def _default_translation_model(backend: str) -> str:
    backend = (backend or "").lower().strip()
    if backend == "openrouter":
        return "deepseek/deepseek-v4-flash"
    if backend == "deepseek":
        return "deepseek-v4-flash"
    return "qwen2.5:14b"


def _default_whisper_backend() -> str:
    return "whispercpp" if IS_APPLE_SILICON else "faster-whisper"


def _default_compute_type() -> str:
    return "int8" if IS_APPLE_SILICON else "auto"


def _default_chunk_size() -> int:
    return 16 if IS_APPLE_SILICON else 20


def _normalize_gpu_base_url(val: str) -> str:
    return val.rstrip("/").rstrip(":")


def _derive_remote_whisper_url(gpu_base_url: str, settings: dict) -> str:
    explicit = settings.get("REMOTE_WHISPER_BASE_URL")
    if explicit is not None:
        return explicit
    return f"{gpu_base_url}:5051" if gpu_base_url else ""


def _derive_ollama_url(gpu_base_url: str, settings: dict) -> str:
    explicit = settings.get("OLLAMA_BASE_URL")
    if explicit is not None:
        return explicit
    return f"{gpu_base_url}:11434" if gpu_base_url else "http://127.0.0.1:11434"


class Config:
    SECRET_KEY = _get_setting(SETTINGS, "SECRET_KEY", "dev-key-media-subtitler")

    # Media library directory.
    MEDIA_DIR = _get_setting(SETTINGS, "MEDIA_DIR", "media")

    # --- ASR ---
    ASR_BACKEND = _get_nonempty_setting(
        SETTINGS,
        "ASR_BACKEND",
        _get_nonempty_setting(SETTINGS, "WHISPER_BACKEND", _default_whisper_backend()),
    )
    ASR_MODEL = _get_nonempty_setting(
        SETTINGS,
        "ASR_MODEL",
        _get_nonempty_setting(SETTINGS, "WHISPER_MODEL", "large-v3"),
    )
    ASR_DEVICE = _get_nonempty_setting(
        SETTINGS,
        "ASR_DEVICE",
        _get_nonempty_setting(SETTINGS, "WHISPER_DEVICE", "auto"),
    )
    ASR_COMPUTE_TYPE = _get_nonempty_setting(
        SETTINGS,
        "ASR_COMPUTE_TYPE",
        _get_nonempty_setting(SETTINGS, "WHISPER_COMPUTE_TYPE", _default_compute_type()),
    )
    QWEN_ASR_CHUNK_SECONDS = _get_setting_int(SETTINGS, "QWEN_ASR_CHUNK_SECONDS", 90)
    # Backward-compatible aliases for older code and existing settings.
    WHISPER_BACKEND = ASR_BACKEND
    WHISPER_MODEL = ASR_MODEL
    WHISPER_DEVICE = ASR_DEVICE
    WHISPER_COMPUTE_TYPE = ASR_COMPUTE_TYPE
    WHISPER_CPP_COMMAND = _get_setting(SETTINGS, "WHISPER_CPP_COMMAND", "whisper-cli")
    WHISPER_CPP_MODEL_PATH = _get_setting(SETTINGS, "WHISPER_CPP_MODEL_PATH", "")
    WHISPER_CPP_THREADS = _get_setting_int(SETTINGS, "WHISPER_CPP_THREADS", 0)
    GPU_BASE_URL = _normalize_gpu_base_url(_get_setting(SETTINGS, "GPU_BASE_URL", ""))
    REMOTE_WHISPER_BASE_URL = _derive_remote_whisper_url(GPU_BASE_URL, SETTINGS)

    OPENAI_API_KEY = _get_setting(SETTINGS, "OPENAI_API_KEY", "")

    # --- Translation ---
    TRANSLATION_BACKEND = _get_setting(SETTINGS, "TRANSLATION_BACKEND", "deepseek").strip().lower()
    OLLAMA_BASE_URL = _derive_ollama_url(GPU_BASE_URL, SETTINGS)
    OPENROUTER_BASE_URL = _get_setting(SETTINGS, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_API_KEY = _get_setting(SETTINGS, "OPENROUTER_API_KEY", "")
    OPENROUTER_REFERER = _get_setting(SETTINGS, "OPENROUTER_REFERER", "")
    OPENROUTER_APP_TITLE = _get_setting(SETTINGS, "OPENROUTER_APP_TITLE", "media_subtitler")

    DEEPSEEK_BASE_URL = _get_setting(SETTINGS, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_API_KEY = _get_setting(SETTINGS, "DEEPSEEK_API_KEY", "")

    TRANSLATION_MODEL = _get_setting(
        SETTINGS,
        "TRANSLATION_MODEL",
        _default_translation_model(TRANSLATION_BACKEND),
    )
    TRANSLATION_CHUNK_SIZE = _get_setting_int(SETTINGS, "TRANSLATION_CHUNK_SIZE", _default_chunk_size())
    TRANSLATION_TIMEOUT = _get_setting_int(SETTINGS, "TRANSLATION_TIMEOUT", 120)

    # --- Languages ---
    TARGET_LANGUAGE = _get_setting(SETTINGS, "TARGET_LANGUAGE", "zh").strip().lower()
