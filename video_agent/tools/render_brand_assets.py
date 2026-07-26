"""Pre-render intro/outro MP4s. Run once during setup."""
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

from video_agent.config import (
    SHORT_FORMAT, BRAND_DARK_NAVY, BRAND_NAVY_2, BRAND_GOLD, BRAND_TEXT_LIGHT,
    BRAND_LOGO_GOLD_PATH, BRAND_FONT_BODY, BRAND_FONT_HEADING,
    INTRO_VIDEO_PATH, OUTRO_VIDEO_PATH,
)
from video_agent.visual_engine.text_card import _load_font

log = logging.getLogger(__name__)


def _ffmpeg(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-400:]}")


def _make_card(text_lines: list[tuple[str, str, int]], output_png: Path) -> None:
    w, h = SHORT_FORMAT["resolution"]
    img = Image.new("RGB", (w, h), color=BRAND_DARK_NAVY)
    draw = ImageDraw.Draw(img)
    total = sum(size for _, _, size in text_lines) + 30 * (len(text_lines) - 1)
    y = (h - total) // 2
    for text, color, size in text_lines:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), text, font=font, fill=color)
        y += size + 30
    img.save(output_png, "PNG")


def _png_to_mp4(png: Path, output_mp4: Path, duration_s: float) -> None:
    fps = SHORT_FORMAT["fps"]
    _ffmpeg([
        "ffmpeg", "-y", "-loop", "1", "-t", str(duration_s),
        "-i", str(png), "-r", str(fps), "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-an", str(output_mp4),
    ])


def render_intro(output_mp4: Path, duration_s: float = 3.0) -> Path:
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "intro.png"
        _make_card([
            ("HRSU INDORE", BRAND_GOLD, 110),
            ("Calcium Nitrate Specialists", BRAND_TEXT_LIGHT, 44),
        ], png)
        _png_to_mp4(png, output_mp4, duration_s)
    return output_mp4


def render_outro(output_mp4: Path, duration_s: float = 5.0) -> Path:
    """Renders the v2 outro: gradient bg + logo + tagline + strong CTA + subtle zoom-out."""
    from video_agent.motion.ken_burns import MotionPlan, render_motion_clip
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "outro.png"
        _draw_outro_card(png)
        plan = MotionPlan(direction="out", start_xy=(0, 0), end_xy=(0, 0),
                          start_scale=1.05, end_scale=1.0)
        render_motion_clip(png, plan, output_mp4, duration_s, fps=30)
    return output_mp4


def _draw_outro_card(out_png: Path):
    """Composes the static outro layout: gradient + logo + tagline + CTA block."""
    from PIL import ImageFont
    from video_agent.safezone import FRAME_W, FRAME_H

    def _hex_to_rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    # Vertical gradient: BRAND_DARK_NAVY at top → BRAND_NAVY_2 at bottom
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    g_draw = ImageDraw.Draw(img)
    top = _hex_to_rgb(BRAND_DARK_NAVY)
    bot = _hex_to_rgb(BRAND_NAVY_2)
    for y in range(FRAME_H):
        t = y / (FRAME_H - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        g_draw.line([(0, y), (FRAME_W, y)], fill=c)

    draw = ImageDraw.Draw(img)

    # Brand mark — large "HRSU" wordmark in gold, with "INDORE" beneath.
    # Designed to work regardless of whether the logo PNG is present.
    brand_y = 480
    hrsu_font = _safe_truetype(BRAND_FONT_HEADING, 220)
    hrsu_text = "HRSU"
    b = draw.textbbox((0, 0), hrsu_text, font=hrsu_font)
    tw = b[2] - b[0]
    draw.text(((FRAME_W - tw) // 2, brand_y), hrsu_text,
              font=hrsu_font, fill=BRAND_GOLD)

    indore_font = _safe_truetype(BRAND_FONT_BODY, 70)
    indore_text = "INDORE"
    b = draw.textbbox((0, 0), indore_text, font=indore_font)
    tw = b[2] - b[0]
    draw.text(((FRAME_W - tw) // 2, brand_y + 240),
              indore_text, font=indore_font, fill=BRAND_TEXT_LIGHT)

    # Optional logo overlay (drawn on top if present, but no longer required)
    logo_path = Path(BRAND_LOGO_GOLD_PATH)
    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((180, 180))
            img.paste(logo, ((FRAME_W - logo.width) // 2, brand_y - 220), logo)
        except Exception:
            pass

    # Tagline (gold accent rule above)
    accent_w, accent_h = 240, 4
    draw.rectangle(
        [((FRAME_W - accent_w) // 2, brand_y + 370),
         ((FRAME_W + accent_w) // 2, brand_y + 370 + accent_h)],
        fill=BRAND_GOLD,
    )
    tagline_font = _safe_truetype(BRAND_FONT_BODY, 42)
    tagline = "Industrial Chemicals. Engineered Trust."
    b = draw.textbbox((0, 0), tagline, font=tagline_font)
    tw = b[2] - b[0]
    draw.text(((FRAME_W - tw) // 2, brand_y + 410),
              tagline, font=tagline_font, fill=BRAND_TEXT_LIGHT)

    # CTA block — strong, near bottom safe zone
    cta1_font = _safe_truetype(BRAND_FONT_BODY, 52)
    cta1 = "Source your calcium nitrate at"
    b = draw.textbbox((0, 0), cta1, font=cta1_font)
    tw = b[2] - b[0]
    draw.text(((FRAME_W - tw) // 2, 1480), cta1, font=cta1_font,
              fill=BRAND_TEXT_LIGHT)

    cta2_font = _safe_truetype(BRAND_FONT_HEADING, 96)
    cta2 = "hrsuindore.com"
    b = draw.textbbox((0, 0), cta2, font=cta2_font)
    tw = b[2] - b[0]
    draw.text(((FRAME_W - tw) // 2, 1560), cta2, font=cta2_font, fill=BRAND_GOLD)

    img.save(out_png)


def _safe_truetype(family_name: str, size: int):
    """Load a TrueType font by family name, falling back to PIL default if missing."""
    from PIL import ImageFont
    candidates = [
        f"{family_name}.ttf",
        f"{family_name}-Regular.ttf",
        f"C:/Windows/Fonts/{family_name}.ttf",
        f"C:/Windows/Fonts/{family_name.lower()}.ttf",
        "arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--intro-only", action="store_true")
    p.add_argument("--outro-only", action="store_true")
    args = p.parse_args()
    if not args.outro_only:
        render_intro(Path(INTRO_VIDEO_PATH))
        log.info("Wrote %s", INTRO_VIDEO_PATH)
    if not args.intro_only:
        render_outro(Path(OUTRO_VIDEO_PATH))
        log.info("Wrote %s", OUTRO_VIDEO_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
