"""Wikimedia Commons API. Free, no key required. Best for diagrams + science."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)
_API = "https://commons.wikimedia.org/w/api.php"
_HEADERS = {"User-Agent": "HRSU-VideoBot/2.0 (sujay@swastika.co.in)"}


class WikimediaSource(BaseSource):
    name = "wikimedia"
    authority_weight = 10

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        try:
            r = requests.get(_API, headers=_HEADERS, timeout=15, params={
                "action": "query", "format": "json", "list": "search",
                "srsearch": query, "srnamespace": 6,    # File: namespace
                "srlimit": limit * 2,
            })
            r.raise_for_status()
            titles = [s["title"] for s in r.json().get("query", {})
                                              .get("search", [])
                      if s["title"].lower().endswith(
                          (".jpg", ".jpeg", ".png", ".svg"))][:limit]
        except Exception as e:
            log.warning("Wikimedia search failed for %r: %s", query, e)
            return []

        if not titles:
            return []

        try:
            r = requests.get(_API, headers=_HEADERS, timeout=15, params={
                "action": "query", "format": "json",
                "titles": "|".join(titles), "prop": "imageinfo",
                "iiprop": "url|size|extmetadata",
            })
            r.raise_for_status()
        except Exception as e:
            log.warning("Wikimedia imageinfo failed: %s", e)
            return []

        out = []
        for page in r.json().get("query", {}).get("pages", {}).values():
            ii = page.get("imageinfo", [{}])[0]
            url = ii.get("url", "")
            if not url:
                continue
            meta = ii.get("extmetadata", {})
            caption = meta.get("ImageDescription", {}).get("value", "")
            caption = caption or page.get("title", "")
            out.append(RawCandidate(
                source=self.name,
                url=url,
                caption=caption,
                width=int(ii.get("width", 0)),
                height=int(ii.get("height", 0)),
            ))
        return out
