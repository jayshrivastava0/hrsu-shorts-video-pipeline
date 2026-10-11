"""Retrieval for the claim verifier: ranked, cached, never-raising web evidence."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.retrieval.fetch import fetch_text
from shorts_engine.retrieval.passages import best_passages, key_terms_query
from shorts_engine.retrieval.search import SearchHit, web_search
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)

__all__ = ["Passage", "Retriever", "SearchHit", "authority_queries", "key_terms_query",
           "query_variants", "source_kind"]

_AUTHORITATIVE = tuple(config.PAPER_DOMAINS) + tuple(config.STANDARD_DOMAINS)
_RANK = {"blog_citation": 0, "authoritative": 1, "web": 2}


@dataclass(frozen=True)
class Passage:
    url: str
    text: str
    kind: str  # "blog_citation" | "authoritative" | "web"


def _safe_parse(url: str):
    """urlparse that returns None instead of raising ValueError (e.g. "http://[bad")."""
    try:
        return urlparse(url.strip())
    except ValueError:
        return None


def source_kind(url: str) -> str:
    """'authoritative' for government/edu/standards/paper hosts, else 'web'."""
    parts = _safe_parse(url)
    try:
        host = ((parts.hostname if parts else "") or "").lower()
    except ValueError:
        host = ""
    if host.endswith((".gov", ".edu")):
        return "authoritative"
    if any(host == d or host.endswith("." + d) for d in _AUTHORITATIVE):
        return "authoritative"
    return "web"


def _norm_url(url: str) -> str:
    """Dedupe/cache key: no fragment, no trailing slash, lowercase scheme and host."""
    parts = _safe_parse(url)
    if parts is None:
        return url.strip().lower()
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


def query_variants(claim_text: str) -> list[str]:
    """Search queries tried in order for a claim (spec section 12): the claim itself,
    its key terms, and its key terms plus "why how" (finds mechanism explanations).
    Empty queries and duplicates (compared after normalize_for_match, so a query that
    differs only in case is not searched twice) are skipped."""
    terms = key_terms_query(claim_text)
    out: list[str] = []
    seen: set[str] = set()
    for q in (claim_text.strip(), terms, f"{terms} why how" if terms else ""):
        key = normalize_for_match(q)
        if q and key not in seen:
            seen.add(key)
            out.append(q)
    return out


# Host groups for authority-biased searches (small groups keep the queries short).
AUTHORITY_GROUPS = (
    ("epa.gov", "osha.gov", "cdc.gov", "usgs.gov", "nist.gov"),
    ("echa.europa.eu", "eur-lex.europa.eu", "who.int", "oecd.org", "fao.org"),
    ("pubchem.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "acs.org", "rsc.org"),
    ("sciencedirect.com", "springer.com", "tandfonline.com", "wiley.com", "iso.org"),
)


def authority_queries(claim_text: str) -> list[str]:
    """Search queries biased toward authorities: the claim's key terms plus a `site:`
    group each (government/regulators, EU/international, chemistry databases and
    societies, publishers/standards). Empty when the claim has no key terms."""
    terms = key_terms_query(claim_text)
    if not terms:
        return []
    return [f"{terms} " + " OR ".join(f"site:{h}" for h in group)
            for group in AUTHORITY_GROUPS]


def _default_search(query: str) -> list[SearchHit]:
    # raise_on_failure: a failed search must be distinguishable from an empty one
    return web_search(query, raise_on_failure=True)


class Retriever:
    """Finds evidence passages for a claim. Blog citations first, then ranked web hits.

    Never raises: search/fetch failures yield fewer (or zero) passages. Query variants
    (`query_variants`) are tried in order until one yields passages; passages of
    different variants are never merged, and they are always scored against the claim
    text. Results are cached per normalised claim so repair rounds do not repeat
    searches -- except an EMPTY result caused by a search failure, which is not cached
    so a later call retries. (A fetch failure is only detected when an injected
    `fetch_fn` raises; the default fetch_text never raises and returns "" on failure,
    so in production only search failures leave a result uncached.)
    `last_search_failed` says whether the last uncached retrieve() saw a search failure.
    Every URL is fetched at most once per Retriever (failed/empty pages cached too).
    The fetch budget
    (config.RETRIEVAL_MAX_FETCHES, per variant; cached pages cost nothing) is shared:
    at most half goes to the blog citations that best match the claim, the rest to
    ranked search hits.
    """

    def __init__(self, citation_urls: list[str], *, search_fn=None, fetch_fn=None) -> None:
        self._citations = list(citation_urls)
        self._search = search_fn or _default_search
        self._fetch = fetch_fn or fetch_text
        self._cache: dict[str, list[Passage]] = {}
        self._pages: dict[str, str] = {}
        self._failed_urls: set[str] = set()
        self.last_search_failed = False

    def _fetch_once(self, url: str) -> str:
        key = _norm_url(url)
        if key not in self._pages:
            try:
                self._pages[key] = self._fetch(url) or ""
            except Exception as exc:
                logger.warning(f"fetch failed for {url!r}: {exc}")
                self._pages[key] = ""
                self._failed_urls.add(key)
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
        path_of = lambda u: ((_safe_parse(u).netloc + " " + _safe_parse(u).path)  # noqa: E731
                             if _safe_parse(u) else u)
        scored = sorted(enumerate(unique),
                        key=lambda iu: (-len(claim_words & _content_words(path_of(iu[1]))),
                                        iu[0]))
        return [u for _, u in scored[:limit]]

    def retrieve(self, claim_text: str) -> list[Passage]:
        key = normalize_for_match(claim_text)
        if key in self._cache:
            return self._cache[key]
        passages: list[Passage] = []
        search_failed = fetch_failed = False
        try:
            for query in query_variants(claim_text):
                found, search_ok, fetch_ok = self._retrieve(query, claim_text)
                search_failed = search_failed or not search_ok
                fetch_failed = fetch_failed or not fetch_ok
                if found:
                    passages = found
                    break
        except Exception as exc:
            logger.warning(f"retrieval failed for {claim_text!r}: {exc}")
            passages, fetch_failed = [], True
        self.last_search_failed = search_failed
        if passages or not (search_failed or fetch_failed):
            self._cache[key] = passages
        return passages

    def _retrieve(self, query: str, claim_text: str) -> tuple[list[Passage], bool, bool]:
        """Passages for one query variant -> (passages, search_ok, fetch_ok)."""
        search_ok = True
        try:
            hits = list(self._search(query))
        except Exception as exc:
            logger.warning(f"search failed for {query!r}: {exc}")
            hits, search_ok = [], False
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
        fetch_ok = not any(_norm_url(u) in self._failed_urls for u, _k in candidates)
        return passages[:config.RETRIEVAL_MAX_PASSAGES], search_ok, fetch_ok

    def retrieve_authoritative(self, claim_text: str) -> list[Passage]:
        """Passages for `claim_text` from AUTHORITATIVE hosts only (source_kind), via the
        `site:`-biased authority queries tried in order until one yields passages.
        Never raises; cached per claim like `retrieve` (a failure-caused empty result is
        not cached); each URL is fetched at most once and the per-query fetch budget is
        config.RETRIEVAL_MAX_FETCHES."""
        key = "auth:" + normalize_for_match(claim_text)
        if key in self._cache:
            return self._cache[key]
        passages: list[Passage] = []
        failed = False
        try:
            for query in authority_queries(claim_text):
                try:
                    hits = list(self._search(query))
                except Exception as exc:
                    logger.warning(f"authority search failed for {query!r}: {exc}")
                    failed = True
                    continue
                urls, seen = [], set()
                for h in hits:
                    nu = _norm_url(h.url)
                    if source_kind(h.url) == "authoritative" and nu not in seen:
                        seen.add(nu)
                        urls.append(h.url)
                found: list[Passage] = []
                for url in urls[:config.RETRIEVAL_MAX_FETCHES]:
                    text = self._fetch_once(url)
                    failed = failed or _norm_url(url) in self._failed_urls
                    found.extend(Passage(url, p, "authoritative")
                                 for p in best_passages(text, claim_text, 2))
                if found:
                    passages = found[:config.RETRIEVAL_MAX_PASSAGES]
                    break
        except Exception as exc:
            logger.warning(f"authoritative retrieval failed for {claim_text!r}: {exc}")
            failed = True
        if passages or not failed:
            self._cache[key] = passages
        return passages
