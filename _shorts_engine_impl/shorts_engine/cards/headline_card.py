"""HEADLINE_CARD: big Playfair statement, one gold accent word, fade+rise."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def pick_accent(text: str) -> str:
    m = _NUM.search(text)
    if m:
        return m.group(0)
    words = [w.strip(".,;:!?") for w in text.split()]
    return max(words, key=len) if words else ""


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    text = payload["text"].strip()
    accent = (payload.get("accent") or pick_accent(text)).lower().strip(".,;:!?")
    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    font, lines, _ = theme.fit_text(d, text, "heading", max_w, max_size=104)
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (24, 8)
    line_h = int((ascent + descent) * 1.18)
    block_h = line_h * len(lines)
    y0 = max(config.SAFE_TOP_PX,
             (config.CANVAS_H - block_h) // 2 - 120)
    for i, line in enumerate(lines):
        alpha, dy = theme.fade_rise(t, i)
        if alpha <= 0:
            continue
        # draw word-by-word so the accent word can be gold
        total_w = d.textlength(line, font=font)
        x = (config.CANVAS_W - total_w) // 2
        y = y0 + i * line_h + dy
        for word in line.split(" "):
            color = theme.GOLD if word.lower().strip(".,;:!?") == accent else theme.TEXT
            if alpha < 1.0:
                color = tuple(int(c * alpha + bg * (1 - alpha))
                              for c, bg in zip(color, theme.NAVY))
            d.text((x, y), word, font=font, fill=color)
            x += d.textlength(word + " ", font=font)
    # gold underline sweep beneath the block after text lands
    p = theme.ease_out_cubic((t - 0.35) / 0.5)
    if p > 0:
        w = int(220 * p)
        cy = y0 + block_h + 28
        d.rectangle([(config.CANVAS_W - w) // 2, cy,
                     (config.CANVAS_W + w) // 2, cy + 6], fill=theme.GOLD)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
