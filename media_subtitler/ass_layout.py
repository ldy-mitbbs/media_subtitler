"""Preserve libaribcaption ASS events and add a translation beneath each row.

This deliberately handles static, positioned ARIB output, not arbitrary ASS
typesetting. Unsupported geometry or insufficient space raises a clear error
instead of silently moving or restyling the Japanese captions.
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


CACHE_MARKER = "; Media Subtitler ARIB layout v1"
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


class LayoutError(ValueError):
    pass


def _timestamp(value):
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _units(text):
    # Conservative estimate with extra width reserved by the writer. Explicit
    # positioning disables libass collision avoidance, so do not auto-wrap.
    return sum(
        0 if unicodedata.combining(c) else
        0.5 if c.isspace() else
        1.0 if unicodedata.east_asian_width(c) in "WF" or c in "MW@" else 0.7
        for c in text
    )


def _plain(text):
    return re.sub(r"\{[^}]*\}", "", text).replace(r"\h", " ").strip()


def _escape(text):
    # Model output must never introduce ASS commands, hard breaks or drawings.
    return " ".join(text.split()).replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def _overlap(a, b):
    # FFmpeg's centisecond rounding can overlap successive cues by 0.01s.
    return min(a["end"], b["end"]) - max(a["start"], b["start"]) > 0.015


def restore_arib_background(header, style_format):
    """Repair FFmpeg/libaribcaption's mismatched default ASS background alpha.

    The converter compares events against RGBA black/128, but emits an opaque
    black default style. ASS alpha is inverted, so that default must be 0x7f.
    Explicit per-event alpha tags still override this fallback. Only the known
    Default/BorderStyle=4/opaque-black combination needs correction.
    """
    repaired = []
    for line in header:
        if line.strip().startswith("Style:"):
            fields = line.split(":", 1)[1].strip().split(",", len(style_format) - 1)
            style = dict(zip(style_format, fields))
            if (style.get("name") == "Default" and style.get("borderstyle") == "4"
                    and re.fullmatch(r"&H0+&?", style.get("backcolour", ""), re.I)):
                fields[style_format.index("backcolour")] = "&H7F000000"
                line = "Style: " + ",".join(fields)
        repaired.append(line)
    return repaired


@dataclass
class AribLayout:
    header: list
    events: list
    event_format: list
    style_format: list
    style_names: set
    rows: list
    boxes: list
    width: float
    height: float
    stream_index: int | None = None

    def segments(self):
        return [
            {"start": r["start"], "end": r["end"], "text": r["text"], "ass_row": i}
            for i, r in enumerate(self.rows)
        ]

    def matches(self, segments):
        return len(segments) == len(self.rows) and all(
            s["text"].strip() == r["text"]
            and abs(s["start"] - r["start"]) < 0.002
            and abs(s["end"] - r["end"]) < 0.002
            for s, r in zip(segments, self.rows)
        )

    def write(self, segments, output_path, font, include_source=True):
        indexed = {}
        for segment in segments:
            row_id = segment.get("ass_row")
            if not isinstance(row_id, int) or row_id in indexed or not 0 <= row_id < len(self.rows):
                raise LayoutError("Translated captions lost their original ASS row mapping")
            indexed[row_id] = segment
        if len(indexed) != len(self.rows):
            raise LayoutError("Translated captions do not match the original ASS rows")

        name = "AribTranslation"
        while name in self.style_names:
            name += "_"
        values = {
            "name": name, "fontname": font.replace(",", " "), "fontsize": "18",
            "primarycolour": "&H00FFFFFF", "secondarycolour": "&H00FFFFFF",
            "outlinecolour": "&H00000000", "backcolour": "&H00000000",
            "scalex": "100", "scaley": "100", "borderstyle": "1",
            "outline": "1", "alignment": "7", "encoding": "1",
        }
        style = "Style: " + ",".join(values.get(k, "0") for k in self.style_format)
        # ARIB coordinates describe the display canvas, with square pixels.
        # Without LayoutRes, libass inherits the TS storage pixel aspect ratio
        # (e.g. 1440x1080 displayed at 16:9) and stretches glyphs a second time.
        header = [line for line in restore_arib_background(self.header, self.style_format)
                  if not line.strip().lower().startswith(("layoutresx:", "layoutresy:"))]
        info_index = next(i for i, line in enumerate(header) if line.strip().lower() == "[script info]")
        header[info_index + 1:info_index + 1] = [
            f"LayoutResX: {self.width:g}", f"LayoutResY: {self.height:g}",
        ]
        if not include_source:
            info_index = next(i for i, line in enumerate(header) if line.strip().lower() == "[script info]")
            header.insert(info_index + 1, "; Media Subtitler ARIB translation v1")
            if self.stream_index is not None:
                header.insert(info_index + 2, f"; Source stream index: {self.stream_index}")
        # Insert the new style immediately before [Events]. Source event tags
        # and PlayRes remain intact; only the faulty default alpha is repaired.
        event_index = next(i for i, line in enumerate(header) if line.strip().lower() == "[events]")
        header.insert(event_index, style)
        translated = []
        layer = max((b["layer"] for b in self.boxes), default=0) + 1
        for row_id, row in enumerate(self.rows):
            target = str(indexed[row_id].get("target_text") or "").strip()
            if not target or target == row["text"]:
                continue
            target = _escape(target)
            gap = max(2.0, self.height / 135)
            x = max(2.0, row["x"])
            y = row["y"] + row["height"] + gap
            bottom = self.height - gap
            for box in self.boxes:
                if not _overlap(row, box):
                    continue
                if box["y"] >= row["y"] + row["height"] - 0.1:
                    bottom = min(bottom, box["y"] - 2)
            room = bottom - y - 2  # reserve the translation outline
            width = self.width - x - gap
            size = min(row["height"] * 4 / 9, room, width / max(1, _units(target) + 2))
            minimum = min(row["height"] * 0.3, self.height / 54)
            if size < minimum or width <= 0:
                raise LayoutError(
                    f"Not enough space below ARIB caption at {row['start']:.2f}s: "
                    f"{row['text'][:40]}. Shorten the translation or use another layout."
                )
            fields = {
                "layer": str(layer), "start": row["start_ass"], "end": row["end_ass"],
                "style": name, "name": "", "marginl": "0", "marginr": "0",
                "marginv": "0", "effect": "",
                "text": rf"{{\an7\pos({x:g},{y:g})\fs{size:.2f}\q2}}" + target,
            }
            translated.append("Dialogue: " + ",".join(fields.get(k, "") for k in self.event_format))
        # Compute every placement before opening the destination, so a layout
        # error cannot leave a half-written bilingual file.
        Path(output_path).write_text(
            "\n".join(header + (self.events if include_source else []) + translated) + "\n",
            encoding="utf-8-sig",
        )


def read_arib_ass(path):
    """Read FFmpeg/libaribcaption ASS extracted with -fix_sub_duration."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    header, events, boxes = [], [], []
    styles = {}
    style_format = event_format = None
    width = height = 0
    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.lower()
        if section != "[events]" or not stripped.startswith(("Dialogue:", "Comment:")):
            header.append(line)
        else:
            events.append(line)
        if section == "[script info]":
            if stripped.lower().startswith("playresx:"):
                width = float(stripped.split(":", 1)[1])
            if stripped.lower().startswith("playresy:"):
                height = float(stripped.split(":", 1)[1])
        if stripped.startswith("Format:"):
            fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
            if section == "[v4+ styles]":
                style_format = fields
            elif section == "[events]":
                event_format = fields
        elif section == "[v4+ styles]" and stripped.startswith("Style:") and style_format:
            fields = stripped.split(":", 1)[1].strip().split(",", len(style_format) - 1)
            style = dict(zip(style_format, fields))
            styles[style["name"]] = style
        elif section == "[events]" and stripped.startswith("Dialogue:"):
            if not event_format or event_format[-1] != "text":
                raise LayoutError("Unsupported ARIB ASS event format")
            fields = stripped.split(":", 1)[1].strip().split(",", len(event_format) - 1)
            event = dict(zip(event_format, fields))
            text = event.get("text", "")
            plain = _plain(text)
            if not plain:
                continue
            position = re.search(r"\\pos\((" + _NUMBER + r"),(" + _NUMBER + r")\)", text)
            alignment = re.findall(r"\\an([1-9])", text)
            if (not position or not alignment or alignment[-1] != "7"
                    or re.search(r"\\(?:move|t|org)\(|\\(?:fr[xyz]?|fax|fay)", text)
                    or re.search(r"\\p[1-9]", text) or r"\N" in text or r"\n" in text):
                raise LayoutError("ARIB layout requires static, top-left positioned single-line ASS events")
            style = styles.get(event.get("style"))
            if not style:
                raise LayoutError("ARIB ASS event references an unknown style")
            sizes = [float(style.get("fontsize", 36))] + [float(n) for n in re.findall(r"\\fs(" + _NUMBER + ")", text)]
            # Leading overrides replace the style size (e.g. 18-point ruby).
            size = sizes[-1] if len(sizes) == 2 else max(sizes)
            sy = re.findall(r"\\fscy(" + _NUMBER + ")", text)
            sx = re.findall(r"\\fscx(" + _NUMBER + ")", text)
            spacing = re.findall(r"\\fsp(" + _NUMBER + ")", text)
            glyph_height = size * float(sy[-1] if sy else style.get("scaley", 100)) / 100
            glyph_width = size * float(sx[-1] if sx else style.get("scalex", 100)) / 100
            advance = float(spacing[-1] if spacing else style.get("spacing", 0))
            start, end = _timestamp(event["start"]), _timestamp(event["end"])
            if not 0 <= start < end < 35999:
                raise LayoutError("ARIB ASS has unresolved durations; extract again with -fix_sub_duration")
            x, y = map(float, position.groups())
            boxes.append({
                "start": start, "end": end, "start_ass": event["start"], "end_ass": event["end"],
                "x": x, "y": y, "height": glyph_height,
                "width": _units(plain) * glyph_width + len(plain) * advance,
                "text": plain, "layer": int(event.get("layer", 0)),
            })
    if not width or not height or not style_format or not event_format or not boxes:
        raise LayoutError("No positioned ARIB subtitle rows found")

    groups = {}
    for box in boxes:
        # Keep small reading annotations (ruby) in the source ASS, but translate
        # the associated main line only. Standalone small captions still count.
        ruby = any(
            other is not box and _overlap(box, other)
            and box["height"] <= other["height"] * 0.65
            and 0 <= other["y"] - box["y"] - box["height"] <= other["height"] * 0.6
            and box["x"] < other["x"] + other["width"]
            and box["x"] + box["width"] > other["x"]
            for other in boxes
        )
        if not ruby:
            groups.setdefault((box["start"], box["end"], box["y"]), []).append(box)
    rows = []
    for key in sorted(groups):
        parts = sorted(groups[key], key=lambda b: b["x"])
        row = dict(parts[0])
        text = parts[0]["text"]
        for previous, part in zip(parts, parts[1:]):
            space = " " if part["x"] - previous["x"] - previous["width"] > part["height"] * 0.35 else ""
            text += space + part["text"]
        row["text"] = text
        row["height"] = max(p["height"] for p in parts)
        rows.append(row)
    layout = AribLayout(header, events, event_format, style_format, set(styles), rows, boxes, width, height)
    stream_index = re.search(r"^; Source stream index: (\d+)$", "\n".join(lines), re.MULTILINE)
    if stream_index:
        layout.stream_index = int(stream_index.group(1))
    return layout


def load_cached_layout(ass_path, segments):
    path = Path(ass_path)
    if not path.exists() or CACHE_MARKER not in path.read_text(encoding="utf-8-sig"):
        return None
    layout = read_arib_ass(path)
    if not layout.matches(segments):
        raise LayoutError(
            "The saved .orig.srt no longer matches .orig.ass. Re-extract without "
            "--skip-transcription to preserve the original caption layout."
        )
    return layout
