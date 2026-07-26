"""DuckDuckGo image search via the duckduckgo-search package."""
from __future__ import annotations
import logging
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

try:
    from ddgs import DDGS           # new package name (pip install ddgs)
except ImportError:
    try:
        from duckduckgo_search import DDGS   # legacy name fallback
    except ImportError:
        DDGS = None


class DuckDuckGoSource(BaseSource):
    name = "duckduckgo"
    authority_weight = 5

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if DDGS is None:
            log.warning("ddgs not installed; run: pip install ddgs")
            return []
        out = []
        try:
            with DDGS() as ddgs:
                for item in ddgs.images(query, max_results=limit, safesearch="moderate"):
                    url = item.get("image", "")
                    if not url:
                        continue
                    out.append(RawCandidate(
                        source=self.name, url=url,
                        caption=item.get("title", ""),
                        width=int(item.get("width", 0)),
                        height=int(item.get("height", 0)),
                    ))
        except Exception as e:
            log.warning("DDG search failed for %r: %s", query, e)
            return []
        return out
