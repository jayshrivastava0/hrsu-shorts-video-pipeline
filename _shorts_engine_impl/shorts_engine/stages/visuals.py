"""Stage 6 — VISUALS: render every shot to mp4. Designed cards render
directly; PAPER_CARD/BROLL resolve via the real acquisition ladder
(own library → blog images → free APIs → scrape, and cited-paper front-page
fetch), falling back to their declared card only on a miss. Never-blank is
enforced with a bright-pixel check on a sampled mid frame."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from shorts_engine import config
from shorts_engine.cards import (encoder, headline_card, stat_card, diagram_card,
                                 quote_card, logo_cta_card, paper_card, broll_frame)
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

RENDERERS = {
    "HEADLINE_CARD": headline_card.render,
    "STAT_CARD": stat_card.render,
    "DIAGRAM": diagram_card.render,
    "QUOTE_CARD": quote_card.render,
    "LOGO_CTA": logo_cta_card.render,
    "PAPER_CARD": paper_card.render,
    "BROLL": broll_frame.render,
}


def _acquire(**kwargs):
    from shorts_engine.sourcing.ladder import acquire
    return acquire(**kwargs)


def _fetch_front_page(url: str, torture: bool):
    from shorts_engine.sourcing.paper_page import fetch_front_page
    return fetch_front_page(url, torture=torture)


_FOCAL_TO_LAYOUT = {"center": "auto", "left": "inset", "right": "inset",
                    "top": "inset", "bottom": "inset"}


def _fallback_of(shot: dict, reason: str):
    fb = shot.get("fallback")
    if not fb or fb.get("type") not in RENDERERS:
        raise EngineError(f"{shot['id']}: {shot['type']} has no renderable fallback")
    return fb["type"], fb["payload"], {"resolved": "fallback", "reason": reason,
                                       "planned_type": shot["type"]}


def resolve_shot(shot: dict, ctx=None, post=None) -> tuple[str, dict, dict]:
    stype = shot["type"]
    if stype in RENDERERS and stype not in ("BROLL", "PAPER_CARD"):
        return stype, shot["payload"], {"resolved": "designed"}
    torture = bool(getattr(ctx, "flags", {}).get("torture", False)) if ctx else True
    if stype == "BROLL":
        if ctx is None:
            return _fallback_of(shot, "no_context")
        res = _acquire(wish=shot["payload"].get("wish", ""),
                       narration_span=shot.get("narration_span", ""),
                       workspace=Path(ctx.workspace),
                       post_images=(post or {}).get("images", []),
                       torture=torture)
        if res["image_path"]:
            layout = _FOCAL_TO_LAYOUT.get(res["focal_hint"], "auto")
            return "BROLL", {"image_path": res["image_path"], "layout": layout,
                             "caption": shot["payload"].get("wish", "")}, \
                   {"resolved": "acquired", "acquisition": res["provenance"]}
        return _fallback_of(shot, res["provenance"].get("reason") or "no_acceptance")
    if stype == "PAPER_CARD":
        url = shot["payload"].get("url", "")
        page = _fetch_front_page(url, torture) if url else None
        if page is not None:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.removeprefix("www.")
            marker = shot["payload"].get("marker")
            return "PAPER_CARD", {
                "image_path": str(page),
                "highlight": shot["payload"].get("highlight", ""),
                "citation": f"Source [{marker}] — {domain}",
            }, {"resolved": "acquired"}
        return _fallback_of(shot, "torture_mode" if torture else "paper_fetch_failed")
    raise EngineError(f"{shot['id']}: unknown shot type {stype}")


def sample_frame(mp4: Path, t: float, out_png: Path) -> Path:
    res = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}",
         "-i", str(mp4), "-frames:v", "1", str(out_png)],
        capture_output=True, text=True)
    if res.returncode != 0 or not out_png.exists():
        raise EngineError(f"frame sample failed for {mp4}: {res.stderr}")
    return out_png


def content_pixels(frame_png: Path) -> int:
    arr = np.asarray(Image.open(frame_png).convert("L"))
    return int((arr > config.LUMA_CONTENT_THRESHOLD).sum())


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    shots = json.loads((ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    shots_dir = ws / "shots"
    shots_dir.mkdir(exist_ok=True)
    report = {"shots": []}
    prev_beat = None
    first_beat = shots[0]["beat"] if shots else None
    for shot in shots:
        fade = config.TRANSITION_FADE_S if (
            shot["beat"] != prev_beat and shot["beat"] != first_beat) else 0.0
        prev_beat = shot["beat"]
        rtype, payload, prov = resolve_shot(shot, ctx, post)
        out = shots_dir / f"shot_{shot['id']}.mp4"
        RENDERERS[rtype](payload, shot["duration_s"], out, fade_in_s=fade)
        png = shots_dir / f"shot_{shot['id']}_mid.png"
        sample_frame(out, shot["duration_s"] / 2, png)
        pixels = content_pixels(png)
        if pixels < config.MIN_CONTENT_PIXELS:
            raise EngineError(
                f"VISUALS: shot {shot['id']} ({rtype}) rendered without visible "
                f"content ({pixels} bright px < {config.MIN_CONTENT_PIXELS}) — "
                f"never-blank violated")
        report["shots"].append({
            "id": shot["id"], "beat": shot["beat"], "rendered_type": rtype,
            "duration_s": shot["duration_s"], "fade_in_s": fade,
            "content_pixels": pixels, "provenance": prov, "payload": payload,
        })
        logger.info("visuals: %s -> %s (%d px)", shot["id"], rtype, pixels)
    (ws / "visuals_report.json").write_text(json.dumps(report, indent=2),
                                            encoding="utf-8")
    return {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
