"""
FACTS stage -- canonical.txt to factsheet.json.

This module provides:
- normalize_for_match(): Text normalization for verbatim comparison
- locate_verbatim(): Locate a quote inside canonical text (offset or None)
- split_sentences(): Lightweight sentence splitter
- mine_candidate_sentences(): Deterministic miner for numeric sentences
- FACT_WRAP_SCHEMA: JSON schema for the LLM fact-wrapping response
- wrap_sentences_into_facts(): LLM wrapper that turns mined sentences into
  structured fact entries
- gate_facts(): Deterministic verbatim gate -- the never-unverified invariant
- run(): Stage entry point (canonical.txt + post.json -> factsheet.json)

Design: facts are LOCATED, not generated. A deterministic miner
(mine_candidate_sentences) finds numeric sentences in the canonical text; the
LLM's only job (wrap_sentences_into_facts) is to wrap already-verbatim
sentences into structured fields (value/unit/tags/etc.) -- it is never shown
the full canonical text and never asked to produce a number "about" the
post. A separate, deterministic gate (gate_facts) re-verifies every entry by
string-locating its verbatim_quote back in the canonical text and by
checking that every digit in `value` actually appears inside that quote. Any
entry that fails either check is dropped, never silently repaired. This
makes fabricated statistics (including sibling-post "poison" carried over
from a mis-isolated scrape) structurally impossible: the LLM cannot inject a
number that doesn't already exist, verbatim, in the source blog post.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable

from shorts_engine.errors import EngineError
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)


# ── Text normalization ──────────────────────────────────────────────────────
_DASH_TRANSLATION = str.maketrans({
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
})
_QUOTE_TRANSLATION = str.maketrans({
    "‘": "'", "’": "'",   # left/right single quotation mark
    "“": '"', "”": '"',  # left/right double quotation mark
})
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_match(s: str) -> str:
    """
    Normalize text for verbatim comparison.

    Steps:
      1. Unify non-breaking spaces (unicode U+00A0 and the literal "&nbsp;"
         entity, in case it survived HTML unescaping upstream) to a space
      2. Unify dash variants (en dash, em dash, minus sign) to a hyphen
      3. Unify curly quotes to straight quotes
      4. Lowercase
      5. Collapse all whitespace runs to a single space, strip ends

    Used both to locate quotes verbatim in the canonical text and to check
    that a fact's numeric value appears inside its own quote -- so a fact
    can never be treated as "verbatim" only because of a stray dash/quote
    style difference between what the LLM echoed and what the blog says.

    Args:
        s: Raw text (canonical text or a candidate quote)

    Returns:
        Normalized text, safe to compare/search across dash/quote/whitespace
        variants.
    """
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    s = s.translate(_DASH_TRANSLATION)
    s = s.translate(_QUOTE_TRANSLATION)
    s = s.lower()
    return _WHITESPACE_RE.sub(" ", s).strip()


def locate_verbatim(quote: str, canonical: str) -> int | None:
    """
    Locate a quote inside canonical text, after normalization.

    Args:
        quote: Candidate verbatim quote (e.g., an LLM-echoed sentence)
        canonical: The full canonical text to search within

    Returns:
        The character offset of the match inside the NORMALIZED canonical
        text, or None if the quote cannot be found. An empty or
        whitespace-only quote always returns None.
    """
    normalized_quote = normalize_for_match(quote)
    if not normalized_quote:
        return None
    pos = normalize_for_match(canonical).find(normalized_quote)
    return pos if pos >= 0 else None


# ── Sentence splitting ──────────────────────────────────────────────────────
# Split after a sentence-ending punctuation mark + whitespace, but only when
# followed by an uppercase letter, an opening quote, or an opening paren --
# this keeps decimals ("3.5 kg") from being split mid-value (there is no
# whitespace right after the decimal point, so the pattern simply never
# matches there).
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z“"(])')


def split_sentences(text: str) -> list[str]:
    """
    Split canonical text into sentences.

    Args:
        text: Canonical (plain) text

    Returns:
        List of trimmed, non-empty sentences in document order.
    """
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


# ── Candidate mining ─────────────────────────────────────────────────────────
_HAS_DIGIT_RE = re.compile(r"\d")


def mine_candidate_sentences(canonical: str) -> list[str]:
    """
    Mine sentences that contain at least one digit (candidate facts).

    This is the deterministic miner: only sentences that already contain a
    number are ever offered to the LLM, so the LLM is never in a position to
    introduce a number that wasn't already in the source text.

    Args:
        canonical: Canonical (plain) text of the blog post

    Returns:
        Numeric sentences, exact-string deduplicated, in document order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for sent in split_sentences(canonical):
        if _HAS_DIGIT_RE.search(sent) and sent not in seen:
            seen.add(sent)
            out.append(sent)
    return out


# ── LLM wrap ─────────────────────────────────────────────────────────────────
FACT_WRAP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verbatim_quote": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "claim_summary": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {
                            "enum": ["metric", "spec", "benefit", "risk",
                                     "region", "compliance"],
                        },
                    },
                    "procurement_significance": {
                        "type": "integer", "minimum": 1, "maximum": 5,
                    },
                    "citation_marker": {"type": ["integer", "null"]},
                },
                "required": ["id", "verbatim_quote", "value", "unit",
                             "claim_summary", "tags",
                             "procurement_significance", "citation_marker"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}

_WRAP_SYSTEM = (
    "You extract procurement-relevant facts for a B2B chemical supplier's "
    "video script. You are given sentences COPIED VERBATIM from a blog "
    "post. For each useful sentence, produce a fact entry whose "
    "verbatim_quote is an EXACT substring copy of one given sentence (never "
    "reworded, never paraphrased). Skip boilerplate. value/unit must appear "
    "inside the quote. citation_marker is the superscript number if the "
    "sentence shows one, else null."
)

_MAX_CANDIDATE_SENTENCES = 40


def wrap_sentences_into_facts(
    sentences: list[str],
    post_meta: dict,
    *,
    local_only: bool = False,
    llm: Callable[..., dict] | None = None,
) -> list[dict]:
    """
    Ask the LLM to wrap mined sentences into structured fact entries.

    The LLM is never shown the full canonical text and never asked to
    invent a number -- it only receives sentences that the deterministic
    miner (mine_candidate_sentences) already confirmed contain a digit, and
    its job is to describe/tag them. The verbatim gate (gate_facts)
    re-verifies the result independently, so a misbehaving LLM cannot ship
    an unverifiable fact.

    Args:
        sentences: Candidate sentences (from mine_candidate_sentences)
        post_meta: Post metadata dict (title/region/category from post.json)
        local_only: If True, use the local model tier (passed through to llm)
        llm: Injectable LLM callable; defaults to None, which resolves to
            `text_llm.generate_schema_json` at CALL time (not at import/def
            time) -- this matters because it means monkeypatching
            `text_llm.generate_schema_json` is honored even when callers
            (like run() below) don't pass `llm=` explicitly.

    Returns:
        List of raw fact-entry dicts as returned by the LLM (not yet
        gated/offset-annotated -- see gate_facts). Returns [] immediately,
        without calling the LLM, if `sentences` is empty.
    """
    if not sentences:
        return []
    if llm is None:
        llm = text_llm.generate_schema_json

    numbered = "\n".join(f"- {s}" for s in sentences)
    prompt = (
        f"Blog: {post_meta.get('title')} (region={post_meta.get('region')}, "
        f"category={post_meta.get('category')})\n\n"
        f"Sentences:\n{numbered}\n\n"
        f"Return the facts JSON now. Use ids f1, f2, ... in order."
    )
    result = llm(prompt, _WRAP_SYSTEM, FACT_WRAP_SCHEMA, local_only=local_only)
    return result["facts"]


# ── Verbatim gate (the never-unverified invariant) ──────────────────────────
_NON_VALUE_DIGIT_RE = re.compile(r"[^\d.]")


def gate_facts(entries: list[dict], canonical: str) -> tuple[list[dict], list[dict]]:
    """
    Deterministically re-verify every LLM-produced fact entry.

    Two checks, either of which drops the entry:
      1. `verbatim_quote` must string-locate inside `canonical` (after
         normalization) -- this is what makes fabricated sentences
         (including sibling-post "poison") structurally impossible.
      2. Every digit/decimal-point group inside `value` must itself appear
         inside the (normalized) quote -- this catches a real quote paired
         with an invented number. A `value` with no digits at all (a purely
         qualitative fact) trivially passes this check.

    Args:
        entries: Raw fact-entry dicts (from wrap_sentences_into_facts)
        canonical: Canonical (plain) text of the blog post

    Returns:
        (kept, dropped) -- kept entries gain a "char_offset" key; dropped
        entries gain a "reason" key. All original fields are preserved on
        both sides, and input order is preserved within each list.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for entry in entries:
        offset = locate_verbatim(entry["verbatim_quote"], canonical)
        if offset is None:
            dropped.append({**entry, "reason": "not located in canonical text"})
            continue

        digit_groups = _NON_VALUE_DIGIT_RE.sub(" ", entry["value"]).split()
        quote_norm = normalize_for_match(entry["verbatim_quote"])
        if digit_groups and not all(d in quote_norm for d in digit_groups):
            dropped.append({**entry, "reason": "value digits not inside the quote"})
            continue

        kept.append({**entry, "char_offset": offset})
    return kept, dropped


# ── Stage runner ─────────────────────────────────────────────────────────────
def _brand_facts_payload() -> list[dict]:
    """
    Build the `brand_facts` section of factsheet.json.

    Lazily imports shorts_engine.brand (a separate, later task). Until that
    module lands, this degrades to an empty list (with a warning) instead of
    crashing the FACTS stage. Once shorts_engine/brand.py exists, a
    genuinely missing/invalid brand_facts file still raises
    EngineConfigError and fails the run loudly -- brand claims must never be
    silently invented -- because only ImportError (module not found) is
    swallowed here, not EngineConfigError.

    Returns:
        List of {"id", "text", "kind"} dicts (differentiators then CTAs), or
        [] if shorts_engine.brand is not yet available.
    """
    try:
        from shorts_engine.brand import load_brand_facts
    except ImportError:
        logger.warning(
            "shorts_engine.brand is not available yet -- factsheet.json will "
            "have an empty brand_facts list"
        )
        return []

    brand = load_brand_facts()
    differentiators = [
        {"id": d["id"], "text": d["text"], "kind": "differentiator"}
        for d in brand.differentiators
    ]
    cta = [
        {"id": f"cta{i}", "text": t, "kind": "cta"}
        for i, t in enumerate(brand.cta_lines, start=1)
    ]
    return differentiators + cta


def run(ctx) -> dict[str, str]:
    """
    Run the FACTS stage.

    Reads canonical.txt + post.json from the workspace, mines numeric
    candidate sentences, asks the LLM to wrap them into structured facts,
    re-verifies every entry with the deterministic verbatim gate, and writes
    factsheet.json.

    Args:
        ctx: StageContext with manifest, workspace, flags
             (`ctx.flags["local_only"]` selects the local model tier)

    Returns:
        dict with a "factsheet" key pointing to the output file (relative to
        the workspace), for merging into the run manifest's artifacts.

    Raises:
        EngineError: If no numeric sentences are found, or if every fact
            entry fails the verbatim gate -- both are fail-loud conditions;
            the stage never silently ships an empty or unverifiable
            factsheet.
    """
    workspace = Path(ctx.workspace)
    canonical = (workspace / "canonical.txt").read_text(encoding="utf-8")
    post_meta = json.loads((workspace / "post.json").read_text(encoding="utf-8"))

    sentences = mine_candidate_sentences(canonical)
    if not sentences:
        raise EngineError("no numeric sentences found in canonical text")
    logger.info(f"mined {len(sentences)} candidate sentences")

    local_only = bool(ctx.flags.get("local_only", False))
    entries = wrap_sentences_into_facts(
        sentences[:_MAX_CANDIDATE_SENTENCES], post_meta, local_only=local_only,
    )

    kept, dropped = gate_facts(entries, canonical)
    if not kept:
        raise EngineError(f"all {len(entries)} fact entries failed the verbatim gate")

    payload = {
        "facts": kept,
        "brand_facts": _brand_facts_payload(),
        "dropped": dropped,
    }
    (workspace / "factsheet.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"factsheet: {len(kept)} kept, {len(dropped)} dropped")
    return {"factsheet": "factsheet.json"}
