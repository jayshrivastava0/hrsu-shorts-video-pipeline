"""Retrieval for the claim verifier: ranked, cached, never-raising web evidence."""
from __future__ import annotations

import logging
import re
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


def _norm_url(url: str) -> str:
    """Dedupe/cache key: no fragment, no trailing slash, lowercase scheme and host."""
    parts = urlparse(url.strip())
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"the", "and", "for", "with", "that", "this", "from", "are", "was",
                        "into", "its", "www", "com", "org", "html", "htm", "php", "https",
                        "http", "index"})


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


class Retriever:
    """Finds evidence passages for a claim. Blog citations first, then ranked web hits.

    Never raises: search/fetch failures yield fewer (or zero) passages. Results are
    cached per normalised claim so repair rounds do not repeat searches, and every URL
    is fetched at most once per Retriever (empty results cached too). The per-claim
    fetch budget (config.RETRIEVAL_MAX_FETCHES) is shared: at most half goes to the
    blog citations that best match the claim, the rest to ranked search hits.
    """

    def __init__(self, citation_urls: list[str], *, search_fn=None, fetch_fn=None) -> None:
        self._citations = list(citation_urls)
        self._search = search_fn or web_search
        self._fetch = fetch_fn or fetch_text
        self._cache: dict[str, list[Passage]] = {}
        self._pages: dict[str, str] = {}

    def _fetch_once(self, url: str) -> str:
        key = _norm_url(url)
        if key not in self._pages:
            try:
                self._pages[key] = self._fetch(url) or ""
            except Exception as exc:
                logger.warning(f"fetch failed for {url!r}: {exc}")
                self._pages[key] = ""
        return self._pages[key]

    def _pick_citations(self, claim_text: str, limit: int) -> list[str]:
        """Citations (deduped) ordered by content-word overlap of their URL with the claim;
        ties keep the blog's original order."""
        seen: set[str] = set()
        unique = []
        for u in self._citations:
            if _norm_url(u) not in seen:
                seen.add(_norm_url(u))
                unique.append(u)
        claim_words = _content_words(normalize_for_match(claim_text))
        path_of = lambda u: (urlparse(u).netloc + " " + urlparse(u).path)  # noqa: E731
        scored = sorted(enumerate(unique),
                        key=lambda iu: (-len(claim_words & _content_words(path_of(iu[1]))),
                                        iu[0]))
        return [u for _, u in scored[:limit]]

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
        budget = config.RETRIEVAL_MAX_FETCHES
        ordered_cites = self._pick_citations(claim_text, len(self._citations))
        cite_keys = {_norm_url(u) for u in self._citations}
        ranked: list[tuple[str, str]] = []
        seen: set[str] = set(cite_keys)        # never refetch a citation URL as a hit
        for url, kind in sorted(((h.url, source_kind(h.url)) for h in hits),
                                key=lambda uk: _RANK[uk[1]]):
            if _norm_url(url) not in seen:
                seen.add(_norm_url(url))
                ranked.append((url, kind))
        n_cites = min(len(ordered_cites), budget // 2)
        n_hits = min(len(ranked), budget - n_cites)
        n_cites = min(len(ordered_cites), budget - n_hits)  # unused hit slots go back
        candidates = ([(u, "blog_citation") for u in ordered_cites[:n_cites]]
                      + ranked[:n_hits])
        passages: list[Passage] = []
        for url, kind in candidates:
            text = self._fetch_once(url)
            for p in best_passages(text, claim_text, 2):
                passages.append(Passage(url, p, kind))
        passages.sort(key=lambda p: _RANK[p.kind])  # stable: keeps page order within a kind
        return passages[:config.RETRIEVAL_MAX_PASSAGES]
