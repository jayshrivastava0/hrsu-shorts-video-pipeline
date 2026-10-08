# shorts_engine/explanation.py
"""Pure (no LLM, no I/O) logic for the explanation plan.

Provides the planner's JSON schema, structural validation, the claim-kind
rules, and the number+unit gate that extends the never-unverified invariant
from "every number traces to a source" to "every number also carries a unit
that is known (or defined in the step's terms) AND appears in the source".
That last rule is what stops a model inventing "425 dS/m" from a blog that
only says "425 EC".
"""
from __future__ import annotations

import copy
import re

from shorts_engine import config
from shorts_engine.brand import BrandFacts
from shorts_engine.stages.facts import locate_verbatim, normalize_for_match

CLAIM_KINDS = ("blog_stated", "external_fact", "reasoning", "illustrative")
VERDICTS = ("supported", "contradicted", "unsupported")

CLAIM_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "kind": {"enum": list(CLAIM_KINDS)},
        "quote": {"type": "string"},
    },
    "required": ["id", "text", "kind", "quote"],
    "additionalProperties": False,
}

_STEP_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "step_id": {"type": "string"},
        "claim_text": {"type": "string"},
        "claims": {"type": "array", "items": CLAIM_ITEM_SCHEMA},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"},
                               "definition": {"type": "string"},
                               "unit": {"type": "string"}},
                "required": ["term", "definition", "unit"],
                "additionalProperties": False,
            },
        },
        "visual_intent": {
            "type": "object",
            "properties": {"entities": {"type": "array", "items": {"type": "string"}},
                           "relationship": {"type": "string"},
                           "quantity": {"type": "string"}},
            "required": ["entities", "relationship", "quantity"],
            "additionalProperties": False,
        },
    },
    "required": ["step_id", "claim_text", "claims", "terms", "visual_intent"],
    "additionalProperties": False,
}

PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        # No minItems here (nor on a step's claims): a thin plan must reach
        # validate_plan and HOLD, not fail schema validation and crash the run.
        "steps": {"type": "array", "items": _STEP_ITEM_SCHEMA},
        "payoff": {
            "type": "object",
            "properties": {"takeaway": {"type": "string"},
                           "differentiator_id": {"type": "string"}},
            "required": ["takeaway", "differentiator_id"],
            "additionalProperties": False,
        },
    },
    "required": ["question", "steps", "payoff"],
    "additionalProperties": False,
}

# ── number + unit gate ──────────────────────────────────────────────────────
KNOWN_UNITS = frozenset({
    "%", "percent", "ppm", "ppb", "mg/l", "g/l", "kg/m3", "kg/m³", "g/m3", "g/m³",
    "mg/kg", "g/kg", "kg", "mg", "g", "t", "tonnes", "tons", "l", "ml", "m3", "m³",
    "mm", "cm", "m", "km", "°c", "°f", "ds/m", "ms/cm", "µs/cm", "us/cm", "bar",
    "mpa", "kpa", "psi", "mol/l", "mmol/l", "h", "hr", "hours", "hour", "min",
    "minutes", "minute", "s", "seconds", "days", "day", "weeks", "months", "years",
    "x", "times", "fold", "steps", "stages", "cycles", "batches", "samples",
})

_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<gap>[ \t]*-?)"
    r"(?P<unit>%|°?[A-Za-zµ]+(?:/[A-Za-zµ]+)?[²³23]?)?"
)
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_RANGE_TAIL_RE = re.compile(r"\s*(?:to|-|–|and)\s*\d")
_NO_UNIT_BEFORE_RE = re.compile(r"\b(?:ph|step|stage|phase)\s*$")


def has_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(text or ""))


def _norm_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    unit = unit.lower()
    return "%" if unit == "percent" else unit


def _tokens(text: str) -> list[dict]:
    """Numbers in `text` with their unit. A range opener ("1.5 to 3 kg") takes
    the unit of the number that closes the range."""
    toks: list[dict] = []
    for m in _NUMBER_RE.finditer(text or ""):
        is_open = bool(_RANGE_TAIL_RE.match(text[m.end("num"):]))
        toks.append({
            "num": m.group("num").replace(",", ""),
            "unit": None if is_open else _norm_unit(m.group("unit")),
            "open": is_open,
            "before": text[:m.start()].lower(),
        })
    for i in range(len(toks) - 2, -1, -1):
        if toks[i]["open"]:
            toks[i]["unit"] = toks[i + 1]["unit"]
    return toks


def gate_claim_numbers(text: str, source: str, terms: list[dict] | None = None) -> list[str]:
    """Errors for numbers in `text` that do not trace to `source`, or lack a unit.

    Every number must appear as a standalone number in `source` (not inside a
    word like H2S, not a prefix of a longer decimal). Unless exempt, it must
    carry a unit that is known (KNOWN_UNITS) or defined in `terms`, and the
    exact (number, unit) PAIR must appear in `source` ("%" and "percent" are
    equivalent). A range opener ("1.5 to 3 kg") takes the closing number's
    unit. Years and numbers after pH/step/stage/phase only need to trace.
    """
    src = _tokens(normalize_for_match(source))
    src_nums = {t["num"] for t in src}
    src_pairs = {(t["num"], t["unit"]) for t in src if t["unit"]}
    term_units = {t.get("unit", "").strip().lower() for t in (terms or [])
                  if t.get("unit", "").strip()}
    errs: list[str] = []
    for t in _tokens(text):
        num, unit = t["num"], t["unit"]
        if num not in src_nums:
            errs.append(f"number {num!r} does not trace to the source")
            continue
        if _YEAR_RE.fullmatch(num) or _NO_UNIT_BEFORE_RE.search(t["before"]):
            continue
        if not unit:
            if not t["open"]:
                errs.append(f"number {num!r} has no unit")
        elif unit not in KNOWN_UNITS and unit not in term_units:
            errs.append(f"number {num!r} has unknown unit {unit!r} (not a known unit and "
                        f"not defined in the step's terms)")
        elif (num, unit) not in src_pairs:
            errs.append(f"unit {unit!r} for {num!r} does not appear with that number "
                        f"in the source")
    return errs


# ── structural validation ───────────────────────────────────────────────────
def validate_plan(plan: dict, canonical: str, brand: BrandFacts) -> list[str]:
    """Deterministic checks on a planner draft. Returns error strings (empty = ok)."""
    errs: list[str] = []
    steps = plan.get("steps", [])
    if len(steps) < config.MIN_VERIFIED_STEPS:
        errs.append(f"structure: need at least {config.MIN_VERIFIED_STEPS} steps, "
                    f"got {len(steps)}")
    if has_number(plan.get("question", "")):
        errs.append("question: must not contain numbers")
    if has_number(plan.get("payoff", {}).get("takeaway", "")):
        errs.append("payoff: takeaway must not contain numbers")
    diff_ids = {d["id"] for d in brand.differentiators}
    if plan.get("payoff", {}).get("differentiator_id") not in diff_ids:
        errs.append(f"payoff: differentiator_id must be one of {sorted(diff_ids)}")

    seen_steps: set[str] = set()
    seen_claims: set[str] = set()
    for s in steps:
        sid = s.get("step_id")
        if sid in seen_steps:
            errs.append(f"structure: duplicate step id {sid!r}")
        seen_steps.add(sid)
        if not s.get("claims"):
            errs.append(f"step[{sid}]: needs at least one claim")
        if not s.get("visual_intent", {}).get("entities"):
            errs.append(f"step[{sid}]: visual_intent.entities must not be empty")
        for c in s.get("claims", []):
            cid = c.get("id")
            if cid in seen_claims:
                errs.append(f"structure: duplicate claim id {cid!r}")
            seen_claims.add(cid)
            kind = c.get("kind")
            if kind not in CLAIM_KINDS:
                errs.append(f"claim[{cid}]: unknown kind {kind!r}")
            elif kind == "blog_stated":
                quote = c.get("quote", "")
                if not quote or locate_verbatim(quote, canonical) is None:
                    errs.append(f"claim[{cid}]: blog_stated quote not found verbatim "
                                f"in the article")
                else:
                    errs += [f"claim[{cid}]: {e}"
                             for e in gate_claim_numbers(c["text"], quote, s.get("terms"))]
            elif kind == "illustrative" and has_number(c.get("text", "")):
                errs.append(f"claim[{cid}]: illustrative claims must not contain numbers")
    return errs


# ── plan document helpers ───────────────────────────────────────────────────
def finalize_claim(raw: dict) -> dict:
    """Planner claim -> stored claim (adds support/verdict/needs_number)."""
    return {
        "id": raw["id"],
        "text": raw["text"],
        "kind": raw["kind"],
        "support": {"type": "blog_quote" if raw["kind"] == "blog_stated" else None,
                    "quote": raw.get("quote", ""), "url": None},
        "verdict": None,
        "needs_number": has_number(raw["text"]),
    }


def to_plan_document(raw: dict, attempts: int) -> dict:
    """Planner output -> explanation_plan.json document (input is not mutated)."""
    doc = copy.deepcopy(raw)
    for step in doc["steps"]:
        step["claims"] = [finalize_claim(c) for c in step["claims"]]
    doc["status"] = "planned"
    doc["attempts"] = attempts
    doc["dropped_claims"] = []
    doc["dropped_steps"] = []
    doc["hold_reasons"] = []
    return doc


def supported_claims(step: dict) -> list[dict]:
    return [c for c in step.get("claims", []) if c.get("verdict") == "supported"]
