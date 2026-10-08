"""Retrieval for the claim verifier: ranked, cached, never-raising web evidence."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.retrieval.fetch import fetch_text
from shorts_engine.retrieval.passages import best_passages
from shorts_engine.retrieval.search import SearchHit, web_search
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)

__all__ = ["Passage", "Retriever", "SearchHit", "source_kind"]

_AUTHORITATIVE = tuple(config.PAPER_DOMAINS) + tuple(config.STANDARD_DOMAINS)
_RANK = {"blog_citation": 0, "authoritative": 1, "web": 2}


@dataclass(frozen=True)
class Passage:
    url: str
    text: str
    kind: str  # "blog_citation" | "authoritative" | "web"


def source_kind(url: str) -> str:
    """'authoritative' for government/edu/standards/paper hosts, else 'web'."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith((".gov", ".edu")):
        return "authoritative"
    if any(host == d or host.endswith("." + d) for d in _AUTHORITATIVE):
        return "authoritative"
    return "web"


class Retriever:
    """Finds evidence passages for a claim. Blog citations first, then ranked web hits.

    Never raises: search/fetch failures yield fewer (or zero) passages. Results are
    cached per normalised claim so repair rounds do not repeat searches.
    """

    def __init__(self, citation_urls: list[str], *, search_fn=None, fetch_fn=None) -> None:
        self._citations = list(citation_urls)
        self._search = search_fn or web_search
        self._fetch = fetch_fn or fetch_text
        self._cache: dict[str, list[Passage]] = {}

    def retrieve(self, claim_text: str) -> list[Passage]:
        key = normalize_for_match(claim_text)
        if key not in self._cache:
            try:
                self._cache[key] = self._retrieve(claim_text)
            except Exception as exc:
                logger.warning(f"retrieval failed for {claim_text!r}: {exc}")
                self._cache[key] = []
        return self._cache[key]

    def _retrieve(self, claim_text: str) -> list[Passage]:
        try:
            hits = list(self._search(claim_text))
        except Exception as exc:
            logger.warning(f"search failed for {claim_text!r}: {exc}")
            hits = []
        candidates = [(u, "blog_citation") for u in self._citations]
        ranked = sorted(((h.url, source_kind(h.url)) for h in hits),
                        key=lambda uk: _RANK[uk[1]])
        candidates += [c for c in ranked if c[0] not in self._citations]
        passages: list[Passage] = []
        for url, kind in candidates[:config.RETRIEVAL_MAX_FETCHES]:
            if len(passages) >= config.RETRIEVAL_MAX_PASSAGES:
                break
            text = self._fetch(url)
            for p in best_passages(text, claim_text, 2):
                passages.append(Passage(url, p, kind))
        passages.sort(key=lambda p: _RANK[p.kind])  # stable: keeps page order within a kind
        return passages[:config.RETRIEVAL_MAX_PASSAGES]
