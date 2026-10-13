"""Custom YouTube thumbnail (1280x720 JPEG) cut from the video's own hook frame.

The explainer videos are portrait, the thumbnail is landscape. The hook card (question + lead
diagram) shown at ~6 s is scaled to 1280 wide and a 720-tall window is cut from it, choosing
the window with the most visual content. Nothing is drawn or written that is not already in
the video, so the thumbnail cannot claim anything the video does not.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

W, H = 1280, 720
HOOK_SECONDS = 6.0
CAPTION_CUT = 0.81             # drop the burned-in caption band (keep-out starts at ~83% of the frame)
MAX_BYTES = 2_000_000          # YouTube limit is 2 MB


def extract_frame(video: Path | str, out_png: Path | str, at_s: float = HOOK_SECONDS) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(at_s), "-i", str(video),
                    "-frames:v", "1", str(out_png)], check=True)
    return Path(out_png)


def _content_score(img) -> float:
    import numpy as np
    arr = np.asarray(img.convert("L").resize((135, 240)), dtype="float32")
    inner = arr[8:-40, 6:-6]                      # skip the HRSU header band and the caption band
    return float(inner.std() + np.abs(np.diff(inner, axis=0)).mean() * 4)


def _duration(video: Path | str) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(video)])
    return float(out.decode().strip())


def _mechanism_frame(video: Path | str, workdir: Path):
    """The busiest frame between 25% and 75% of the video (a diagram, not a title card)."""
    from PIL import Image
    dur = _duration(video)
    best, best_score = None, -1.0
    for i, frac in enumerate((0.25, 0.32, 0.4, 0.48, 0.56, 0.64, 0.72)):
        png = extract_frame(video, workdir / f"cand_{i}.png", dur * frac)
        img = Image.open(png).convert("RGB")
        score = _content_score(img)
        if score > best_score:
            best, best_score = img.copy(), score
        png.unlink(missing_ok=True)
    return best


def make_thumbnail(video: Path | str, out_jpg: Path | str, *, at_s: float = HOOK_SECONDS) -> Optional[Path]:
    """Write a 1280x720 JPEG thumbnail; returns its path, or None if anything fails.

    Two real frames of the video side by side on a blurred copy of the hook frame: the hook card
    (the question) on the left and the busiest mid-video diagram on the right.
    """
    try:
        from PIL import Image, ImageFilter
        out_jpg = Path(out_jpg)
        workdir = out_jpg.parent
        hook_png = extract_frame(video, workdir / (out_jpg.stem + ".hook.png"), at_s)
        hook = Image.open(hook_png).convert("RGB")
        mech = _mechanism_frame(video, workdir) or hook
        bg = hook.resize((W, round(hook.height * W / hook.width)), Image.LANCZOS)
        bg = bg.crop((0, 0, W, H)).filter(ImageFilter.GaussianBlur(28))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (10, 20, 40)), 0.55)
        th = H - 40
        tw = round(hook.width * th / (hook.height * CAPTION_CUT))
        gap, border = 36, 3
        total = tw * 2 + gap
        x0 = (W - total) // 2
        for i, im in enumerate((hook, mech)):
            panel = im.crop((0, 0, im.width, round(im.height * CAPTION_CUT))).resize((tw, th), Image.LANCZOS)
            x = x0 + i * (tw + gap)
            bg.paste(Image.new("RGB", (tw + 2 * border, th + 2 * border), (212, 175, 55)), (x - border, 20 - border))
            bg.paste(panel, (x, 20))
        for quality in (92, 85, 75, 65):
            bg.save(out_jpg, "JPEG", quality=quality, optimize=True)
            if out_jpg.stat().st_size <= MAX_BYTES:
                break
        hook_png.unlink(missing_ok=True)
        return out_jpg
    except Exception:
        return None
