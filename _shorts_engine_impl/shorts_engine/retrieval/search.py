"""Text web search (never raises). The repo's only other DuckDuckGo code is image search."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from shorts_engine import config

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS  # new package name
except ImportError:
    try:
        from duckduckgo_search import DDGS  # legacy name (in requirements.txt)
    except ImportError:
        DDGS = None


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


def _default_factory():
    return DDGS() if DDGS is not None else None


def web_search(query: str, max_results: int = config.RETRIEVAL_MAX_RESULTS, *,
               ddgs_factory=None, sleep=time.sleep) -> list[SearchHit]:
    """Search the web for `query`. Returns [] on any failure after bounded retries."""
    factory = ddgs_factory or _default_factory
    for attempt in range(1, config.RETRIEVAL_SEARCH_RETRIES + 1):
        try:
            client = factory()
            if client is None:
                logger.warning("ddgs not installed; web search unavailable")
                return []
            raw = client.text(query, max_results=max_results)
            return [SearchHit(r.get("href", ""), r.get("title", ""), r.get("body", ""))
                    for r in raw if r.get("href")]
        except Exception as exc:  # network, rate limit, library errors
            logger.warning(f"web_search attempt {attempt} failed for {query!r}: {exc}")
            if attempt < config.RETRIEVAL_SEARCH_RETRIES:
                sleep(config.LLM_RETRY_DELAY_S * attempt)
    return []
