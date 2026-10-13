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
# What a claim does in its step (spec section 12): `mechanism` is the why/how, `fact` is
# supporting detail. How a step follows from earlier ones is NOT a claim: it is the step's
# `depends_on` (ids of earlier claims, checked deterministically) plus a words-only
# `transition` that only guides the narration.
CLAIM_ROLES = ("fact", "mechanism")

CLAIM_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "kind": {"enum": list(CLAIM_KINDS)},
        "role": {"enum": list(CLAIM_ROLES)},
        "quote": {"type": "string"},
    },
    "required": ["id", "text", "kind", "role", "quote"],
    "additionalProperties": False,
}

_STEP_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "step_id": {"type": "string"},
        "claim_text": {"type": "string"},
        "claims": {"type": "array", "items": CLAIM_ITEM_SCHEMA},
        # ids of EARLIER steps' claims this step builds on ([] for the first step)
        "depends_on": {"type": "array", "items": {"type": "string"}},
        # words-only narration guidance (no facts, no digits); may be empty
        "transition": {"type": "string"},
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
    "required": ["step_id", "claim_text", "claims", "depends_on", "transition", "terms",
                 "visual_intent"],
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

# ── post kind (technical vs strategic / executive) ─────────────────────────
POST_KINDS = ("technical", "strategic")
# A claim's `stance` (strategic plans only): what honesty class it belongs to.
#   checkable -- a dated / named / numeric fact (agreement, tariff, volume, price, %, named
#                organisation): needs a cited blog quote or an authority, else it is dropped
#   analysis  -- HRSU's interpretation, risk framing or recommendation: allowed, narrated
#                AS the post's argument (attribution, not hedging)
#   framework -- decision criteria / structure, each element taken from the blog
CLAIM_STANCES = ("checkable", "analysis", "framework")

_STRATEGIC_TERMS = (
    "tariff", "trade policy", "trade agreement", "trade framework", "free trade", r"fta",
    "supply chain", "sourcing strateg", "procurement strateg", r"capex", r"opex",
    "resilien", r"esg", "geopolit", "sanction", "market pulse", "market trend",
    "sustainab", "policy", "strategic", "diversif", "single-source", "executive", "board",
)
_TECHNICAL_TERMS = (
    r"ppm", r"mg/l", r"kg/m", "dosage", "dosing", "corrosion", r"h2s", r"anfo",
    "mechanism", "concentration", "wastewater", "setting time", "oxidi", "solubility",
    "reaction", "slurry", "sulfide", "sulphide", "nitrate ion", "cold weather", "compressive",
)


def _term_score(text: str, terms) -> float:
    total = 0
    for t in terms:
        total += min(5, len(re.findall(t, text)))
    return total


def heuristic_post_kind(title: str, category: str | None, text: str) -> str | None:
    """Cheap, deterministic post-kind guess: "strategic" / "technical", or None when the
    evidence is too close (the planner-role LLM breaks the tie). Title and category count
    triple; body hits are capped per term. Without clear strategic evidence the answer is
    "technical" (the pipeline's original track)."""
    head = f"{title or ''} {category or ''}".lower()
    body = (text or "").lower()
    strat = 3 * _term_score(head, _STRATEGIC_TERMS) + _term_score(body, _STRATEGIC_TERMS)
    tech = 3 * _term_score(head, _TECHNICAL_TERMS) + _term_score(body, _TECHNICAL_TERMS)
    if strat < 8:                    # strategic needs positive evidence; technical is the default
        return "technical"
    if strat >= 1.8 * tech:
        return "strategic"
    if tech >= 1.8 * strat:
        return "technical"
    return None


def _strategic_schemas() -> tuple[dict, dict]:
    claim = copy.deepcopy(CLAIM_ITEM_SCHEMA)
    claim["properties"]["stance"] = {"enum": list(CLAIM_STANCES)}
    claim["required"] = claim["required"] + ["stance"]
    plan = copy.deepcopy(PLAN_SCHEMA)
    plan["properties"]["steps"]["items"]["properties"]["claims"]["items"] = claim
    return claim, plan


STRATEGIC_CLAIM_ITEM_SCHEMA, STRATEGIC_PLAN_SCHEMA = _strategic_schemas()


def plan_schema(post_kind: str | None) -> dict:
    return STRATEGIC_PLAN_SCHEMA if post_kind == "strategic" else PLAN_SCHEMA


def claim_schema(post_kind: str | None) -> dict:
    return STRATEGIC_CLAIM_ITEM_SCHEMA if post_kind == "strategic" else CLAIM_ITEM_SCHEMA


# ── number + unit gate ──────────────────────────────────────────────────────
KNOWN_UNITS = frozenset({
    "%", "percent", "ppm", "ppb", "mg/l", "g/l", "kg/m3", "kg/m³", "g/m3", "g/m³",
    "mg/kg", "g/kg", "kg", "mg", "g", "t", "tonnes", "tons", "l", "ml", "m3", "m³",
    "mm", "cm", "m", "km", "°c", "°f", "ds/m", "ms/cm", "µs/cm", "us/cm", "bar",
    "mpa", "kpa", "psi", "mol/l", "mmol/l", "h", "hr", "hours", "hour", "min",
    "minutes", "minute", "s", "seconds", "days", "day", "weeks", "months", "years",
    "x", "times", "fold", "steps", "stages", "cycles", "batches", "samples",
    # length / distance
    "meter", "meters", "metre", "metres", "kilometer", "kilometers", "kilometre",
    "kilometres", "mile", "miles", "ft", "feet", "foot", "nm", "inch", "inches",
    # money, magnitude and rates
    "usd", "eur", "inr", "gbp", "aud", "cny", "dollar", "dollars", "euro", "euros",
    "rupee", "rupees", "yuan", "million", "billion", "trillion", "thousand", "crore",
    "lakh", "bn", "bps", "pp", "basis",
    # logistics and counts
    "teu", "teus", "container", "containers", "vessel", "vessels", "ship", "ships",
    "shipment", "shipments", "mt", "kt", "mtpa", "tpa", "ktpa", "tonne", "ton",
    "countries", "country", "plants", "suppliers", "months", "month", "week", "year",
    "quarter", "quarters", "$", "€", "£", "₹", "¥",
})
_CURRENCY_SYMBOLS = ("$", "€", "£", "₹", "¥")

_NUMBER_RE = re.compile(
    r"(?<![\w.)\]])(?:(?<![\u00b7*])|(?!\d+[A-Z]))"   # not after ")"/"]"; not a hydrate "·4H2O"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<gap>[ \t]*-?)"
    r"(?P<unit>%|°?[A-Za-zµ]+(?:/[A-Za-zµ]+)?[²³23]?)?"
)
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_RANGE_TAIL_RE = re.compile(r"\s*(?:to|-|–|and)\s*\d")
_NO_UNIT_BEFORE_RE = re.compile(r"\b(?:ph|step|stage|phase)\s*$")


def has_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(text or ""))


def standalone_numbers(text: str) -> set[str]:
    """Standalone numbers in `text` (commas stripped; not digits inside words like H2S)."""
    return {t["num"] for t in _tokens(normalize_for_match(text or ""))}


# Spellings of ONE unit are interchangeable ("1600 meters" == "1600 m"); different units
# never are ("m" != "miles", "g" != "kg").
_UNIT_ALIASES = {
    "percent": "%", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "kilometer": "km", "kilometers": "km", "kilometre": "km", "kilometres": "km",
    "mile": "miles", "foot": "ft", "feet": "ft", "tonne": "tonnes", "ton": "tons",
    "day": "days", "week": "weeks", "month": "months", "year": "years", "hour": "hours",
    "hr": "hours", "container": "containers", "vessel": "vessels", "ship": "ships",
    "shipment": "shipments", "dollar": "dollars", "euro": "euros", "rupee": "rupees",
    "quarter": "quarters", "teus": "teu",
}


def _norm_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    unit = unit.lower()
    return _UNIT_ALIASES.get(unit, unit)


def _tokens(text: str) -> list[dict]:
    """Numbers in `text` with their unit. A range opener ("1.5 to 3 kg") takes
    the unit of the number that closes the range."""
    toks: list[dict] = []
    for m in _NUMBER_RE.finditer(text or ""):
        is_open = bool(_RANGE_TAIL_RE.match(text[m.end("num"):]))
        toks.append({
            "num": m.group("num").replace(",", ""),
            "unit": None if is_open else _norm_unit(m.group("unit")),
            "raw_unit": m.group("unit") or "",
            "open": is_open,
            "before": text[:m.start()].lower(),
        })
    for i in range(len(toks) - 2, -1, -1):
        if toks[i]["open"]:
            toks[i]["unit"] = toks[i + 1]["unit"]
    for t in toks:  # "$1,600": a leading currency symbol is the unit
        if t["unit"] is None and not t["open"] and t["before"].rstrip().endswith(_CURRENCY_SYMBOLS):
            t["unit"] = t["before"].rstrip()[-1]
    return toks


def _is_prose_unit(raw: str) -> bool:
    """A plain lowercase word of >= 3 letters ("meters", "vessels") as the source writes it.
    All-caps / mixed-case tokens ("EC", "dS") are never accepted on the source's say-so."""
    return len(raw) >= 3 and raw.isalpha() and raw == raw.lower()


def gate_claim_numbers(text: str, source: str, terms: list[dict] | None = None, *,
                       source_units: bool = False) -> list[str]:
    """Errors for numbers in `text` that do not trace to `source`, or lack a unit.

    Every number must appear as a standalone number in `source` (not inside a
    word like H2S, not a prefix of a longer decimal). Unless exempt, it must
    carry a unit that is known (KNOWN_UNITS) or defined in `terms`, and the
    exact (number, unit) PAIR must appear in `source` ("%" and "percent" are
    equivalent). A range opener ("1.5 to 3 kg") takes the closing number's
    unit. Years and numbers after pH/step/stage/phase only need to trace.

    `source_units=True` (only for text gated against the blog's OWN words: a blog quote or
    the verified-claim pool) also accepts an otherwise-unknown unit when it is a plain
    lowercase word AND the exact (number, unit) pair appears in `source` -- the source
    states it, so the pair match is what matters ("1600 meters"). The pair requirement and
    the in-word/decimal/substring rules are unchanged; acronyms like "EC" stay unknown.
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
        elif (unit not in KNOWN_UNITS and unit not in term_units
              and not (source_units and _is_prose_unit(t["raw_unit"])
                       and (num, unit) in src_pairs)):
            errs.append(f"number {num!r} has unknown unit {unit!r} (not a known unit and "
                        f"not defined in the step's terms)")
        elif (num, unit) not in src_pairs:
            errs.append(f"unit {unit!r} for {num!r} does not appear with that number "
                        f"in the source")
    return errs


# ── structural validation ───────────────────────────────────────────────────
def validate_plan(plan: dict, canonical: str, brand: BrandFacts, *,
                  post_kind: str | None = None) -> list[str]:
    """Deterministic checks on a planner draft. Returns error strings (empty = ok).
    `post_kind="strategic"` adds the stance rules (see stance_errors)."""
    errs: list[str] = []
    steps = plan.get("steps", [])
    if len(steps) < config.MIN_VERIFIED_STEPS:
        errs.append(f"structure: need at least {config.MIN_VERIFIED_STEPS} steps, "
                    f"got {len(steps)}")
    if not (plan.get("question") or "").strip():
        errs.append("question: must not be empty (state the one question the video answers)")
    if has_number(plan.get("question", "")):
        errs.append("question: must not contain numbers")
    if has_number(plan.get("payoff", {}).get("takeaway", "")):
        errs.append("payoff: takeaway must not contain numbers")
    diff_ids = {d["id"] for d in brand.differentiators}
    if plan.get("payoff", {}).get("differentiator_id") not in diff_ids:
        errs.append(f"payoff: differentiator_id must be one of {sorted(diff_ids)}")

    seen_steps: set[str] = set()
    seen_claims: set[str] = set()
    step_of_claim = {c.get("id"): pos for pos, s in enumerate(steps)
                     for c in s.get("claims", []) if isinstance(c.get("id"), str)}
    for pos, s in enumerate(steps):
        sid = s.get("step_id")
        if sid in seen_steps:
            errs.append(f"structure: duplicate step id {sid!r}")
        seen_steps.add(sid)
        if not s.get("claims"):
            errs.append(f"step[{sid}]: needs at least one claim")
        if not s.get("visual_intent", {}).get("entities"):
            errs.append(f"step[{sid}]: visual_intent.entities must not be empty")
        claims = s.get("claims", [])
        if not any(c.get("role") == "mechanism" and c.get("kind") != "illustrative"
                   for c in claims):
            errs.append(f"step[{sid}]: missing a 'mechanism' claim -- add a claim with role "
                        f"'mechanism' that explains WHY/HOW this happens (what physically or "
                        f"chemically goes on), not just what happens")
        earlier_ids = [cid for cid, p in step_of_claim.items() if p < pos]
        errs += _depends_on_errors(s, pos, step_of_claim, earlier_ids)
        errs += [f"step[{sid}]: {e}" for e in transition_errors(s.get("transition"))]
        for c in claims:
            cid = c.get("id")
            if cid in seen_claims:
                errs.append(f"structure: duplicate claim id {cid!r}")
            seen_claims.add(cid)
            role = c.get("role")
            if role not in CLAIM_ROLES:
                errs.append(f"claim[{cid}]: unknown role {role!r} (must be one of "
                            f"{list(CLAIM_ROLES)})")
            elif role == "mechanism" and c.get("kind") == "illustrative":
                errs.append(f"claim[{cid}]: a 'mechanism' claim must not be illustrative "
                            f"(an analogy is not a mechanism; state the real why/how)")
            kind = c.get("kind")
            if post_kind == "strategic":
                errs += stance_errors(c)
            if kind not in CLAIM_KINDS:
                errs.append(f"claim[{cid}]: unknown kind {kind!r}")
            elif kind == "blog_stated":
                quote = c.get("quote", "")
                if not quote or locate_verbatim(quote, canonical) is None:
                    errs.append(f"claim[{cid}]: blog_stated quote not found verbatim "
                                f"in the article")
                else:
                    errs += [f"claim[{cid}]: {e}"
                             for e in gate_claim_numbers(c["text"], quote, s.get("terms"),
                                                source_units=True)]
            elif kind == "illustrative" and has_number(c.get("text", "")):
                errs.append(f"claim[{cid}]: illustrative claims must not contain numbers")
    return errs + _term_order_errors(plan)


def stance_errors(claim: dict) -> list[str]:
    """Strategic-track rules tying a claim's `stance` to its `kind`:
    checkable -> blog_stated or external_fact; analysis/framework -> blog_stated (the
    post's own words) or reasoning from verified premises -- never an outside "fact";
    illustrative claims may carry any stance."""
    cid, stance, kind = claim.get("id"), claim.get("stance"), claim.get("kind")
    if stance not in CLAIM_STANCES:
        return [f"claim[{cid}]: unknown stance {stance!r} (must be one of {list(CLAIM_STANCES)})"]
    if kind == "illustrative":
        return []
    if stance == "checkable" and kind not in ("blog_stated", "external_fact"):
        return [f"claim[{cid}]: a 'checkable' fact must be blog_stated (a quote that carries "
                f"the blog's citation) or external_fact, not {kind!r}"]
    if stance in ("analysis", "framework") and kind not in ("blog_stated", "reasoning"):
        return [f"claim[{cid}]: an '{stance}' claim is the post's own argument: kind must be "
                f"blog_stated (quote it) or reasoning, not {kind!r}"]
    return []


def transition_errors(transition) -> list[str]:
    """A step's `transition` is words-only narration guidance: a string (may be empty) with
    no digits and at most config.TRANSITION_MAX_WORDS words. Used by validate_plan and by
    the VERIFY repair before it applies a repaired transition."""
    if transition is None:
        return ["transition: missing (use an empty string for none)"]
    if not isinstance(transition, str):
        return ["transition: must be a string"]
    errs = []
    if re.search(r"\d", transition):
        errs.append("transition: must not contain digits (it is words-only guidance; numbers "
                    "belong in verified claims)")
    n = len(transition.split())
    if n > config.TRANSITION_MAX_WORDS:
        errs.append(f"transition: {n} words, at most {config.TRANSITION_MAX_WORDS} words")
    return errs


def _depends_on_errors(step: dict, pos: int, step_of_claim: dict[str, int],
                       earlier_ids: list[str]) -> list[str]:
    """depends_on: a list of claim ids of EARLIER steps; non-empty after the first step,
    empty for the first step. Errors name the valid earlier ids so a retry can converge."""
    sid = step.get("step_id")
    deps = step.get("depends_on")
    choose = f"choose from {sorted(earlier_ids)}" if earlier_ids else "no earlier claims"
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        return [f"step[{sid}]: depends_on must be a list of claim ids"
                + ("" if pos == 0 else f" ({choose})")]
    if pos == 0:
        return ([f"step[{sid}]: depends_on must be empty for the first step (it follows from "
                 f"the question)"] if deps else [])
    errs = []
    if not deps:
        errs.append(f"step[{sid}]: missing depends_on -- list the ids of the earlier claims "
                    f"this step builds on ({choose})")
    for d in deps:
        where = step_of_claim.get(d)
        if where is None:
            errs.append(f"step[{sid}]: depends_on {d!r} is an unknown claim id ({choose})")
        elif where == pos:
            errs.append(f"step[{sid}]: depends_on {d!r} is a claim of the same step; it must "
                        f"point to an earlier step ({choose})")
        elif where > pos:
            errs.append(f"step[{sid}]: depends_on {d!r} is a claim of a later step; it must "
                        f"point to an earlier step ({choose})")
    return errs


_MIN_ORDERED_TERM_LEN = 4


def _uses_term(term: str, text: str) -> bool:
    """Case-insensitive whole-word/phrase match (no letter/digit directly either side)."""
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
                          text or "", re.IGNORECASE))


def _term_order_errors(plan: dict) -> list[str]:
    """A term defined in a step's `terms` must not appear in the claim texts of an EARLIER
    step (unless that or an even earlier step also defines it). Terms shorter than
    _MIN_ORDERED_TERM_LEN chars and terms in the plan's question are ignored."""
    errs: list[str] = []
    steps = plan.get("steps", [])
    question = plan.get("question", "")
    first_def: dict[str, int] = {}
    for pos, s in enumerate(steps):
        for t in s.get("terms") or []:
            key = (t.get("term") or "").strip().lower()
            if key:
                first_def.setdefault(key, pos)
    reported: set[str] = set()
    for pos, s in enumerate(steps):
        for t in s.get("terms") or []:
            term = (t.get("term") or "").strip()
            key = term.lower()
            if (len(term) < _MIN_ORDERED_TERM_LEN or key in reported
                    or first_def.get(key) != pos or _uses_term(term, question)):
                continue
            for earlier in steps[:pos]:
                if any(_uses_term(term, c.get("text", "")) for c in earlier.get("claims", [])):
                    reported.add(key)
                    errs.append(
                        f"terms: {term!r} is defined in step[{s.get('step_id')}] but already "
                        f"used in step[{earlier.get('step_id')}]'s claims -- define it in "
                        f"step[{earlier.get('step_id')}]'s terms where it is first used, or "
                        f"reorder the steps so it is defined before it is used")
                    break
    return errs


# ── plan document helpers ───────────────────────────────────────────────────
def finalize_claim(raw: dict) -> dict:
    """Planner claim -> stored claim (adds support/verdict/needs_number).

    A claim with no `role` (plans stored before roles existed) or with the retired role
    "link" (plans stored before depends_on replaced link claims) becomes a "fact".
    """
    role = raw.get("role")
    out = {
        "id": raw["id"],
        "text": raw["text"],
        "kind": raw["kind"],
        "role": role if role in CLAIM_ROLES else "fact",
        "support": {"type": "blog_quote" if raw["kind"] == "blog_stated" else None,
                    "quote": raw.get("quote", ""), "url": None},
        "verdict": None,
        "needs_number": has_number(raw["text"]),
    }
    if raw.get("stance") in CLAIM_STANCES:
        out["stance"] = raw["stance"]
    return out


def to_plan_document(raw: dict, attempts: int, post_kind: str | None = None) -> dict:
    """Planner output -> explanation_plan.json document (input is not mutated)."""
    doc = copy.deepcopy(raw)
    for step in doc["steps"]:
        step["claims"] = [finalize_claim(c) for c in step["claims"]]
        step["depends_on"] = depends_on_ids(step)
        step["transition"] = step.get("transition") or ""
    doc["status"] = "planned"
    doc["post_kind"] = post_kind or "technical"
    doc["attempts"] = attempts
    doc["dropped_claims"] = []
    doc["dropped_steps"] = []
    doc["hold_reasons"] = []
    return doc


def supported_claims(step: dict) -> list[dict]:
    return [c for c in step.get("claims", []) if c.get("verdict") == "supported"]


def depends_on_ids(step: dict) -> list[str]:
    """A step's `depends_on` claim ids; [] when absent (plans stored before depends_on
    existed) or malformed. Non-string entries are ignored."""
    deps = step.get("depends_on")
    return [d for d in deps if isinstance(d, str)] if isinstance(deps, list) else []
