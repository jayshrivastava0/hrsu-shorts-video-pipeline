"""Pexels Photo Search API. Free 200 req/hour with API key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import PEXELS_API_KEY

log = logging.getLogger(__name__)
_API = "https://api.pexels.com/v1/search"


class PexelsSource(BaseSource):
    name = "pexels"
    authority_weight = 8

    def __init__(self, api_key: str | None = PEXELS_API_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Pexels skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API,
                params={"query": query, "per_page": limit,
                        "orientation": "landscape", "size": "large"},
                headers={"Authorization": self.api_key},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Pexels search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("photos", [])[:limit]:
            urls = item.get("src", {})
            url = urls.get("large2x", "") or urls.get("original", "")
            out.append(RawCandidate(
                source=self.name,
                url=url,
                caption=item.get("alt", "") or item.get("photographer", ""),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                extra={"photographer": item.get("photographer", "")},
            ))
        return [c for c in out if c.url]
