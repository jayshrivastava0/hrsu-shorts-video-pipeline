"""QUOTE_CARD: verbatim blog sentence (≤120 chars) + source chip."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)


def trim_quote(q: str, limit: int = 120) -> str:
    q = " ".join(q.split())
    if len(q) <= limit:
        return q
    cut = q[:limit].rsplit(" ", 1)[0].rstrip(".,;: ")
    return cut + "…"


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    mark_font = theme.resolve_font("heading", 220)
    a0, dy0 = theme.fade_rise(t, 0)
    if a0 > 0:
        col = tuple(int(c * a0) for c in theme.GOLD)
        d.text((config.SAFE_SIDE_PX, config.SAFE_TOP_PX + 60), "“",
               font=mark_font, fill=col)
    quote = trim_quote(str(payload.get("quote", "")))
    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    f, lines, _ = theme.fit_text(d, quote, "heading", max_w, max_size=66)
    a1, dy1 = theme.fade_rise(t, 1)
    if a1 > 0:
        col = tuple(int(c * a1 + n * (1 - a1)) for c, n in zip(theme.TEXT, theme.NAVY))
        theme.paste_text_block(img, lines, f, 640 + dy1, col)
    src = payload.get("source")
    if src:
        theme.draw_citation_chip(img, str(src))
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
