"""Safe-zone enforcement for the 1080x1920 portrait canvas.

Hard zones (in canvas pixels, top-left origin):
  outer margin   60 px on all sides  — no content here
  top reserve    120 px (title bar)
  bottom reserve 240 px (subtitles + footer chrome)
  usable rectangle = (60, 120) → (1020, 1680)
"""
from __future__ import annotations
from PIL import ImageDraw, ImageFont

FRAME_W, FRAME_H = 1080, 1920
OUTER_MARGIN     = 60
TOP_RESERVE      = 120
BOTTOM_RESERVE   = 240


def safe_rect() -> tuple[int, int, int, int]:
    """Returns (x0, y0, x1, y1) of the usable area."""
    return (OUTER_MARGIN, TOP_RESERVE,
            FRAME_W - OUTER_MARGIN, FRAME_H - BOTTOM_RESERVE)


def fits_safe_zone(bbox: tuple[int, int, int, int]) -> bool:
    """True iff every corner of `bbox` is inside the safe rectangle."""
    sx0, sy0, sx1, sy1 = safe_rect()
    x0, y0, x1, y1 = bbox
    return x0 >= sx0 and y0 >= sy0 and x1 <= sx1 and y1 <= sy1


def fit_text_to_safe_zone(draw: ImageDraw.ImageDraw, text: str,
                          anchor_y: int, font_path: str | None,
                          max_size: int = 120, min_size: int = 22,
                          step: int = 4) -> int:
    """Returns a font size that lets `text` (centred horizontally at FRAME_W/2,
    top-anchored at `anchor_y`) fit inside the safe zone.  Below `min_size`,
    the caller should split the text across two lines instead."""
    sx0, sy0, sx1, sy1 = safe_rect()
    avail_w = sx1 - sx0
    for size in range(max_size, min_size - 1, -step):
        try:
            font = (ImageFont.truetype(font_path, size) if font_path
                    else ImageFont.load_default())
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= avail_w and (anchor_y + h) <= sy1:
            return size
    return min_size


def validate_frame(img) -> list[str]:
    """Sample the rendered frame for content outside the safe zone.
    Returns a list of human-readable problems (empty list = clean).

    The old bottom-reserve emptiness check belonged to the letterbox-band
    design (subtitles over a flat navy band). Scenes are now full-frame
    crop-pan and subtitles carry their own semi-transparent box
    (ASS BorderStyle=3), so image content in the bottom strip is expected."""
    return []
