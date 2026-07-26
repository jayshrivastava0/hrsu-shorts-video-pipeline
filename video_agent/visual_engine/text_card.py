"""Pillow-rendered hook & CTA cards. Brand colors from config."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from video_agent.config import (
    BRAND_GOLD, BRAND_DARK_NAVY, BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("PlayfairDisplay-Bold.ttf", "Playfair Display Bold.ttf",
                 "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines, line = [], ""
    for w in words:
        cand = f"{line} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font)
        if bbox[2] - bbox[0] <= max_width:
            line = cand
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def render_text_card(output_path: Path, *, layout: str, text: str,
                     resolution: tuple[int, int] = (1080, 1920)) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    img = Image.new("RGB", (w, h), color=BRAND_DARK_NAVY)
    draw = ImageDraw.Draw(img)

    if layout == "hook":
        size = 120 if len(text.split()) <= 8 else 90
        color = BRAND_GOLD
    else:  # cta
        size = 100
        color = BRAND_TEXT_LIGHT

    font = _load_font(size)
    margin = 80
    lines = _wrap(text, font, w - 2 * margin, draw)
    while size > 36:
        line_h = font.size + 18
        block_h = line_h * len(lines)
        if block_h <= h * 0.7 and len(lines) <= 6:
            break
        size -= 8
        font = _load_font(size)
        lines = _wrap(text, font, w - 2 * margin, draw)

    line_h = font.size + 18
    block_h = line_h * len(lines)
    y = (h - block_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), line, font=font, fill=color)
        y += line_h

    if layout == "cta":
        sub_font = _load_font(40)
        sub = "Need calcium nitrate?"
        sub_bbox = draw.textbbox((0, 0), sub, font=sub_font)
        draw.text(
            ((w - (sub_bbox[2] - sub_bbox[0])) // 2, h // 2 - block_h // 2 - 100),
            sub, font=sub_font, fill=BRAND_TEXT_MUTED,
        )
        underline_y = (h + block_h) // 2 + 40
        draw.line([(margin, underline_y), (w - margin, underline_y)],
                  fill=BRAND_GOLD, width=4)

    img.save(output_path, "PNG")
    return output_path
