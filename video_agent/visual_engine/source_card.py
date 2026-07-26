"""Render a sourced image (inline or PDF page) as a branded 9:16 card.

Layout:
  - Top bar (110 px): BRAND_DARK_NAVY bg, gold left-edge accent, caption text
  - Source image: letterboxed onto BRAND_DARK_NAVY, centered
  - Bottom bar (90 px): BRAND_DARK_NAVY bg, URL hostname right-aligned
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_agent.config import (
    BRAND_DARK_NAVY, BRAND_GOLD, BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
)

_TOP_BAR_H = 110
_BOT_BAR_H = 90
_ACCENT_W = 8
_MARGIN = 60

# Fallback to PIL default font if Poppins isn't available on this machine.
def _font(size: int):
    try:
        return ImageFont.truetype("poppins.ttf", size)
    except OSError:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def render_source_card(
    output_path: Path,
    *,
    source: dict,
    resolution: tuple[int, int] = (1080, 1920),
) -> Path:
    """Composite source['path'] into a branded 9:16 PNG.

    Args:
        output_path: Where to write the PNG.
        source: Dict with keys: path, caption, source_url.
        resolution: (width, height) of the output card.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = resolution
    navy = _hex_to_rgb(BRAND_DARK_NAVY)
    gold = _hex_to_rgb(BRAND_GOLD)
    text_light = _hex_to_rgb(BRAND_TEXT_LIGHT)
    text_muted = _hex_to_rgb(BRAND_TEXT_MUTED)

    card = Image.new("RGB", (w, h), navy)
    draw = ImageDraw.Draw(card)

    # ── Top bar ──────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, w, _TOP_BAR_H], fill=navy)
    draw.rectangle([0, 0, _ACCENT_W, _TOP_BAR_H], fill=gold)   # gold left edge
    caption = (source.get("caption") or "")[:50]
    draw.text((_MARGIN, _TOP_BAR_H // 2), caption,
              fill=text_light, font=_font(36), anchor="lm")

    # ── Source image ─────────────────────────────────────────────────────────
    img_area_top = _TOP_BAR_H
    img_area_bot = h - _BOT_BAR_H
    img_area_w = w - _MARGIN * 2          # slight horizontal margin
    img_area_h = img_area_bot - img_area_top

    src_img = Image.open(str(source["path"])).convert("RGB")
    src_img.thumbnail((img_area_w, img_area_h), Image.LANCZOS)

    paste_x = (w - src_img.width) // 2
    paste_y = img_area_top + (img_area_h - src_img.height) // 2
    card.paste(src_img, (paste_x, paste_y))

    # ── Bottom bar ───────────────────────────────────────────────────────────
    draw.rectangle([0, h - _BOT_BAR_H, w, h], fill=navy)
    url = source.get("source_url", "")
    try:
        hostname = urllib.parse.urlparse(url).hostname or url[:30]
    except Exception:
        hostname = url[:30]
    draw.text((w - _MARGIN, h - _BOT_BAR_H // 2), hostname,
              fill=text_muted, font=_font(28), anchor="rm")

    card.save(str(output_path), format="PNG")
    return output_path
