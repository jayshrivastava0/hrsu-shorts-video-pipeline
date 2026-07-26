"""Unsplash Search API. Free 50 req/hour with a developer key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import UNSPLASH_ACCESS_KEY

log = logging.getLogger(__name__)
_API = "https://api.unsplash.com/search/photos"


class UnsplashSource(BaseSource):
    name = "unsplash"
    authority_weight = 8

    def __init__(self, api_key: str | None = UNSPLASH_ACCESS_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Unsplash skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API,
                params={"query": query, "per_page": limit,
                        "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {self.api_key}"},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Unsplash search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("results", [])[:limit]:
            out.append(RawCandidate(
                source=self.name,
                url=item.get("urls", {}).get("raw", ""),
                caption=item.get("alt_description") or "",
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                extra={"author": item.get("user", {}).get("name", "")},
            ))
        return [c for c in out if c.url]
