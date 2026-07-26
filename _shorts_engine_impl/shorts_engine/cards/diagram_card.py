"""DIAGRAM card: flow / before_after / comparison / dosing_scale templates."""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_X0 = config.SAFE_SIDE_PX
_X1 = config.CANVAS_W - config.SAFE_SIDE_PX
_MAXW = _X1 - _X0


def visible_nodes(n: int, stage: int, total: int) -> int:
    stage = max(1, min(stage, total))
    return math.ceil(n * stage / total)


def _node_box(d: ImageDraw.ImageDraw, img, label: str, y: int, h: int,
              alpha: float, accent: bool) -> None:
    col = theme.GOLD if accent else theme.TEXT
    col = tuple(int(c * alpha + n * (1 - alpha)) for c, n in zip(col, theme.NAVY))
    d.rounded_rectangle([_X0 + 40, y, _X1 - 40, y + h], radius=22,
                        outline=col, width=3)
    f, lines, _ = theme.fit_text(d, label, "body", _MAXW - 160, max_size=46,
                                 max_lines=2)
    ty = y + (h - len(lines) * 52) // 2
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((config.CANVAS_W - w) // 2, ty), line, font=f, fill=col)
        ty += 52


def _flow(img, d, payload, t):
    labels = payload.get("labels") or []
    if not 2 <= len(labels) <= 4:
        raise EngineError(f"flow diagram needs 2–4 labels, got {len(labels)}")
    stage = int(payload.get("reveal_stage", 1))
    total = int(payload.get("reveal_total", 1))
    n_show = visible_nodes(len(labels), stage, total)
    n_prev = visible_nodes(len(labels), stage - 1, total) if stage > 1 else 0
    box_h, gap = 150, 96
    block = len(labels) * box_h + (len(labels) - 1) * gap
    y = max(config.SAFE_TOP_PX + 40, (config.CANVAS_H - block) // 2 - 60)
    for i, label in enumerate(labels[:n_show]):
        if i < n_prev:
            alpha = 1.0  # carried over from earlier shot — static
        else:
            alpha, _ = theme.fade_rise(t, i - n_prev)
        _node_box(d, img, label, y, box_h, max(alpha, 0.0), accent=(i == len(labels) - 1))
        if i < n_show - 1:
            ay = y + box_h + gap // 2
            acol = tuple(int(c * alpha) for c in theme.GOLD)
            d.polygon([(540 - 16, ay - 12), (540 + 16, ay - 12), (540, ay + 18)],
                      fill=acol)
        y += box_h + gap


def _panel(img, d, title, items, y0, y1, accent):
    col = theme.GOLD if accent else theme.MUTED
    d.rounded_rectangle([_X0, y0, _X1, y1], radius=24, outline=col, width=3)
    f = theme.resolve_font("body", 40)
    d.text((_X0 + 36, y0 + 24), title.upper(), font=f, fill=col)
    body = theme.resolve_font("body", 44)
    ty = y0 + 100
    for it in items:
        d.text((_X0 + 36, ty), f"• {it}", font=body, fill=theme.TEXT)
        ty += 62


def _before_after(img, d, payload, t):
    mid = config.CANVAS_H // 2
    _panel(img, d, "Before", payload.get("before") or [], config.SAFE_TOP_PX + 60,
           mid - 40, accent=False)
    _panel(img, d, "After", payload.get("after") or [], mid + 40,
           config.CANVAS_H - config.SAFE_BOTTOM_PX - 60, accent=True)


def _comparison(img, d, payload, t):
    left, right = payload.get("left") or {}, payload.get("right") or {}
    midx = config.CANVAS_W // 2
    for side, x0, x1, accent in ((left, _X0, midx - 20, False),
                                 (right, midx + 20, _X1, True)):
        col = theme.GOLD if accent else theme.MUTED
        d.rounded_rectangle([x0, 500, x1, 1300], radius=24, outline=col, width=3)
        f = theme.resolve_font("body", 42)
        d.text((x0 + 28, 530), str(side.get("title", "")).upper(), font=f, fill=col)
        body = theme.resolve_font("body", 36)
        ty = 620
        for it in side.get("items", []):
            d.text((x0 + 28, ty), f"• {it}", font=body, fill=theme.TEXT)
            ty += 54


def _dosing_scale(img, d, payload, t):
    lo, hi = float(payload["lo"]), float(payload["hi"])
    mn, mx = float(payload.get("min", 0)), float(payload.get("max", max(hi * 1.5, hi + 1)))
    y = 980
    d.rectangle([_X0, y, _X1, y + 14], fill=theme.MUTED)
    span = mx - mn or 1.0
    bx0 = _X0 + int((_X1 - _X0) * (lo - mn) / span)
    bx1 = _X0 + int((_X1 - _X0) * (hi - mn) / span)
    p = theme.ease_out_cubic(t / 0.8)
    bx1p = bx0 + int((bx1 - bx0) * p)
    d.rectangle([bx0, y - 10, max(bx0 + 4, bx1p), y + 24], fill=theme.GOLD)
    f = theme.resolve_font("body", 44)
    d.text((bx0 - 20, y - 80), str(payload["lo"]), font=f, fill=theme.GOLD)
    d.text((bx1 - 20, y - 80), str(payload["hi"]), font=f, fill=theme.GOLD)
    d.text((_X1 - 140, y + 40), str(payload.get("unit", "")), font=f, fill=theme.MUTED)
    label = payload.get("label")
    if label:
        lf, lines, _ = theme.fit_text(d, label, "heading", _MAXW, max_size=60)
        theme.paste_text_block(img, lines, lf, 620, theme.TEXT)


_TEMPLATES = {"flow": _flow, "before_after": _before_after,
              "comparison": _comparison, "dosing_scale": _dosing_scale}


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    template = payload.get("template")
    fn = _TEMPLATES.get(template)
    if fn is None:
        raise EngineError(f"unknown diagram template {template!r}")
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    fn(img, d, payload, t)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
