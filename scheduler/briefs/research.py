"""Source gathering and claim verification for brief-driven videos.

Search and LLM are injected so callers (and tests) never hit the network by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

_TWO_PART_SLDS = {"co", "com", "org", "gov", "ac", "net", "edu"}


@dataclass
class Source:
    id: str
    title: str
    url: str
    snippet: str
    publisher: str


@dataclass
class ClaimVerdict:
    claim: str
    supported: bool
    source_ids: list[str] = field(default_factory=list)


def publisher_of(url: str) -> str:
    """Registrable domain of a URL, e.g. https://www.epa.gov/x -> epa.gov."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _TWO_PART_SLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _default_search(query: str, limit: int) -> list[dict]:
    # Imported lazily so importing this module never pulls in the search client.
    from shorts_engine.retrieval.search import web_search

    hits = web_search(query, max_results=limit)
    return [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]


# Publishers a procurement manager or executive would accept as evidence, beyond .gov/.edu and the standards and
# paper domains the engine already treats as authoritative. HRSU's own site is included (its Market Pulse index).
# Edit this list to change which sources market and ESG videos may cite on screen.
TRUSTED_PUBLISHERS = frozenset({
    "hrsuindore.com", "iea.org", "eia.gov", "worldbank.org", "oecd.org", "imf.org", "fao.org", "unctad.org",
    "reuters.com", "ft.com", "bloomberg.com", "wsj.com", "economist.com",
    "spglobal.com", "icis.com", "argusmedia.com", "imarcgroup.com", "chemanalyst.com", "wto.org",
})
TRUSTED_SITE_QUERY_DOMAINS = ("eia.gov", "iea.org", "hrsuindore.com")


def is_trusted(url: str) -> bool:
    """True for a trusted publisher, or any host the engine classifies as authoritative (gov, edu, standards, papers)."""
    from shorts_engine.retrieval import source_kind

    publisher = publisher_of(url)
    return publisher in TRUSTED_PUBLISHERS or any(
        publisher.endswith("." + d) for d in TRUSTED_PUBLISHERS) or source_kind(url) == "authoritative"


def trusted_site_queries(base_query: str) -> list[str]:
    """One site-restricted query per trusted domain, built from the first words of `base_query`."""
    words = " ".join(base_query.split()[:7])
    return [f"{words} site:{d}" for d in TRUSTED_SITE_QUERY_DOMAINS] if words else []


MAX_QUERIES = 3
_QUERY_WORDS = 12
_QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}


def fallback_queries(brief_text: str) -> list[str]:
    """Deterministic short queries: the first and the second `_QUERY_WORDS` words of the brief."""
    words = brief_text.split()
    chunks = [words[i:i + _QUERY_WORDS] for i in range(0, min(len(words), 2 * _QUERY_WORDS), _QUERY_WORDS)]
    return [" ".join(c) for c in chunks if c] or [brief_text.strip()[:100]]


def make_queries(brief_text: str, llm: Callable[[str, str, dict], dict] | None = None) -> list[str]:
    """Up to MAX_QUERIES short web-search queries for the brief; never raises.

    `llm(system, prompt, schema) -> dict` proposes them; any failure or malformed reply falls back to
    `fallback_queries` (a whole 300-character brief makes a poor, slow search query).
    """
    if llm is not None:
        try:
            reply = llm(
                "You write web search queries. Reply with JSON only.",
                f"Write up to {MAX_QUERIES} short web search queries (at most 10 words each) that would find "
                f"factual, citable sources for this video brief:\n{brief_text}",
                _QUERY_SCHEMA,
            )
            raw = reply.get("queries") if isinstance(reply, dict) else None
            queries = [q.strip()[:100] for q in raw if isinstance(q, str) and q.strip()] if isinstance(raw, list) else []
            if queries:
                return queries[:MAX_QUERIES]
        except Exception:
            pass
    return fallback_queries(brief_text)


def gather_sources(
    brief_text: str,
    search: Callable[[str], list[dict]] | None = None,
    limit: int = 12,
    queries: list[str] | None = None,
    trusted_only: bool = False,
) -> list[Source]:
    """Search each query (default: short queries derived from the brief) and return up to `limit` deduplicated sources.

    A query whose search raises is skipped; the others still contribute. With `trusted_only`, hits from
    publishers that are not trusted (see `is_trusted`) are dropped before ids are assigned.
    """
    raw: list[dict] = []
    for query in (queries or fallback_queries(brief_text)):
        try:
            raw += search(query) if search is not None else _default_search(query, limit)
        except Exception:
            continue
    sources: list[Source] = []
    seen: set[str] = set()
    for hit in raw:
        url = (hit.get("url") or "").strip()
        if not url or url in seen or len(sources) >= limit:
            continue
        if trusted_only and not is_trusted(url):
            continue
        seen.add(url)
        sources.append(Source(
            id=f"S{len(sources) + 1}",
            title=str(hit.get("title") or ""),
            url=url,
            snippet=str(hit.get("snippet") or ""),
            publisher=publisher_of(url),
        ))
    return sources


def _claim_prompt(claim: str, sources: list[Source]) -> str:
    lines = [
        "Decide whether the sources below support the claim.",
        "Answer with supported (true/false) and the ids of the sources that support it.",
        "",
        f"Claim: {claim}",
        "",
        "Sources:",
    ]
    for s in sources:
        lines.append(f"[{s.id}] {s.title} ({s.publisher}): {s.snippet}")
    return "\n".join(lines)


def verify_claims(
    claims: list[str],
    sources: list[Source],
    llm: Callable[[str], dict],
) -> list[ClaimVerdict]:
    """Ask `llm` once per claim whether the sources support it."""
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        result = llm(_claim_prompt(claim, sources)) or {}
        known = {s.id for s in sources}
        ids = [str(i) for i in result.get("source_ids") or [] if str(i) in known]
        supported = result.get("supported") is True and bool(ids)
        verdicts.append(ClaimVerdict(
            claim=claim,
            supported=supported,
            source_ids=ids if supported else [],
        ))
    return verdicts
