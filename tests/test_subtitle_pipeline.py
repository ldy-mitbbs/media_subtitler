import json
from pathlib import Path

import pytest
import requests

from app.models.subtitle_pipeline import SubtitlePipeline


def _pipeline(**overrides):
    cfg = {
        "MEDIA_DIR": overrides.pop("MEDIA_DIR", "media"),
        "TRANSLATION_BACKEND": "ollama",
        "TRANSLATION_MODEL": "qwen2.5:14b",
        "TRANSLATION_CHUNK_SIZE": overrides.pop("TRANSLATION_CHUNK_SIZE", 4),
        "TRANSLATION_TIMEOUT": 120,
        "TARGET_LANGUAGE": "zh",
    }
    cfg.update(overrides)
    return SubtitlePipeline(cfg)


def test_load_json_with_fallback_handles_cp932_bytes(tmp_path):
    payload = {
        "result": {"language": "ja"},
        "transcription": [
            {"text": "圖師 凜", "offsets": {"from": 0, "to": 1250}},
        ],
    }
    json_path = tmp_path / "transcription.json"
    json_path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("cp932"))

    loaded = SubtitlePipeline._load_json_with_fallback(json_path)

    assert loaded == payload


def test_load_json_with_fallback_handles_cp949_bytes(tmp_path):
    payload = {
        "result": {"language": "ko"},
        "transcription": [
            {"text": "안녕하세요", "offsets": {"from": 0, "to": 1500}},
        ],
    }
    json_path = tmp_path / "transcription_ko.json"
    json_path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("cp949"))

    loaded = SubtitlePipeline._load_json_with_fallback(json_path)

    assert loaded == payload


def test_count_cjk_chars_includes_hangul():
    assert SubtitlePipeline._count_cjk_chars("안녕하세요") == 5
    assert SubtitlePipeline._count_cjk_chars("こんにちは") == 5
    assert SubtitlePipeline._count_cjk_chars("你好") == 2
    assert SubtitlePipeline._count_cjk_chars("hello") == 0


def test_repair_mojibake_text_restores_utf8_interpreted_as_latin1():
    # Build the mojibake form deterministically from real Japanese text by
    # round-tripping through latin-1, which models the bug pattern observed
    # when whisper.cpp output is mis-decoded.
    original = "午後一番のおすすめ商品"
    mojibake = original.encode("utf-8").decode("latin-1")

    assert SubtitlePipeline._repair_mojibake_text(mojibake) == original


def test_translate_with_recovery_splits_batch_on_timeout(mocker):
    pipeline = _pipeline()

    def translate_chunk(texts, source_language, target_language=None, stream_cb=None):
        if len(texts) > 1:
            raise requests.Timeout("timed out")
        return [{"target": f"zh:{texts[0]}"}]

    mocked = mocker.patch.object(pipeline, "_translate_chunk", side_effect=translate_chunk)

    translations = pipeline._translate_with_recovery(
        ["a", "b", "c", "d"], source_language="ja"
    )

    assert translations == [
        {"target": "zh:a"},
        {"target": "zh:b"},
        {"target": "zh:c"},
        {"target": "zh:d"},
    ]
    assert mocked.call_count == 7


def test_translate_chunk_accepts_zh_key(mocker):
    pipeline = _pipeline()

    def fake_chat(messages, stream_cb=None, json_mode=False):
        return json.dumps({"items": [{"zh": "你好"}, {"target": "再见"}]})

    mocker.patch.object(pipeline, "_chat_completion", side_effect=fake_chat)

    out = pipeline._translate_chunk(["こんにちは", "さようなら"], source_language="ja")
    assert out == [{"target": "你好"}, {"target": "再见"}]


def test_translate_segments_emits_source_plus_target(mocker):
    pipeline = _pipeline(TRANSLATION_CHUNK_SIZE=10)
    mocker.patch.object(
        pipeline,
        "_translate_chunk",
        return_value=[{"target": "你好"}, {"target": "谢谢"}],
    )

    segments = [
        {"start": 0.0, "end": 1.0, "text": "こんにちは"},
        {"start": 1.0, "end": 2.0, "text": "ありがとう"},
    ]
    out = pipeline._translate_segments(segments, source_language="ja")

    assert [seg["text"] for seg in out] == [
        "こんにちは\n你好",
        "ありがとう\n谢谢",
    ]


def test_process_skip_transcription_uses_existing_orig_srt(tmp_path, mocker):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    orig_srt = media_path.with_suffix(".orig.srt")
    orig_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n안녕\n\n",
        encoding="utf-8",
    )

    pipeline = _pipeline(TRANSLATION_CHUNK_SIZE=10)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[
            {
                "start": 0.0,
                "end": 1.0,
                "source_text": "안녕\n먼저 갈게요",
                "target_text": "你好\n我先走了",
                "text": "안녕\n먼저 갈게요\n你好\n我先走了",
            }
        ],
    )

    result = pipeline.process(
        media_path, skip_transcription=True, source_language_hint="ko"
    )

    transcribe_mock.assert_not_called()
    assert result["segment_count"] == 1
    assert result["source_language"] == "ko"
    assert result["target_language"] == "zh"
    bilingual = Path(result["bilingual_srt"])
    assert bilingual.exists()
    assert bilingual.name.endswith(".bilingual.srt")
    styled = Path(result["bilingual_ass"])
    assert styled.exists()
    assert styled.name.endswith(".bilingual.ass")
    styled_text = styled.read_text(encoding="utf-8-sig")
    assert "Style: Source" in styled_text
    assert "Style: Translation" in styled_text
    assert styled_text.count("Dialogue: 0,0:00:00.00,0:00:01.00,Source") == 1
    assert r"{\rSource}안녕\N먼저 갈게요\N{\rTranslation}你好\N我先走了" in styled_text


def test_process_skip_transcription_requires_existing_orig_srt(tmp_path):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline()

    with pytest.raises(RuntimeError, match="original SRT not found"):
        pipeline.process(media_path, skip_transcription=True)


def test_process_uses_embedded_subtitles_before_whisper(tmp_path, mocker):
    media_path = tmp_path / "sample01.mkv"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline(TRANSLATION_CHUNK_SIZE=10)

    def fake_which(command):
        return f"/usr/bin/{command}" if command in {"ffprobe", "ffmpeg"} else None

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0].endswith("ffprobe"):
            return mocker.Mock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "index": 2,
                                "codec_name": "subrip",
                                "tags": {"language": "ko", "title": "Korean"},
                            }
                        ]
                    }
                ),
                stderr="",
            )

        Path(cmd[-1]).write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n안녕\n\n",
            encoding="utf-8",
        )
        return mocker.Mock(returncode=0, stdout="", stderr="")

    mocker.patch("shutil.which", side_effect=fake_which)
    run_mock = mocker.patch("subprocess.run", side_effect=fake_run)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 1.0, "text": "안녕\n你好"}],
    )

    result = pipeline.process(media_path)

    transcribe_mock.assert_not_called()
    assert result["source_language"] == "ko"
    assert result["segment_count"] == 1
    assert Path(result["original_srt"]).read_text(encoding="utf-8").count("안녕") == 1
    ffmpeg_cmd = run_mock.call_args_list[1].args[0]
    map_arg = ffmpeg_cmd.index("-map")
    assert ffmpeg_cmd[map_arg:map_arg + 2] == ["-map", "0:2"]


def test_process_stops_when_embedded_subtitle_extract_fails(tmp_path, mocker):
    media_path = tmp_path / "sample01.mkv"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline(TRANSLATION_CHUNK_SIZE=10)

    def fake_which(command):
        return f"/usr/bin/{command}" if command in {"ffprobe", "ffmpeg"} else None

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0].endswith("ffprobe"):
            return mocker.Mock(
                returncode=0,
                stdout=json.dumps(
                    {"streams": [{"index": 3, "codec_name": "ass", "tags": {"language": "ja"}}]}
                ),
                stderr="",
            )
        return mocker.Mock(returncode=1, stdout="", stderr="cannot convert")

    mocker.patch("shutil.which", side_effect=fake_which)
    mocker.patch("subprocess.run", side_effect=fake_run)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 1.0, "text": "こんにちは\n你好"}],
    )

    with pytest.raises(RuntimeError, match="ffmpeg subtitle extraction failed"):
        pipeline.process(media_path)

    transcribe_mock.assert_not_called()


def test_arib_caption_requires_ffmpeg_decoder(tmp_path, mocker):
    media_path = tmp_path / "sample01.ts"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline()

    def fake_which(command):
        return f"/usr/bin/{command}" if command in {"ffprobe", "ffmpeg"} else None

    mocker.patch("shutil.which", side_effect=fake_which)

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if "-decoders" in cmd:
            return mocker.Mock(returncode=0, stdout=" S..... subrip SubRip subtitle\n", stderr="")
        return mocker.Mock(
            returncode=0,
            stdout=json.dumps({"streams": [{"index": 3, "codec_name": "arib_caption"}]}),
            stderr="",
        )

    mocker.patch("subprocess.run", side_effect=fake_run)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")

    with pytest.raises(RuntimeError, match="cannot decode arib_caption"):
        pipeline.process(media_path)

    transcribe_mock.assert_not_called()


def test_arib_caption_accepts_libaribcaption_decoder(tmp_path, mocker):
    media_path = tmp_path / "sample01.ts"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline()

    def fake_which(command):
        return f"/usr/bin/{command}" if command in {"ffprobe", "ffmpeg"} else None

    mocker.patch("shutil.which", side_effect=fake_which)

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if "-decoders" in cmd:
            return mocker.Mock(
                returncode=0,
                stdout=" S..... libaribcaption ARIB STD-B24 caption decoder\n",
                stderr="",
            )
        if cmd[0].endswith("ffprobe"):
            return mocker.Mock(
                returncode=0,
                stdout=json.dumps({"streams": [{"index": 3, "codec_name": "arib_caption"}]}),
                stderr="",
            )
        Path(cmd[-1]).write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n\n",
            encoding="utf-8",
        )
        return mocker.Mock(returncode=0, stdout="", stderr="")

    mocker.patch("subprocess.run", side_effect=fake_run)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 1.0, "text": "こんにちは\n你好"}],
    )

    result = pipeline.process(media_path)

    transcribe_mock.assert_not_called()
    assert result["source_language"] == "ja"
    assert result["segment_count"] == 1


def test_process_stop_after_transcription_skips_translation(tmp_path, mocker):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline()
    mocker.patch.object(
        pipeline,
        "_transcribe",
        return_value=(
            [{"start": 0.0, "end": 1.0, "text": "안녕"}],
            "ko",
        ),
    )
    translate_mock = mocker.patch.object(pipeline, "_translate_segments")

    result = pipeline.process(media_path, stop_after_transcription=True)

    translate_mock.assert_not_called()
    assert result["stage"] == "transcribed"
    assert result["bilingual_srt"] is None
    assert result["bilingual_ass"] is None
    assert result["segment_count"] == 1
    assert Path(result["original_srt"]).exists()


def test_remote_faster_whisper_backend_posts_audio_and_writes_srt(tmp_path, mocker):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline(
        WHISPER_BACKEND="remote-faster-whisper",
        REMOTE_WHISPER_BASE_URL="http://gpu.example:5051",
    )
    mocker.patch("shutil.which", return_value="/usr/bin/ffmpeg")
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        Path(cmd[-1]).write_bytes(b"wav")
        return mocker.Mock(returncode=0, stderr="", stdout="")

    mocker.patch("subprocess.run", side_effect=fake_run)

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "language": "ko",
                "elapsed_seconds": 1.2,
                "segments": [{"start": 0.0, "end": 1.0, "text": "안녕"}],
            }

    post_mock = mocker.patch("requests.post", return_value=FakeResponse())
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 1.0, "text": "안녕\n你好"}],
    )

    result = pipeline.process(media_path)

    assert result["source_language"] == "ko"
    assert result["whisper_backend"] == "remote-faster-whisper"
    assert Path(result["original_srt"]).read_text(encoding="utf-8").count("안녕") == 1
    assert post_mock.call_args.args[0] == "http://gpu.example:5051/transcribe"


def test_qwen3_asr_backend_writes_approximate_srt(tmp_path, mocker):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline(
        ASR_BACKEND="qwen3-asr",
        ASR_MODEL="Qwen/Qwen3-ASR-1.7B",
        QWEN_ASR_CHUNK_SECONDS=90,
    )
    mocker.patch.object(pipeline, "_find_embedded_subtitle_stream", return_value=None)
    mocker.patch("app.models.subtitle_pipeline.Qwen3ASRModel", autospec=True)
    mocker.patch("app.models.subtitle_pipeline.torch", autospec=True)
    mocker.patch("shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch.object(pipeline, "_extract_audio_mono_16k")
    mocker.patch.object(pipeline, "_probe_audio_duration", return_value=90.0)
    wav_path = tmp_path / "chunk.wav"
    wav_path.write_bytes(b"wav")
    mocker.patch.object(pipeline, "_split_audio_chunks", return_value=[wav_path])

    class FakeResult:
        language = "Japanese"
        text = "こんにちは。ありがとう。"

    class FakeModel:
        def transcribe(self, **kwargs):  # noqa: ARG002
            return [FakeResult()]

    from_pretrained = mocker.patch(
        "app.models.subtitle_pipeline.Qwen3ASRModel.from_pretrained",
        return_value=FakeModel(),
    )
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 90.0, "text": "こんにちは\n你好"}],
    )

    result = pipeline.process(media_path)

    assert result["source_language"] == "Japanese"
    assert result["asr_backend"] == "qwen3-asr"
    assert Path(result["original_srt"]).read_text(encoding="utf-8").count("こんにちは") == 1
    from_pretrained.assert_called_once()


def test_start_translation_resumes_with_overrides(tmp_path, mocker):
    from app.models.subtitle_pipeline import SubtitleJobManager

    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")
    orig_srt = media_path.with_suffix(".orig.srt")
    orig_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n안녕\n\n",
        encoding="utf-8",
    )

    cfg = {
        "MEDIA_DIR": str(tmp_path),
        "TRANSLATION_BACKEND": "ollama",
        "TRANSLATION_MODEL": "qwen2.5:14b",
        "TRANSLATION_CHUNK_SIZE": 4,
        "TARGET_LANGUAGE": "zh",
    }
    manager = SubtitleJobManager(cfg)

    seen = {}

    def fake_process(self, media_path, **kwargs):  # noqa: ARG001
        seen["model"] = self.translation_model
        seen["target"] = kwargs.get("target_language")
        seen["skip"] = kwargs.get("skip_transcription")
        # Ensure orig.srt always exists so a translate phase reading it works.
        Path(orig_srt).touch()
        return {
            "source_language": "ko",
            "target_language": kwargs.get("target_language") or "zh",
            "segment_count": 1,
            "original_srt": str(orig_srt),
            "bilingual_srt": str(media_path).replace(".mp4", ".bilingual.srt"),
            "bilingual_ass": str(media_path).replace(".mp4", ".bilingual.ass"),
            "translation_model": self.translation_model,
            "translation_backend": self.translation_backend,
            "whisper_model": self.whisper_model_name,
            "whisper_backend": self.whisper_backend,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "stage": "completed",
        }

    mocker.patch.object(
        SubtitlePipeline,
        "process",
        autospec=True,
        side_effect=fake_process,
    )

    job_id = manager.start_job(media_path, stop_after_transcription=True)
    # Wait for the queued thread to finish.
    for _ in range(50):
        job = manager.get_job(job_id)
        if job and job.get("status") in ("awaiting_translation", "failed"):
            break
        import time as _t
        _t.sleep(0.02)
    assert manager.get_job(job_id)["status"] == "awaiting_translation"

    manager.start_translation(
        job_id,
        target_language="en",
        translation_model="my/test-model",
    )
    for _ in range(50):
        job = manager.get_job(job_id)
        if job and job.get("status") in ("completed", "failed", "awaiting_translation"):
            if job.get("status") == "completed":
                break
        import time as _t
        _t.sleep(0.02)

    job = manager.get_job(job_id)
    assert job["status"] == "completed", f"error={job.get('error')!r}"
    assert seen["model"] == "my/test-model"
    assert seen["target"] == "en"
    assert seen["skip"] is True
