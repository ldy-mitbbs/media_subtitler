"""Pre-run token / cost estimation for the subtitle pipeline.

Two sources are supported:

1. Existing ``.orig.srt`` next to the media file — we know the exact subtitle
   text, so we can produce a fairly accurate token estimate.
2. ffprobe duration only — we fall back to a heuristic based on average
   drama subtitle density (~6 lines / minute, ~18 chars / line).

Token counts are approximations only: real tokenizers vary across providers.
We use a conservative average of 2 characters per token (CJK leans toward
~1.5, Latin scripts toward ~4; drama dialogue is mixed).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

from app.models.subtitle_pipeline import read_srt


# Per-chunk overhead from the system + user prompt scaffolding (excluding
# the actual subtitle text). Roughly measured against gpt/gemini tokenizers.
_PROMPT_OVERHEAD_TOKENS_PER_CHUNK = 220
# Per-line JSON wrapping overhead inside the user payload.
_PER_LINE_OVERHEAD_TOKENS = 6
# Average chars per token for mixed CJK + Latin drama dialogue.
_CHARS_PER_TOKEN = 2.0
# Heuristic when only duration is known.
_LINES_PER_MINUTE = 6.0
_CHARS_PER_LINE = 18.0


def _approx_tokens_from_chars(chars: float) -> int:
    return int(math.ceil(chars / _CHARS_PER_TOKEN))


def _estimate_from_segments(texts: list[str], chunk_size: int) -> dict:
    chunk_size = max(1, int(chunk_size or 20))
    line_count = len(texts)
    char_count = sum(len(t or "") for t in texts)
    chunks = max(1, math.ceil(line_count / chunk_size)) if line_count else 0

    text_tokens = _approx_tokens_from_chars(char_count)
    line_overhead = line_count * _PER_LINE_OVERHEAD_TOKENS
    chunk_overhead = chunks * _PROMPT_OVERHEAD_TOKENS_PER_CHUNK

    input_tokens = text_tokens + line_overhead + chunk_overhead
    # Translated output is typically a similar number of characters as the
    # input subtitle text, plus some JSON wrapping per line.
    output_tokens = text_tokens + line_count * 4

    return {
        "segment_count": line_count,
        "char_count": char_count,
        "chunk_count": chunks,
        "chunk_size": chunk_size,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _ffprobe_duration_seconds(media_path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
        if completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace") or "{}")
        duration = float(payload.get("format", {}).get("duration") or 0.0)
        return duration if duration > 0 else None
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def _estimate_from_duration(duration_seconds: float, chunk_size: int) -> dict:
    minutes = max(0.0, duration_seconds / 60.0)
    line_count = int(round(minutes * _LINES_PER_MINUTE))
    char_count = int(round(line_count * _CHARS_PER_LINE))
    fake_texts = ["x" * int(_CHARS_PER_LINE)] * line_count
    base = _estimate_from_segments(fake_texts, chunk_size)
    base.update(
        {
            "char_count": char_count,
            "duration_seconds": duration_seconds,
        }
    )
    return base


def estimate_tokens(media_path: Path, chunk_size: int = 20) -> dict:
    """Return token estimate for translating ``media_path``.

    Resolution order:
    - sibling ``.orig.srt`` -> exact-ish (uses real text)
    - ffprobe duration -> heuristic
    - unknown -> zeros plus ``source = 'unknown'``.
    """

    media_path = Path(media_path)
    orig_srt = media_path.with_suffix(".orig.srt")

    if orig_srt.exists():
        try:
            segments = read_srt(orig_srt)
            texts = [s.get("text", "") for s in segments]
            estimate = _estimate_from_segments(texts, chunk_size)
            estimate["source"] = "orig_srt"
            estimate["orig_srt"] = str(orig_srt)
            return estimate
        except Exception:
            pass

    duration = _ffprobe_duration_seconds(media_path)
    if duration is not None:
        estimate = _estimate_from_duration(duration, chunk_size)
        estimate["source"] = "duration_heuristic"
        return estimate

    return {
        "segment_count": 0,
        "char_count": 0,
        "chunk_count": 0,
        "chunk_size": chunk_size,
        "input_tokens": 0,
        "output_tokens": 0,
        "source": "unknown",
    }


def estimate_cost(input_tokens: int, output_tokens: int, pricing_entry: dict | None) -> dict | None:
    """Compute USD cost for the given token estimate against an OpenRouter pricing entry."""
    if not pricing_entry:
        return None
    try:
        prompt_rate = float(pricing_entry.get("prompt") or 0)
        completion_rate = float(pricing_entry.get("completion") or 0)
    except (TypeError, ValueError):
        return None
    prompt_usd = input_tokens * prompt_rate
    completion_usd = output_tokens * completion_rate
    return {
        "prompt_usd": prompt_usd,
        "completion_usd": completion_usd,
        "total_usd": prompt_usd + completion_usd,
        "prompt_price_per_token": prompt_rate,
        "completion_price_per_token": completion_rate,
    }
