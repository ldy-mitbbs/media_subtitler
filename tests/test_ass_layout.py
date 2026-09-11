from pathlib import Path
import re

import pytest

from media_subtitler.ass_layout import CACHE_MARKER, LayoutError, load_cached_layout, read_arib_ass
from app.models.subtitle_pipeline import SubtitlePipeline, write_srt


HEADER = (Path(__file__).parent / "fixtures/arib-positioned.ass").read_text().split("Dialogue:")[0]


def event(text, x=118, y=389, start="0:00:01.00", end="0:00:04.00", tags=""):
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,," + rf"{{\an7}}{{\pos({x},{y})}}{{\fsp4}}{{\bord3}}" + tags + text


def layout_for(tmp_path, events):
    p = tmp_path / "test.orig.ass"
    p.write_text(HEADER + "\n".join(events) + "\n")
    return read_arib_ass(p)


def translations(layout, targets):
    return [dict(s, target_text=t) for s, t in zip(layout.segments(), targets)]


def test_preserves_source_events_and_places_chinese_between_rows(tmp_path):
    originals = [event("どうして", tags=r"{\1c&H00ffff&}"), event("絶対 駄目よ｡", x=158, y=449)]
    layout = layout_for(tmp_path, originals)
    out = tmp_path / "bilingual.ass"
    layout.write(translations(layout, ["为什么", "绝对不行。"]), out, "PingFang SC")
    text = out.read_text(encoding="utf-8-sig")
    for source in originals:
        assert source in text
    assert "PlayResX: 960" in text and "Style: Default,sans-serif,36," in text
    assert r"\pos(118,429)" in text
    assert r"\pos(158,489)" in text
    chinese = [l for l in text.splitlines() if l.startswith("Dialogue: 1,")]
    assert len(chinese) == 2
    assert all(
        "0:00:01.00,0:00:04.00,AribTranslation" in l for l in chinese
    )
    size = float(re.search(r"\\fs([\d.]+)", chinese[0]).group(1))
    assert 429 + size + 1 < 449


def test_ruby_preserved_without_duplicate_translation_and_color_runs_joined(tmp_path):
    ruby = event("なまえ", y=359, tags=r"{\fs18}")
    originals = [ruby, event("名前"), event("です", x=198, tags=r"{\1c&H00ffff&}")]
    layout = layout_for(tmp_path, originals)
    assert [s["text"] for s in layout.segments()] == ["名前です"]
    out = tmp_path / "bilingual.ass"
    layout.write(translations(layout, ["是名字。"]), out, "PingFang SC")
    assert ruby in out.read_text(encoding="utf-8-sig")


def test_literal_angle_brackets_and_repeated_broadcast_rows_are_retained(tmp_path):
    layout = layout_for(tmp_path, [
        event("<サントリー>", start=f"0:00:0{i}.00", end=f"0:00:0{i+1}.00")
        for i in range(1, 5)
    ])
    assert len(layout.segments()) == 4
    assert all(s["text"] == "<サントリー>" for s in layout.segments())


def test_long_translation_shrinks_and_escapes_override_injection(tmp_path):
    layout = layout_for(tmp_path, [event("はい", x=720, y=449)])
    out = tmp_path / "bilingual.ass"
    layout.write(translations(layout, ["这是一句比较长的中文翻译内容啊"]), out, "PingFang SC")
    last = out.read_text(encoding="utf-8-sig").splitlines()[-1]
    size = float(re.search(r"\\fs([\d.]+)", last).group(1))
    assert 10 <= size < 16
    layout.write(translations(layout, [r"是的{\pos(0,0)}\N" + "\n好的"]), out, "PingFang SC")
    last = out.read_text(encoding="utf-8-sig").splitlines()[-1]
    assert r"\pos(0,0)" not in last and r"\N" not in last


def test_no_room_fails_without_overwriting_existing_output(tmp_path):
    layout = layout_for(tmp_path, [event("上の行"), event("下の行", y=430)])
    out = tmp_path / "bilingual.ass"
    out.write_text("existing output")
    with pytest.raises(LayoutError, match="Not enough space"):
        layout.write(translations(layout, ["上一行", "下一行"]), out, "PingFang SC")
    assert out.read_text() == "existing output"


@pytest.mark.parametrize("bad", [
    event("未確定", end="9:59:59.99"),
    event(r"二行\N字幕"),
    event("動く", tags=r"{\move(0,0,10,10)}"),
])
def test_unsupported_input_reports_a_layout_error(tmp_path, bad):
    with pytest.raises(LayoutError):
        layout_for(tmp_path, [bad])


def test_translation_retry_restores_rows_and_detects_edited_srt(tmp_path, mocker):
    media = tmp_path / "test.ts"
    media.write_bytes(b"test")
    layout = layout_for(tmp_path, [event("はい"), event("はい", y=449)])
    source_ass = media.with_suffix(".orig.ass")
    source_ass.write_text(source_ass.read_text().replace(
        "[Script Info]", "[Script Info]\n" + CACHE_MARKER + "\n; Source stream index: 2"
    ))
    write_srt(layout.segments(), media.with_suffix(".orig.srt"))
    pipeline = SubtitlePipeline({"TRANSLATION_BACKEND": "ollama", "TARGET_LANGUAGE": "zh"})
    mocker.patch.object(pipeline, "_translate_with_recovery", return_value=[{"target": "是的"}, {"target": "好的"}])
    probe = mocker.patch.object(pipeline, "_find_embedded_subtitle_stream")
    result = pipeline.process(media, skip_transcription=True)
    probe.assert_not_called()
    assert result["source_language"] == "ja"
    assert result["original_ass"] == str(source_ass)
    output = Path(result["bilingual_ass"]).read_text(encoding="utf-8-sig")
    assert output.count("AribTranslation,,0,0,0,,") == 2
    overlay = Path(result["translation_ass"]).read_text(encoding="utf-8-sig")
    assert "はい" not in overlay
    assert "; Source stream index: 2" in overlay
    assert "是的" in overlay and "好的" in overlay
    changed = layout.segments()
    changed[0]["text"] = "edited"
    with pytest.raises(LayoutError, match="no longer matches"):
        load_cached_layout(source_ass, changed)


def test_missing_row_mapping_is_rejected(tmp_path):
    layout = layout_for(tmp_path, [event("はい")])
    with pytest.raises(LayoutError, match="row mapping"):
        layout.write([{"target_text": "是的"}], tmp_path / "out.ass", "PingFang SC")


def test_layout_module_import_does_not_load_source_tree_facade():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", "import sys; import media_subtitler.ass_layout; "
         "assert 'media_subtitler.pipeline' not in sys.modules"],
        check=True,
    )


@pytest.mark.parametrize("include_source", [True, False])
def test_arib_display_canvas_prevents_anamorphic_video_stretch(tmp_path, include_source):
    layout = layout_for(tmp_path, [event("全身が包まれています｡")])
    original_header = list(layout.header)
    out = tmp_path / "output.ass"
    layout.write(translations(layout, ["包裹着全身。"]), out, "PingFang SC", include_source=include_source)
    text = out.read_text(encoding="utf-8-sig")
    assert "LayoutResX: 960\nLayoutResY: 540" in text
    assert "PlayResX: 960" in text and "PlayResY: 540" in text
    assert layout.header == original_header
    assert r"\pos(118,429)" in text
    assert (layout.events[0] in text) == include_source


@pytest.mark.parametrize("backcolour", ["&H0", "&H00000000", "&H7F000000"])
def test_background_default_is_transparent_and_explicit_alphas_survive(tmp_path, backcolour):
    originals = [
        event("背景なし"),
        event("不透明", x=158, y=449, tags=r"{\4a&H00&}"),
        event("透明", start="0:00:05.00", end="0:00:06.00", tags=r"{\4a&Hff&}"),
    ]
    layout = layout_for(tmp_path, originals)
    layout.header = [line.replace(",&H0,&H0,", f",&H0,{backcolour},") for line in layout.header]
    original_header = list(layout.header)
    out = tmp_path / "bilingual.ass"
    layout.write(translations(layout, ["没有背景", "不透明背景", "透明背景"]), out, "PingFang SC")
    text = out.read_text(encoding="utf-8-sig")
    source_style = next(line for line in text.splitlines() if line.startswith("Style: Default,"))
    assert source_style.split(",")[6] == "&HFF000000"
    assert all(line in text for line in originals)
    assert layout.header == original_header
    translated_style = next(line for line in text.splitlines() if line.startswith("Style: AribTranslation,"))
    assert translated_style.split(",")[6] == "&H00000000"
    assert translated_style.split(",")[15] == "1"


@pytest.mark.parametrize("name,backcolour,borderstyle", [
    ("Default", "&HFF000000", "4"),
    ("Default", "&H40000000", "4"),
    ("Default", "&H000000FF", "4"),
    ("Default", "&H0", "1"),
    ("Custom", "&H0", "4"),
])
def test_transparent_background_preserves_other_style_settings(name, backcolour, borderstyle):
    from media_subtitler.ass_layout import transparent_arib_background
    fmt = ["name", "backcolour", "borderstyle"]
    header = [f"Style: {name},{backcolour},{borderstyle}"]
    assert transparent_arib_background(header, fmt) == header
