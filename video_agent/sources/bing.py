"""Bing Image Search API v7. Free 1k req/mo with a key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import BING_API_KEY

log = logging.getLogger(__name__)
_API = "https://api.bing.microsoft.com/v7.0/images/search"


class BingSource(BaseSource):
    name = "bing"
    authority_weight = 5

    def __init__(self, api_key: str | None = BING_API_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Bing skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API, params={"q": query, "count": limit,
                              "safeSearch": "Strict",
                              "imageType": "Photo", "size": "Large"},
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Bing search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("value", [])[:limit]:
            url = item.get("contentUrl", "")
            if not url:
                continue
            out.append(RawCandidate(
                source=self.name, url=url,
                caption=item.get("name", ""),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
            ))
        return out
