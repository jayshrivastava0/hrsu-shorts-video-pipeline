"""Pixabay image + video search. Free, royalty-free, no attribution required.

Docs: https://pixabay.com/api/docs/
Rate limit: 100 req/hour (images), 30 req/hour (videos).
"""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import PIXABAY_API_KEY

log = logging.getLogger(__name__)
_IMG_API = "https://pixabay.com/api/"
_VID_API = "https://pixabay.com/api/videos/"


class PixabaySource(BaseSource):
    name = "pixabay"
    authority_weight = 8  # same tier as Pexels

    def __init__(self, api_key: str | None = PIXABAY_API_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Pixabay skipped — no PIXABAY_API_KEY set")
            return []
        results = self._search_images(query, limit) + self._search_videos(query, limit // 2)
        return results[:limit]

    def _search_images(self, query: str, limit: int) -> list[RawCandidate]:
        try:
            r = requests.get(_IMG_API, timeout=15, params={
                "key": self.api_key, "q": query,
                "image_type": "photo", "orientation": "horizontal",
                "min_width": 1280, "per_page": limit,
                "safesearch": "true", "order": "popular",
            })
            r.raise_for_status()
        except Exception as e:
            log.warning("Pixabay image search failed for %r: %s", query, e)
            return []
        out = []
        for hit in r.json().get("hits", [])[:limit]:
            url = hit.get("largeImageURL") or hit.get("webformatURL", "")
            if not url:
                continue
            out.append(RawCandidate(
                source=self.name,
                url=url,
                caption=hit.get("tags", ""),
                width=int(hit.get("imageWidth", 0)),
                height=int(hit.get("imageHeight", 0)),
            ))
        return out

    def _search_videos(self, query: str, limit: int) -> list[RawCandidate]:
        if limit < 1:
            return []
        try:
            r = requests.get(_VID_API, timeout=15, params={
                "key": self.api_key, "q": query,
                "video_type": "film", "per_page": limit,
                "safesearch": "true", "order": "popular",
            })
            r.raise_for_status()
        except Exception as e:
            log.warning("Pixabay video search failed for %r: %s", query, e)
            return []
        out = []
        for hit in r.json().get("hits", [])[:limit]:
            videos = hit.get("videos", {})
            # Prefer medium (1280px) or large (1920px)
            v = videos.get("large") or videos.get("medium") or {}
            url = v.get("url", "")
            if not url:
                continue
            out.append(RawCandidate(
                source=self.name,
                url=url,
                caption=hit.get("tags", ""),
                is_clip=True,
                duration_s=float(hit.get("duration", 0)),
            ))
        return out
