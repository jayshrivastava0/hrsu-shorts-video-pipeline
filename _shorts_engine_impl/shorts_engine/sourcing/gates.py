"""Deterministic pre-judge gates. A hard reject here is FINAL (F2) — no
downstream signal (judge score, tier, authority) can override it."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

from shorts_engine import config

logger = logging.getLogger(__name__)


def blacklisted(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    for dom in config.DOMAIN_BLACKLIST:
        if host == dom or host.endswith("." + dom):
            return True
    return False


def resolution_ok(width: int, height: int) -> bool:
    return max(width or 0, height or 0) >= config.MIN_LONG_EDGE_PX


def _url_key(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def seen_before(url: str, seen: set[str]) -> bool:
    key = _url_key(url)
    if key in seen:
        return True
    seen.add(key)
    return False


def watermarked(img_path: Path) -> bool:
    from video_agent.sources.watermark import is_watermarked
    config.SOURCING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    flagged, reason = is_watermarked(Path(img_path), config.SOURCING_CACHE_DIR)
    if flagged:
        logger.info("watermark gate rejected %s: %s", img_path, reason)
    return flagged


def run_pre_gates(cand, seen: set[str]) -> str | None:
    """Pre-download gates in order. Returns rejection reason or None."""
    if blacklisted(cand.url):
        return "blacklisted"
    if seen_before(cand.url, seen):
        return "duplicate"
    if not resolution_ok(cand.width, cand.height):
        return "low_resolution"
    return None
