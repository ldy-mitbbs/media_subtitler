import re
import time
import unicodedata
from pathlib import Path

import requests
from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from app.models.cost_estimator import estimate_cost, estimate_tokens
from app.models.subtitle_pipeline import LANGUAGE_NAMES, SubtitleJobManager


main_bp = Blueprint("main", __name__)

_PRICING_CACHE = {"data": None, "fetched_at": 0.0}
_PRICING_TTL_SECONDS = 30 * 60

# DeepSeek doesn't expose pricing via API, so we hardcode published USD/token rates.
# Source: https://api-docs.deepseek.com/quick_start/pricing (cache-miss prices).
# Note: deepseek-chat and deepseek-reasoner will be deprecated on 2026/07/24;
# they map to deepseek-v4-flash non-thinking / thinking modes respectively.
_DEEPSEEK_PRICING = {
    "deepseek-v4-flash": {
        "prompt": 0.14 / 1_000_000,
        "completion": 0.28 / 1_000_000,
        "context_length": 1_000_000,
        "name": "DeepSeek V4 Flash",
    },
    "deepseek-v4-pro": {
        # Listed price is $1.74 / $3.48 per 1M tokens (a 75% promo discount of
        # $0.435 / $0.87 runs through 2026/05/31 — we use the standard rate
        # so estimates remain valid after the promo expires).
        "prompt": 1.74 / 1_000_000,
        "completion": 3.48 / 1_000_000,
        "context_length": 1_000_000,
        "name": "DeepSeek V4 Pro",
    },
    "deepseek-chat": {
        "prompt": 0.14 / 1_000_000,
        "completion": 0.28 / 1_000_000,
        "context_length": 1_000_000,
        "name": "DeepSeek Chat (alias of v4-flash, deprecates 2026/07/24)",
    },
    "deepseek-reasoner": {
        "prompt": 0.14 / 1_000_000,
        "completion": 0.28 / 1_000_000,
        "context_length": 1_000_000,
        "name": "DeepSeek Reasoner (alias of v4-flash thinking, deprecates 2026/07/24)",
    },
}


def _language_options():
    """Sorted (code, name) pairs for UI selectors."""
    return sorted(LANGUAGE_NAMES.items(), key=lambda kv: kv[1].lower())


def _safe_unicode_filename(filename):
    """Sanitize an uploaded filename while preserving non-ASCII characters
    (e.g. Japanese, Korean). werkzeug.secure_filename strips them entirely.

    Strips path separators, control characters, and characters that are
    problematic on common filesystems, but keeps CJK and other Unicode
    letters intact.
    """
    if not filename:
        return ""
    # Take the basename portion only; reject any directory components.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    # Normalize so width/compatibility variants collapse predictably.
    name = unicodedata.normalize("NFC", name)
    # Drop control chars and characters illegal on Windows/macOS filesystems.
    name = "".join(
        ch for ch in name
        if unicodedata.category(ch)[0] != "C" and ch not in '<>:"/\\|?*'
    )
    # Collapse whitespace runs to single spaces and trim surrounding dots/spaces.
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or ""


def _fetch_openrouter_pricing(force=False):
    """Fetch & cache OpenRouter pricing. Returns slug -> {prompt, completion, ...} in USD/token."""
    now = time.time()
    if (
        not force
        and _PRICING_CACHE["data"] is not None
        and (now - _PRICING_CACHE["fetched_at"]) < _PRICING_TTL_SECONDS
    ):
        return _PRICING_CACHE["data"]

    base_url = current_app.config.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    url = f"{base_url.rstrip('/')}/models"
    headers = {}
    api_key = current_app.config.get("OPENROUTER_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException:
        return _PRICING_CACHE["data"] or {}

    pricing = {}
    for model in payload.get("data", []) or []:
        slug = model.get("id")
        price = model.get("pricing") or {}
        if not slug:
            continue
        try:
            prompt = float(price.get("prompt") or 0)
            completion = float(price.get("completion") or 0)
        except (TypeError, ValueError):
            continue
        pricing[slug] = {
            "prompt": prompt,
            "completion": completion,
            "context_length": model.get("context_length"),
            "name": model.get("name") or slug,
            "created": model.get("created"),
        }

    _PRICING_CACHE["data"] = pricing
    _PRICING_CACHE["fetched_at"] = now
    return pricing


def _estimate_cost(model, usage):
    """USD cost dict for a given OpenRouter model + usage tokens, or None if not priceable."""
    if not model or not usage:
        return None
    pricing = _fetch_openrouter_pricing()
    entry = pricing.get(model)
    if not entry:
        return None
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    prompt_cost = prompt_tokens * float(entry.get("prompt") or 0)
    completion_cost = completion_tokens * float(entry.get("completion") or 0)
    return {
        "prompt_usd": prompt_cost,
        "completion_usd": completion_cost,
        "total_usd": prompt_cost + completion_cost,
        "prompt_price_per_token": entry.get("prompt"),
        "completion_price_per_token": entry.get("completion"),
    }


def get_subtitle_manager():
    manager = current_app.extensions.get("subtitle_job_manager")
    if manager is None:
        manager = SubtitleJobManager(current_app.config)
        current_app.extensions["subtitle_job_manager"] = manager
    return manager


def _media_dir():
    return Path(current_app.config["MEDIA_DIR"]).resolve()


@main_bp.route("/")
def index():
    return render_template(
        "index.html",
        target_language=current_app.config.get("TARGET_LANGUAGE", "zh"),
        language_options=_language_options(),
    )


@main_bp.route("/api/media")
def list_media():
    manager = get_subtitle_manager()
    return jsonify({"files": manager.list_media_files()})


@main_bp.route("/api/config")
def get_config():
    cfg = current_app.config
    return jsonify(
        {
            "whisper": {
                "backend": cfg.get("WHISPER_BACKEND"),
                "model": cfg.get("WHISPER_MODEL"),
                "device": cfg.get("WHISPER_DEVICE"),
                "compute_type": cfg.get("WHISPER_COMPUTE_TYPE"),
                "remote_base_url": cfg.get("REMOTE_WHISPER_BASE_URL"),
            },
            "translation": {
                "backend": cfg.get("TRANSLATION_BACKEND"),
                "model": cfg.get("TRANSLATION_MODEL"),
                "ollama_base_url": cfg.get("OLLAMA_BASE_URL"),
            },
            "gpu_base_url": cfg.get("GPU_BASE_URL"),
            "target_language": cfg.get("TARGET_LANGUAGE"),
        }
    )


@main_bp.route("/api/openrouter/pricing")
def openrouter_pricing():
    """Return cached OpenRouter pricing keyed by model slug. Tokens are USD/token."""
    refresh = request.args.get("refresh") == "1"
    pricing = _fetch_openrouter_pricing(force=refresh)
    return jsonify({"pricing": pricing, "fetched_at": _PRICING_CACHE["fetched_at"]})


# Family-substring allowlist for translation-suitable text models.
# Order roughly reflects display order within each tier.
_MODEL_FAMILY_ALLOWLIST = (
    "google/gemini-3", "google/gemini-2.5-flash", "google/gemini-2.0-flash",
    "google/gemma-4", "google/gemma-3",
    "openai/gpt-5-mini", "openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4o",
    "anthropic/claude-haiku", "anthropic/claude-3.5-haiku",
    "deepseek/deepseek-chat", "deepseek/deepseek-v3", "deepseek/deepseek-v4",
    "mistralai/mistral", "qwen/qwen", "meta-llama/llama-3", "inclusionai/ling",
)
# Exclude any slug containing these substrings (multimodal / non-text use cases).
_MODEL_EXCLUDE_SUBSTR = (
    "image", "audio", "tts", "embedding", "moderation",
    "search-preview", "customtools", "vision", "-coder", "speciale",
    "distill", "-r1", "-r2",  # reasoning variants are slow/expensive for line-by-line MT
)
# Mirror of SubtitlePipeline._NO_JSON_MODE_PATTERNS (kept separate to avoid an import cycle).
_NO_JSON_MODE_PATTERNS = (":free", "gemma", "tencent/")


def _model_is_translation_suitable(slug):
    s = (slug or "").lower()
    if not any(fam in s for fam in _MODEL_FAMILY_ALLOWLIST):
        return False
    if any(bad in s for bad in _MODEL_EXCLUDE_SUBSTR):
        return False
    # Exclude private/preview-only proxy aliases (those starting with '~').
    if s.startswith("~"):
        return False
    return True


def _supports_json_mode(slug):
    s = (slug or "").lower()
    return not any(p in s for p in _NO_JSON_MODE_PATTERNS)


def _adaptive_chunk_size(backend, model):
    """Pick a translation chunk size based on model tier.

    Smaller/cheaper models reliably handle fewer lines per request before
    JSON output truncates, items get merged/dropped, or attention drops on
    later items. Tier boundaries based on OpenRouter avg ($/1M token) cost.
    """
    backend = (backend or "").lower()
    model_lc = (model or "").lower()
    # DeepSeek V4 family handles large chunks reliably regardless of backend.
    if "deepseek-v4" in model_lc:
        return 20
    if backend == "ollama":
        return 8
    if backend == "deepseek":
        # DeepSeek v4-flash is very cheap and has 1M context; can handle
        # large chunks reliably in non-thinking mode.
        return 20
    if backend != "openrouter" or not model:
        return 10
    if ":free" in (model or "").lower():
        return 5
    pricing = _fetch_openrouter_pricing()
    entry = pricing.get(model)
    if not entry:
        return 10
    avg_per_1m = ((float(entry.get("prompt") or 0) + float(entry.get("completion") or 0)) / 2) * 1e6
    if avg_per_1m <= 0.0:
        return 5  # priced as free
    if avg_per_1m <= 0.30:
        return 8
    if avg_per_1m <= 1.50:
        return 15
    return 20


@main_bp.route("/api/openrouter/models")
def openrouter_models():
    """Return a curated list of translation-suitable OpenRouter models with pricing."""
    refresh = request.args.get("refresh") == "1"
    pricing = _fetch_openrouter_pricing(force=refresh)
    items = []
    for slug, entry in pricing.items():
        if not _model_is_translation_suitable(slug):
            continue
        prompt = float(entry.get("prompt") or 0)
        completion = float(entry.get("completion") or 0)
        items.append({
            "slug": slug,
            "name": entry.get("name") or slug,
            "prompt_per_token": prompt,
            "completion_per_token": completion,
            "context_length": entry.get("context_length"),
            "is_free": prompt == 0 and completion == 0,
            "supports_json_mode": _supports_json_mode(slug),
            "created": entry.get("created"),
        })
    # Sort: paid before free, then newest first (by OpenRouter `created`).
    # Slug as final tiebreaker for stable order.
    def _sort_key(m):
        return (m["is_free"], -(m["created"] or 0), m["slug"])
    items.sort(key=_sort_key)
    return jsonify({
        "models": items,
        "fetched_at": _PRICING_CACHE["fetched_at"],
    })


@main_bp.route("/api/estimate")
def estimate_job_cost():
    """Pre-run token + cost estimate for a media file or pending job.

    Query params (one of selected_file or job_id required):
      selected_file: path under MEDIA_DIR
      job_id: estimate using the media_path of an existing job
      translation_model: override OpenRouter model slug
      chunk_size: override translation chunk size
    """
    selected = (request.args.get("selected_file") or "").strip()
    job_id = (request.args.get("job_id") or "").strip()

    if job_id:
        manager = get_subtitle_manager()
        job = manager.get_job(job_id)
        if not job:
            return jsonify({"success": False, "message": "Job not found"}), 404
        candidate = Path(job.get("media_path") or "").resolve()
        if not candidate.exists():
            return jsonify({"success": False, "message": "Job media missing"}), 404
        selected_label = candidate.name
    else:
        if not selected:
            return jsonify({"success": False, "message": "selected_file or job_id required"}), 400
        media_dir = _media_dir()
        candidate = (media_dir / selected).resolve()
        try:
            candidate.relative_to(media_dir)
        except ValueError:
            return jsonify({"success": False, "message": "Invalid selected file"}), 400
        if not candidate.exists() or not candidate.is_file():
            return jsonify({"success": False, "message": "File not found"}), 404
        selected_label = selected

    cfg = current_app.config
    chunk_size = int(
        request.args.get("chunk_size") or cfg.get("TRANSLATION_CHUNK_SIZE") or 20
    )
    model = (request.args.get("translation_model") or "").strip() or cfg.get("TRANSLATION_MODEL")
    backend = (request.args.get("translation_backend") or "").strip().lower() or cfg.get(
        "TRANSLATION_BACKEND"
    )

    tokens = estimate_tokens(candidate, chunk_size=chunk_size)

    cost = None
    pricing_entry = None
    if backend == "openrouter" and model:
        pricing = _fetch_openrouter_pricing()
        pricing_entry = pricing.get(model)
        cost = estimate_cost(tokens["input_tokens"], tokens["output_tokens"], pricing_entry)
    elif backend == "deepseek" and model:
        pricing_entry = _DEEPSEEK_PRICING.get(model)
        cost = estimate_cost(tokens["input_tokens"], tokens["output_tokens"], pricing_entry)

    return jsonify(
        {
            "success": True,
            "file": selected_label,
            "translation_model": model,
            "translation_backend": backend,
            "tokens": tokens,
            "cost": cost,
            "pricing": pricing_entry,
        }
    )


@main_bp.route("/api/jobs", methods=["POST"])
def create_job():
    manager = get_subtitle_manager()
    upload = request.files.get("media_file")
    selected_file = (request.form.get("selected_file") or "").strip()
    source_language = (request.form.get("source_language") or "").strip().lower() or None
    target_language = (request.form.get("target_language") or "").strip().lower() or None
    whisper_model = (request.form.get("whisper_model") or "").strip() or None
    whisper_backend = (request.form.get("whisper_backend") or "").strip().lower() or None
    gpu_base_url = (request.form.get("gpu_base_url") or "").strip().rstrip("/").rstrip(":") or None
    translation_model = (request.form.get("translation_model") or "").strip() or None
    translation_backend = (request.form.get("translation_backend") or "").strip().lower() or None
    chunk_size_raw = (request.form.get("translation_chunk_size") or "").strip()
    try:
        user_chunk_size = int(chunk_size_raw) if chunk_size_raw else None
    except ValueError:
        user_chunk_size = None
    mode = (request.form.get("mode") or "full").strip().lower()
    stop_after_transcription = mode == "transcribe"
    skip_transcription = mode == "translate"

    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)

    media_path = None
    if upload and upload.filename:
        safe_name = _safe_unicode_filename(upload.filename)
        if not safe_name:
            return jsonify({"success": False, "message": "Invalid file name"}), 400
        upload_dir = media_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        media_path = upload_dir / safe_name
        upload.save(str(media_path))
    elif selected_file:
        candidate = (media_dir / selected_file).resolve()
        try:
            candidate.relative_to(media_dir)
        except ValueError:
            return jsonify({"success": False, "message": "Invalid selected file"}), 400
        if not candidate.exists() or not candidate.is_file():
            return jsonify({"success": False, "message": "Selected file does not exist"}), 400
        media_path = candidate
    else:
        return (
            jsonify(
                {"success": False, "message": "Please upload a file or choose an existing media file"}
            ),
            400,
        )

    if skip_transcription:
        existing_srt = media_path.with_suffix(".orig.srt")
        if not existing_srt.exists():
            return (
                jsonify({
                    "success": False,
                    "message": (
                        f"Translate-only mode requires {existing_srt.name} next to the "
                        "media file. Run transcription first."
                    ),
                }),
                400,
            )

    job_id = manager.start_job(
        media_path,
        source_language_hint=source_language,
        target_language=target_language,
        overrides={
            "WHISPER_BACKEND": whisper_backend,
            "WHISPER_MODEL": whisper_model,
            "GPU_BASE_URL": gpu_base_url,
            "REMOTE_WHISPER_BASE_URL": f"{gpu_base_url}:5051" if gpu_base_url else None,
            "OLLAMA_BASE_URL": f"{gpu_base_url}:11434" if gpu_base_url else None,
            "TRANSLATION_BACKEND": translation_backend,
            "TRANSLATION_MODEL": translation_model,
            "TRANSLATION_CHUNK_SIZE": user_chunk_size if user_chunk_size else _adaptive_chunk_size(
                translation_backend or current_app.config.get("TRANSLATION_BACKEND"),
                translation_model or current_app.config.get("TRANSLATION_MODEL"),
            ),
        },
        stop_after_transcription=stop_after_transcription,
        skip_transcription=skip_transcription,
    )
    return jsonify({"success": True, "job_id": job_id})


@main_bp.route("/api/jobs/<job_id>/translate", methods=["POST"])
def translate_job(job_id):
    """Run the translation phase for a job that's awaiting translation."""
    manager = get_subtitle_manager()
    target_language = (request.form.get("target_language") or "").strip().lower() or None
    translation_model = (request.form.get("translation_model") or "").strip() or None
    translation_backend = (request.form.get("translation_backend") or "").strip().lower() or None
    chunk_size_raw = (request.form.get("translation_chunk_size") or "").strip()
    try:
        user_chunk_size = int(chunk_size_raw) if chunk_size_raw else None
    except ValueError:
        user_chunk_size = None

    try:
        manager.start_translation(
            job_id,
            target_language=target_language,
            translation_model=translation_model,
            translation_backend=translation_backend,
            translation_chunk_size=user_chunk_size or _adaptive_chunk_size(
                translation_backend or current_app.config.get("TRANSLATION_BACKEND"),
                translation_model or current_app.config.get("TRANSLATION_MODEL"),
            ),
        )
    except KeyError:
        return jsonify({"success": False, "message": "Job not found"}), 404
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify({"success": True, "job_id": job_id})


@main_bp.route("/api/jobs/<job_id>")
def job_status(job_id):
    manager = get_subtitle_manager()
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404

    result = job.get("result")
    cost = None
    if result:
        backend = result.get("translation_backend") or current_app.config.get("TRANSLATION_BACKEND")
        model = result.get("translation_model")
        usage = result.get("usage") or {}
        if backend == "openrouter":
            cost = _estimate_cost(model, usage)
        elif backend == "deepseek":
            entry = _DEEPSEEK_PRICING.get(model)
            if entry:
                cost = estimate_cost(
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    entry,
                )

    log = job.get("log")
    log_tail = list(log)[-60:] if log else []

    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": job.get("status"),
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "error": job.get("error"),
            "result": result,
            "cost": cost,
            "media_path": job.get("media_path"),
            "log": log_tail,
        }
    )


@main_bp.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    manager = get_subtitle_manager()
    try:
        manager.cancel_job(job_id)
    except KeyError:
        return jsonify({"success": False, "message": "Job not found"}), 404
    return jsonify({"success": True, "job_id": job_id})


@main_bp.route("/api/jobs/<job_id>/download/<output_kind>")
def job_download(job_id, output_kind):
    manager = get_subtitle_manager()
    job = manager.get_job(job_id)
    if not job or not job.get("result"):
        return jsonify({"success": False, "message": "Output not ready"}), 404

    key_map = {"original": "original_srt", "bilingual": "bilingual_srt"}
    output_key = key_map.get(output_kind)
    if not output_key:
        return jsonify({"success": False, "message": "Invalid output kind"}), 400

    output_path = Path(job["result"].get(output_key, ""))
    if not output_path.exists():
        return jsonify({"success": False, "message": "File not found"}), 404

    return send_from_directory(
        output_path.parent,
        output_path.name,
        as_attachment=True,
        mimetype="application/x-subrip",
    )
