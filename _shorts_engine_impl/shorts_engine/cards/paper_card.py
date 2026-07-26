"""PAPER_CARD: the cited paper's front page as a 'receipts' shot — inset at
78% width on the brand background, -2° tilt with soft shadow, gold underline
sweep over the title region, slow 1.05 push-in. The user's top request from
the first watch-through."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_INSET_FRAC = 0.78
_TILT_DEG = -2.0
_PUSH_MAX = 1.05
_CENTER_Y = 820


@functools.lru_cache(maxsize=8)
def _load_page(path_str: str) -> Image.Image:
    p = Path(path_str)
    if not p.exists():
        raise EngineError(f"paper front page missing: {p}")
    try:
        return Image.open(p).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise EngineError(f"paper front page unreadable: {p}: {e}") from e


def _tilted_paper(src: Image.Image, width: int) -> Image.Image:
    """Paper resized to `width`, tilted, with a soft drop shadow. RGBA."""
    h = int(src.height * width / src.width)
    h = min(h, int(width * 1.5))  # clamp very tall pages to 3:2 of width
    paper = src.resize((width, h)).convert("RGBA")
    d = ImageDraw.Draw(paper)
    d.rectangle([0, 0, width - 1, h - 1], outline=(200, 200, 200, 255), width=2)
    pad = 60
    canvas = Image.new("RGBA", (width + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        [pad + 10, pad + 14, pad + width + 10, pad + h + 14],
        fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(paper, (pad, pad))
    return canvas.rotate(_TILT_DEG, expand=True, resample=Image.BICUBIC)


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    src = _load_page(str(payload["image_path"]))
    img = theme.background(t)
    d = ImageDraw.Draw(img)

    push = 1.0 + (_PUSH_MAX - 1.0) * min(1.0, t / max(duration, 0.01))
    inset_w = int(config.CANVAS_W * _INSET_FRAC * push)
    composite = _tilted_paper(src, inset_w)
    x = (config.CANVAS_W - composite.width) // 2
    y = _CENTER_Y - composite.height // 2
    img.paste(composite, (x, y), composite)

    # gold underline sweep across the title region (~12% down the inset)
    p = theme.ease_out_cubic(t / 0.8)
    if p > 0:
        sweep_w = int(inset_w * 0.72 * p)
        sx = (config.CANVAS_W - int(inset_w * 0.72)) // 2
        sy = y + int(composite.height * 0.16)
        d.rectangle([sx, sy, sx + sweep_w, sy + 8], fill=theme.GOLD)

    highlight = " ".join(str(payload.get("highlight", "")).split()[:8])
    if highlight:
        max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        f, lines, _ = theme.fit_text(d, highlight, "heading", max_w, max_size=56)
        ty = min(y + composite.height + 24,
                 config.CANVAS_H - config.SAFE_BOTTOM_PX - 180)
        theme.paste_text_block(img, lines, f, ty, theme.TEXT)

    chip = payload.get("citation")
    if chip:
        theme.draw_citation_chip(img, str(chip))
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
