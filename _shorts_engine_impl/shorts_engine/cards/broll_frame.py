"""Real-asset framing. Landscape/square are NEVER crop-panned (inset matte or
blur-fill keeps the whole image visible); Ken Burns only on portrait assets."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_MAX_W = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
_MAX_H = 1100
_CENTER_Y = 880
KEN_BURNS_MAX_ZOOM = 1.08


def is_portrait(w: int, h: int) -> bool:
    return h / w >= 1.25


def placement(img_w: int, img_h: int) -> tuple[int, int, int, int]:
    scale = min(_MAX_W / img_w, _MAX_H / img_h)
    w, h = int(img_w * scale), int(img_h * scale)
    x0 = (config.CANVAS_W - w) // 2
    y0 = _CENTER_Y - h // 2
    return (x0, y0, x0 + w, y0 + h)


def kenburns_window(img_w: int, img_h: int, t: float, duration: float,
                    max_zoom: float = KEN_BURNS_MAX_ZOOM) -> tuple[int, int, int, int]:
    p = min(1.0, max(0.0, t / duration)) if duration > 0 else 0.0
    zoom = 1.0 + (max_zoom - 1.0) * p
    w, h = int(img_w / zoom), int(img_h / zoom)
    x0, y0 = (img_w - w) // 2, (img_h - h) // 2
    return (x0, y0, x0 + w, y0 + h)


@functools.lru_cache(maxsize=16)
def _load(path_str: str) -> Image.Image:
    p = Path(path_str)
    if not p.exists():
        raise EngineError(f"broll asset missing: {p}")
    return Image.open(p).convert("RGB")


def _kenburns_frame(src: Image.Image, t: float, duration: float) -> Image.Image:
    win = kenburns_window(src.width, src.height, t, duration)
    crop = src.crop(win)
    # cover-fit portrait crop to canvas (portrait→portrait: minimal edge loss ≤8%)
    scale = max(config.CANVAS_W / crop.width, config.CANVAS_H / crop.height)
    w, h = int(crop.width * scale), int(crop.height * scale)
    crop = crop.resize((w, h))
    x = (w - config.CANVAS_W) // 2
    y = (h - config.CANVAS_H) // 2
    return crop.crop((x, y, x + config.CANVAS_W, y + config.CANVAS_H))


def _blurfill_frame(src: Image.Image, t: float) -> Image.Image:
    scale = max(config.CANVAS_W / src.width, config.CANVAS_H / src.height)
    bg = src.resize((int(src.width * scale) + 1, int(src.height * scale) + 1))
    x = (bg.width - config.CANVAS_W) // 2
    y = (bg.height - config.CANVAS_H) // 2
    bg = bg.crop((x, y, x + config.CANVAS_W, y + config.CANVAS_H))
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    dark = Image.new("RGB", bg.size, theme.NAVY)
    bg = Image.blend(bg, dark, 0.35)
    box = placement(src.width, src.height)
    inset = src.resize((box[2] - box[0], box[3] - box[1]))
    bg.paste(inset, (box[0], box[1]))
    d = ImageDraw.Draw(bg)
    d.rectangle(box, outline=theme.GOLD, width=3)
    return bg


def _inset_frame(src: Image.Image, caption: str, t: float) -> Image.Image:
    img = theme.background(t)
    box = placement(src.width, src.height)
    inset = src.resize((box[2] - box[0], box[3] - box[1]))
    img.paste(inset, (box[0], box[1]))
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline=theme.GOLD, width=3)
    if caption:
        f, lines, _ = theme.fit_text(d, caption, "body", _MAX_W, max_size=40,
                                     max_lines=2)
        theme.paste_text_block(img, lines, f, box[3] + 30, theme.MUTED)
    return img


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    src = _load(str(payload["image_path"]))
    layout = payload.get("layout", "auto")
    if layout == "auto":
        layout = "kenburns" if is_portrait(src.width, src.height) else "blurfill"
    if layout == "kenburns" and not is_portrait(src.width, src.height):
        logger.warning("Ken Burns requested for landscape asset — using blurfill")
        layout = "blurfill"
    if layout == "kenburns":
        return _kenburns_frame(src, t, duration)
    if layout == "inset":
        return _inset_frame(src, str(payload.get("caption", "")), t)
    return _blurfill_frame(src, t)


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
