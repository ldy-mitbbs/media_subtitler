import json
from pathlib import Path

import pytest
import requests

from app.models.subtitle_pipeline import (
    SubtitlePipeline,
    SubtitleJobManager,
    clean_extracted_subtitle_segments,
    clean_extracted_subtitle_text,
    detect_video_play_res,
    _dedupe_repeated_segments,
    _find_media_tool,
    _is_fatal_http_error,
    format_srt_timestamp,
    language_display_name,
    parse_srt_timestamp,
    read_srt,
    write_bilingual_ass,
    write_srt,
)
from app.routes import _safe_unicode_filename, _supports_json_mode, _adaptive_chunk_size


# ------------------------------------------------------------------ SRT I/O

class TestSRTIO:
    def test_write_and_read_srt_roundtrip(self, tmp_path):
        segments = [
            {"start": 0.5, "end": 2.3, "text": "Hello world"},
            {"start": 3.0, "end": 4.5, "text": "Line one\nLine two"},
        ]
        path = tmp_path / "test.srt"
        write_srt(segments, path)
        loaded = read_srt(path)

        assert len(loaded) == 2
        assert loaded[0]["start"] == pytest.approx(0.5)
        assert loaded[0]["end"] == pytest.approx(2.3)
        assert loaded[0]["text"] == "Hello world"
        assert loaded[1]["text"] == "Line one\nLine two"

    def test_read_srt_skips_malformed_blocks(self, tmp_path):
        path = tmp_path / "bad.srt"
        path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nOK\n\n"
            "no time line here\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nAlso OK\n\n",
            encoding="utf-8",
        )
        loaded = read_srt(path)
        assert len(loaded) == 2
        assert loaded[0]["text"] == "OK"
        assert loaded[1]["text"] == "Also OK"

    def test_format_srt_timestamp_zero(self):
        assert format_srt_timestamp(0) == "00:00:00,000"

    def test_format_srt_timestamp_with_millis(self):
        assert format_srt_timestamp(3661.123) == "01:01:01,123"

    def test_parse_srt_timestamp_roundtrip(self):
        ts = "01:23:45,678"
        parsed = parse_srt_timestamp(ts)
        assert parsed == pytest.approx(1 * 3600 + 23 * 60 + 45 + 0.678)

    def test_read_srt_empty_file(self, tmp_path):
        path = tmp_path / "empty.srt"
        path.write_text("", encoding="utf-8")
        assert read_srt(path) == []


class TestExtractedSubtitleCleanup:
    def test_clean_extracted_subtitle_text_strips_arib_markup(self):
        text = (
            '<font face="sans-serif" size="36">{\\an7}(安藤)ここです</font>'
            '<font face="sans-serif" size="36">{\\an7}この部屋です</font>'
        )

        assert clean_extracted_subtitle_text(text) == "(安藤)ここです\nこの部屋です"

    def test_clean_extracted_subtitle_segments_drops_empty_and_repairs_long_end_times(self):
        segments = [
            {"start": 2.219, "end": 4294920.0, "text": '<font size="36"></font>'},
            {"start": 50.350, "end": 4295000.0, "text": "{\\an7}ここです"},
            {"start": 53.353, "end": 55.853, "text": "{\\an7}先に入れ"},
        ]

        cleaned = clean_extracted_subtitle_segments(segments)

        assert [segment["text"] for segment in cleaned] == ["ここです", "先に入れ"]
        assert cleaned[0]["end"] == pytest.approx(53.352)
        assert cleaned[1]["end"] == pytest.approx(55.853)


class TestBilingualASS:
    def test_write_bilingual_ass_uses_custom_play_resolution(self, tmp_path):
        path = tmp_path / "out.ass"
        write_bilingual_ass(
            [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "source_text": "source",
                    "target_text": "translation",
                }
            ],
            path,
            play_res=(1440, 1080),
        )

        content = path.read_text(encoding="utf-8-sig")

        assert "PlayResX: 1440" in content
        assert "PlayResY: 1080" in content

    def test_write_bilingual_ass_falls_back_to_default_play_resolution(self, tmp_path):
        path = tmp_path / "out.ass"
        write_bilingual_ass([], path, play_res=("bad", 0))

        content = path.read_text(encoding="utf-8-sig")

        assert "PlayResX: 1920" in content
        assert "PlayResY: 1080" in content

    def test_detect_video_play_res_reads_first_video_stream(self, monkeypatch, tmp_path):
        class Completed:
            returncode = 0
            stdout = '{"streams":[{"width":1440,"height":1080}]}'

        monkeypatch.setattr("app.models.subtitle_pipeline.shutil.which", lambda _: "ffprobe")
        monkeypatch.setattr(
            "app.models.subtitle_pipeline.subprocess.run",
            lambda *args, **kwargs: Completed(),
        )

        assert detect_video_play_res(tmp_path / "episode.ts") == (1440, 1080)


# ------------------------------------------------------------------ dedupe

class TestDedupeRepeatedSegments:
    def test_drops_beyond_two_consecutive(self):
        segments = [
            {"text": "a"}, {"text": "a"}, {"text": "a"}, {"text": "a"},
            {"text": "b"}, {"text": "b"}, {"text": "b"},
        ]
        out = _dedupe_repeated_segments(segments)
        texts = [s["text"] for s in out]
        assert texts == ["a", "a", "b", "b"]

    def test_keeps_two_consecutive(self):
        segments = [{"text": "x"}, {"text": "x"}]
        out = _dedupe_repeated_segments(segments)
        assert [s["text"] for s in out] == ["x", "x"]

    def test_skips_empty_text(self):
        segments = [{"text": ""}, {"text": "  "}, {"text": "ok"}]
        out = _dedupe_repeated_segments(segments)
        assert [s["text"] for s in out] == ["ok"]


# ------------------------------------------------------------------ fatal error detection

class TestFatalHttpError:
    def test_401_is_fatal(self):
        assert _is_fatal_http_error(401, "")

    def test_403_is_fatal(self):
        assert _is_fatal_http_error(403, "")

    def test_404_is_fatal(self):
        assert _is_fatal_http_error(404, "")

    def test_400_with_model_not_found_is_fatal(self):
        assert _is_fatal_http_error(400, "model not found bla")

    def test_400_with_invalid_api_key_is_fatal(self):
        assert _is_fatal_http_error(400, "invalid api key")

    def test_400_generic_is_not_fatal(self):
        assert not _is_fatal_http_error(400, "bad request")

    def test_500_is_not_fatal(self):
        assert not _is_fatal_http_error(500, "server error")

    def test_429_is_not_fatal(self):
        assert not _is_fatal_http_error(429, "rate limited")


# ------------------------------------------------------------------ JSON extraction

class TestExtractJson:
    def test_plain_json(self):
        assert SubtitlePipeline._extract_json('{"items": []}') == {"items": []}

    def test_json_with_markdown_fence(self):
        text = "```json\n{\"target\": \"hello\"}\n```"
        assert SubtitlePipeline._extract_json(text) == {"target": "hello"}

    def test_json_with_generic_fence(self):
        text = "```\n{\"target\": \"hello\"}\n```"
        assert SubtitlePipeline._extract_json(text) == {"target": "hello"}

    def test_json_embedded_in_text(self):
        text = "Sure! Here is the result: {\"target\": \"hello\"} Hope that helps."
        assert SubtitlePipeline._extract_json(text) == {"target": "hello"}

    def test_nested_braces(self):
        text = '{"outer": {"inner": "val"}}'
        assert SubtitlePipeline._extract_json(text) == {"outer": {"inner": "val"}}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="valid JSON"):
            SubtitlePipeline._extract_json("not json at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="valid JSON"):
            SubtitlePipeline._extract_json("")


# ------------------------------------------------------------------ retry wait parsing

class TestParseRetryWait:
    def test_retry_after_seconds(self):
        class FakeResp:
            headers = {"Retry-After": "10"}
        assert SubtitlePipeline._parse_retry_wait(FakeResp()) == 10.0

    def test_retry_after_capped(self):
        class FakeResp:
            headers = {"Retry-After": "500"}
        assert SubtitlePipeline._parse_retry_wait(FakeResp()) == 120.0

    def test_rate_limit_reset_epoch(self):
        import time
        class FakeResp:
            headers = {"X-RateLimit-Reset": str(int(time.time()) + 5)}
        wait = SubtitlePipeline._parse_retry_wait(FakeResp())
        assert 4.0 < wait < 7.0

    def test_rate_limit_reset_ms_epoch(self):
        import time
        class FakeResp:
            headers = {"X-RateLimit-Reset": str(int((time.time() + 5) * 1000))}
        wait = SubtitlePipeline._parse_retry_wait(FakeResp())
        assert 4.0 < wait < 7.0

    def test_default_fallback(self):
        class FakeResp:
            headers = {}
        assert SubtitlePipeline._parse_retry_wait(FakeResp()) == 5.0


def test_find_media_tool_checks_common_gui_launch_paths(monkeypatch):
    target = "/opt/homebrew/bin/ffprobe"

    monkeypatch.setattr("app.models.subtitle_pipeline.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "app.models.subtitle_pipeline.Path.exists",
        lambda self: str(self) == target,
    )
    monkeypatch.setattr(
        "app.models.subtitle_pipeline.os.access",
        lambda path, mode: str(path) == target,
    )

    assert _find_media_tool("ffprobe") == target


# ------------------------------------------------------------------ record usage

class TestRecordUsage:
    def test_record_usage_updates_counters(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        pipeline._record_usage({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        assert pipeline.translation_usage == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def test_record_usage_computes_total_if_missing(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        pipeline._record_usage({"prompt_tokens": 5, "completion_tokens": 7})
        assert pipeline.translation_usage["total_tokens"] == 12

    def test_record_usage_ignores_non_dict(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        pipeline._record_usage(None)
        assert pipeline.translation_usage["total_tokens"] == 0


# ------------------------------------------------------------------ language / naming

class TestLanguageHelpers:
    def test_language_display_name_known(self):
        assert language_display_name("ja") == "Japanese"
        assert language_display_name("ko") == "Korean"

    def test_language_display_name_unknown(self):
        assert language_display_name("xx") == "xx"

    def test_language_display_name_empty(self):
        assert language_display_name("") == ""

    def test_describe_source_language_known(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._describe_source_language("ja") == "Japanese"

    def test_describe_source_language_unknown(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._describe_source_language("unknown") == "the source language"


# ------------------------------------------------------------------ filename sanitize

class TestSafeUnicodeFilename:
    def test_preserved_cjk(self):
        assert _safe_unicode_filename("さすらい署長.mkv") == "さすらい署長.mkv"

    def test_strips_path_traversal(self):
        assert _safe_unicode_filename("../../etc/passwd") == "passwd"

    def test_strips_control_chars(self):
        assert _safe_unicode_filename("file\x00name.txt") == "filename.txt"

    def test_strips_windows_illegal(self):
        assert _safe_unicode_filename("a<b>.txt") == "ab.txt"

    def test_collapse_whitespace(self):
        assert _safe_unicode_filename("  hello   world  ") == "hello world"

    def test_empty_fallback(self):
        assert _safe_unicode_filename("") == ""


# ------------------------------------------------------------------ backend resolution

class TestResolveBackend:
    def test_auto_prefers_whispercpp_when_available(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/whisper-cli")
        cfg = {
            "WHISPER_BACKEND": "auto",
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._resolve_backend() == "whispercpp"

    def test_auto_falls_back_to_faster_whisper(self, mocker):
        mocker.patch("shutil.which", return_value=None)
        cfg = {
            "WHISPER_BACKEND": "auto",
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._resolve_backend() == "faster-whisper"

    def test_explicit_faster_whisper(self):
        cfg = {
            "WHISPER_BACKEND": "faster-whisper",
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._resolve_backend() == "faster-whisper"

    def test_explicit_qwen3_asr(self):
        cfg = {
            "ASR_BACKEND": "qwen3-asr",
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._resolve_backend() == "qwen3-asr"

    def test_unsupported_backend_raises(self):
        cfg = {
            "ASR_BACKEND": "unknown",
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        with pytest.raises(RuntimeError, match="Unsupported ASR_BACKEND"):
            pipeline._resolve_backend()


# ------------------------------------------------------------------ JSON mode support

class TestSupportsJsonMode:
    def test_openrouter_free_disabled(self):
        cfg = {
            "TRANSLATION_BACKEND": "openrouter",
            "TRANSLATION_MODEL": "meta-llama/llama-3.1-8b:free",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert not pipeline._supports_json_mode()

    def test_openrouter_paid_enabled(self):
        cfg = {
            "TRANSLATION_BACKEND": "openrouter",
            "TRANSLATION_MODEL": "google/gemini-2.5-flash-lite",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._supports_json_mode()

    def test_ollama_always_enabled(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "qwen2.5:14b",
            "TARGET_LANGUAGE": "zh",
        }
        pipeline = SubtitlePipeline(cfg)
        assert pipeline._supports_json_mode()


# ------------------------------------------------------------------ adaptive chunk size

class TestAdaptiveChunkSize:
    def test_ollama_small(self):
        assert _adaptive_chunk_size("ollama", "qwen2.5:14b") == 8

    def test_deepseek_v4(self):
        assert _adaptive_chunk_size("deepseek", "deepseek-v4-flash") == 20

    def test_openrouter_free(self):
        assert _adaptive_chunk_size("openrouter", "meta-llama/llama-3.1-8b:free") == 5

    def test_unknown_defaults_to_10(self):
        assert _adaptive_chunk_size("unknown", "") == 10


# ------------------------------------------------------------------ error budget / circuit breaker

class TestTranslationErrorBudget:
    def test_error_budget_triggers_fatal(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
            "TRANSLATION_ERROR_BUDGET": 4,
        }
        pipeline = SubtitlePipeline(cfg)
        pipeline._note_translation_error()
        pipeline._note_translation_error()
        pipeline._note_translation_error()
        # 4th error should trigger (count == budget)
        with pytest.raises(RuntimeError, match="Aborting after 4 translation errors"):
            pipeline._note_translation_error()

    def test_zero_budget_disables_check(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
            "TRANSLATION_ERROR_BUDGET": 0,
        }
        pipeline = SubtitlePipeline(cfg)
        for _ in range(100):
            pipeline._note_translation_error()  # should not raise


# ------------------------------------------------------------------ cancel event

class TestCancelEvent:
    def test_cancel_event_stops_translation(self):
        cfg = {
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": "dummy",
            "TARGET_LANGUAGE": "zh",
            "TRANSLATION_CHUNK_SIZE": 2,
        }
        pipeline = SubtitlePipeline(cfg)
        cancel = __import__("threading").Event()
        cancel.set()

        segments = [
            {"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 1.0, "end": 2.0, "text": "b"},
        ]
        with pytest.raises(RuntimeError, match="cancelled"):
            pipeline._translate_segments(segments, source_language="ja", cancel_event=cancel)


# ------------------------------------------------------------------ job manager basics

class TestJobManagerBasics:
    def test_list_media_files_recursive(self, tmp_path):
        cfg = {"MEDIA_DIR": str(tmp_path)}
        manager = SubtitleJobManager(cfg)

        (tmp_path / "show" / "season1").mkdir(parents=True)
        (tmp_path / "show" / "season1" / "ep01.mkv").write_text("fake")
        (tmp_path / "show" / "ep02.mp4").write_text("fake")
        (tmp_path / "readme.txt").write_text("not media")

        files = manager.list_media_files()
        assert sorted(files) == ["show/ep02.mp4", "show/season1/ep01.mkv"]

    def test_start_and_get_job(self, tmp_path):
        media = tmp_path / "vid.mp4"
        media.write_text("fake")
        cfg = {"MEDIA_DIR": str(tmp_path)}
        manager = SubtitleJobManager(cfg)
        job_id = manager.start_job(media)
        job = manager.get_job(job_id)
        assert job["status"] in ("queued", "running", "failed")
        assert job["media_path"] == str(media)

    def test_cancel_job_sets_event(self, tmp_path):
        media = tmp_path / "vid.mp4"
        media.write_text("fake")
        cfg = {"MEDIA_DIR": str(tmp_path)}
        manager = SubtitleJobManager(cfg)
        job_id = manager.start_job(media)
        manager.cancel_job(job_id)
        job = manager.get_job(job_id)
        assert job["cancel_event"].is_set()

    def test_cancel_nonexistent_raises(self, tmp_path):
        cfg = {"MEDIA_DIR": str(tmp_path)}
        manager = SubtitleJobManager(cfg)
        with pytest.raises(KeyError):
            manager.cancel_job("no-such-id")
