import json
from pathlib import Path

import pytest
import requests

from app.models.subtitle_pipeline import (
    FatalTranslationError,
    SubtitlePipeline,
    _collapse_degenerate_repeats,
    _translation_token_budget,
)


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


def test_load_json_with_fallback_handles_utf8_bom(tmp_path):
    payload = {"result": {"language": "ja"}, "transcription": []}
    json_path = tmp_path / "transcription_bom.json"
    json_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

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


def test_repair_mojibake_segments_handles_single_short_clip_segment():
    original = "こんにちは。これは字幕テスト用の短い動画です。"
    mojibake = original.encode("utf-8").decode("latin-1")
    segments = [{"start": 0.0, "end": 4.0, "text": mojibake}]

    repaired = SubtitlePipeline._repair_mojibake_segments(segments)

    assert repaired[0]["text"] == original


@pytest.mark.parametrize("error_type", [requests.Timeout, requests.ConnectionError])
def test_translate_with_recovery_retries_same_batch_on_network_error(mocker, error_type):
    pipeline = _pipeline(TRANSLATION_ERROR_BUDGET=1)
    expected = [{"target": "你好"}, {"target": "再见"}]
    translate = mocker.patch.object(
        pipeline, "_translate_chunk",
        side_effect=[error_type("Can't assign requested address"), error_type("offline"), expected],
    )
    sleep = mocker.patch("app.models.subtitle_pipeline.time.sleep")
    warnings = []

    result = pipeline._translate_with_recovery(
        ["こんにちは", "さようなら"], source_language="ja", error_cb=warnings.append,
    )

    assert result == expected
    assert translate.call_count == 3
    assert all(call.args[0] == ["こんにちは", "さようなら"] for call in translate.call_args_list)
    assert sleep.call_args_list == [mocker.call(2), mocker.call(4)]
    assert pipeline._translation_error_count == 0
    assert "Can't assign requested address" in warnings[0]
    assert "retry 2/3" in warnings[1]


@pytest.mark.parametrize("single_line_fallback", [False, True])
def test_network_failure_aborts_without_splitting_or_retaining_source(mocker, single_line_fallback):
    pipeline = _pipeline()
    error = requests.ConnectionError("[Errno 49] Can't assign requested address")
    chunk = mocker.patch.object(pipeline, "_translate_chunk", side_effect=error)
    single = mocker.patch.object(pipeline, "_translate_single", side_effect=error)
    sleep = mocker.patch("app.models.subtitle_pipeline.time.sleep")
    translate = pipeline._fallback_translations if single_line_fallback else pipeline._translate_with_recovery

    with pytest.raises(FatalTranslationError, match="network request failed after 4 attempts") as caught:
        translate(["a", "b", "c", "d"], source_language="ja")

    assert "Can't assign requested address" in str(caught.value)
    assert "credentials/quota" not in str(caught.value)
    assert caught.value.__cause__ is error
    assert sleep.call_args_list == [mocker.call(2), mocker.call(4), mocker.call(8)]
    assert chunk.call_count == (0 if single_line_fallback else 4)
    assert single.call_count == (4 if single_line_fallback else 0)
    assert pipeline._translation_error_count == 0


def test_single_line_fallback_recovers_from_network_error(mocker):
    pipeline = _pipeline()
    translate = mocker.patch.object(
        pipeline, "_translate_single",
        side_effect=[requests.Timeout("read timed out"), {"target": "你好"}],
    )
    mocker.patch("app.models.subtitle_pipeline.time.sleep")

    assert pipeline._fallback_translations(["こんにちは"], source_language="ja") == [{"target": "你好"}]
    assert translate.call_count == 2


@pytest.mark.parametrize("cancel_before_request", [False, True])
def test_network_retry_honors_cancellation(mocker, cancel_before_request):
    pipeline = _pipeline()
    event = mocker.Mock()
    event.is_set.return_value = cancel_before_request
    event.wait.return_value = True
    pipeline._active_cancel_event = event
    translate = mocker.patch.object(
        pipeline, "_translate_single", side_effect=requests.ConnectionError("offline"),
    )

    with pytest.raises(FatalTranslationError, match="cancelled by user"):
        pipeline._fallback_translations(["a", "b"], source_language="ja")

    assert translate.call_count == (0 if cancel_before_request else 1)
    if cancel_before_request:
        event.wait.assert_not_called()
    else:
        event.wait.assert_called_once_with(2)


def test_auth_failure_is_not_retried_as_a_network_error(mocker):
    pipeline = _pipeline()
    response = requests.Response()
    response.status_code = 401
    response._content = b"Invalid API key"
    translate = mocker.patch.object(
        pipeline, "_translate_chunk", side_effect=requests.HTTPError(response=response),
    )
    sleep = mocker.patch("app.models.subtitle_pipeline.time.sleep")

    with pytest.raises(FatalTranslationError, match="unrecoverable HTTP 401: Invalid API key"):
        pipeline._translate_with_recovery(["a", "b"], source_language="ja")

    assert translate.call_count == 1
    sleep.assert_not_called()


def test_translate_with_recovery_still_splits_batch_on_item_count_mismatch(mocker):
    pipeline = _pipeline()

    def translate_chunk(texts, source_language, target_language=None, stream_cb=None):
        if len(texts) > 1:
            return []
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

    def fake_chat(messages, stream_cb=None, json_mode=False, max_tokens=None):
        return json.dumps({"items": [{"zh": "你好"}, {"target": "再见"}]})

    mocker.patch.object(pipeline, "_chat_completion", side_effect=fake_chat)

    out = pipeline._translate_chunk(["こんにちは", "さようなら"], source_language="ja")
    assert out == [{"target": "你好"}, {"target": "再见"}]


def test_collapse_degenerate_repeats_tames_whisper_loop():
    # Real failure: a 30s whisper segment came back as 洋館屋 + 97 more 屋, which
    # made the translation model echo the loop until it hit max_tokens.
    segments = [
        {"start": 0.0, "end": 30.0, "text": "あの、洋館屋、洋館屋" + "屋" * 95},
        {"start": 30.0, "end": 31.0, "text": "そっか" + "っ" * 88},
    ]

    out = _collapse_degenerate_repeats(segments)

    assert out[0]["text"] == "あの、洋館屋、洋館屋屋"
    assert out[1]["text"] == "そっか" + "っっ"


def test_collapse_degenerate_repeats_leaves_real_dialogue_alone():
    segments = [
        {"start": 0.0, "end": 1.0, "text": "まあまあ、いいじゃない!!!"},
        {"start": 1.0, "end": 2.0, "text": "そうそう"},
    ]

    out = _collapse_degenerate_repeats(segments)

    assert [seg["text"] for seg in out] == [
        "まあまあ、いいじゃない!!!",
        "そうそう",
    ]


def test_translation_token_budget_is_bounded():
    # Short lines get a small cap; nothing ever gets the provider's 8192 default.
    assert _translation_token_budget(["こんにちは"]) < 512
    assert _translation_token_budget(["あ" * 100000]) == 8192


def test_translate_chunk_caps_output_tokens(mocker):
    pipeline = _pipeline()
    spy = mocker.patch.object(
        pipeline,
        "_chat_completion",
        return_value=json.dumps({"items": [{"target": "你好"}]}),
    )

    pipeline._translate_chunk(["こんにちは"], source_language="ja")

    assert spy.call_args.kwargs["max_tokens"] == _translation_token_budget(["こんにちは"])


def test_fallback_keeps_source_text_when_a_single_line_wont_parse(mocker):
    """One pathological line must not throw away the rest of the run."""
    pipeline = _pipeline()

    def translate_single(text, source_language, target_language=None, stream_cb=None):
        if text == "bad":
            raise ValueError("Model did not return valid JSON")
        return {"target": f"zh:{text}"}

    mocker.patch.object(pipeline, "_translate_single", side_effect=translate_single)

    out = pipeline._fallback_translations(["a", "bad", "b"], source_language="ja")

    assert out == [{"target": "zh:a"}, {"target": ""}, {"target": "zh:b"}]
    assert pipeline._translation_error_count == 1


def test_fallback_still_aborts_once_the_error_budget_is_spent(mocker):
    pipeline = _pipeline(TRANSLATION_ERROR_BUDGET=2)
    mocker.patch.object(
        pipeline,
        "_translate_single",
        side_effect=ValueError("Model did not return valid JSON"),
    )

    with pytest.raises(FatalTranslationError, match="Last error: ValueError: Model did not return valid JSON"):
        pipeline._fallback_translations(["a", "b", "c"], source_language="ja")


def test_unsupported_translation_backend_raises():
    with pytest.raises(RuntimeError, match="Unsupported TRANSLATION_BACKEND"):
        _pipeline(TRANSLATION_BACKEND="nope")


def test_lmstudio_backend_routes_to_openai_compatible(mocker):
    pipeline = _pipeline(
        TRANSLATION_BACKEND="lmstudio",
        TRANSLATION_MODEL="qwen2.5-14b-instruct",
        LMSTUDIO_BASE_URL="http://192.168.0.209:1234/v1",
    )
    spy = mocker.patch.object(
        pipeline, "_chat_completion_openai_compatible", return_value="你好"
    )

    out = pipeline._chat_completion([{"role": "user", "content": "こんにちは"}])

    assert out == "你好"
    _, kwargs = spy.call_args
    assert kwargs["base_url"] == "http://192.168.0.209:1234/v1"
    # LM Studio ignores the bearer token but the OpenAI client needs a non-empty one.
    assert kwargs["api_key"]


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


@pytest.mark.parametrize("network_fails", [False, True])
def test_process_reuses_translation_session_and_always_closes_it(tmp_path, mocker, network_fails):
    media = tmp_path / "sample.ts"
    media.write_bytes(b"fake")
    media.with_suffix(".orig.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nありがとう\n\n", encoding="utf-8",
    )
    pipeline = _pipeline(
        TRANSLATION_BACKEND="deepseek", DEEPSEEK_API_KEY="test-key", TRANSLATION_CHUNK_SIZE=1,
    )
    session = requests.Session()
    session_factory = mocker.patch("requests.Session", return_value=session)
    close = mocker.spy(session, "close")
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": '{"items":[{"target":"你好"}]}'}}],
    }).encode()
    post = mocker.patch.object(session, "post", return_value=response)
    fresh_post = mocker.patch("requests.post", side_effect=AssertionError("unexpected fresh session"))
    mocker.patch("app.models.subtitle_pipeline.time.sleep")
    mocker.patch("app.models.subtitle_pipeline.detect_video_play_res", return_value=(1920, 1080))
    if network_fails:
        post.side_effect = requests.ConnectionError("offline")
        with pytest.raises(FatalTranslationError, match="network request failed"):
            pipeline.process(media, skip_transcription=True)
        assert post.call_count == 4
    else:
        result = pipeline.process(media, skip_transcription=True)
        assert result["stage"] == "completed"
        assert post.call_count == 2
    fresh_post.assert_not_called()
    session_factory.assert_called_once_with()
    close.assert_called_once_with()
    assert pipeline._translation_session is None
    assert pipeline._active_cancel_event is None


@pytest.mark.parametrize("hd_pid", ["0x130", "0x240"])
def test_broadcast_subtitles_follow_hd_program_before_language(hd_pid, mocker):
    pipeline = _pipeline()
    mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
    run = mocker.patch("subprocess.run", return_value=mocker.Mock(
        returncode=0, stdout=json.dumps({
            "streams": [
                {"index": 0, "codec_type": "video", "width": 320, "height": 180},
                {"index": 2, "codec_name": "arib_caption", "id": "0x887",
                 "tags": {"language": "ja"}},
                {"index": 8, "codec_type": "video", "width": 1440, "height": 1080},
                {"index": 9, "codec_name": "arib_caption", "id": hd_pid},
            ],
            "programs": [
                {"program_id": 1, "streams": [{"index": 0}, {"index": 2}]},
                {"program_id": 2, "streams": [{"index": 8}, {"index": 9}]},
            ],
        }),
    ))
    selected = pipeline._find_embedded_subtitle_stream("broadcast.ts", language_hint="ja")
    assert selected["index"] == 9
    assert selected["stream_id"] == hd_pid
    assert "1440x1080" in selected["label"]
    assert run.call_args.kwargs["timeout"] == 30


def test_main_program_without_captions_does_not_borrow_mobile_captions(mocker):
    pipeline = _pipeline()
    mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
    mocker.patch("subprocess.run", return_value=mocker.Mock(
        returncode=0, stdout=json.dumps({
            "streams": [
                {"index": 0, "codec_type": "video", "width": 320, "height": 180},
                {"index": 2, "codec_name": "arib_caption"},
                {"index": 8, "codec_type": "video", "width": 1920, "height": 1080},
            ],
            "programs": [
                {"streams": [{"index": 0}, {"index": 2}]},
                {"streams": [{"index": 8}]},
            ],
        }),
    ))
    assert pipeline._find_embedded_subtitle_stream("broadcast.ts") is None


def test_non_broadcast_language_selection_still_works(mocker):
    pipeline = _pipeline()
    mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
    mocker.patch("subprocess.run", return_value=mocker.Mock(
        returncode=0, stdout=json.dumps({"streams": [
            {"index": 0, "codec_type": "video", "width": 1920, "height": 1080},
            {"index": 2, "codec_name": "subrip", "tags": {"language": "en"}},
            {"index": 3, "codec_name": "subrip", "tags": {"language": "ja"}},
        ]}),
    ))
    assert pipeline._find_embedded_subtitle_stream("movie.mkv", "ja")["index"] == 3


def test_arib_extraction_maps_stable_pid_even_if_probe_indices_change(tmp_path, mocker):
    pipeline = _pipeline()
    mocker.patch("shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch.object(pipeline, "_ffmpeg_has_decoder", return_value=True)
    commands = []

    def extract(cmd, **kwargs):
        commands.append(cmd)
        Path(cmd[-1]).write_text(
            (Path(__file__).parent / "fixtures/arib-positioned.ass").read_text(),
            encoding="utf-8",
        )
        return 0, ""

    mocker.patch.object(pipeline, "_run_ffmpeg_command", side_effect=extract)
    segments, _ = pipeline._extract_arib_layout(
        tmp_path / "broadcast.ts", {"index": 2, "stream_id": "0x240"},
        tmp_path / "broadcast.orig.srt",
    )
    assert segments
    cmd = commands[0]
    assert cmd[cmd.index("-map") + 1] == "0:i:0x240"


def test_empty_arib_track_keeps_reason_instead_of_starting_whisper(tmp_path, mocker):
    media = tmp_path / "empty.ts"
    media.write_bytes(b"fake")
    pipeline = _pipeline()
    mocker.patch.object(pipeline, "_find_embedded_subtitle_stream", return_value={
        "index": 9, "codec": "arib_caption", "stream_id": "0x130",
        "label": "ARIB / PID 0x130 / main video 1440x1080",
    })
    mocker.patch.object(pipeline, "_extract_embedded_subtitle", side_effect=ValueError(
        "No positioned ARIB subtitle rows found",
    ))
    transcribe = mocker.patch.object(pipeline, "_transcribe")
    with pytest.raises(RuntimeError, match="PID 0x130.*No positioned ARIB subtitle rows found"):
        pipeline.process(media)
    transcribe.assert_not_called()


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
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")

    seen = {}
    def fake_ffmpeg(cmd, **kwargs):  # noqa: ARG001
        seen["cmd"] = cmd
        Path(cmd[-1]).write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n안녕\n\n",
            encoding="utf-8",
        )
        return 0, ""

    mocker.patch("shutil.which", side_effect=fake_which)
    mocker.patch("subprocess.run", side_effect=fake_run)
    mocker.patch.object(pipeline, "_run_ffmpeg_command", side_effect=fake_ffmpeg)
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
    ffmpeg_cmd = seen["cmd"]
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
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")

    mocker.patch("shutil.which", side_effect=fake_which)
    mocker.patch("subprocess.run", side_effect=fake_run)
    mocker.patch.object(pipeline, "_run_ffmpeg_command", return_value=(1, "cannot convert"))
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
        raise AssertionError(f"unexpected subprocess.run call: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)
    def fake_ffmpeg(cmd, **kwargs):  # noqa: ARG001
        Path(cmd[-1]).write_text(
            (Path(__file__).parent / "fixtures/arib-positioned.ass").read_text(),
            encoding="utf-8",
        )
        return 0, ""

    mocker.patch.object(pipeline, "_run_ffmpeg_command", side_effect=fake_ffmpeg)
    transcribe_mock = mocker.patch.object(pipeline, "_transcribe")
    mocker.patch.object(
        pipeline,
        "_translate_with_recovery",
        return_value=[{"target": "你好"}],
    )

    result = pipeline.process(media_path)

    transcribe_mock.assert_not_called()
    assert result["source_language"] == "ja"
    assert result["segment_count"] == 1


    assert Path(result["original_ass"]).exists()
    assert "AribTranslation" in Path(result["bilingual_ass"]).read_text(encoding="utf-8-sig")

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


def test_job_manager_update_config_refreshes_cached_pipeline(tmp_path):
    from app.models.subtitle_pipeline import SubtitleJobManager

    cfg = {
        "MEDIA_DIR": str(tmp_path),
        "TRANSLATION_BACKEND": "deepseek",
        "TRANSLATION_MODEL": "deepseek-v4-flash",
        "DEEPSEEK_API_KEY": "",
    }
    manager = SubtitleJobManager(cfg)

    cfg["DEEPSEEK_API_KEY"] = "sk-test"
    manager.update_config(cfg)

    assert manager.pipeline.deepseek_api_key == "sk-test"
    assert manager._build_pipeline({}).deepseek_api_key == "sk-test"


def test_openrouter_asr_backend_posts_audio_and_writes_srt(tmp_path, mocker):
    media_path = tmp_path / "sample01.mp4"
    media_path.write_bytes(b"fake")

    pipeline = _pipeline(
        ASR_BACKEND="openrouter",
        ASR_MODEL="qwen/qwen3-asr-flash-2026-02-10",
        OPENROUTER_API_KEY="sk-or-test-key",
        QWEN_ASR_CHUNK_SECONDS=90,
    )
    mocker.patch.object(pipeline, "_find_embedded_subtitle_stream", return_value=None)
    mocker.patch("shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch.object(pipeline, "_extract_audio_mono_16k")
    mocker.patch.object(pipeline, "_probe_audio_duration", return_value=90.0)
    wav_path = tmp_path / "chunk.wav"
    wav_path.write_bytes(b"wav")
    mocker.patch.object(pipeline, "_split_audio_chunks", return_value=[wav_path])

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "text": "こんにちは。ありがとう。",
            }

    post_mock = mocker.patch("requests.post", return_value=FakeResponse())
    mocker.patch.object(
        pipeline,
        "_translate_segments",
        return_value=[{"start": 0.0, "end": 90.0, "text": "こんにちは\n你好"}],
    )

    result = pipeline.process(media_path)

    assert result["source_language"] == "unknown"
    assert result["asr_backend"] == "openrouter"
    assert Path(result["original_srt"]).read_text(encoding="utf-8").count("こんにちは") == 1
    post_mock.assert_called_once()
    url_called = post_mock.call_args.args[0]
    assert url_called == "https://openrouter.ai/api/v1/audio/transcriptions"

    # Verify base64 audio and JSON payload structure
    kwargs = post_mock.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer sk-or-test-key"
    payload = kwargs["json"]
    assert payload["model"] == "qwen/qwen3-asr-flash-2026-02-10"
    assert "data" in payload["input_audio"]
    assert payload["input_audio"]["format"] == "wav"
