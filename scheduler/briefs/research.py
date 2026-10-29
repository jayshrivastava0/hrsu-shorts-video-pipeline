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


def gather_sources(
    brief_text: str,
    search: Callable[[str], list[dict]] | None = None,
    limit: int = 12,
) -> list[Source]:
    """Run one search for the brief and return up to `limit` deduplicated sources."""
    raw = search(brief_text) if search is not None else _default_search(brief_text, limit)
    sources: list[Source] = []
    seen: set[str] = set()
    for hit in raw:
        url = (hit.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(Source(
            id=f"S{len(sources) + 1}",
            title=str(hit.get("title") or ""),
            url=url,
            snippet=str(hit.get("snippet") or ""),
            publisher=publisher_of(url),
        ))
        if len(sources) >= limit:
            break
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
        verdicts.append(ClaimVerdict(
            claim=claim,
            supported=bool(result.get("supported", False)),
            source_ids=[str(i) for i in result.get("source_ids") or []],
        ))
    return verdicts
