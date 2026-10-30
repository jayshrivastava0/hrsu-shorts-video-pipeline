"""Discover published blog post URLs (index pages first, sitemap as fallback).

Never raises: any network/parse failure yields [] (the caller treats that as "nothing to do").
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

BLOG_BASE = "https://hrsuindore.com/blog/"
SITEMAP_URL = "https://hrsuindore.com/sitemap.xml"
RESERVED_SLUGS = {"page", "tag", "tags", "category", "categories", "feed", "rss", "search", "author"}
_SLUG = r"[a-z0-9][a-z0-9\-_]*"
_URL_RE = re.compile(
    r"(?:https?://(?:www\.)?hrsuindore\.com)?/blog/(" + _SLUG + r")/?(?=[\"'<\s#?]|$)",
    re.IGNORECASE,
)


def canonical_url(slug: str) -> str:
    return f"{BLOG_BASE}{slug.lower()}/"


def parse_post_urls(text: str) -> list[str]:
    """Extract canonical post URLs from index HTML or sitemap XML, in document order, de-duplicated."""
    seen: dict[str, None] = {}
    for m in _URL_RE.finditer(text or ""):
        slug = m.group(1).lower()
        if slug in RESERVED_SLUGS:
            continue
        seen.setdefault(canonical_url(slug), None)
    return list(seen)


def _default_fetch(url: str) -> str:
    import requests
    resp = requests.get(url, timeout=30, headers={"User-Agent": "HRSU-video-scheduler/1.0"})
    resp.raise_for_status()
    return resp.text


def fetch_post_urls(fetch: Optional[Callable[[str], str]] = None, max_pages: int = 3) -> list[str]:
    """Blog post URLs in index order (newest first on the live blog). [] on total failure."""
    fetch = fetch or _default_fetch
    urls: list[str] = []
    for page in range(1, max_pages + 1):
        page_url = BLOG_BASE if page == 1 else f"{BLOG_BASE}page/{page}/"
        try:
            found = parse_post_urls(fetch(page_url))
        except Exception as exc:
            logger.warning("discover: %s failed: %s", page_url, exc)
            break
        new = [u for u in found if u not in urls]
        if not new:
            break
        urls.extend(new)
    if urls:
        return urls
    try:
        return parse_post_urls(fetch(SITEMAP_URL))
    except Exception as exc:
        logger.warning("discover: sitemap fallback failed: %s", exc)
        return []


def fetch_all_post_urls(fetch: Optional[Callable[[str], str]] = None, max_pages: int = 100) -> list[str]:
    """Exhaustive listing for seeding: every index page plus the sitemap, de-duplicated (index order first)."""
    fetch = fetch or _default_fetch
    urls = fetch_post_urls(fetch, max_pages=max_pages)
    try:
        urls += [u for u in parse_post_urls(fetch(SITEMAP_URL)) if u not in urls]
    except Exception as exc:
        logger.warning("discover: sitemap unavailable while seeding: %s", exc)
    return urls
