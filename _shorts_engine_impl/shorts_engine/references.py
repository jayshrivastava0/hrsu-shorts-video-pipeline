"""Claim -> authoritative-source attribution. Pure logic: no LLM, no network.

Maps every supported claim of the explanation plan to the real sources behind it:

* a `blog_quote` claim: the citation markers the blog attaches to the quoted sentence;
  the canonical text writes them as bare digits glued to the preceding word or closing
  paren ("...explosive properties5. How..." / "calcium nitrate1 disrupts" / "(ECHA)3"),
  so a marker is 1-2 digits directly after a lowercase letter or ")" and before
  whitespace/punctuation/end, and only counts if `post["citations"]` has that marker;
* a `source` claim: its `support.url`;
* a `reasoning` claim: inherits its premises' sources, only when every premise is sourced.

Outputs (see `write_outputs`): references.json (claims/sources/grade/chips) and
video_description.txt. CLI: `python -m shorts_engine.references <workspace>`.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.retrieval import source_kind
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)

STRONG_SHARE = 0.70
ADEQUATE_SHARE = 0.40
GRADE_SENTENCE = "Every claim in this video was checked against its source; references above."

# Hosts that are never treated as authority (encyclopedias, forums, blog platforms).
WEAK_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wikihow.com", "quora.com", "reddit.com",
    "medium.com", "blogspot.com", "wordpress.com", "substack.com", "tumblr.com",
    "stackexchange.com", "stackoverflow.com", "answers.com", "yahoo.com", "facebook.com",
    "linkedin.com", "youtube.com", "britannica.com", "fandom.com", "pinterest.com",
)

# Exact host (or suffix) -> chip label.
_LABELS = (
    ("eur-lex.europa.eu", "EUR-Lex"),
    ("echa.europa.eu", "ECHA"),
    ("chem.echa.europa.eu", "ECHA"),
    ("epa.gov", "US EPA"),
    ("who.int", "WHO"),
    ("nist.gov", "NIST"),
    ("osha.gov", "OSHA"),
    ("cdc.gov", "CDC"),
    ("usgs.gov", "USGS"),
    ("iso.org", "ISO"),
    ("europa.eu", "European Union"),
    ("oecd.org", "OECD"),
    ("worldbank.org", "World Bank"),
    ("fao.org", "FAO"),
    ("unep.org", "UNEP"),
)
# Second-level public suffixes where the registrable domain has 3 labels.
_SLD = {"co", "com", "gov", "ac", "org", "net", "edu"}

_MARKER_RE = re.compile(r"(?<=[a-z)])(\d{1,2})(?=[\s.,;:)\]]|$)")
_TIER_RANK = {"authoritative": 3, "weak": 2, "blog": 1, "none": 0}
_NUM_RE = re.compile(r"\d")


# ── hosts / labels / tiers ───────────────────────────────────────────────────
def _host(url: str) -> str:
    try:
        h = (urlparse((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _host_in(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_weak_host(url: str) -> bool:
    return _host_in(_host(url), WEAK_DOMAINS)


def publisher_label(url: str) -> str:
    """Short publisher label: curated names for known authorities, else the registrable
    domain (usgs.gov -> 'usgs.gov', pubs.acs.org -> 'acs.org')."""
    host = _host(url)
    for dom, label in _LABELS:
        if host == dom or host.endswith("." + dom):
            return label
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SLD and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def source_tier(url: str, kind: str | None) -> str:
    """'authoritative' for standards/papers/government/intergovernmental/academic hosts,
    else 'weak' (encyclopedias, forums, blogs and any unknown web page)."""
    if not url or is_weak_host(url):
        return "weak"
    if kind in ("standard", "paper") or source_kind(url) == "authoritative":
        return "authoritative"
    host = _host(url)
    if _host_in(host, tuple(config.PAPER_DOMAINS) + tuple(config.STANDARD_DOMAINS)):
        return "authoritative"
    return "weak"


def _norm_url(url: str) -> str:
    return (url or "").strip().split("#")[0].rstrip("/").lower()


# ── marker extraction ────────────────────────────────────────────────────────
def strip_markers(canonical: str, valid: set[int]) -> tuple[str, list[tuple[int, int]]]:
    """Canonical text without marker-shaped digits + [(position in stripped text, marker)]
    for those that are real citation numbers (so quotes still locate when a post has no
    matching citation entry)."""
    out: list[str] = []
    marks: list[tuple[int, int]] = []
    last = 0
    removed = 0
    for m in _MARKER_RE.finditer(canonical):
        n = int(m.group(1))
        out.append(canonical[last:m.start()])
        if n in valid:
            marks.append((m.start() - removed, n))
        removed += m.end() - m.start()
        last = m.end()
    out.append(canonical[last:])
    return "".join(out), marks


def _quote_pattern(quote: str):
    toks = normalize_for_match(quote).split(" ")
    toks = [t for t in toks if t]
    if not toks:
        return None
    return re.compile(r"\s+".join(re.escape(t) for t in toks), re.IGNORECASE)


def _norm_canon(text: str) -> str:
    # Length-preserving mirror of normalize_for_match's character mapping.
    t = text.replace("\xa0", " ")
    return t.translate(str.maketrans({"–": "-", "—": "-", "−": "-",
                                      "‘": "'", "’": "'", "“": '"', "”": '"'}))


def markers_for_quote(quote: str, clean: str, marks: list[tuple[int, int]]) -> list[int] | None:
    """Markers attached to the quoted sentence(s), in order: those inside the span, plus
    any marker directly after it. None when the quote cannot be located."""
    haystack = _norm_canon(clean)
    m = None
    # the planner copies sentences with their citation digits ("...directions1."), while
    # `clean` has them stripped: try the quote as written, then without marker digits
    for q in (quote, _MARKER_RE.sub("", quote)):
        pat = _quote_pattern(_norm_canon(q))
        if pat is None:
            return None
        m = pat.search(haystack)
        if m is not None:
            break
    if m is None:
        return None
    start, end = m.start(), m.end()
    found: list[int] = []
    for pos, n in marks:
        inside = start < pos <= end
        after = pos > end and not clean[end:pos].strip()
        if (inside or after) and n not in found:
            found.append(n)
    return found


# ── main build ───────────────────────────────────────────────────────────────
def _supported(step: dict) -> list[dict]:
    return [c for c in step.get("claims", []) if c.get("verdict") == "supported"]


def build_references(plan: dict, post: dict, canonical: str) -> dict:
    cites = {c.get("marker"): c for c in (post.get("citations") or []) if c.get("marker") is not None}
    by_url = {_norm_url(c.get("url", "")): c for c in cites.values()}
    clean, marks = strip_markers(canonical or "", set(cites))

    sources: list[dict] = []
    source_ix: dict[str, dict] = {}

    def source_for(url: str, kind: str | None, title: str) -> dict:
        key = _norm_url(url)
        if key not in source_ix:
            tier = source_tier(url, kind)
            s = {"id": f"s{len(sources) + 1}", "title": title or "",
                 "publisher": publisher_label(url), "url": url,
                 "kind": kind if kind in ("standard", "paper", "web") else "web",
                 "tier": tier, "claim_ids": []}
            sources.append(s)
            source_ix[key] = s
        return source_ix[key]

    def tier_of(srcs: list[dict]) -> str:
        if not srcs:
            return "blog"
        return "authoritative" if any(s["tier"] == "authoritative" for s in srcs) else "weak"

    claims: dict[str, dict] = {}
    step_of: dict[str, int] = {}
    order: list[dict] = []
    for pos, step in enumerate(plan.get("steps", [])):
        for c in _supported(step):
            step_of[c["id"]] = pos
            order.append((pos, step, c))

    def finish(cid: str, srcs: list[dict]) -> None:
        for s in srcs:
            if cid not in s["claim_ids"]:
                s["claim_ids"].append(cid)
        tier = tier_of(srcs)
        chip = None
        if tier == "authoritative":
            best = next(s for s in srcs if s["tier"] == "authoritative")
            chip = best["publisher"]
        claims[cid] = {"sources": [s["id"] for s in srcs], "tier": tier, "chip": chip}

    sid_ix = lambda sid: next(s for s in sources if s["id"] == sid)  # noqa: E731

    # Pass 1: direct claims.
    for pos, step, c in order:
        if c.get("kind") == "reasoning":
            continue
        sup = c.get("support") or {}
        srcs: list[dict] | None = []
        if sup.get("type") == "source" and sup.get("url"):
            ref = by_url.get(_norm_url(sup["url"]), {})
            srcs = [source_for(sup["url"], ref.get("kind"), ref.get("title", ""))]
        elif sup.get("type") == "blog_quote":
            ms = markers_for_quote(sup.get("quote") or c.get("text", ""), clean, marks)
            srcs = None if ms is None else [
                source_for(cites[n]["url"], cites[n].get("kind"), cites[n].get("title", ""))
                for n in ms if n in cites and cites[n].get("url")]
        else:
            srcs = None
        corr = c.get("corroboration") or {}
        if corr.get("url") and source_tier(corr["url"], corr.get("kind")) == "authoritative":
            extra = source_for(corr["url"], corr.get("kind"), corr.get("title", ""))
            if extra not in (srcs or []):
                srcs = (srcs or []) + [extra]
        if srcs is None:
            claims[c["id"]] = {"sources": [], "tier": "none", "chip": None}
            continue
        finish(c["id"], srcs)

    # Pass 2: reasoning claims, in plan order (chains resolve earlier-first).
    for pos, step, c in order:
        if c.get("kind") != "reasoning":
            continue
        premises = [d for d in (step.get("depends_on") or []) if d in claims]
        premises += [o["id"] for o in _supported(step)
                     if o["kind"] != "reasoning" and o["id"] in claims]
        premises += [o["id"] for o in _supported(step)
                     if o["kind"] == "reasoning" and o["id"] != c["id"] and o["id"] in claims
                     and step_of[o["id"]] == pos and o["id"] not in premises
                     and _earlier(order, o["id"], c["id"])]
        premises = list(dict.fromkeys(premises))
        if not premises or any(claims[p]["tier"] not in ("authoritative", "weak") for p in premises):
            claims[c["id"]] = {"sources": [], "tier": "none", "chip": None}
            continue
        ids: list[str] = []
        for p in premises:
            for sid in claims[p]["sources"]:
                if sid not in ids:
                    ids.append(sid)
        finish(c["id"], [sid_ix(i) for i in ids])

    # Grade over non-illustrative supported claims.
    key = [c for _, _, c in order if c.get("kind") != "illustrative"]
    n_auth = sum(1 for c in key if claims[c["id"]]["tier"] == "authoritative")
    n_weak = sum(1 for c in key if claims[c["id"]]["tier"] == "weak")
    n_blog = sum(1 for c in key if claims[c["id"]]["tier"] in ("blog", "none"))
    share = n_auth / len(key) if key else 0.0
    verdict = "strong" if share >= STRONG_SHARE else "adequate" if share >= ADEQUATE_SHARE else "thin"
    return {"claims": claims, "sources": sources,
            "grade": {"key_claims": len(key), "authoritative": n_auth, "blog_only": n_blog,
                      "weak": n_weak, "verdict": verdict}}


def _earlier(order, a: str, b: str) -> bool:
    ids = [c["id"] for _, _, c in order]
    return ids.index(a) < ids.index(b)


# ── chips ─────────────────────────────────────────────────────────────────────
def pick_chip_claims(refs: dict, plan: dict, limit: int = 4) -> list[dict]:
    """Up to `limit` most consequential claims that have a chip: one per step, numeric
    claims first, mechanism/fact roles only (never reasoning), spread across steps."""
    cands: list[tuple] = []
    for pos, step in enumerate(plan.get("steps", [])):
        best = None
        for c in _supported(step):
            r = refs["claims"].get(c["id"])
            if not r or not r.get("chip") or c.get("kind") in ("reasoning", "illustrative"):
                continue
            if c.get("role") not in ("mechanism", "fact"):
                continue
            score = (1 if (c.get("needs_number") or _NUM_RE.search(c.get("text", ""))) else 0,
                     1 if c.get("role") == "mechanism" else 0)
            if best is None or score > best[0]:
                best = (score, c)
        if best:
            cands.append((best[0], pos, step, best[1]))
    cands.sort(key=lambda t: (-t[0][0], -t[0][1], t[1]))
    chosen = sorted(cands[:limit], key=lambda t: t[1])
    return [{"claim_id": c["id"], "step_id": step.get("step_id"),
             "label": refs["claims"][c["id"]]["chip"], "text": c.get("text", "")}
            for _, _, step, c in chosen]


# ── description ───────────────────────────────────────────────────────────────
def render_description(refs: dict, post: dict, plan: dict) -> str:
    lines = [(plan.get("question") or "").strip(), ""]
    auth = [s for s in refs["sources"] if s["tier"] == "authoritative"]
    if auth:
        lines.append("Sources")
        for i, s in enumerate(auth, 1):
            parts = [s["publisher"]] + ([s["title"]] if s["title"] else []) + [s["url"]]
            lines.append(f"{i}. " + " - ".join(parts))
        lines.append("")
    if post.get("url"):
        lines.append(f"Full technical guide: {post['url']}")
    if refs["grade"]["verdict"] == "strong":
        lines.append("")
        lines.append(GRADE_SENTENCE)
    return "\n".join(lines).rstrip() + "\n"


# ── IO / CLI ──────────────────────────────────────────────────────────────────
def write_outputs(workspace) -> dict:
    ws = Path(workspace)
    plan = json.loads((ws / "explanation_plan.json").read_text(encoding="utf-8"))
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    canonical = (ws / "canonical.txt").read_text(encoding="utf-8")
    refs = build_references(plan, post, canonical)
    refs["chips"] = pick_chip_claims(refs, plan)
    (ws / "references.json").write_text(json.dumps(refs, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    (ws / "video_description.txt").write_text(render_description(refs, post, plan),
                                              encoding="utf-8")
    logger.info(f"references written: grade={refs['grade']['verdict']}, "
                f"chips={len(refs['chips'])}, sources={len(refs['sources'])}")
    return refs


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m shorts_engine.references <workspace>", file=sys.stderr)
        return 2
    refs = write_outputs(argv[0])
    g = refs["grade"]
    print(f"grade={g['verdict']} authoritative={g['authoritative']}/{g['key_claims']} "
          f"chips={[c['label'] for c in refs['chips']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
