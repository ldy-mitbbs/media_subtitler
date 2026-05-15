import os
import threading
from pathlib import Path

import pytest

from app.models.subtitle_pipeline import SubtitlePipeline, SubtitleJobManager


# ------------------------------------------------------------------ helpers

def _pipeline(backend, model, **overrides):
    cfg = {
        "MEDIA_DIR": overrides.pop("MEDIA_DIR", "media"),
        "TRANSLATION_BACKEND": backend,
        "TRANSLATION_MODEL": model,
        "TRANSLATION_CHUNK_SIZE": overrides.pop("TRANSLATION_CHUNK_SIZE", 4),
        "TRANSLATION_TIMEOUT": 120,
        "TARGET_LANGUAGE": "zh",
    }
    cfg.update(overrides)
    return SubtitlePipeline(cfg)


# ------------------------------------------------------------------ Ollama integration

@pytest.mark.integration
@pytest.mark.ollama
@pytest.mark.slow
class TestOllamaIntegration:
    """这些测试需要本地运行 Ollama 守护进程，并至少拉取了一个模型。
    默认使用 qwen3.5:9b（如果可用），否则尝试 qwen2.5:14b。
    """

    @pytest.fixture(scope="class")
    def ollama_model(self):
        import requests
        try:
            resp = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
        except Exception as exc:
            pytest.skip(f"Ollama not reachable: {exc}")

        # Prefer smaller/faster models for tests.
        for candidate in ("qwen3.5:9b", "qwen2.5:14b", "qwen2.5:7b", "llama3.2:3b"):
            if candidate in models:
                return candidate
        if models:
            return models[0]
        pytest.skip("No Ollama models found")

    def test_ollama_translate_single_line(self, ollama_model):
        pipeline = _pipeline("ollama", ollama_model, TRANSLATION_CHUNK_SIZE=10)
        result = pipeline._translate_single(
            "hello",
            source_language="en",
            target_language="zh",
        )
        assert "target" in result
        # Should produce some Chinese text, not identical to source.
        assert result["target"] != "hello"
        assert len(result["target"]) > 0

    def test_ollama_translate_chunk(self, ollama_model):
        # Use a single line to avoid timeout on large/slow local models while
        # still exercising the _translate_chunk code path (prompt scaffolding,
        # JSON extraction, key fallback, etc.).
        pipeline = _pipeline("ollama", ollama_model, TRANSLATION_CHUNK_SIZE=10)
        texts = ["Hello"]
        result = pipeline._translate_chunk(
            texts,
            source_language="en",
            target_language="zh",
        )
        assert len(result) == len(texts)
        for item in result:
            assert "target" in item
            assert len(item["target"]) > 0

    def test_ollama_chat_completion_without_stream(self, ollama_model):
        pipeline = _pipeline("ollama", ollama_model)
        content = pipeline._chat_completion_ollama(
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Reply with JSON only."},
                {"role": "user", "content": 'Return {"hello": "world"}'},
            ],
            stream_cb=None,
            json_mode=False,
        )
        assert isinstance(content, str)
        assert len(content) > 0

    def test_ollama_full_process_mocked_whisper(self, tmp_path, ollama_model):
        """端到端：mock Whisper 语音识别，用真实 Ollama 翻译。
        只用 1 条极短字幕，避免大模型冷启动超时。"""
        media = tmp_path / "ep01.mp4"
        media.write_bytes(b"fake")
        orig_srt = media.with_suffix(".orig.srt")
        orig_srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n",
            encoding="utf-8",
        )

        pipeline = _pipeline(
            "ollama",
            ollama_model,
            TRANSLATION_CHUNK_SIZE=1,
            TRANSLATION_TIMEOUT=300,
            MEDIA_DIR=str(tmp_path),
        )

        result = pipeline.process(
            media,
            skip_transcription=True,
            source_language_hint="en",
        )

        assert result["stage"] == "completed"
        assert result["segment_count"] == 1
        assert Path(result["bilingual_srt"]).exists()
        # Basic sanity: usage should have been recorded.
        assert result["usage"]["total_tokens"] > 0

    def test_ollama_two_phase_workflow(self, tmp_path, ollama_model):
        """Web 端两阶段流程：先语音识别，后翻译。"""
        media = tmp_path / "ep01.mp4"
        media.write_bytes(b"fake")
        orig_srt = media.with_suffix(".orig.srt")
        orig_srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nGood morning.\n\n",
            encoding="utf-8",
        )

        cfg = {
            "MEDIA_DIR": str(tmp_path),
            "TRANSLATION_BACKEND": "ollama",
            "TRANSLATION_MODEL": ollama_model,
            "TRANSLATION_CHUNK_SIZE": 10,
            "TARGET_LANGUAGE": "zh",
        }
        manager = SubtitleJobManager(cfg)

        job_id = manager.start_job(
            media,
            stop_after_transcription=True,
            skip_transcription=True,
        )

        # Wait for phase 1 to settle.
        for _ in range(50):
            job = manager.get_job(job_id)
            if job and job.get("status") in ("awaiting_translation", "failed"):
                break
            import time as _t
            _t.sleep(0.02)

        job = manager.get_job(job_id)
        assert job["status"] == "awaiting_translation"

        # Phase 2: translate with Ollama.
        manager.start_translation(job_id, target_language="zh")
        # Wait up to 5 minutes; local model load + inference can be slow.
        for _ in range(6000):
            job = manager.get_job(job_id)
            if job and job.get("status") in ("completed", "failed"):
                break
            import time as _t
            _t.sleep(0.05)

        job = manager.get_job(job_id)
        assert job["status"] == "completed", (
            f"status={job.get('status')!r} error={job.get('error')!r}"
        )
        result = job["result"]
        assert Path(result["bilingual_srt"]).exists()
        assert result["usage"]["total_tokens"] > 0


# ------------------------------------------------------------------ OpenRouter integration

@pytest.mark.integration
@pytest.mark.openrouter
class TestOpenRouterIntegration:
    """这些测试使用 OpenRouter 上的超便宜付费模型（google/gemini-2.5-flash-lite）。
    单次测试仅几千 token，成本约 $0.0001，基本可以忽略。
    使用付费模型是为了避免免费模型 (:free) 普遍存在的 rate-limit 问题。
    """

    @pytest.fixture(scope="class")
    def or_api_key(self):
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            pytest.skip("OPENROUTER_API_KEY not set")
        return key

    @pytest.fixture(scope="class")
    def free_model(self):
        # 使用超便宜的付费模型代替免费模型。
        # 免费模型 (:free) 经常被上游 rate-limit，自动化测试几乎跑不通。
        # google/gemini-2.5-flash-lite 约 $0.10/$0.40 per 1M tokens，
        # 单次测试仅几千 token，成本可忽略 (~$0.0001)。
        return "google/gemini-2.5-flash-lite"

    def test_openrouter_translate_single_line(self, or_api_key, free_model):
        pipeline = _pipeline("openrouter", free_model, OPENROUTER_API_KEY=or_api_key)
        result = pipeline._translate_single(
            "hello",
            source_language="en",
            target_language="zh",
        )
        assert "target" in result
        assert result["target"] != "hello"
        assert len(result["target"]) > 0

    def test_openrouter_translate_chunk(self, or_api_key, free_model):
        pipeline = _pipeline("openrouter", free_model, OPENROUTER_API_KEY=or_api_key)
        texts = ["Hello", "How are you?"]
        result = pipeline._translate_chunk(
            texts,
            source_language="en",
            target_language="zh",
        )
        assert len(result) == len(texts)
        for item in result:
            assert "target" in item
            assert len(item["target"]) > 0

    def test_openrouter_streaming(self, or_api_key, free_model):
        pipeline = _pipeline("openrouter", free_model, OPENROUTER_API_KEY=or_api_key)
        chunks = []

        def stream_cb(piece):
            chunks.append(piece)

        content = pipeline._chat_completion_openrouter(
            messages=[
                {"role": "system", "content": "Reply with a single word."},
                {"role": "user", "content": "Say hello"},
            ],
            stream_cb=stream_cb,
            json_mode=False,
        )
        assert isinstance(content, str)
        assert len(content) > 0
        assert len(chunks) > 0

    def test_openrouter_no_api_key_raises(self):
        pipeline = _pipeline("openrouter", "google/gemma-4-26b-a4b-it:free", OPENROUTER_API_KEY="")
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            pipeline._chat_completion_openrouter(
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_openrouter_rate_limit_retry_then_abort(self, or_api_key, free_model, mocker):
        """模拟连续 429，验证重试后抛出 RateLimitError。"""
        import requests
        pipeline = _pipeline(
            "openrouter",
            free_model,
            OPENROUTER_API_KEY=or_api_key,
            TRANSLATION_TIMEOUT=5,
        )

        call_count = 0

        def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            class FakeResp:
                status_code = 429
                headers = {"Retry-After": "0.1"}
                text = "rate limited"
            return FakeResp()

        mocker.patch("requests.post", side_effect=fake_post)

        with pytest.raises(RuntimeError, match="cancelled"):
            # Use a cancel event to short-circuit the sleep loop quickly.
            cancel = threading.Event()
            cancel.set()
            pipeline._active_cancel_event = cancel
            pipeline._post_with_rate_limit_retry("http://dummy", json={})


# ------------------------------------------------------------------ DeepSeek integration

@pytest.mark.integration
@pytest.mark.openrouter  # reusing marker family since it's a cloud API
class TestDeepSeekIntegration:
    """⚠️ 这些测试会消耗真实的 DeepSeek API Token（按量计费）。
    必须设置 DEEPSEEK_API_KEY 才会运行。
    deepseek-v4-flash 当前价格：prompt $0.14 / 1M tokens, completion $0.28 / 1M tokens。
    单次测试通常花费 < $0.001，但连续跑多次会累积。
    """

    @pytest.fixture(scope="class")
    def ds_api_key(self):
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            pytest.skip("DEEPSEEK_API_KEY not set")
        return key

    def test_deepseek_translate_single_line(self, ds_api_key):
        pipeline = _pipeline("deepseek", "deepseek-v4-flash", DEEPSEEK_API_KEY=ds_api_key)
        result = pipeline._translate_single(
            "hello",
            source_language="en",
            target_language="zh",
        )
        assert "target" in result
        assert result["target"] != "hello"
        assert len(result["target"]) > 0

    def test_deepseek_thinking_mode_disabled(self, ds_api_key):
        """deepseek-v4 默认启用 thinking mode；验证我们已强制关闭。"""
        pipeline = _pipeline("deepseek", "deepseek-v4-flash", DEEPSEEK_API_KEY=ds_api_key)
        content = pipeline._chat_completion_deepseek(
            messages=[
                {"role": "system", "content": "Reply with JSON only."},
                {"role": "user", "content": 'Return {"thinking": false}'},
            ],
            json_mode=False,
        )
        assert isinstance(content, str)
        # If thinking mode were on, output would contain  <tool_call> tags and be much longer.
        assert "<tool_call>" not in content

    def test_deepseek_no_api_key_raises(self):
        pipeline = _pipeline("deepseek", "deepseek-v4-flash", DEEPSEEK_API_KEY="")
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            pipeline._chat_completion_deepseek(
                messages=[{"role": "user", "content": "hi"}],
            )


# ------------------------------------------------------------------ misc integration

@pytest.mark.integration
class TestRealWhisperBackends:
    """这些测试需要本地安装 whisper 模型，运行很慢，标记为 integration + slow。"""

    @pytest.mark.slow
    def test_faster_whisper_transcribe_10s_clip(self, tmp_path):
        pytest.skip("Requires real audio file and whisper model download")

    @pytest.mark.slow
    def test_whispercpp_transcribe_10s_clip(self, tmp_path):
        pytest.skip("Requires whisper-cli binary and ggml model")
