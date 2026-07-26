"""LOGO_CTA end card: logo (or wordmark), one differentiator, CTA, domain."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

_logo_path: Path = config.BRAND_LOGO_FILE


@functools.lru_cache(maxsize=1)
def _load_logo() -> Image.Image | None:
    try:
        img = Image.open(_logo_path).convert("RGBA")
        w = 420
        h = int(img.height * w / img.width)
        return img.resize((w, h))
    except Exception:
        logger.warning("brand logo unreadable at %s — using wordmark", _logo_path)
        return None


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    y = config.SAFE_TOP_PX + 150
    logo = _load_logo()
    a0, dy0 = theme.fade_rise(t, 0)
    if logo is not None:
        if a0 > 0:
            faded = logo.copy()
            alpha = faded.getchannel("A").point(lambda px: int(px * a0))
            faded.putalpha(alpha)
            img.paste(faded, ((config.CANVAS_W - logo.width) // 2, y + dy0), faded)
        y += logo.height + 90
    else:
        wf = theme.resolve_font("heading", 160)
        col = tuple(int(c * a0) for c in theme.GOLD)
        w = d.textlength("HRSU", font=wf)
        d.text(((config.CANVAS_W - w) // 2, y + dy0), "HRSU", font=wf, fill=col)
        y += 260

    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    for i, (text, kind, color, size) in enumerate([
            (payload.get("differentiator", ""), "heading", theme.TEXT, 58),
            (payload.get("cta_line", ""), "body", theme.MUTED, 46)]):
        if not text:
            continue
        f, lines, _ = theme.fit_text(d, str(text), kind, max_w, max_size=size)
        a, dy = theme.fade_rise(t, i + 1)
        if a > 0:
            col = tuple(int(c * a + n * (1 - a)) for c, n in zip(color, theme.NAVY))
            y += theme.paste_text_block(img, lines, f, y + dy, col) + 56

    domain = payload.get("domain", "")
    if domain:
        f = theme.resolve_font("body", 64)
        a, dy = theme.fade_rise(t, 3)
        if a > 0:
            col = tuple(int(c * a + n * (1 - a)) for c, n in zip(theme.GOLD, theme.NAVY))
            w = d.textlength(domain, font=f)
            yd = config.CANVAS_H - config.SAFE_BOTTOM_PX - 140 + dy
            d.text(((config.CANVAS_W - w) // 2, yd), domain, font=f, fill=col)
            d.rectangle([(config.CANVAS_W - w) // 2, yd + 88,
                         (config.CANVAS_W + w) // 2, yd + 94], fill=col)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
