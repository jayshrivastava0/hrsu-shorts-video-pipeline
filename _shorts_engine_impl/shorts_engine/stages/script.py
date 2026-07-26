"""
SCRIPT stage -- factsheet.json (+ post.json) to script.json.

This module provides:
- extract_numeric_tokens(): find numeric tokens in text (thousands-separator
  commas stripped, so "10,000" and "10000" compare equal)
- gate_numbers(): the never-unverified invariant -- every number in a beat's
  narration/card_text must trace back to a referenced fact's verbatim quote,
  a referenced brand differentiator's text, a brand CTA line, or the domain
- gate_banned(): rejects SCRIPT_BANNED_PHRASES (AI-isms), FEAR_FILLER_PATTERNS
  (hype/fear marketing), and brand.banned_claims anywhere in a beat
- gate_word_budget(): per-beat word count vs. BEAT_TEMPLATE seconds x
  WORDS_PER_SECOND, tolerated by WORD_BUDGET_TOLERANCE
- gate_card_text(): card_text must be <=7 words and must not echo a 5-gram
  of its own beat's narration
- gate_differentiator(): exactly one brand differentiator id, cited in the
  cta beat's fact_ids only
- run_gates(): structural check (beat count/order) + aggregation of the five
  gates above
- SCRIPT_SCHEMA / CRITIQUE_SCHEMA: JSON schema contracts for the writer and
  critic LLM calls
- writer/critique/run(): the stage entry point (write -> gate, retrying with
  the gate errors echoed back on failure; critique once; if the critique
  score is low, rewrite -- itself gate-retried the same way -- then save
  script.json)

Design: the writer LLM never sees raw blog HTML or canonical text -- only
already-verbatim facts (from factsheet.json) and brand differentiators. Every
number it uses must be traceable, deterministically, back to one of those
sources via gate_numbers. This carries the never-unverified invariant from
FACTS into SCRIPT (spec §2, §4 Stage 3).
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

from shorts_engine import config
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import GateFailure
from shorts_engine.llm import text_llm
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)


# ── Numeric token extraction ────────────────────────────────────────────────
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numeric_tokens(text: str) -> list[str]:
    """
    Extract numeric tokens from text, with thousands-separator commas
    stripped (so "10,000" normalizes to "10000", matching how gate_numbers
    compares numbers against fact/differentiator text that may not use
    comma separators).

    Args:
        text: Beat narration or card_text.

    Returns:
        List of numeric tokens in order of appearance (e.g. "1.5", "3",
        "10000"). Returns [] for text with no digits.
    """
    return [t.replace(",", "") for t in _NUM_RE.findall(text or "")]


# ── gate_numbers: the never-unverified invariant ────────────────────────────
def _allowed_pool(fact_ids: list[str], factsheet: dict, brand: BrandFacts) -> str:
    """
    Build the normalized text pool a beat's numbers are allowed to come
    from: the verbatim quotes of its referenced facts, the text of any
    referenced brand differentiator, plus (always, regardless of fact_ids)
    every brand CTA line and the brand domain.

    Args:
        fact_ids: The beat's fact_ids (may reference facts or differentiators).
        factsheet: Parsed factsheet.json (dict with a "facts" list).
        brand: Loaded BrandFacts.

    Returns:
        Normalized (normalize_for_match), comma-stripped text pool.
    """
    facts_by_id = {f["id"]: f for f in factsheet.get("facts", [])}
    diffs_by_id = {d["id"]: d["text"] for d in brand.differentiators}

    parts: list[str] = []
    for fid in fact_ids:
        if fid in facts_by_id:
            parts.append(facts_by_id[fid]["verbatim_quote"])
        elif fid in diffs_by_id:
            parts.append(diffs_by_id[fid])
    parts.extend(brand.cta_lines)
    parts.append(brand.domain)
    return normalize_for_match(" | ".join(parts)).replace(",", "")


def gate_numbers(beats: list[dict], factsheet: dict, brand: BrandFacts) -> list[str]:
    """
    The never-unverified invariant: every numeric token in a beat's
    narration or card_text (and optional diagram_labels) must appear, as a
    standalone number (not merely as a textual substring of a larger number),
    inside that beat's allowed pool (see _allowed_pool).

    Args:
        beats: List of beat dicts (each with "beat", "narration",
            "fact_ids", "card_text", "broll_wish", and optional "diagram_labels").
        factsheet: Parsed factsheet.json.
        brand: Loaded BrandFacts.

    Returns:
        List of error strings, one per untraced numeric token found (empty
        if every number traces).
    """
    errs: list[str] = []
    for b in beats:
        pool = _allowed_pool(b.get("fact_ids", []), factsheet, brand)
        for source in ("narration", "card_text"):
            for tok in extract_numeric_tokens(b.get(source, "")):
                pattern = rf"(?<![\d.]){re.escape(tok)}(?![\d])"
                if not re.search(pattern, pool):
                    errs.append(
                        f"numbers[{b.get('beat')}]: {tok!r} in {source} does not "
                        f"trace to any referenced fact"
                    )
        for label in b.get("diagram_labels") or []:
            for tok in extract_numeric_tokens(label):
                pattern = rf"(?<![\d.]){re.escape(tok)}(?![\d])"
                if not re.search(pattern, pool):
                    errs.append(
                        f"numbers[{b.get('beat')}]: {tok!r} in diagram label does not "
                        f"trace to any referenced fact"
                    )
    return errs


# ── gate_banned ──────────────────────────────────────────────────────────────
def gate_banned(beats: list[dict], brand: BrandFacts) -> list[str]:
    """
    Reject SCRIPT_BANNED_PHRASES (AI-isms), FEAR_FILLER_PATTERNS (hype/fear
    marketing phrases), and brand.banned_claims (hard-blocked claims)
    anywhere in a beat's narration or card_text. Matching is a plain,
    case-insensitive substring check.

    Args:
        beats: List of beat dicts.
        brand: Loaded BrandFacts (supplies banned_claims).

    Returns:
        List of error strings, one per banned phrase found in a beat.
    """
    banned = (
        [p.lower() for p in config.SCRIPT_BANNED_PHRASES]
        + [p.lower() for p in config.FEAR_FILLER_PATTERNS]
        + [p.lower() for p in brand.banned_claims]
    )
    errs: list[str] = []
    for b in beats:
        text = f"{b.get('narration', '')} {b.get('card_text', '')}".lower()
        for phrase in banned:
            if phrase in text:
                errs.append(f"banned[{b.get('beat')}]: contains {phrase!r}")
    return errs


# ── gate_word_budget ─────────────────────────────────────────────────────────
def gate_word_budget(beats: list[dict]) -> list[str]:
    """
    Check each beat's narration word count against BEAT_TEMPLATE's
    per-beat seconds range, converted to words via WORDS_PER_SECOND and
    tolerated by WORD_BUDGET_TOLERANCE (spec §4 Stage 3: 2.6 words/s ±20%).

    Args:
        beats: List of beat dicts, positionally aligned with
            config.BEAT_TEMPLATE (structure is validated separately by
            run_gates; this function just zips positionally).

    Returns:
        List of error strings, one per beat outside its word budget.
    """
    errs: list[str] = []
    tol = config.WORD_BUDGET_TOLERANCE
    for b, spec in zip(beats, config.BEAT_TEMPLATE):
        words = len(b.get("narration", "").split())
        lo = spec["min_s"] * config.WORDS_PER_SECOND * (1 - tol)
        hi = spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol)
        if not (lo <= words <= hi):
            # ceil/floor, NOT {:.0f} rounding: word counts are integers, so
            # the displayed bounds must be the integer-feasible range. A live
            # run's {:.0f} formatting turned lo=8.32, hi=18.72 into the
            # paradoxical "19 words outside [8, 19]" -- the retry-echoed
            # message told the model 19 was simultaneously the maximum and
            # too many, so it oscillated instead of converging.
            errs.append(
                f"budget[{spec['beat']}]: {words} words outside "
                f"[{math.ceil(lo)}, {math.floor(hi)}]"
            )
    return errs


# ── gate_total_duration ──────────────────────────────────────────────────────
def gate_total_duration(beats: list[dict]) -> list[str]:
    """
    Check the script's AGGREGATE estimated duration against SHOTLIST's total
    video window (config.TOTAL_MIN_S..TOTAL_MAX_S).

    gate_word_budget only bounds each beat individually; BEAT_TEMPLATE's
    per-beat min_s values sum to well under TOTAL_MIN_S, so a script where
    every beat independently passes its own word budget can still be, in
    aggregate, too short for a valid video -- SHOTLIST has no LLM and no
    retry path of its own, so this must be caught here, before script.json
    is finalized, where the existing writer retry-with-echo loop can act on
    it exactly like any other gate failure.

    The error message names an exact word deficit/surplus and which
    specific beats have headroom, rather than a vague "lengthen the shorter
    beats" -- live runs showed the writer repeatedly landing 1-6 words
    short of the floor even after being told the aggregate target range, so
    the retry-echoed error must do the arithmetic for it. A small buffer is
    added on top of the bare deficit since the writer has shown a
    consistent tendency to undershoot a stated target, not just a range.

    Args:
        beats: List of beat dicts, positionally aligned with
            config.BEAT_TEMPLATE (structure is validated separately by
            run_gates; this function just zips positionally).

    Returns:
        A single-element list with the aggregate error if the total falls
        outside [TOTAL_MIN_S, TOTAL_MAX_S], else [].
    """
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    total_s = total_words / config.WORDS_PER_SECOND
    if config.TOTAL_MIN_S <= total_s <= config.TOTAL_MAX_S:
        return []

    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    max_words = round(config.TOTAL_MAX_S * config.WORDS_PER_SECOND)
    tol = config.WORD_BUDGET_TOLERANCE

    if total_words < min_words:
        deficit = min_words - total_words + 3  # small buffer vs. undershoot
        headroom = []
        for b, spec in zip(beats, config.BEAT_TEMPLATE):
            words = len(b.get("narration", "").split())
            hi = int(spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol))
            if hi > words:
                headroom.append((hi - words, spec["beat"], words, hi))
        headroom.sort(reverse=True)
        suggestion = "; ".join(
            f"{beat} (currently {words} words, can go up to {hi})"
            for _room, beat, words, hi in headroom[:3]
        )
        return [
            f"total_duration: {total_s:.1f}s ({total_words} words) is short of "
            f"the {config.TOTAL_MIN_S:.0f}s floor ({min_words} words). Add "
            f"AT LEAST {deficit} more words total, distributed across the "
            f"beats with the most room: {suggestion}."
        ]

    surplus = total_words - max_words + 3
    return [
        f"total_duration: {total_s:.1f}s ({total_words} words) is over the "
        f"{config.TOTAL_MAX_S:.0f}s ceiling ({max_words} words). Cut AT "
        f"LEAST {surplus} words total across the beats."
    ]


# ── deterministic word top-up (last-resort convergence) ────────────────────
# Number-free, non-banned filler clauses per beat: safe to append blindly
# because they can never trip gate_numbers (no digits) or gate_banned (no
# hype/AI-ism phrases). Used only after the writer LLM has exhausted every
# retry and the sole remaining failure is an aggregate-duration shortfall --
# live runs showed the writer oscillating 1-6 words under the floor across
# 9 straight attempts even with the exact deficit and per-beat headroom
# spelled out, so a prompt-only fix cannot be relied on to converge.
_TOPUP_PHRASES: dict[str, list[str]] = {
    "hook": ["for", "procurement", "teams", "evaluating", "suppliers", "today"],
    "stakes": ["this", "affects", "cost", "control", "and", "supply",
               "reliability", "directly"],
    "mechanism": ["understanding", "this", "mechanism", "helps", "teams",
                  "set", "realistic", "expectations", "before", "sourcing"],
    "proof": ["this", "result", "held", "up", "under", "real", "operating",
              "conditions", "in", "the", "field"],
    "cta": ["reach", "out", "to", "discuss", "your", "specific", "sourcing",
            "requirements", "today"],
}


def apply_word_topup(beats: list[dict]) -> list[dict]:
    """
    Deterministically pad narration word counts to clear the aggregate
    TOTAL_MIN_S floor, without touching any beat's own gate_word_budget
    ceiling. Only called when the LLM writer/rewriter has exhausted its
    retries and the only remaining gate failure is a duration shortfall
    (see run()) -- this never substitutes for the LLM on a content issue.

    Args:
        beats: List of beat dicts that failed only on aggregate duration.

    Returns:
        A new list of beat dicts (input is not mutated) with filler words
        appended to the beats with the most headroom, largest-headroom
        first, until the floor is met or headroom is exhausted.
    """
    tol = config.WORD_BUDGET_TOLERANCE
    beats = [dict(b) for b in beats]
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    deficit = min_words - total_words
    if deficit <= 0:
        return beats

    specs = {s["beat"]: s for s in config.BEAT_TEMPLATE}
    order = sorted(
        beats,
        key=lambda b: (specs[b["beat"]]["max_s"] * config.WORDS_PER_SECOND
                       * (1 + tol)) - len(b.get("narration", "").split()),
        reverse=True,
    )
    for b in order:
        if deficit <= 0:
            break
        spec = specs[b["beat"]]
        hi = int(spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol))
        words = b.get("narration", "").split()
        room = hi - len(words)
        if room <= 0:
            continue
        pool = _TOPUP_PHRASES.get(b["beat"], _TOPUP_PHRASES["stakes"])
        take = min(room, deficit, len(pool))
        if take <= 0:
            continue
        narration = b.get("narration", "").rstrip()
        if narration and narration[-1] not in ".!?":
            narration += "."
        b["narration"] = (narration + " " + " ".join(pool[:take])).strip() + "."
        deficit -= take
    return beats


def _only_duration_shortfall(errors: list[str]) -> bool:
    """True if every gate error is an aggregate-duration shortfall (never a
    surplus, content, or structural failure) -- the only case
    apply_word_topup is safe to use in place of another LLM retry."""
    return bool(errors) and all(
        e.startswith("total_duration:") and "short of" in e for e in errors
    )


# ── gate_card_text ───────────────────────────────────────────────────────────
def gate_card_text(beats: list[dict]) -> list[str]:
    """
    Enforce card_text hygiene: at most 7 words, and must not echo a 5-word
    sliding window ("5-gram") of the beat's own narration -- cards should
    add information, not repeat the voiceover verbatim.

    Args:
        beats: List of beat dicts.

    Returns:
        List of error strings (a beat can produce up to two: one for
        echoing narration, one for exceeding the word limit).
    """
    errs: list[str] = []
    for b in beats:
        card = normalize_for_match(b.get("card_text", ""))
        narr = normalize_for_match(b.get("narration", ""))
        words = card.split()
        if len(words) >= 5:
            for i in range(len(words) - 4):
                window = " ".join(words[i:i + 5])
                if window in narr:
                    errs.append(f"card[{b.get('beat')}]: card_text duplicates narration")
                    break
        if len(words) > 7:
            errs.append(f"card[{b.get('beat')}]: card_text longer than 7 words")
    return errs


# ── gate_differentiator ──────────────────────────────────────────────────────
def gate_differentiator(beats: list[dict], brand: BrandFacts) -> list[str]:
    """
    Enforce spec §7: exactly one brand differentiator id must be cited, and
    only in the cta beat (the last beat) -- never earlier.

    Args:
        beats: List of beat dicts; the last element is treated as the cta
            beat (structure is validated separately by run_gates).
        brand: Loaded BrandFacts (supplies the approved differentiator ids).

    Returns:
        List of error strings: at most one for the cta beat's count, plus
        one per earlier beat that references a differentiator.
    """
    diff_ids = {d["id"] for d in brand.differentiators}
    errs: list[str] = []
    if not beats:
        return errs

    cta = beats[-1]
    in_cta = [f for f in cta.get("fact_ids", []) if f in diff_ids]
    if len(in_cta) != 1:
        errs.append(
            f"differentiator[cta]: expected exactly one of {sorted(diff_ids)}, "
            f"got {in_cta}"
        )
    for b in beats[:-1]:
        early = [f for f in b.get("fact_ids", []) if f in diff_ids]
        if early:
            errs.append(
                f"differentiator[{b.get('beat')}]: brand differentiators belong "
                f"in the CTA beat only, found {early}"
            )
    return errs


# ── run_gates: structure check + aggregation ────────────────────────────────
def run_gates(beats: list[dict], factsheet: dict, brand: BrandFacts) -> list[str]:
    """
    Validate beat structure (count and beat-name order against
    config.BEAT_TEMPLATE), then run all five content gates.

    A structural mismatch short-circuits with a single error -- there is no
    point checking word budgets, differentiator placement, etc. against a
    beat list that doesn't even match the locked template.

    Args:
        beats: Candidate list of beat dicts (from the writer LLM).
        factsheet: Parsed factsheet.json.
        brand: Loaded BrandFacts.

    Returns:
        List of error strings (empty if the script passes every gate).
    """
    expected_beats = [s["beat"] for s in config.BEAT_TEMPLATE]
    if len(beats) != len(config.BEAT_TEMPLATE):
        return [f"structure: expected {len(config.BEAT_TEMPLATE)} beats, got {len(beats)}"]
    if [b.get("beat") for b in beats] != expected_beats:
        return [f"structure: beat order must be {expected_beats}"]

    return (
        gate_numbers(beats, factsheet, brand)
        + gate_banned(beats, brand)
        + gate_word_budget(beats)
        + gate_total_duration(beats)
        + gate_card_text(beats)
        + gate_differentiator(beats, brand)
    )


# ── Writer / critique schemas (Task 12) ─────────────────────────────────────
SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"enum": [s["beat"] for s in config.BEAT_TEMPLATE]},
                    "narration": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "card_text": {"type": "string"},
                    "broll_wish": {"type": "string"},
                    "diagram_labels": {"type": "array", "items": {"type": "string"},
                                       "maxItems": 4},
                },
                "required": ["beat", "narration", "fact_ids", "card_text",
                             "broll_wish"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["beats"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "actionable_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "coherence_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "hrsu_reason_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "revise_notes": {"type": "string"},
    },
    "required": ["actionable_score", "coherence_score", "hrsu_reason_score",
                 "revise_notes"],
    "additionalProperties": False,
}

_WRITER_SYSTEM = (
    "You write 35-50 second video scripts for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype. HARD RULES: "
    "every number you use MUST come from a provided fact's verbatim quote, and "
    "that fact's id MUST be listed in the beat's fact_ids. Never invent "
    "statistics. card_text is at most 7 words and must not repeat the "
    "narration. The cta beat cites exactly one brand differentiator id."
)

_CRITIC_SYSTEM = (
    "You are a skeptical procurement manager reviewing a video script. Score "
    "0-10: actionable_score (did I learn something usable?), coherence_score "
    "(is the chemistry/mechanism described correctly and clearly?), "
    "hrsu_reason_score (is there one credible reason to consider HRSU?). "
    "Give concrete revise_notes."
)

_CRITIQUE_PASS_THRESHOLD = 7


def _beat_rules() -> str:
    """Render config.BEAT_TEMPLATE as a human-readable seconds/words block
    for the writer prompt. The word ranges shown are the ACTUAL integer
    bounds gate_word_budget accepts (tolerance included, ceil/floor'd) --
    showing the narrower no-tolerance nominal range understated each beat's
    real ceiling, which mattered once the aggregate total-duration floor
    forced beats toward their upper bounds: the model needs to know the
    true room it has."""
    tol = config.WORD_BUDGET_TOLERANCE
    return "\n".join(
        f"- {s['beat']}: {s['min_s']:.0f}-{s['max_s']:.0f}s "
        f"({math.ceil(s['min_s'] * config.WORDS_PER_SECOND * (1 - tol))}-"
        f"{math.floor(s['max_s'] * config.WORDS_PER_SECOND * (1 + tol))} "
        f"words allowed)"
        for s in config.BEAT_TEMPLATE
    )


def _writer_prompt(post_meta: dict, factsheet: dict, brand: BrandFacts, *,
                    gate_errors: list[str] | None = None,
                    revise_notes: str = "") -> str:
    """
    Build the writer LLM prompt: post metadata, the ONLY allowed facts
    (verbatim quotes), the brand differentiators (exactly one to be cited
    in the cta beat), the CTA domain, and the locked beat rules. Optionally
    appends prior gate failures (for a gate-retry) or reviewer revise_notes
    (for a critique-triggered rewrite) -- never both at once, per run()'s
    call pattern.
    """
    facts_block = "\n".join(
        f"[{f['id']}] \"{f['verbatim_quote']}\" (value={f['value']} {f['unit']}, "
        f"citation={f['citation_marker']})"
        for f in factsheet.get("facts", [])
    )
    diff_block = "\n".join(f"[{d['id']}] {d['text']}" for d in brand.differentiators)
    example_diff_id = brand.differentiators[0]["id"] if brand.differentiators else "b_example"
    prompt = (
        f"Blog: {post_meta.get('title')} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"FACTS (the ONLY allowed sources of numbers):\n{facts_block}\n\n"
        f"BRAND DIFFERENTIATORS (cite exactly one, in the cta beat only):\n"
        f"{diff_block}\n\nCTA domain: {brand.domain}\n\n"
        f"To cite a differentiator, put its id directly in the cta beat's "
        f"fact_ids array (e.g. \"fact_ids\": [\"{example_diff_id}\"]). Do NOT "
        f"invent a new field such as \"brand_differentiator\" for it.\n\n"
        f"Beat structure:\n{_beat_rules()}\n\n"
        f"The five beats' narration word counts must SUM to between "
        f"{config.TOTAL_MIN_S * config.WORDS_PER_SECOND:.0f} and "
        f"{config.TOTAL_MAX_S * config.WORDS_PER_SECOND:.0f} words combined, "
        f"while each beat stays inside its own word range above.\n\n"
        f"On the mechanism beat you MAY add \"diagram_labels\": 2-4 short "
        f"process-step labels (3 words or fewer each, no numbers unless "
        f"quoted from a fact).\n\n"
        f"Write the 5 beats now as JSON."
    )
    if gate_errors:
        prompt += (
            "\n\nYour previous draft FAILED these checks — fix every one:\n"
            + "\n".join(f"- {e}" for e in gate_errors)
        )
    if revise_notes:
        prompt += f"\n\nReviewer notes to address:\n{revise_notes}"
    return prompt


def run(ctx) -> dict[str, str]:
    """
    Run the SCRIPT stage: write the 5-beat script, gate it (retrying with
    gate errors echoed back on failure, up to config.LLM_MAX_RETRIES
    attempts), critique it once, and -- if any critique score is below
    _CRITIQUE_PASS_THRESHOLD -- rewrite based on the critique's revise_notes,
    itself gate-retried up to config.LLM_MAX_RETRIES attempts (gate errors
    echoed back the same way as the initial write, after the first rewrite
    attempt). Writes script.json with the final beats, the critique, and
    the number of writer attempts used.

    Args:
        ctx: StageContext with manifest, workspace, flags
             (`ctx.flags["local_only"]` selects the local model tier).

    Returns:
        dict with a "script" key pointing to script.json (relative to the
        workspace), for merging into the run manifest's artifacts.

    Raises:
        GateFailure: If every writer attempt fails the gates, or if every
            critique-triggered rewrite attempt also fails the gates. The
            engine never ships an unverified or off-template script.
    """
    workspace = Path(ctx.workspace)
    post_meta = json.loads((workspace / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((workspace / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = bool(ctx.flags.get("local_only", False))

    beats: list[dict] | None = None
    errors: list[str] = ["no attempt yet"]
    attempts = 0
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        attempts = attempt
        result = text_llm.generate_schema_json(
            _writer_prompt(post_meta, factsheet, brand,
                           gate_errors=None if attempt == 1 else errors),
            _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only,
        )
        beats = result["beats"]
        errors = run_gates(beats, factsheet, brand)
        if not errors:
            break
        logger.warning(f"script gates failed (attempt {attempt}/{config.LLM_MAX_RETRIES}): {errors}")
    if errors and _only_duration_shortfall(errors):
        padded = apply_word_topup(beats)
        padded_errors = run_gates(padded, factsheet, brand)
        if not padded_errors:
            logger.info("script: applied deterministic word top-up to clear duration floor")
            beats, errors = padded, []
    if errors:
        raise GateFailure(errors)

    critique = text_llm.generate_schema_json(
        "Script:\n" + json.dumps(beats, ensure_ascii=False, indent=2),
        _CRITIC_SYSTEM, CRITIQUE_SCHEMA, local_only=local_only,
    )

    lowest = min(critique["actionable_score"], critique["coherence_score"],
                 critique["hrsu_reason_score"])
    if lowest < _CRITIQUE_PASS_THRESHOLD:
        logger.info(f"critique below bar ({lowest}), rewriting: {critique['revise_notes']}")
        rewrite_errors: list[str] = []
        rewritten = beats
        for rewrite_attempt in range(1, config.LLM_MAX_RETRIES + 1):
            result = text_llm.generate_schema_json(
                _writer_prompt(
                    post_meta, factsheet, brand,
                    gate_errors=rewrite_errors or None,
                    revise_notes=critique["revise_notes"] if rewrite_attempt == 1 else "",
                ),
                _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only,
            )
            rewritten = result["beats"]
            rewrite_errors = run_gates(rewritten, factsheet, brand)
            if not rewrite_errors:
                break
            logger.warning(
                f"rewrite gates failed (attempt {rewrite_attempt}/"
                f"{config.LLM_MAX_RETRIES}): {rewrite_errors}"
            )
        if rewrite_errors and _only_duration_shortfall(rewrite_errors):
            padded = apply_word_topup(rewritten)
            padded_errors = run_gates(padded, factsheet, brand)
            if not padded_errors:
                logger.info("script: applied deterministic word top-up to clear duration floor")
                rewritten, rewrite_errors = padded, []
        if rewrite_errors:
            raise GateFailure(rewrite_errors)
        beats = rewritten

    payload = {"beats": beats, "critique": critique, "attempts": attempts}
    (workspace / "script.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"script written: {len(beats)} beats, attempts={attempts}")
    return {"script": "script.json"}
