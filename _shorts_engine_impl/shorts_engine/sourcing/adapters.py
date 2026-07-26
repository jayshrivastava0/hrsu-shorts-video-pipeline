"""Tier construction over existing video_agent sources + download helper.
Bing is intentionally absent (spec §6.1: keyed/retired, no keyless path)."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from shorts_engine import config

logger = logging.getLogger(__name__)

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 hrsu-shorts-engine")}


def tier_sources(tier: str) -> list:
    """Instantiate a tier's sources; anything unconstructable is skipped.
    Class names below are CONFIRMED against the actual source files (see
    brief header) — do not re-derive them."""
    specs: list = []
    if tier == "api":
        from shorts_engine.sourcing.openverse import OpenverseSource
        from video_agent.sources.pexels import PexelsSource
        from video_agent.sources.pixabay import PixabaySource
        from video_agent.sources.unsplash import UnsplashSource
        from video_agent.sources.wikimedia import WikimediaSource
        specs = [PexelsSource, PixabaySource, UnsplashSource,
                 OpenverseSource, WikimediaSource]
    elif tier == "scrape":
        from video_agent.sources.duckduckgo import DuckDuckGoSource
        from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
        specs = [DuckDuckGoSource, GoogleImagesBrowserSource]
    out = []
    for cls in specs:
        try:
            out.append(cls())
        except Exception as e:  # noqa: BLE001 — missing key/env: skip source
            logger.warning("tier %s: source %s unavailable: %s", tier, cls, e)
    return out


def search_tier(tier: str, query: str, limit_per_source: int = 4) -> list:
    """Round-robin the tier's sources until PER_TIER_CANDIDATES collected."""
    per_source: list[list] = []
    for src in tier_sources(tier):
        try:
            per_source.append(src.search(query, limit=limit_per_source))
        except Exception as e:  # noqa: BLE001
            logger.warning("source %s search failed: %s",
                           getattr(src, "name", src), e)
    out, i = [], 0
    while len(out) < config.PER_TIER_CANDIDATES and any(per_source):
        for results in per_source:
            if i < len(results) and len(out) < config.PER_TIER_CANDIDATES:
                out.append(results[i])
        if all(i >= len(r) for r in per_source):
            break
        i += 1
    return out


def download(cand, dest_dir: Path) -> Path | None:
    """Stream a candidate to disk, verify it opens, backfill real dims."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(urlparse(cand.url).path).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    dest = dest_dir / (hashlib.sha256(cand.url.encode()).hexdigest()[:16] + ext)
    try:
        with requests.get(cand.url, stream=True, timeout=20, headers=_UA) as r:
            if r.status_code != 200:
                return None
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        with Image.open(dest) as img:
            cand.width, cand.height = img.size
        return dest
    except Exception as e:  # noqa: BLE001 — bad candidate, ladder moves on
        logger.info("download failed for %s: %s", cand.url, e)
        dest.unlink(missing_ok=True)
        return None
