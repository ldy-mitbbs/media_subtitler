"""Subtitle pipeline for Japanese / Korean drama dialogue.

Produces bilingual SRT files: original transcript + translated line per cue.
Prompts are tuned for natural drama dialogue rather than generic copy.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import tempfile
import uuid
import collections
from locale import getpreferredencoding
from pathlib import Path

import requests

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - optional dep
    WhisperModel = None


# Languages supported by Whisper (and broadly available in modern translation
# models). The display name is what gets injected into translation prompts;
# unknown codes fall back to the raw code.
LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gl": "Galician",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "jw": "Javanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "la": "Latin",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "my": "Burmese",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yue": "Cantonese",
    "zh": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
}


def language_display_name(code):
    """Return a human-readable language name for a code, falling back to the code."""
    if not code:
        return ""
    normalized = str(code).strip().lower()
    return LANGUAGE_NAMES.get(normalized, normalized)


def format_srt_timestamp(seconds):
    total_ms = max(0, int(seconds * 1000))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _truncate(text, limit):
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "\u2026"


class RateLimitError(RuntimeError):
    """Raised when the translation API rate-limits us beyond what we can wait out."""


class FatalTranslationError(RuntimeError):
    """Raised for unrecoverable model/config errors (e.g. JSON mode unsupported).

    Differs from RateLimitError in that retrying with smaller chunks won't help —
    the model itself rejected the request shape, so we should abort immediately.
    """


_FATAL_PROVIDER_HINTS = (
    "model not found",
    "invalid model",
    "unauthorized",
    "invalid api key",
    "insufficient credit",
)


def _is_fatal_http_error(status_code, body):
    if status_code in (401, 403, 404):
        return True
    if status_code != 400:
        return False
    body_l = (body or "").lower()
    return any(hint in body_l for hint in _FATAL_PROVIDER_HINTS)


def _dedupe_repeated_segments(segments):
    """Collapse runs of consecutive identical segments to at most 2 in a row.

    Whisper sometimes hallucinates the same phrase for hundreds of segments
    on quiet/noisy audio. We keep up to two consecutive copies (in case a
    line is genuinely repeated in dialogue) and drop the rest.
    """
    cleaned = []
    prev_text = None
    repeat_count = 0
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        if text == prev_text:
            repeat_count += 1
            if repeat_count >= 2:
                continue
        else:
            prev_text = text
            repeat_count = 0
        cleaned.append(segment)
    return cleaned


def write_srt(segments, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, segment in enumerate(segments, start=1):
            f.write(f"{idx}\n")
            f.write(
                f"{format_srt_timestamp(segment['start'])} --> {format_srt_timestamp(segment['end'])}\n"
            )
            f.write(f"{segment['text'].strip()}\n\n")


def parse_srt_timestamp(value):
    hours, minutes, remainder = value.split(":", 2)
    seconds, millis = remainder.split(",", 1)
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + (int(millis) / 1000.0)
    )


def read_srt(input_path):
    content = Path(input_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip(), flags=re.MULTILINE)
    segments = []

    for block in blocks:
        lines = [line.rstrip("\r") for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue

        time_line = lines[1]
        if "-->" not in time_line:
            continue

        start_raw, end_raw = [part.strip() for part in time_line.split("-->", 1)]
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue

        segments.append(
            {
                "start": parse_srt_timestamp(start_raw),
                "end": parse_srt_timestamp(end_raw),
                "text": text,
            }
        )

    return segments


class SubtitlePipeline:
    def __init__(self, config):
        # Accept either MEDIA_DIR (drama_subtitler) or DOWNLOAD_DIR (legacy).
        media_dir = config.get("MEDIA_DIR") or config.get("DOWNLOAD_DIR") or "media"
        self.media_dir = Path(media_dir)

        self.whisper_backend = config.get("WHISPER_BACKEND", "faster-whisper")
        self.whisper_model_name = config.get("WHISPER_MODEL", "large-v3")
        self.whisper_device = config.get("WHISPER_DEVICE", "auto")
        self.whisper_compute_type = config.get("WHISPER_COMPUTE_TYPE", "auto")
        self.whisper_cpp_command = config.get("WHISPER_CPP_COMMAND", "whisper-cli")
        self.whisper_cpp_model_path = config.get("WHISPER_CPP_MODEL_PATH", "")
        self.whisper_cpp_threads = int(config.get("WHISPER_CPP_THREADS", 0) or 0)
        self.openai_api_key = config.get("OPENAI_API_KEY", "") or ""

        self.ollama_base_url = config.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        self.translation_backend = (
            str(config.get("TRANSLATION_BACKEND", "ollama")).strip().lower() or "ollama"
        )
        if self.translation_backend not in {"ollama", "openrouter", "deepseek"}:
            raise RuntimeError(
                f"Unsupported TRANSLATION_BACKEND: {self.translation_backend!r} "
                "(expected 'ollama', 'openrouter', or 'deepseek')"
            )
        self.openrouter_base_url = config.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
        self.openrouter_api_key = config.get("OPENROUTER_API_KEY", "") or ""
        self.openrouter_referer = config.get("OPENROUTER_REFERER", "") or ""
        self.openrouter_app_title = config.get("OPENROUTER_APP_TITLE", "") or ""
        self.deepseek_base_url = config.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        self.deepseek_api_key = config.get("DEEPSEEK_API_KEY", "") or ""
        self.translation_model = config.get("TRANSLATION_MODEL", "qwen2.5:14b")
        self.translation_chunk_size = int(config.get("TRANSLATION_CHUNK_SIZE", 20))
        self.translation_timeout = int(config.get("TRANSLATION_TIMEOUT", 120))

        # Languages
        self.target_language = str(config.get("TARGET_LANGUAGE", "zh")).strip().lower() or "zh"

        # Cumulative LLM token usage for the most recent process() call.
        self.translation_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._active_cancel_event = None
        # Circuit-breaker state for the current process() call.
        self._translation_error_count = 0
        self._translation_error_budget = int(
            config.get("TRANSLATION_ERROR_BUDGET", 10)
        )
        # Per-run JSON-mode flag; reset on each process() call. Auto-disables
        # if the model rejects response_format mid-run.
        self._json_mode_enabled = True

    def _note_translation_error(self):
        self._translation_error_count += 1
        if (
            self._translation_error_budget > 0
            and self._translation_error_count >= self._translation_error_budget
        ):
            raise FatalTranslationError(
                f"Aborting after {self._translation_error_count} translation errors "
                f"(budget: {self._translation_error_budget}). "
                "Pick a different model or check API credentials/quota."
            )

    # ------------------------------------------------------------------ public

    def process(
        self,
        media_path,
        progress_cb=None,
        skip_transcription=False,
        translation_stream_cb=None,
        source_language_hint=None,
        target_language=None,
        stop_after_transcription=False,
        cancel_event=None,
    ):
        media_path = Path(media_path)
        if not media_path.exists():
            raise FileNotFoundError(f"Media file not found: {media_path}")

        target_lang = (
            (target_language or "").strip().lower() or self.target_language
        )

        # Reset usage counters for this run.
        self.translation_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._translation_error_count = 0
        self._json_mode_enabled = self._supports_json_mode()

        original_srt = media_path.with_suffix(".orig.srt")
        if skip_transcription:
            if not original_srt.exists():
                raise RuntimeError(
                    f"Skip-transcription enabled but original SRT not found: {original_srt}"
                )

            if progress_cb:
                progress_cb(55, f"Loading existing transcription: {original_srt.name}")

            segments = read_srt(original_srt)
            segments = self._repair_mojibake_segments(segments)
            segments = _dedupe_repeated_segments(segments)
            source_language = source_language_hint or "unknown"
            if not segments:
                raise RuntimeError(f"No subtitle segments found in: {original_srt}")
        else:
            if progress_cb:
                progress_cb(5, "Preparing transcription backend")

            backend = self._resolve_backend()
            segments, source_language = self._transcribe(
                media_path, backend, progress_cb, language_hint=source_language_hint
            )

            if not segments:
                raise RuntimeError("No speech segments detected")

            write_srt(segments, original_srt)

        normalized_lang = (source_language or "unknown").strip().lower()

        if stop_after_transcription:
            if progress_cb:
                progress_cb(55, "Transcription complete; awaiting translation")
            return {
                "source_language": source_language,
                "target_language": target_lang,
                "segment_count": len(segments),
                "original_srt": str(original_srt),
                "bilingual_srt": None,
                "translation_model": None,
                "translation_backend": None,
                "whisper_model": self.whisper_model_name,
                "whisper_backend": self.whisper_backend,
                "usage": dict(self.translation_usage),
                "stage": "transcribed",
            }

        target_name = LANGUAGE_NAMES.get(target_lang, target_lang)
        if progress_cb:
            progress_cb(60, f"Translating subtitles to {target_name}")

        bilingual_segments = self._translate_segments(
            segments,
            source_language=normalized_lang,
            target_language=target_lang,
            progress_cb=progress_cb,
            stream_cb=translation_stream_cb,
            error_cb=translation_stream_cb,
            cancel_event=cancel_event,
        )
        # Clear the cancel event reference once translation finishes so it
        # doesn't leak into a later call.
        self._active_cancel_event = None
        bilingual_srt = media_path.with_suffix(".bilingual.srt")
        write_srt(bilingual_segments, bilingual_srt)

        result = {
            "source_language": source_language,
            "target_language": target_lang,
            "segment_count": len(segments),
            "original_srt": str(original_srt),
            "bilingual_srt": str(bilingual_srt),
            "translation_model": self.translation_model,
            "translation_backend": self.translation_backend,
            "whisper_model": self.whisper_model_name,
            "whisper_backend": self.whisper_backend,
            "usage": dict(self.translation_usage),
            "stage": "completed",
        }

        if progress_cb:
            progress_cb(100, "Completed")

        return result

    # ------------------------------------------------------------------ whisper

    def _resolve_backend(self):
        backend = (self.whisper_backend or "faster-whisper").strip().lower()
        if backend in {"whispercpp", "whisper.cpp"}:
            return "whispercpp"
        if backend in {"faster-whisper", "faster_whisper"}:
            return "faster-whisper"
        if backend == "openai":
            return "openai"
        if backend == "auto":
            return "whispercpp" if shutil.which(self.whisper_cpp_command) else "faster-whisper"
        raise RuntimeError(f"Unsupported WHISPER_BACKEND: {self.whisper_backend}")

    def _transcribe(self, media_path, backend, progress_cb=None, language_hint=None):
        if backend == "whispercpp":
            return self._transcribe_with_whispercpp(media_path, progress_cb, language_hint)
        if backend == "openai":
            return self._transcribe_with_openai(media_path, progress_cb, language_hint)
        return self._transcribe_with_faster_whisper(media_path, progress_cb, language_hint)

    def _transcribe_with_faster_whisper(self, media_path, progress_cb=None, language_hint=None):
        if WhisperModel is None:
            raise RuntimeError(
                "faster-whisper is not installed. Install dependencies or set WHISPER_BACKEND=whispercpp."
            )

        if progress_cb:
            progress_cb(5, "Loading faster-whisper model")

        model = WhisperModel(
            self.whisper_model_name,
            device=self.whisper_device,
            compute_type=self.whisper_compute_type,
        )

        if progress_cb:
            progress_cb(10, "Transcribing audio")

        kwargs = {
            "vad_filter": True,
            # Hallucination-loop mitigations: when condition_on_previous_text is
            # True (the default), the decoder feeds its own previous output back
            # in, which on quiet/noisy audio causes the model to lock onto a
            # single phrase and emit it for hundreds of segments in a row.
            "condition_on_previous_text": False,
            "no_repeat_ngram_size": 3,
            "repetition_penalty": 1.05,
            "compression_ratio_threshold": 2.4,
        }
        if language_hint:
            kwargs["language"] = language_hint

        raw_segments, info = model.transcribe(str(media_path), **kwargs)
        segments = []
        prev_text = None
        repeat_count = 0
        for segment in raw_segments:
            text = segment.text.strip()
            if not text:
                continue
            if text == prev_text:
                repeat_count += 1
                # Drop runs of >2 consecutive identical segments — these are
                # almost always Whisper hallucination loops, not real dialogue.
                if repeat_count >= 2:
                    continue
            else:
                repeat_count = 0
                prev_text = text
            segments.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": text,
                }
            )
            if progress_cb:
                progress = min(55, 10 + int(len(segments) / 200 * 45))
                progress_cb(progress, f"Transcribing ({len(segments)} segments)")

        return segments, getattr(info, "language", language_hint or "unknown")

    def _transcribe_with_whispercpp(self, media_path, progress_cb=None, language_hint=None):
        command_path = shutil.which(self.whisper_cpp_command)
        if not command_path:
            raise RuntimeError(
                f"whisper.cpp CLI not found: {self.whisper_cpp_command}. Install whisper.cpp and ensure '{self.whisper_cpp_command}' is on PATH."
            )

        model_path = self._resolve_whispercpp_model_path(media_path)

        with tempfile.TemporaryDirectory(prefix="drama-subtitler-") as temp_dir:
            temp_dir_path = Path(temp_dir)
            wav_path = temp_dir_path / "input.wav"
            output_base = temp_dir_path / "transcription"

            if progress_cb:
                progress_cb(5, "Extracting audio for whisper.cpp")

            self._extract_audio_for_whispercpp(media_path, wav_path)

            if progress_cb:
                progress_cb(10, f"Running whisper.cpp ({self.whisper_model_name})")

            cmd = [
                command_path,
                "-m",
                str(model_path),
                "-f",
                str(wav_path),
                "-ojf",
                "-of",
                str(output_base),
                "-l",
                language_hint or "auto",
                "-np",
                # --- Hallucination-loop mitigations (matches faster-whisper). ---
                # Don't carry previous text as context; this is the main cause
                # of whisper getting stuck repeating one phrase for hundreds
                # of segments on quiet/noisy audio.
                "-mc", "0",
                # Suppress non-speech tokens (music/silence markers) which
                # often trigger the loop.
                "-sns",
                # Tighten the entropy threshold so a low-entropy (repetitive)
                # decode triggers temperature fallback / segment skip.
                "-et", "2.4",
                "-lpt", "-1.0",
            ]
            if self.whisper_cpp_threads > 0:
                cmd.extend(["-t", str(self.whisper_cpp_threads)])

            completed = subprocess.run(cmd, capture_output=True, check=False)
            if completed.returncode != 0:
                stderr = (
                    completed.stderr.decode("utf-8", errors="replace").strip()
                    or completed.stdout.decode("utf-8", errors="replace").strip()
                    or "unknown whisper.cpp error"
                )
                raise RuntimeError(f"whisper.cpp failed: {stderr}")

            json_path = output_base.with_suffix(".json")
            if not json_path.exists():
                raise RuntimeError(f"whisper.cpp did not produce JSON output: {json_path}")

            payload = self._load_json_with_fallback(json_path)
            transcription = payload.get("transcription", [])
            segments = []
            for item in transcription:
                offsets = item.get("offsets", {}) if isinstance(item, dict) else {}
                text = str(item.get("text", "")).strip() if isinstance(item, dict) else ""
                start_ms = offsets.get("from")
                end_ms = offsets.get("to")
                if start_ms is None or end_ms is None or not text:
                    continue
                segments.append(
                    {
                        "start": float(start_ms) / 1000.0,
                        "end": float(end_ms) / 1000.0,
                        "text": text,
                    }
                )
                if progress_cb:
                    progress = min(55, 10 + int(len(segments) / 200 * 45))
                    progress_cb(progress, f"Transcribing ({len(segments)} segments)")

            source_language = payload.get("result", {}).get("language", language_hint or "unknown")
            segments = self._repair_mojibake_segments(segments)
            segments = _dedupe_repeated_segments(segments)
            return segments, source_language

    @staticmethod
    def _load_json_with_fallback(json_path):
        # whisper.cpp output may not be UTF-8 on macOS/Japanese locale setups.
        raw_bytes = Path(json_path).read_bytes()
        encodings = [
            "utf-8",
            "utf-8-sig",
            getpreferredencoding(False) or "utf-8",
            "cp932",
            "shift_jis",
            "cp949",
            "euc-kr",
            "latin-1",
        ]
        tried = set()

        for encoding in encodings:
            if not encoding or encoding in tried:
                continue
            tried.add(encoding)
            try:
                return json.loads(raw_bytes.decode(encoding))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

        return json.loads(raw_bytes.decode("utf-8", errors="replace"))

    @staticmethod
    def _repair_mojibake_text(text):
        if not text:
            return text

        suspicious_chars = sum(
            text.count(char)
            for char in ("ã", "å", "ç", "æ", "è", "é", "ê", "ë", "ì", "í", "î", "ï", "ð")
        )
        if suspicious_chars < 2:
            return text

        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except UnicodeError:
            return text

        if SubtitlePipeline._count_cjk_chars(repaired) > SubtitlePipeline._count_cjk_chars(text):
            return repaired
        return text

    @staticmethod
    def _repair_mojibake_segments(segments):
        """Detect mojibake at the corpus level and repair every segment uniformly.

        Per-segment heuristics can't see whether a short line like 'おお' is
        correct or coincidentally mojibake-free. Decide once for the whole
        job: if a meaningful share of segments contain Latin-1-decoded UTF-8,
        repair every segment that round-trips cleanly. This handles JA/KO
        and any other CJK content produced by whisper.cpp on systems that
        emit non-UTF-8 JSON.
        """
        if not segments:
            return segments

        suspicious_marker_chars = ("ã", "å", "ç", "æ", "è", "é", "ê", "ë", "ì", "í", "î", "ï", "ð")
        affected = 0
        for seg in segments:
            text = seg.get("text", "") or ""
            if any(c in text for c in suspicious_marker_chars):
                affected += 1
        # If at least 5% of segments (or 3 segments, whichever is larger)
        # contain mojibake markers, treat the whole job as mojibake.
        threshold = max(3, int(len(segments) * 0.05))
        if affected < threshold:
            return segments

        repaired_count = 0
        for seg in segments:
            text = seg.get("text", "") or ""
            if not text:
                continue
            try:
                raw = text.encode("latin-1")
            except UnicodeError:
                continue
            try:
                repaired = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Tail of the segment may have been truncated mid-byte (SRT
                # line wrapping). Drop the trailing partial bytes instead of
                # giving up entirely.
                repaired = raw.decode("utf-8", errors="ignore")
            # Only swap if the repaired version has at least as many CJK
            # characters as the original; this prevents corrupting lines that
            # happen to already be valid UTF-8 (e.g. ASCII phone numbers).
            if SubtitlePipeline._count_cjk_chars(repaired) >= SubtitlePipeline._count_cjk_chars(text):
                if repaired != text:
                    seg["text"] = repaired
                    repaired_count += 1
        return segments

    @staticmethod
    def _count_cjk_chars(text):
        total = 0
        for char in text:
            codepoint = ord(char)
            # Hiragana/katakana
            if 0x3040 <= codepoint <= 0x30FF:
                total += 1
            # CJK unified ideographs
            elif 0x4E00 <= codepoint <= 0x9FFF:
                total += 1
            # Hangul syllables
            elif 0xAC00 <= codepoint <= 0xD7A3:
                total += 1
            # Hangul jamo
            elif 0x1100 <= codepoint <= 0x11FF:
                total += 1
        return total

    def _resolve_whispercpp_model_path(self, media_path):
        if self.whisper_cpp_model_path:
            model_path = Path(self.whisper_cpp_model_path).expanduser()
            if model_path.exists():
                return model_path.resolve()
            raise RuntimeError(f"WHISPER_CPP_MODEL_PATH not found: {model_path}")

        model_candidates = []
        model_name = self.whisper_model_name
        if model_name.endswith(".bin"):
            model_candidates.append(Path(model_name).expanduser())
        else:
            model_candidates.extend(
                [
                    Path("models") / f"ggml-{model_name}.bin",
                    Path(media_path).parent / f"ggml-{model_name}.bin",
                    self.media_dir / f"ggml-{model_name}.bin",
                ]
            )

        for candidate in model_candidates:
            expanded = candidate.expanduser()
            if expanded.exists():
                return expanded.resolve()

        searched = ", ".join(str(path) for path in model_candidates)
        raise RuntimeError(
            "whisper.cpp model not found. Set WHISPER_CPP_MODEL_PATH or place the model at one of: "
            f"{searched}"
        )

    @staticmethod
    def _extract_audio_for_whispercpp(media_path, wav_path):
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is required to prepare audio for whisper.cpp")

        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            str(media_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, check=False)
        if completed.returncode != 0:
            stderr = (
                completed.stderr.decode("utf-8", errors="replace").strip()
                or completed.stdout.decode("utf-8", errors="replace").strip()
                or "unknown ffmpeg error"
            )
            raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}")

    @staticmethod
    def _run_ffmpeg_progress(cmd, *, label: str = "Extracting audio", progress_cb=None):
        """Run ffmpeg and stream progress messages from stderr/stdout.

        ffmpeg prints lines like ``size=  1234kB time=00:02:30.00 ...``
        to stderr. We parse the ``time=`` field and pulse it through
        *progress_cb* so the UI doesn't look frozen during a long extract.
        """
        import re

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
        last_msg = ""

        def _reader():
            nonlocal last_msg
            for line in proc.stdout:  # type: ignore[union-attr]
                m = time_re.search(line)
                if m:
                    h, mi, s = m.groups()
                    last_msg = f"{label}: {h}:{mi}:{s[:5]}"
                    if progress_cb:
                        progress_cb(None, last_msg)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        proc.wait()
        reader.join(timeout=2)
        return proc.returncode, last_msg

    def _transcribe_with_openai(self, media_path, progress_cb=None, language_hint=None):
        api_key = self.openai_api_key
        if not api_key:
            raise RuntimeError(
                "OpenAI API key is not set. Set OPENAI_API_KEY in config or environment."
            )

        if progress_cb:
            progress_cb(5, "Preparing audio for OpenAI Whisper API")

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is required to prepare audio for OpenAI API")

        max_bytes = 25 * 1024 * 1024

        with tempfile.TemporaryDirectory(prefix="drama-subtitler-openai-") as temp_dir:
            temp_dir_path = Path(temp_dir)
            audio_path = temp_dir_path / "audio.mp3"

            # Extract audio to MP3 at 24 kbps mono to stay under the 25 MB limit.
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                str(media_path),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-b:a",
                "24k",
                str(audio_path),
            ]
            if progress_cb:
                progress_cb(6, "Extracting audio from video...")
            rc, _ = self._run_ffmpeg_progress(
                cmd, label="Extracting audio", progress_cb=progress_cb
            )
            if rc != 0:
                raise RuntimeError("ffmpeg audio extraction failed")

            # Re-compress at a lower bitrate if still too large.
            if audio_path.stat().st_size > max_bytes:
                if progress_cb:
                    progress_cb(7, "Compressing audio further for OpenAI API")
                compressed_path = temp_dir_path / "audio_compressed.mp3"
                cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    str(audio_path),
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-b:a",
                    "16k",
                    str(compressed_path),
                ]
                if progress_cb:
                    progress_cb(7, "Re-compressing audio...")
                rc, _ = self._run_ffmpeg_progress(
                    cmd, label="Re-compressing audio", progress_cb=progress_cb
                )
                if rc != 0:
                    raise RuntimeError("ffmpeg re-compression failed")
                if compressed_path.stat().st_size > max_bytes:
                    raise RuntimeError(
                        f"Audio file is too large even after compression "
                        f"({compressed_path.stat().st_size / 1e6:.1f} MB > 25 MB). "
                        "Consider splitting the media into smaller files."
                    )
                audio_path = compressed_path

            audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
            if progress_cb:
                progress_cb(8, f"Audio ready ({audio_size_mb:.1f} MB), uploading to OpenAI Whisper API")

            url = "https://api.openai.com/v1/audio/transcriptions"
            data = {
                "model": "whisper-1",
                "response_format": "verbose_json",
            }
            if language_hint:
                data["language"] = language_hint

            if progress_cb:
                progress_cb(10, "Waiting for OpenAI Whisper API (this may take 2–5 min for a full episode)...")

            # Pulse a heartbeat message every 10 s so the UI doesn't look frozen
            # while requests.post blocks on the upload + server-side processing.
            _heartbeat_done = threading.Event()
            def _heartbeat():
                for _ in range(30):
                    _heartbeat_done.wait(10)
                    if _heartbeat_done.is_set():
                        break
                    if progress_cb:
                        progress_cb(10, "Still waiting for OpenAI Whisper API...")
            _heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
            _heartbeat_thread.start()

            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, "audio/mpeg")}
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                    timeout=600,
                )

            _heartbeat_done.set()

            if progress_cb:
                progress_cb(50, "Received response from OpenAI Whisper API")

            if resp.status_code != 200:
                try:
                    err = resp.json()
                    msg = err.get("error", {}).get("message", resp.text)
                except Exception:
                    msg = resp.text
                raise RuntimeError(
                    f"OpenAI Whisper API error ({resp.status_code}): {msg}"
                )

            if progress_cb:
                progress_cb(55, "Parsing OpenAI transcription response")

            payload = resp.json()
            segments = []
            for seg in payload.get("segments", []):
                text = str(seg.get("text", "")).strip()
                if not text:
                    continue
                segments.append(
                    {
                        "start": float(seg.get("start", 0)),
                        "end": float(seg.get("end", 0)),
                        "text": text,
                    }
                )

            source_language = payload.get("language", language_hint or "unknown")
            segments = self._repair_mojibake_segments(segments)
            segments = _dedupe_repeated_segments(segments)
            return segments, source_language

    # --------------------------------------------------------------- translation

    def _translate_segments(
        self,
        segments,
        source_language,
        target_language=None,
        progress_cb=None,
        stream_cb=None,
        error_cb=None,
        cancel_event=None,
    ):
        target_lang = (target_language or self.target_language)
        # Stash for low-level HTTP retry loop to honor cancellation.
        self._active_cancel_event = cancel_event
        bilingual_segments = []
        total = len(segments)
        chunks = [
            segments[i : i + self.translation_chunk_size]
            for i in range(0, total, self.translation_chunk_size)
        ]

        def emit(line):
            if stream_cb:
                stream_cb(line if line.endswith("\n") else line + "\n")

        emit(
            f"Translating {total} lines via {self.translation_backend}/{self.translation_model} "
            f"(chunk size {self.translation_chunk_size})"
        )

        for chunk_index, chunk in enumerate(chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Translation cancelled by user")

            texts = [s["text"] for s in chunk]
            emit(f"--- chunk {chunk_index + 1}/{len(chunks)} ({len(texts)} lines) ---")

            try:
                translations = self._translate_with_recovery(
                    texts,
                    source_language=source_language,
                    target_language=target_lang,
                    stream_cb=None,  # raw token streaming would flood the log
                    error_cb=emit,
                )
            except RateLimitError as exc:
                emit(f"  \u26a0 aborting translation: {exc}")
                raise
            except FatalTranslationError as exc:
                emit(f"  \u26a0 aborting translation: {exc}")
                raise

            empty_targets = sum(1 for t in translations if not t.get("target", "").strip())
            if empty_targets:
                emit(
                    f"  \u26a0 {empty_targets}/{len(translations)} responses had no 'target' field "
                    "(falling back to source text — check model response format / JSON support)"
                )

            unchanged = 0
            for original, translated in zip(chunk, translations):
                src = original["text"].strip()
                target_text = translated.get("target", "").strip() or src
                if target_text == src:
                    unchanged += 1
                bilingual_segments.append(
                    {
                        "start": original["start"],
                        "end": original["end"],
                        "text": f"{src}\n{target_text}",
                    }
                )
                # Emit a compact preview line; truncate long subtitles.
                emit(f"  {_truncate(src, 60)}  \u2192  {_truncate(target_text, 80)}")

            if unchanged and unchanged == len(chunk):
                emit(
                    f"  \u26a0 model returned identical text for all {len(chunk)} lines "
                    "in this chunk (translation may not be working)"
                )
            elif unchanged:
                emit(f"  \u26a0 {unchanged}/{len(chunk)} lines unchanged in this chunk")

            if progress_cb:
                completed = min(total, (chunk_index + 1) * self.translation_chunk_size)
                progress = 60 + int((completed / total) * 35)
                progress_cb(progress, f"Translating ({completed}/{total})")

        return bilingual_segments

    def _translate_with_recovery(
        self, texts, source_language, target_language=None, stream_cb=None, error_cb=None
    ):
        if not texts:
            return []

        target_lang = target_language or self.target_language
        try:
            translations = self._translate_chunk(
                texts,
                source_language=source_language,
                target_language=target_lang,
                stream_cb=stream_cb,
            )
            if len(translations) == len(texts):
                return translations
            if error_cb:
                error_cb(
                    f"  \u26a0 model returned {len(translations)} items for {len(texts)} inputs; "
                    "splitting and retrying"
                )
        except (RateLimitError, FatalTranslationError):
            # Don't fan out more requests — propagate so the whole job stops.
            raise
        except requests.HTTPError as exc:
            body = ""
            status = exc.response.status_code if exc.response is not None else None
            try:
                body = exc.response.text[:300] if exc.response is not None else ""
            except Exception:
                pass
            if error_cb:
                error_cb(f"  \u26a0 HTTP {status} from translation API: {body}")
            # Auto-recover from "JSON mode not supported" by disabling it for
            # the rest of the run and retrying once.
            if (
                status == 400
                and "json mode is not supported" in (body or "").lower()
                and self._json_mode_enabled
            ):
                self._json_mode_enabled = False
                if error_cb:
                    error_cb(
                        "  \u2192 disabling JSON mode for this model and retrying "
                        "(model doesn't support response_format)"
                    )
                return self._translate_with_recovery(
                    texts,
                    source_language=source_language,
                    target_language=target_lang,
                    stream_cb=stream_cb,
                    error_cb=error_cb,
                )
            if _is_fatal_http_error(status, body):
                raise FatalTranslationError(
                    f"Translation API returned unrecoverable HTTP {status}: {body[:200]}"
                ) from exc
            self._note_translation_error()
        except requests.RequestException as exc:
            if error_cb:
                error_cb(f"  \u26a0 network error talking to translation API: {exc}")
            self._note_translation_error()
        except ValueError as exc:
            if error_cb:
                error_cb(f"  \u26a0 could not parse translation JSON: {exc}")
            self._note_translation_error()

        if len(texts) == 1:
            return self._fallback_translations(
                texts,
                source_language=source_language,
                target_language=target_lang,
                stream_cb=stream_cb,
                error_cb=error_cb,
            )

        midpoint = max(1, len(texts) // 2)
        left = self._translate_with_recovery(
            texts[:midpoint],
            source_language=source_language,
            target_language=target_lang,
            stream_cb=stream_cb,
            error_cb=error_cb,
        )
        right = self._translate_with_recovery(
            texts[midpoint:],
            source_language=source_language,
            target_language=target_lang,
            stream_cb=stream_cb,
            error_cb=error_cb,
        )
        return left + right

    def _describe_source_language(self, source_language):
        normalized = (source_language or "").strip().lower()
        if normalized in LANGUAGE_NAMES:
            return LANGUAGE_NAMES[normalized]
        if normalized and normalized != "unknown":
            return normalized
        return "the source language"

    def _translate_chunk(self, texts, source_language, target_language=None, stream_cb=None):
        source_name = self._describe_source_language(source_language)
        target_lang = (target_language or self.target_language).strip().lower()
        target_name = LANGUAGE_NAMES.get(target_lang, target_lang)

        prompt = (
            f"You are translating subtitles from {source_name} drama dialogue into {target_name}. "
            "Return strict JSON only with this shape: "
            "{\"items\": [{\"target\": \"...\"}]}. "
            "Each item corresponds to one input subtitle line, in order. "
            "Translate the meaning naturally for spoken drama dialogue: keep emotion, tone, and "
            "pacing; avoid literal word-for-word renderings. Use polite or casual register to "
            "match the speaker. Keep proper nouns intact. Do not add explanations, notes, "
            "stage directions, speaker labels, or quotation marks."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a professional drama subtitle translator. "
                    f"Source language: {source_name}. Target language: {target_name}. "
                    "Output must be valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": f"{prompt}\n\nInput JSON:\n{json.dumps({'items': texts}, ensure_ascii=False)}",
            },
        ]

        content = self._chat_completion(
            messages, stream_cb=stream_cb, json_mode=self._json_mode_enabled
        )
        parsed = self._extract_json(content)

        items = parsed.get("items", []) if isinstance(parsed, dict) else []
        normalized = []
        for item in items:
            if isinstance(item, dict):
                # Accept several shapes: {target}, {<lang_code>}, {translation}.
                target = (
                    item.get("target")
                    or item.get(target_lang)
                    or item.get("translation")
                    or ""
                )
                normalized.append({"target": str(target).strip()})
            elif isinstance(item, str):
                normalized.append({"target": item.strip()})
        return normalized

    def _fallback_translations(
        self, texts, source_language, target_language=None, stream_cb=None, error_cb=None
    ):
        """Translate one line at a time. Errors propagate (no source-text fallback)."""
        results = []
        target_lang = target_language or self.target_language
        for text in texts:
            try:
                results.append(
                    self._translate_single(
                        text,
                        source_language=source_language,
                        target_language=target_lang,
                        stream_cb=stream_cb,
                    )
                )
            except (RateLimitError, FatalTranslationError):
                raise
            except requests.HTTPError as exc:
                body = ""
                status = exc.response.status_code if exc.response is not None else None
                try:
                    body = exc.response.text[:300] if exc.response is not None else ""
                except Exception:
                    pass
                if _is_fatal_http_error(status, body):
                    raise FatalTranslationError(
                        f"Translation API returned unrecoverable HTTP {status}: {body[:200]}"
                    ) from exc
                if error_cb:
                    error_cb(f"  \u26a0 single-line translation failed (HTTP {status})")
                self._note_translation_error()
                # _note_translation_error raises FatalTranslationError once the
                # error budget is exhausted; otherwise re-raise the underlying
                # HTTP error so we don't silently leave a line untranslated.
                raise
            except Exception as exc:
                if error_cb:
                    error_cb(f"  \u26a0 single-line translation failed ({type(exc).__name__}): {exc}")
                self._note_translation_error()
                raise
        return results

    def _translate_single(self, text, source_language, target_language=None, stream_cb=None):
        source_name = self._describe_source_language(source_language)
        target_lang = (target_language or self.target_language).strip().lower()
        target_name = LANGUAGE_NAMES.get(target_lang, target_lang)
        messages = [
            {
                "role": "system",
                "content": (
                    f"Translate this {source_name} drama subtitle into {target_name}. "
                    "Keep wording natural and concise for spoken subtitles. "
                    "Return JSON only: {\"target\":\"...\"}."
                ),
            },
            {"role": "user", "content": text},
        ]

        content = self._chat_completion(
            messages, stream_cb=stream_cb, json_mode=self._json_mode_enabled
        )
        parsed = self._extract_json(content)
        if isinstance(parsed, dict):
            target = (
                parsed.get("target")
                or parsed.get(target_lang)
                or parsed.get("translation")
                or ""
            )
            return {"target": str(target).strip() or text}
        return {"target": text}

    # ----------------------------------------------------------- chat completion

    def _chat_completion(self, messages, stream_cb=None, json_mode=False):
        if self.translation_backend == "openrouter":
            return self._chat_completion_openrouter(messages, stream_cb=stream_cb, json_mode=json_mode)
        if self.translation_backend == "deepseek":
            return self._chat_completion_deepseek(messages, stream_cb=stream_cb, json_mode=json_mode)
        return self._chat_completion_ollama(messages, stream_cb=stream_cb, json_mode=json_mode)

    def _chat_completion_ollama(self, messages, stream_cb=None, json_mode=False):
        payload = {
            "model": self.translation_model,
            "stream": bool(stream_cb),
            "messages": messages,
            "options": {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05},
        }
        if json_mode:
            payload["format"] = "json"

        url = f"{self.ollama_base_url.rstrip('/')}/api/chat"

        if stream_cb:
            response = requests.post(
                url, json=payload, timeout=self.translation_timeout, stream=True
            )
            response.raise_for_status()

            chunks = []
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                packet = json.loads(line)
                piece = packet.get("message", {}).get("content", "")
                if piece:
                    chunks.append(piece)
                    stream_cb(piece)
            return "".join(chunks)

        response = requests.post(url, json=payload, timeout=self.translation_timeout)
        response.raise_for_status()
        data = response.json()
        # Ollama usage fields are eval_count / prompt_eval_count.
        usage = {
            "prompt_tokens": data.get("prompt_eval_count") or 0,
            "completion_tokens": data.get("eval_count") or 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        self._record_usage(usage)
        return data.get("message", {}).get("content", "")

    def _chat_completion_openrouter(self, messages, stream_cb=None, json_mode=False):
        if not self.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Export it in the environment to use the openrouter backend."
            )
        extra_headers = {}
        if self.openrouter_referer:
            extra_headers["HTTP-Referer"] = self.openrouter_referer
        if self.openrouter_app_title:
            extra_headers["X-Title"] = self.openrouter_app_title
        return self._chat_completion_openai_compatible(
            base_url=self.openrouter_base_url,
            api_key=self.openrouter_api_key,
            extra_headers=extra_headers,
            messages=messages,
            stream_cb=stream_cb,
            json_mode=json_mode,
        )

    def _chat_completion_deepseek(self, messages, stream_cb=None, json_mode=False):
        if not self.deepseek_api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Export it in the environment to use the deepseek backend."
            )
        # deepseek-v4-flash / v4-pro default to thinking mode, which produces
        # long internal reasoning traces and is too slow / expensive for
        # line-by-line subtitle translation. Force non-thinking mode unless
        # the user explicitly picks the legacy `deepseek-reasoner` alias.
        extra_payload = {}
        model = (self.translation_model or "").lower()
        if model.startswith("deepseek-v4-") or model == "deepseek-chat":
            extra_payload["thinking"] = {"type": "disabled"}
        return self._chat_completion_openai_compatible(
            base_url=self.deepseek_base_url,
            api_key=self.deepseek_api_key,
            extra_headers=None,
            messages=messages,
            stream_cb=stream_cb,
            json_mode=json_mode,
            extra_payload=extra_payload or None,
        )

    def _chat_completion_openai_compatible(
        self,
        *,
        base_url,
        api_key,
        extra_headers,
        messages,
        stream_cb=None,
        json_mode=False,
        extra_payload=None,
    ):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "model": self.translation_model,
            "messages": messages,
            "temperature": 0.2,
            "top_p": 0.9,
            "stream": bool(stream_cb),
        }
        if stream_cb:
            payload["stream_options"] = {"include_usage": True}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if extra_payload:
            payload.update(extra_payload)

        url = f"{base_url.rstrip('/')}/chat/completions"

        if stream_cb:
            with requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.translation_timeout,
                stream=True,
            ) as response:
                response.raise_for_status()
                chunks = []
                for raw in response.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    if raw.startswith(":"):
                        continue
                    if raw.startswith("data:"):
                        raw = raw[5:].lstrip()
                    if not raw or raw == "[DONE]":
                        if raw == "[DONE]":
                            break
                        continue
                    try:
                        packet = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(packet.get("usage"), dict):
                        self._record_usage(packet["usage"])
                    choices = packet.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        chunks.append(piece)
                        stream_cb(piece)
                return "".join(chunks)

        response = self._post_with_rate_limit_retry(
            url, headers=headers, json=payload, timeout=self.translation_timeout
        )
        response.raise_for_status()
        data = response.json()
        self._record_usage(data.get("usage"))
        choices = data.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    def _post_with_rate_limit_retry(self, url, *, max_retries=3, **kwargs):
        """POST that respects OpenRouter / standard rate-limit headers.

        On HTTP 429, waits according to ``Retry-After`` or
        ``X-RateLimit-Reset`` (capped) and retries up to ``max_retries`` times.
        Raises RateLimitError if still rate-limited after the retries so the
        caller can abort the whole job instead of fanning out more requests.
        """
        attempt = 0
        while True:
            response = requests.post(url, **kwargs)
            if response.status_code != 429:
                return response

            attempt += 1
            wait_s = self._parse_retry_wait(response)
            cancel_event = getattr(self, "_active_cancel_event", None)
            if attempt > max_retries:
                raise RateLimitError(
                    f"Rate limited by translation API after {max_retries} retries "
                    f"(last wait would have been {wait_s:.1f}s). "
                    f"Body: {response.text[:200]}"
                )
            # Slept in 1s slices so cancel is responsive.
            slept = 0.0
            while slept < wait_s:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Translation cancelled by user")
                time.sleep(min(1.0, wait_s - slept))
                slept += 1.0

    @staticmethod
    def _parse_retry_wait(response):
        """Return seconds to wait before retrying a 429 response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.5, min(120.0, float(retry_after)))
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                # OpenRouter returns ms-since-epoch.
                reset_ts = float(reset)
                if reset_ts > 1e12:
                    reset_ts /= 1000.0
                wait = reset_ts - time.time()
                # Body sometimes also has a reset header nested; fall back to a
                # sane window if the reset is in the past or absurdly far away.
                if 0 < wait < 120:
                    return wait + 0.5  # small cushion
            except ValueError:
                pass
        return 5.0

    def _record_usage(self, usage):
        if not isinstance(usage, dict):
            return
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        self.translation_usage["prompt_tokens"] += prompt
        self.translation_usage["completion_tokens"] += completion
        self.translation_usage["total_tokens"] += total

    @staticmethod
    def _extract_json(text):
        text = (text or "").strip()
        # Strip markdown code fences (```json ... ``` or ``` ... ```).
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"```\s*$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find the largest balanced { ... } substring.
        start = text.find("{")
        if start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(text[start : i + 1])
                            except json.JSONDecodeError:
                                break
        raise ValueError("Model did not return valid JSON")

    # Models / providers known to reject response_format=json_object on OpenRouter.
    # We strip JSON mode for these and rely on the prompt + robust extraction.
    _NO_JSON_MODE_PATTERNS = (
        ":free",  # most free providers (e.g. SiliconFlow) don't honor JSON mode
        "gemma",  # Google Gemma family on OpenRouter
        "tencent/",
    )

    def _supports_json_mode(self):
        if self.translation_backend != "openrouter":
            return True  # Ollama 'format: json' is widely supported
        model = (self.translation_model or "").lower()
        return not any(p in model for p in self._NO_JSON_MODE_PATTERNS)


# --------------------------------------------------------------------- jobs

class SubtitleJobManager:
    def __init__(self, config):
        self.base_config = dict(config)
        self.pipeline = SubtitlePipeline(config)
        self.jobs = {}
        self.lock = threading.Lock()

    def list_media_files(self):
        allowed_ext = {
            ".mp4",
            ".m4a",
            ".mp3",
            ".wav",
            ".mkv",
            ".mov",
            ".webm",
            ".aac",
            ".flac",
            ".ts",
            ".avi",
        }
        media_files = []
        base_dir = Path(self.pipeline.media_dir)
        if not base_dir.exists():
            return media_files

        for file_path in sorted(base_dir.rglob("*")):
            if file_path.is_file() and file_path.suffix.lower() in allowed_ext:
                rel = file_path.relative_to(base_dir)
                media_files.append(str(rel))
        return media_files

    def start_job(
        self,
        media_path,
        source_language_hint=None,
        target_language=None,
        overrides=None,
        stop_after_transcription=False,
        skip_transcription=False,
    ):
        job_id = str(uuid.uuid4())
        merged_overrides = {
            k: v for k, v in (overrides or {}).items() if v not in (None, "")
        }
        with self.lock:
            self.jobs[job_id] = {
                "status": "queued",
                "progress": 0,
                "message": "Queued",
                "created_at": int(time.time()),
                "media_path": str(media_path),
                "result": None,
                "error": None,
                "overrides": dict(merged_overrides),
                "source_language_hint": source_language_hint,
                "target_language": target_language,
                "stop_after_transcription": bool(stop_after_transcription),
                "skip_transcription": bool(skip_transcription),
                "log": collections.deque(maxlen=400),
                "cancel_event": threading.Event(),
            }

        thread = threading.Thread(
            target=self._run_job,
            args=(
                job_id,
                media_path,
                source_language_hint,
                target_language,
                merged_overrides,
                bool(stop_after_transcription),
                bool(skip_transcription),
            ),
            daemon=True,
        )
        thread.start()
        return job_id

    def _build_pipeline(self, overrides):
        if not overrides:
            return self.pipeline
        merged = dict(self.base_config)
        merged.update(overrides)
        return SubtitlePipeline(merged)

    def _run_job(
        self,
        job_id,
        media_path,
        source_language_hint,
        target_language=None,
        overrides=None,
        stop_after_transcription=False,
        skip_transcription=False,
    ):
        self._update_job(job_id, status="running", progress=1, message="Starting")

        def progress_cb(progress, message):
            self._update_job(job_id, status="running", progress=progress, message=message)

        cancel_event = self._get_cancel_event(job_id)
        stream_cb = self._make_stream_cb(job_id)

        try:
            pipeline = self._build_pipeline(overrides)
            result = pipeline.process(
                media_path,
                progress_cb=progress_cb,
                source_language_hint=source_language_hint,
                target_language=target_language,
                stop_after_transcription=stop_after_transcription,
                skip_transcription=skip_transcription,
                translation_stream_cb=stream_cb,
                cancel_event=cancel_event,
            )
            if stop_after_transcription:
                self._update_job(
                    job_id,
                    status="awaiting_translation",
                    progress=55,
                    message="Transcription complete; choose a translation model",
                    result=result,
                )
            else:
                self._update_job(
                    job_id,
                    status="completed",
                    progress=100,
                    message="Completed",
                    result=result,
                )
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                progress=100,
                message="Failed",
                error=str(exc),
            )

    def start_translation(
        self,
        job_id,
        target_language=None,
        translation_model=None,
        translation_backend=None,
        translation_chunk_size=None,
    ):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(f"Job not found: {job_id}")
            if job.get("status") not in ("awaiting_translation", "completed"):
                raise RuntimeError(
                    f"Job is not ready for translation (status={job.get('status')})"
                )
            media_path = job.get("media_path")
            existing_overrides = dict(job.get("overrides") or {})
            source_language_hint = (
                (job.get("result") or {}).get("source_language")
                or job.get("source_language_hint")
            )

        # Apply per-translation overrides on top of the original job overrides.
        if translation_model:
            existing_overrides["TRANSLATION_MODEL"] = translation_model
        if translation_backend:
            existing_overrides["TRANSLATION_BACKEND"] = translation_backend
        if translation_chunk_size:
            existing_overrides["TRANSLATION_CHUNK_SIZE"] = int(translation_chunk_size)

        target_lang = target_language or job.get("target_language")

        self._update_job(
            job_id,
            status="running",
            progress=60,
            message="Translating subtitles",
            error=None,
        )

        thread = threading.Thread(
            target=self._run_translation,
            args=(job_id, media_path, source_language_hint, target_lang, existing_overrides),
            daemon=True,
        )
        thread.start()
        return job_id

    def cancel_job(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(f"Job not found: {job_id}")
            event = job.get("cancel_event")
            if event is None:
                event = threading.Event()
                job["cancel_event"] = event
            event.set()
            log = job.get("log")
            if log is not None:
                log.append("\u26a0 Cancellation requested by user")
        return True

    def _get_cancel_event(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            event = job.get("cancel_event")
            if event is None:
                event = threading.Event()
                job["cancel_event"] = event
            else:
                # Reset for a fresh run (e.g., translation phase after transcription).
                event.clear()
            return event

    def _make_stream_cb(self, job_id):
        buffer = {"line": ""}

        def append_line(text):
            with self.lock:
                job = self.jobs.get(job_id)
                if job is not None:
                    log = job.get("log")
                    if log is None:
                        log = collections.deque(maxlen=400)
                        job["log"] = log
                    log.append(text)

        def stream_cb(piece):
            if not piece:
                return
            buffer["line"] += piece
            while "\n" in buffer["line"]:
                line, _, rest = buffer["line"].partition("\n")
                buffer["line"] = rest
                if line.strip():
                    append_line(line.rstrip())

        return stream_cb

    def _run_translation(
        self,
        job_id,
        media_path,
        source_language_hint,
        target_language,
        overrides,
    ):
        def progress_cb(progress, message):
            self._update_job(job_id, status="running", progress=progress, message=message)

        cancel_event = self._get_cancel_event(job_id)
        stream_cb = self._make_stream_cb(job_id)

        try:
            pipeline = self._build_pipeline(overrides)
            result = pipeline.process(
                media_path,
                progress_cb=progress_cb,
                skip_transcription=True,
                source_language_hint=source_language_hint,
                target_language=target_language,
                translation_stream_cb=stream_cb,
                cancel_event=cancel_event,
            )
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job["overrides"] = dict(overrides)
                    if target_language:
                        job["target_language"] = target_language
            self._update_job(
                job_id,
                status="completed",
                progress=100,
                message="Completed",
                result=result,
            )
        except Exception as exc:
            self._update_job(
                job_id,
                status="awaiting_translation",
                progress=55,
                message="Translation failed; pick a model and retry",
                error=str(exc),
            )

    def _update_job(self, job_id, **fields):
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(fields)

    def get_job(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)
