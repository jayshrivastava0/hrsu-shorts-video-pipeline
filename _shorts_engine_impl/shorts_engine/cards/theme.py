"""Brand design system: palette, fonts, background, motion, chip, text fitting.

Cards call `render_card(frame_fn, payload, duration, out_path, fade_in_s)`;
`frame_fn(payload, t, duration) -> PIL.Image` must be pure so tests can assert
on single frames without ffmpeg.
"""
from __future__ import annotations

import functools
import logging
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from shorts_engine import config
from shorts_engine.cards import encoder
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


GOLD = hex_to_rgb(config.BRAND_GOLD)
NAVY = hex_to_rgb(config.BRAND_DARK_NAVY)
NAVY2 = hex_to_rgb(config.BRAND_NAVY_2)
TEXT = hex_to_rgb(config.BRAND_TEXT_LIGHT)
MUTED = hex_to_rgb(config.BRAND_TEXT_MUTED)

# Font ladder: brand ttf dropped into asset_library/fonts/ wins; otherwise
# Windows serif/sans stand-ins; otherwise PIL default (tests still pass).
_FONT_CANDIDATES = {
    "heading": ["PlayfairDisplay-Bold.ttf", "PlayfairDisplay-SemiBold.ttf",
                "georgiab.ttf", "georgia.ttf", "timesbd.ttf"],
    "body": ["Poppins-SemiBold.ttf", "Poppins-Medium.ttf", "Poppins-Regular.ttf",
             "arialbd.ttf", "arial.ttf", "segoeuib.ttf"],
}
_FONT_DIRS = [config.PROJECT_ROOT / "asset_library" / "fonts",
              Path("C:/Windows/Fonts")]


@functools.lru_cache(maxsize=64)
def resolve_font(kind: str, size: int):
    from PIL import ImageFont
    if kind not in _FONT_CANDIDATES:
        raise EngineError(f"unknown font kind {kind!r}")
    for d in _FONT_DIRS:
        for name in _FONT_CANDIDATES[kind]:
            p = d / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:  # corrupt font file — try next
                    continue
    logger.warning("no truetype font found for %s — using PIL default", kind)
    return ImageFont.load_default()


def ease_out_cubic(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


# Deterministic film grain (≈2%), rolled per frame for cheap variation.
_GRAIN = np.random.default_rng(42).normal(0.0, 5.0, (config.CANVAS_H, config.CANVAS_W, 1))


def background(t: float) -> Image.Image:
    """Vertical navy gradient with an 8s midpoint drift loop + film grain."""
    top = np.array(NAVY, dtype=float)
    bot = np.array(NAVY2, dtype=float)
    mid = 0.5 + 0.12 * math.sin(2 * math.pi * t / 8.0)
    ys = np.linspace(0.0, 1.0, config.CANVAS_H)[:, None, None]
    m = np.clip(ys / (2 * mid), 0.0, 1.0)
    arr = top * (1 - m) + bot * m
    arr = arr + np.roll(_GRAIN, int(t * config.FPS) % config.CANVAS_H, axis=0)
    arr = np.broadcast_to(arr, (config.CANVAS_H, config.CANVAS_W, 3)).copy() \
        if arr.shape[1] == 1 else arr
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def fade_rise(t: float, index: int, rise_px: int = 26) -> tuple[float, int]:
    """300ms fade+rise, staggered 80ms per element index."""
    start = 0.08 * index
    p = ease_out_cubic((t - start) / 0.30)
    return p, int(round(rise_px * (1 - p)))


def draw_citation_chip(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    f = resolve_font("body", 30)
    pad = 16
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x0 = config.SAFE_SIDE_PX
    y1 = config.CANVAS_H - config.SAFE_BOTTOM_PX - 16
    y0 = y1 - h - 2 * pad
    d.rounded_rectangle([x0, y0, x0 + w + 2 * pad, y1],
                        radius=(h + 2 * pad) // 2, outline=GOLD, width=2)
    d.text((x0 + pad, y0 + pad - bbox[1]), text, font=f, fill=GOLD)


def fit_text(draw: ImageDraw.ImageDraw, text: str, kind: str, max_w: int,
             max_size: int, min_size: int = 28, max_lines: int = 4):
    """Largest size at which `text` wraps into ≤max_lines lines of ≤max_w px."""
    lines: list[str] = [text]
    for size in range(max_size, min_size - 1, -4):
        f = resolve_font(kind, size)
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=f) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(
                draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, size
    return resolve_font(kind, min_size), lines, min_size


def paste_text_block(img: Image.Image, lines: list[str], font, y_top: int,
                     color: tuple[int, int, int], align: str = "center") -> int:
    d = ImageDraw.Draw(img)
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (24, 8)
    line_h = int((ascent + descent) * 1.18)
    y = y_top
    for line in lines:
        w = d.textlength(line, font=font)
        x = (config.CANVAS_W - w) // 2 if align == "center" else config.SAFE_SIDE_PX
        d.text((x, y), line, font=font, fill=color)
        y += line_h
    return y - y_top


def render_frame_with_fade(frame_fn, payload: dict, t: float, duration: float,
                           fade_in_s: float) -> Image.Image:
    img = frame_fn(payload, t, duration)
    if fade_in_s > 0 and t < fade_in_s:
        img = ImageEnhance.Brightness(img).enhance(ease_out_cubic(t / fade_in_s))
    return img


def render_card(frame_fn, payload: dict, duration: float, out_path: Path,
                fade_in_s: float = 0.0) -> Path:
    n = max(1, round(duration * config.FPS))
    frames = (render_frame_with_fade(frame_fn, payload, i / config.FPS,
                                     duration, fade_in_s) for i in range(n))
    encoder.write_frames_to_mp4(frames, Path(out_path))
    return Path(out_path)
