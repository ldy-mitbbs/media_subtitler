from pathlib import Path

from app.models.cost_estimator import (
    estimate_cost,
    estimate_tokens,
    _estimate_from_segments,
)


def test_estimate_from_segments_scales_with_chars():
    short = _estimate_from_segments(["hi"] * 10, chunk_size=5)
    long = _estimate_from_segments(["hello world"] * 10, chunk_size=5)

    assert short["segment_count"] == 10
    assert short["chunk_count"] == 2
    assert short["chunk_size"] == 5
    assert long["input_tokens"] > short["input_tokens"]
    assert long["output_tokens"] > short["output_tokens"]


def test_estimate_tokens_uses_orig_srt_when_present(tmp_path):
    media = tmp_path / "ep01.mp4"
    media.write_bytes(b"fake")
    srt = media.with_suffix(".orig.srt")
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n\n"
        "2\n00:00:01,500 --> 00:00:02,500\nさようなら\n\n",
        encoding="utf-8",
    )

    estimate = estimate_tokens(media, chunk_size=4)

    assert estimate["source"] == "orig_srt"
    assert estimate["segment_count"] == 2
    assert estimate["char_count"] >= 10
    assert estimate["chunk_count"] == 1
    assert estimate["input_tokens"] > 0
    assert estimate["output_tokens"] > 0


def test_estimate_tokens_unknown_when_no_srt_and_no_ffprobe(tmp_path, monkeypatch):
    media = tmp_path / "ep02.mp4"
    media.write_bytes(b"fake")

    # Force ffprobe lookup to fail.
    monkeypatch.setattr(
        "app.models.cost_estimator.shutil.which", lambda _: None
    )

    estimate = estimate_tokens(media, chunk_size=20)

    assert estimate["source"] == "unknown"
    assert estimate["input_tokens"] == 0


def test_estimate_cost_multiplies_tokens_by_rate():
    pricing = {"prompt": 1e-6, "completion": 2e-6}
    cost = estimate_cost(input_tokens=1000, output_tokens=500, pricing_entry=pricing)

    assert cost["prompt_usd"] == 1000 * 1e-6
    assert cost["completion_usd"] == 500 * 2e-6
    assert cost["total_usd"] == cost["prompt_usd"] + cost["completion_usd"]


def test_estimate_cost_returns_none_without_pricing():
    assert estimate_cost(100, 100, None) is None
