"""STAT_CARD: huge number (count-up when scalar), unit, label, citation chip."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

COUNT_UP_S = 0.8


def _as_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def format_value(current: float, template: str) -> str:
    decimals = len(template.split(".")[1]) if "." in template else 0
    return f"{current:.{decimals}f}"


def display_value(value: str, t: float) -> str:
    """Scalar values count up over COUNT_UP_S; ranges/text render verbatim."""
    target = _as_float(value)
    if target is None:
        return value
    p = theme.ease_out_cubic(t / COUNT_UP_S)
    return format_value(target * p, value)


_VALUE_MAX_SIZE = 190
_UNIT_MAX_SIZE = 56
_VALUE_MIN_SIZE = 64


def _fit_value_unit(d: ImageDraw.ImageDraw, value: str, unit: str, max_w: int):
    """Shrink the value/unit fonts together (same ratio) until the combined
    single line fits max_w. A fixed 190px value font clips off-canvas for
    unusually long fact values (e.g. a multi-number range like
    "180-50-250") -- the leading/trailing characters render outside the
    canvas with no error, which is exactly the kind of defect the VERIFY
    vision gate catches but the legibility auto-fix couldn't repair (it
    only shrinks word-based text fields, not a numeric value string)."""
    for size in range(_VALUE_MAX_SIZE, _VALUE_MIN_SIZE - 1, -6):
        ratio = size / _VALUE_MAX_SIZE
        vf = theme.resolve_font("body", size)
        uf = theme.resolve_font("body", max(28, int(_UNIT_MAX_SIZE * ratio))) if unit else None
        v_w = d.textlength(value, font=vf)
        u_w = d.textlength(" " + unit, font=uf) if uf else 0
        if v_w + u_w <= max_w:
            return vf, uf, v_w, u_w
    vf = theme.resolve_font("body", _VALUE_MIN_SIZE)
    uf = theme.resolve_font(
        "body", max(28, int(_UNIT_MAX_SIZE * _VALUE_MIN_SIZE / _VALUE_MAX_SIZE))
    ) if unit else None
    return vf, uf, d.textlength(value, font=vf), (d.textlength(" " + unit, font=uf) if uf else 0)


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    value = display_value(str(payload["value"]), t)
    unit = str(payload.get("unit") or "")
    label = str(payload.get("label") or "")

    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    vfont, ufont, v_w, u_w = _fit_value_unit(d, value, unit, max_w)
    x0 = (config.CANVAS_W - (v_w + u_w)) // 2
    y_val = 700
    alpha, dy = theme.fade_rise(t, 0)
    if alpha > 0:
        col = tuple(int(c * alpha + n * (1 - alpha))
                    for c, n in zip(theme.TEXT, theme.NAVY))
        d.text((x0, y_val + dy), value, font=vfont, fill=col)
        if unit:
            d.text((x0 + v_w, y_val + dy + 110), " " + unit, font=ufont,
                   fill=theme.MUTED)

    # gold underline sweep under the value
    p = theme.ease_out_cubic(t / COUNT_UP_S)
    if p > 0:
        w = int(max(v_w, 200) * p)
        cy = y_val + 250
        d.rectangle([(config.CANVAS_W - w) // 2, cy,
                     (config.CANVAS_W + w) // 2, cy + 8], fill=theme.GOLD)

    if label:
        max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        lfont, lines, _ = theme.fit_text(d, label, "heading", max_w, max_size=64)
        la, ldy = theme.fade_rise(t, 2)
        if la > 0:
            col = tuple(int(c * la + n * (1 - la))
                        for c, n in zip(theme.TEXT, theme.NAVY))
            theme.paste_text_block(img, lines, lfont, y_val + 310 + ldy, col)

    chip = payload.get("citation")
    if chip:
        theme.draw_citation_chip(img, chip)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
