from PIL import Image, ImageDraw, ImageFont
from video_agent.safezone import (
    fits_safe_zone, fit_text_to_safe_zone, validate_frame,
    OUTER_MARGIN, BOTTOM_RESERVE, TOP_RESERVE, FRAME_W, FRAME_H,
)


def test_text_inside_safe_zone_passes():
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    draw = ImageDraw.Draw(img)
    bbox = (200, 200, 800, 400)
    assert fits_safe_zone(bbox)


def test_text_overflowing_right_margin_fails():
    bbox = (200, 200, 1100, 400)             # right edge > FRAME_W - OUTER_MARGIN
    assert not fits_safe_zone(bbox)


def test_text_in_subtitle_band_fails():
    bbox = (200, FRAME_H - 100, 800, FRAME_H - 50)   # inside bottom reserve
    assert not fits_safe_zone(bbox)


def test_fit_text_shrinks_until_safe():
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    draw = ImageDraw.Draw(img)
    # A long phrase that won't fit at large size
    fitted_size = fit_text_to_safe_zone(
        draw, "DELIVERING TANGIBLE RESULTS FOR AUSTRALIA",
        anchor_y=300, font_path=None, max_size=120, min_size=22,
    )
    assert 22 <= fitted_size <= 120
