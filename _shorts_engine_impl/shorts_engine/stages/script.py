"""
SCRIPT stage -- verified explanation_plan.json (+ post.json, factsheet.json) to script.json.

The writer no longer invents the logic: it narrates the verified plan, one beat per
step, with a hook that poses the plan's question and a CTA built on the payoff. All
deterministic gates stay (number tracing, banned phrases, card_text hygiene, exactly
one differentiator in the CTA, the 30 s floor) but there are no per-purpose word
budgets and no filler top-up: length follows the content, with no ceiling.

A critic from a different model family then scores the FINAL script (coherence,
actionability, HRSU reason, faithfulness to the verified claims). A score below the
bar triggers a rewrite that is itself gated and re-scored, up to
config.SCRIPT_MAX_REWRITES times. A script that cannot reach the bar, or fails the
gates after LLM_MAX_RETRIES attempts, raises HoldForReview -- it never ships and is
never padded.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from shorts_engine import config, explanation
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import EngineError, HoldForReview
from shorts_engine.llm import text_llm
from shorts_engine.stages.facts import normalize_for_match
from shorts_engine.stages.verify_claims import MIN_QUOTE_WORDS

logger = logging.getLogger(__name__)

_CRITIQUE_PASS_THRESHOLD = 7

# ── Numeric token extraction ────────────────────────────────────────────────
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numeric_tokens(text: str) -> list[str]:
    """Numeric tokens in order of appearance, thousands commas stripped ("10,000" -> "10000")."""
    return [t.replace(",", "") for t in _NUM_RE.findall(text or "")]


# ── gate_numbers: the never-unverified invariant ────────────────────────────
def _allowed_pool(fact_ids: list[str], factsheet: dict, brand: BrandFacts,
                  extra: str = "") -> str:
    """Normalized, comma-stripped text a beat's numbers may come from: the verbatim quotes of
    its fact_ids, referenced brand differentiators, every CTA line, the domain, plus `extra`
    (the verified claims of the beat's plan step)."""
    return normalize_for_match(_pool_text(fact_ids, factsheet, brand, extra)).replace(",", "")


def _pool_text(fact_ids: list[str], factsheet: dict, brand: BrandFacts, extra: str = "") -> str:
    """The raw (un-normalized) allowed-pool text; see _allowed_pool."""
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
    if extra:
        parts.append(extra)
    return " | ".join(parts)


_UNIT_ERR_NUM_RE = re.compile(r"(?:for|number) '([^']+)'")


def gate_numbers(beats: list[dict], factsheet: dict, brand: BrandFacts,
                 extra_by_beat: dict[str, str] | None = None,
                 terms_by_beat: dict[str, list[dict]] | None = None) -> list[str]:
    """Every numeric token in a beat's narration, card_text and diagram_labels must appear
    as a standalone number in that beat's allowed pool (see _allowed_pool). Every number in
    the narration must ALSO carry a unit -- known, or defined in the beat's plan-step terms --
    that the pool ties to that same number (explanation.gate_claim_numbers). One error per
    offending token. `extra_by_beat` maps beat name -> extra allowed text (verified plan
    claims); `terms_by_beat` maps beat name -> that plan step's `terms`."""
    errs: list[str] = []
    for b in beats:
        beat = b.get("beat")
        extra = (extra_by_beat or {}).get(beat, "")
        fact_ids = b.get("fact_ids", [])
        pool = _allowed_pool(fact_ids, factsheet, brand, extra)
        sources = [("narration", b.get("narration", "")), ("card_text", b.get("card_text", ""))]
        sources += [("diagram label", label) for label in b.get("diagram_labels") or []]
        untraced_in_narration: set[str] = set()
        for name, text in sources:
            for tok in extract_numeric_tokens(text):
                if not re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d])", pool):
                    errs.append(f"numbers[{beat}]: {tok!r} in {name} does not "
                                f"trace to any referenced fact")
                    if name == "narration":
                        untraced_in_narration.add(tok)
        reported = set(untraced_in_narration)
        for e in explanation.gate_claim_numbers(
                b.get("narration", ""), _pool_text(fact_ids, factsheet, brand, extra),
                (terms_by_beat or {}).get(beat)):
            m = _UNIT_ERR_NUM_RE.search(e)
            num = m.group(1) if m else e
            if num in reported:
                continue
            reported.add(num)
            errs.append(f"numbers[{beat}]: {e} (narration)")
    return errs


# ── gate_banned ──────────────────────────────────────────────────────────────
def gate_banned(beats: list[dict], brand: BrandFacts) -> list[str]:
    """Reject SCRIPT_BANNED_PHRASES, FEAR_FILLER_PATTERNS and brand.banned_claims
    (case-insensitive substring) in a beat's narration or card_text."""
    banned = ([p.lower() for p in config.SCRIPT_BANNED_PHRASES]
              + [p.lower() for p in config.FEAR_FILLER_PATTERNS]
              + [p.lower() for p in brand.banned_claims])
    errs: list[str] = []
    for b in beats:
        text = f"{b.get('narration', '')} {b.get('card_text', '')}".lower()
        for phrase in banned:
            if phrase in text:
                errs.append(f"banned[{b.get('beat')}]: contains {phrase!r}")
    return errs


# ── gate_total_duration ──────────────────────────────────────────────────────
def gate_total_duration(beats: list[dict]) -> list[str]:
    """Aggregate estimated duration vs the config.TOTAL_MIN_S floor. No ceiling."""
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    total_s = total_words / config.WORDS_PER_SECOND
    if total_s >= config.TOTAL_MIN_S:
        return []
    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    deficit = min_words - total_words + 3  # small buffer against undershoot
    return [f"total_duration: {total_s:.1f}s ({total_words} words) is short of the "
            f"{config.TOTAL_MIN_S:.0f}s floor ({min_words} words). Add AT LEAST {deficit} "
            f"more words in total by explaining the steps more fully (never with filler)."]


# ── gate_card_text ───────────────────────────────────────────────────────────
def gate_card_text(beats: list[dict]) -> list[str]:
    """card_text: at most 7 words and no 5-gram copied from the beat's own narration."""
    errs: list[str] = []
    for b in beats:
        card = normalize_for_match(b.get("card_text", ""))
        narr = normalize_for_match(b.get("narration", ""))
        words = card.split()
        if len(words) >= 5:
            for i in range(len(words) - 4):
                if " ".join(words[i:i + 5]) in narr:
                    errs.append(f"card[{b.get('beat')}]: card_text duplicates narration")
                    break
        if len(words) > 7:
            errs.append(f"card[{b.get('beat')}]: card_text longer than 7 words")
    return errs


# ── gate_differentiator ──────────────────────────────────────────────────────
def gate_differentiator(beats: list[dict], brand: BrandFacts) -> list[str]:
    """Exactly one brand differentiator id, cited only in the final (cta) beat."""
    diff_ids = {d["id"] for d in brand.differentiators}
    errs: list[str] = []
    if not beats:
        return errs
    in_cta = [f for f in beats[-1].get("fact_ids", []) if f in diff_ids]
    if len(in_cta) != 1:
        errs.append(f"differentiator[cta]: expected exactly one of {sorted(diff_ids)}, "
                    f"got {in_cta}")
    for b in beats[:-1]:
        early = [f for f in b.get("fact_ids", []) if f in diff_ids]
        if early:
            errs.append(f"differentiator[{b.get('beat')}]: brand differentiators belong "
                        f"in the CTA beat only, found {early}")
    return errs


# ── run_gates ────────────────────────────────────────────────────────────────
def run_gates(beats: list[dict], factsheet: dict, brand: BrandFacts,
              extra_by_beat: dict[str, str] | None = None,
              terms_by_beat: dict[str, list[dict]] | None = None) -> list[str]:
    """Structure check (>= MIN_BEATS beats, final purpose "cta", unique names) then the
    content gates. A structural mismatch short-circuits with a single error."""
    if len(beats) < config.MIN_BEATS:
        return [f"structure: expected at least {config.MIN_BEATS} beats, got {len(beats)}"]
    if beats[-1].get("purpose") != "cta":
        return ['structure: the final beat must have purpose "cta"']
    names = [b.get("beat") for b in beats]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        return [f"structure: beat names must be unique, found duplicate(s): {dupes}"]
    return (gate_numbers(beats, factsheet, brand, extra_by_beat, terms_by_beat)
            + gate_banned(beats, brand)
            + gate_total_duration(beats)
            + gate_card_text(beats)
            + gate_differentiator(beats, brand))


# ── Schemas ──────────────────────────────────────────────────────────────────
_BEAT_FIELDS = {"narration": {"type": "string"}, "card_text": {"type": "string"},
                "broll_wish": {"type": "string"}}
_BEAT_REQUIRED = ["narration", "card_text", "broll_wish"]

SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hook": {"type": "object", "properties": _BEAT_FIELDS,
                 "required": _BEAT_REQUIRED, "additionalProperties": False},
        "steps": {"type": "array", "items": {
            "type": "object",
            "properties": {"step_id": {"type": "string"}, **_BEAT_FIELDS},
            "required": ["step_id"] + _BEAT_REQUIRED, "additionalProperties": False}},
        "cta": {"type": "object", "properties": _BEAT_FIELDS,
                "required": _BEAT_REQUIRED, "additionalProperties": False},
    },
    "required": ["hook", "steps", "cta"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "actionable_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "coherence_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "hrsu_reason_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "faithfulness_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "revise_notes": {"type": "string"},
    },
    "required": ["actionable_score", "coherence_score", "hrsu_reason_score",
                 "faithfulness_score", "revise_notes"],
    "additionalProperties": False,
}

_NO_NUMBERS_HOOK_CTA = "The hook and the CTA must not contain any numbers."

_WRITER_SYSTEM = (
    "You write the narration for an explainer video for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype, like a good teacher. You "
    "are given a VERIFIED explanation plan: narrate it step by step, in order. Open with a "
    "hook that poses the plan's question. Explain each step so a viewer who knows nothing "
    "about it can follow: define each term the first time you use it, and say why the step "
    "leads to the next. Convey ONLY the verified claims listed for each step -- never add a "
    "new fact, number or unit. The last item is the CTA: state the takeaway and cite the "
    "brand differentiator. " + _NO_NUMBERS_HOOK_CTA + " card_text is at most 7 words and must not repeat the narration. "
    "Length follows the explanation: there is a 30 second floor and no maximum, and you must "
    "never pad with filler."
)

_CRITIC_SYSTEM = (
    "You are a skeptical procurement manager reviewing an explainer script against the "
    "verified claims it was written from. Score 0-10: actionable_score (did I learn "
    "something usable?), coherence_score (does each step follow from the last, are terms "
    "defined before use, is the chemistry described clearly?), hrsu_reason_score (is there "
    "one credible reason to consider HRSU?), faithfulness_score (does the narration assert "
    "ANYTHING not in the verified claims? any such addition scores below 5). Give concrete "
    "revise_notes."
)


# ── Beat construction ────────────────────────────────────────────────────────
def _labels(entities: list[str]) -> list[str]:
    return [" ".join(e.split()[:3]) for e in entities[:4] if e.strip()]


def _fact_ids_for_step(step: dict, factsheet: dict) -> list[str]:
    """Ids of factsheet facts whose verbatim quote overlaps a supported blog quote of the step."""
    quotes = [normalize_for_match(c["support"]["quote"]) for c in step["claims"]
              if c.get("support", {}).get("type") == "blog_quote"
              and len((c["support"]["quote"] or "").split()) >= MIN_QUOTE_WORDS]
    ids = []
    for f in factsheet.get("facts", []):
        fq = normalize_for_match(f["verbatim_quote"])
        if any(q in fq or fq in q for q in quotes):
            ids.append(f["id"])
    return ids


def extra_pool_by_beat(plan: dict) -> dict[str, str]:
    """beat name -> text of the step's supported claims and their quotes (number provenance)."""
    pools = {}
    for i, step in enumerate(plan["steps"], start=1):
        parts = []
        for c in step["claims"]:
            if c.get("verdict") == "supported":
                parts += [c["text"], c.get("support", {}).get("quote", "")]
        pools[f"step_{i}"] = " | ".join(p for p in parts if p)
    return pools


def terms_by_step_beat(plan: dict) -> dict[str, list[dict]]:
    """beat name (step_N) -> that plan step's defined terms (for the number+unit gate)."""
    return {f"step_{i}": step.get("terms", []) for i, step in enumerate(plan["steps"], start=1)}


def build_beats(doc: dict, plan: dict, factsheet: dict,
                brand: BrandFacts) -> tuple[list[dict], list[str]]:
    """Writer document -> (beats, errors). Errors when step ids differ from the plan's."""
    expected = [s["step_id"] for s in plan["steps"]]
    got = [s["step_id"] for s in doc["steps"]]
    if got != expected:
        return [], [f"structure: steps must be exactly {expected} in that order, got {got}"]
    beats = [{"beat": "hook", "purpose": "hook", "fact_ids": [], **doc["hook"]}]
    for i, (pstep, wstep) in enumerate(zip(plan["steps"], doc["steps"]), start=1):
        beats.append({
            "beat": f"step_{i}", "purpose": "mechanism", "step_id": pstep["step_id"],
            "narration": wstep["narration"], "card_text": wstep["card_text"],
            "broll_wish": wstep["broll_wish"],
            "fact_ids": _fact_ids_for_step(pstep, factsheet),
            "diagram_labels": _labels(pstep["visual_intent"]["entities"]),
            "visual_intent": pstep["visual_intent"],
        })
    beats.append({"beat": "cta", "purpose": "cta",
                  "fact_ids": [plan["payoff"]["differentiator_id"]], **doc["cta"]})
    return beats, []


# ── Prompts ──────────────────────────────────────────────────────────────────
def _writer_prompt(plan: dict, post_meta: dict, brand: BrandFacts, *,
                   gate_errors: list[str] | None = None, revise_notes: str = "",
                   previous_beats: list[dict] | None = None) -> str:
    blocks = []
    for step in plan["steps"]:
        claims = "\n".join(f"  - {c['text']}" for c in step["claims"]
                           if c.get("verdict") == "supported")
        terms = "\n".join(f"  - {t['term']}: {t['definition']}" for t in step.get("terms", []))
        blocks.append(f"STEP {step['step_id']} (goal: {step['claim_text']})\n"
                      f"Verified claims:\n{claims}\n"
                      + (f"Terms to define:\n{terms}\n" if terms else ""))
    diff = next((d for d in brand.differentiators
                 if d["id"] == plan["payoff"]["differentiator_id"]), None)
    prompt = (
        f"Blog: {post_meta.get('title')} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"QUESTION the video answers: {plan['question']}\n\n" + "\n".join(blocks)
        + f"\nPAYOFF takeaway: {plan['payoff']['takeaway']}\n"
        f"Brand differentiator to cite in the CTA [{plan['payoff']['differentiator_id']}]: "
        f"{diff['text'] if diff else ''}\nCTA domain: {brand.domain}\n\n"
        f"Return a hook, exactly one narration per step using these step_ids in this order "
        f"{[s['step_id'] for s in plan['steps']]}, and a cta. Total narration must reach at "
        f"least {round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)} words (no maximum), "
        f"through explanation, never filler. {_NO_NUMBERS_HOOK_CTA}\n\n"
        f"Write the script now as JSON."
    )
    if gate_errors:
        prompt += ("\n\nYour previous draft FAILED these checks -- fix every one:\n"
                   + "\n".join(f"- {e}" for e in gate_errors))
    if revise_notes:
        if previous_beats:
            prompt += ("\n\nThe reviewer scored this previous draft:\n"
                       + "\n".join(f"- {b['beat']}: {b['narration']}" for b in previous_beats))
        prompt += f"\n\nReviewer notes to address:\n{revise_notes}"
    return prompt


def _critic_prompt(plan: dict, beats: list[dict]) -> str:
    claims = "\n".join(f"- {c['text']}" for s in plan["steps"] for c in s["claims"]
                       if c.get("verdict") == "supported")
    return (f"Verified claims the script may rely on:\n{claims}\n\nScript:\n"
            + json.dumps([{"beat": b["beat"], "narration": b["narration"]} for b in beats],
                         ensure_ascii=False, indent=2))


# ── Writing loop ─────────────────────────────────────────────────────────────
def _write_until_gates_pass(plan, post_meta, factsheet, brand, extra, local_only, *,
                            revise_notes: str = "",
                            previous_beats: list[dict] | None = None
                            ) -> tuple[list[dict], int]:
    terms = terms_by_step_beat(plan)
    errors: list[str] = []
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        doc = text_llm.generate_schema_json(
            _writer_prompt(plan, post_meta, brand, gate_errors=errors or None,
                           revise_notes=revise_notes, previous_beats=previous_beats),
            _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only, role="writer")
        beats, errors = build_beats(doc, plan, factsheet, brand)
        if not errors:
            errors = run_gates(beats, factsheet, brand, extra_by_beat=extra,
                               terms_by_beat=terms)
        if not errors:
            return beats, attempt
        logger.warning(f"script gates failed (attempt {attempt}/{config.LLM_MAX_RETRIES}): "
                       f"{errors}")
    raise HoldForReview(errors)


def run(ctx) -> dict[str, str]:
    """Run the SCRIPT stage and write script.json.

    Raises:
        EngineError: the plan on disk is not verified (run verify_claims first).
        HoldForReview: gates still failing after LLM_MAX_RETRIES attempts, or the final
            critique still below the bar after config.SCRIPT_MAX_REWRITES rewrites.
    """
    ws = Path(ctx.workspace)
    plan = json.loads((ws / "explanation_plan.json").read_text(encoding="utf-8"))
    if plan.get("status") != "verified":
        raise EngineError("explanation_plan.json is not verified; run verify_claims first")
    post_meta = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = bool(ctx.flags.get("local_only", False))
    extra = extra_pool_by_beat(plan)

    beats, attempts = _write_until_gates_pass(plan, post_meta, factsheet, brand, extra,
                                              local_only)
    rewrites = 0
    while True:
        critique = text_llm.generate_schema_json(
            _critic_prompt(plan, beats), _CRITIC_SYSTEM, CRITIQUE_SCHEMA,
            local_only=local_only, role="critic")
        lowest = min(critique["actionable_score"], critique["coherence_score"],
                     critique["hrsu_reason_score"], critique["faithfulness_score"])
        if lowest >= _CRITIQUE_PASS_THRESHOLD:
            break
        if rewrites >= config.SCRIPT_MAX_REWRITES:
            raise HoldForReview([
                f"critique below bar after {rewrites} rewrite(s): {critique['revise_notes']}"])
        rewrites += 1
        logger.info(f"critique below bar ({lowest}), rewrite {rewrites}: "
                    f"{critique['revise_notes']}")
        beats, n = _write_until_gates_pass(plan, post_meta, factsheet, brand, extra,
                                           local_only, revise_notes=critique["revise_notes"],
                                           previous_beats=beats)
        attempts += n

    payload = {"beats": beats, "critique": critique, "attempts": attempts,
               "rewrites": rewrites}
    (ws / "script.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    logger.info(f"script written: {len(beats)} beats, attempts={attempts}, rewrites={rewrites}")
    return {"script": "script.json"}
