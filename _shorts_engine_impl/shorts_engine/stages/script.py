"""
SCRIPT stage -- verified explanation_plan.json (+ post.json, factsheet.json) to script.json.

The writer no longer invents the logic: it narrates the verified plan, one beat per
step (an opening that connects the earlier verified claims the step builds on, its
`depends_on`, in the writer's own words; then the mechanism as cause and effect; then the
supporting facts),
with a hook that poses the plan's question and a CTA that answers it from already
narrated verified claims and cites the payoff's brand differentiator. All
deterministic gates stay (number tracing, banned phrases, card_text hygiene, exactly
one differentiator in the CTA, the 30 s floor), plus gate_flow (each step narrates its
mechanism; a term used before the step that defines it is only logged as a warning), but
there are no per-purpose word
budgets and no filler top-up: length follows the content, with no ceiling.

A critic from a different model family then scores the FINAL script (coherence,
actionability, HRSU reason, faithfulness to the verified claims, flow of the explanation
chain). A score below the
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

from shorts_engine import config, explanation, provenance
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import EngineError, HoldForReview
from shorts_engine.llm import text_llm
from shorts_engine.stages.facts import normalize_for_match
from shorts_engine.stages.verify_claims import MIN_QUOTE_WORDS, effective_stance

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
    as a standalone number in that beat's allowed pool (see _allowed_pool). Every such number
    must ALSO carry a unit -- known, or defined in the beat's plan-step terms -- that the pool
    ties to that same number (explanation.gate_claim_numbers), so no unit-less number reaches
    the screen. One error per offending token per text. `extra_by_beat` maps beat name ->
    extra allowed text (verified plan claims); `terms_by_beat` maps beat name -> that plan
    step's `terms`."""
    errs: list[str] = []
    for b in beats:
        beat = b.get("beat")
        extra = (extra_by_beat or {}).get(beat, "")
        fact_ids = b.get("fact_ids", [])
        pool = _allowed_pool(fact_ids, factsheet, brand, extra)
        raw_pool = _pool_text(fact_ids, factsheet, brand, extra)
        terms = (terms_by_beat or {}).get(beat)
        sources = [("narration", b.get("narration", "")), ("card_text", b.get("card_text", ""))]
        sources += [("diagram label", label) for label in b.get("diagram_labels") or []]
        for name, text in sources:
            reported: set[str] = set()
            for tok in extract_numeric_tokens(text):
                if not re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d])", pool):
                    errs.append(f"numbers[{beat}]: {tok!r} in {name} does not "
                                f"trace to any referenced fact")
                    reported.add(tok)
            for e in explanation.gate_claim_numbers(text, raw_pool, terms, source_units=True):
                m = _UNIT_ERR_NUM_RE.search(e)
                num = m.group(1) if m else e
                if num in reported:
                    continue
                reported.add(num)
                errs.append(f"numbers[{beat}]: {e} ({name})")
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


# ── gate_cta_reason: the CTA names its one specific HRSU reason ──────────────
def gate_cta_reason(beats: list[dict], brand: BrandFacts) -> list[str]:
    """The CTA narration must actually STATE the cited differentiator (not just cite its
    id): at least min(2, n) of the differentiator text's content stems appear in it."""
    if not beats:
        return []
    cta = beats[-1]
    diff_ids = {d["id"]: d["text"] for d in brand.differentiators}
    cited = [f for f in cta.get("fact_ids", []) if f in diff_ids]
    if len(cited) != 1:
        return []                     # gate_differentiator reports it
    want = provenance._content_stems(diff_ids[cited[0]])
    if not want:
        return []
    have = provenance._content_stems(cta.get("narration", ""))
    need = min(2, len(want))
    if len(want & have) < need:
        return [f"cta_reason[cta]: the CTA must state its one specific HRSU reason in the "
                f"brand's own words -- {diff_ids[cited[0]]!r} -- and tie it to the problem "
                f"the video explained (without adding any claim beyond it)"]
    return []


# ── gate_attribution: the post's analysis is presented AS analysis ──────────
_ATTRIBUTION_RE = re.compile(
    r"\b(?:hrsu(?:'s)?|the (?:article|post|author|argument|analysis|case)|argues?|argument|"
    r"analysis|according to|in (?:its|the article's|hrsu's) view|makes the case|contends?|"
    r"frames?|the way (?:hrsu|the article)|(?:article|post) (?:says|sets out|lays out|"
    r"proposes|suggests|recommends))\b", re.IGNORECASE)


def is_strategic(plan: dict | None) -> bool:
    return bool(plan) and plan.get("post_kind") == "strategic"


def gate_attribution(beats: list[dict], plan: dict) -> list[str]:
    """Strategic plans: a step beat that narrates any ANALYSIS claim (HRSU's / the post's
    interpretation, risk framing or recommendation) must say whose argument it is ("The
    argument HRSU makes is ...", "HRSU's analysis points to ..."). Attribution, not
    hedging: the claim itself is still stated plainly."""
    errs: list[str] = []
    steps = {f"step_{i}": s for i, s in enumerate(plan.get("steps", []), start=1)}
    for b in beats:
        step = steps.get(b.get("beat"))
        if step is None:
            continue
        analysis = [c for c in explanation.supported_claims(step)
                    if effective_stance(c) == "analysis"]
        if not analysis:
            continue
        narration = b.get("narration", "")
        if not _ATTRIBUTION_RE.search(narration):
            errs.append(f"attribution[{b['beat']}]: this step narrates the post's own analysis "
                        f"({analysis[0]['text']!r}) -- present it as HRSU's / the article's "
                        f"argument, e.g. 'The argument HRSU makes is ...' or 'HRSU's analysis "
                        f"points to ...', not as established fact")
            continue
        # every analysis claim: the sentence that states it (or the one before it) attributes
        sents = provenance.split_sentences(narration)
        for c in analysis:
            want = provenance._content_stems(c.get("text", ""))
            best, best_n = None, 0
            for i, sent in enumerate(sents):
                n = len(want & provenance._content_stems(sent))
                if n > best_n:
                    best, best_n = i, n
            if best is None or best_n < max(2, len(want) * 0.4):
                continue
            window = " ".join(sents[max(0, best - 1):best + 1])
            if not _ATTRIBUTION_RE.search(window):
                errs.append(f"attribution[{b['beat']}]: the sentence {sents[best]!r} states the "
                            f"post's analysis as fact -- attribute it ('The argument HRSU "
                            f"makes is ...', 'HRSU's analysis points to ...')")
    return errs


# ── gate_flow: the script explains, in order ────────────────────────────────
_FLOW_WORD_RE = re.compile(r"[A-Za-z]+")
_FLOW_STOPWORDS = frozenset({
    "about", "above", "after", "again", "against", "along", "also", "among", "because",
    "before", "being", "below", "between", "cannot", "could", "doing", "during", "every",
    "first", "further", "having", "their", "theirs", "there", "these", "thing", "things",
    "those", "through", "under", "until", "where", "which", "while", "whose", "within",
    "without", "would", "should", "other", "others", "rather", "since", "still", "therefore",
    "thus", "though", "together", "toward", "towards", "whether", "becomes", "become",
    "makes", "making", "means", "often", "really", "several", "something", "always",
})


def _content_words(text: str) -> set[str]:
    """Significant words of `text`: >= 5 letters, not a stopword, lowercased."""
    return {w.lower() for w in _FLOW_WORD_RE.findall(text or "")
            if len(w) >= 5 and w.lower() not in _FLOW_STOPWORDS}


_STEM_SUFFIXES = ("ing", "es", "ed", "ly", "s")


def _stem(word: str) -> str:
    """Crude stem: lowercase, strip ONE of -ing/-es/-ed/-ly/-s (keeping >= 3 letters), then
    a trailing 'e', so "Nitrates"/"nitrate", "oxidise"/"oxidises" and "sulfides"/"sulfide"
    meet. Whole words only: "preoxidationx" never matches "oxidation"."""
    w = word.lower()
    for suf in _STEM_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def _has_phrase(phrase: str, text: str) -> bool:
    """Case-insensitive whole-phrase match (no letter/digit directly either side)."""
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
                          text or "", re.IGNORECASE))


def gate_flow(beats: list[dict], plan: dict) -> list[str]:
    """Cheap, conservative checks that the script explains the plan's chain.

    (a) ERROR: every step beat's narration contains at least one significant content word
        of EACH of its step's supported mechanism claims, compared on a crude stem (else
        the mechanism was skipped).
    (b) WARNING only (logged, never an error): a step's defined term (>= 3 chars) used in
        the narration of an EARLIER beat (the hook or an earlier step) unless an earlier
        step also defines it. The writer cannot always fix this (the plan orders the terms;
        explanation.validate_plan enforces that order at plan time) and the critic's
        flow_score is the real guard. Exempt: a term that is in the plan's question (every
        beat before its definition may pose it), and a term that the earlier step's own
        verified claims already use. Whole-word, case-insensitive."""
    errs: list[str] = []
    steps = {f"step_{i}": s for i, s in enumerate(plan.get("steps", []), start=1)}
    for b in beats:
        step = steps.get(b.get("beat"))
        if step is None:
            continue
        narrated = {_stem(w) for w in _FLOW_WORD_RE.findall(b.get("narration", ""))}
        for c in explanation.supported_claims(step):
            if c.get("role") != "mechanism":
                continue
            words = _content_words(c.get("text", ""))
            if words and not any(_stem(w) in narrated for w in words):
                errs.append(f"flow[{b['beat']}]: mechanism not narrated "
                            f"(explain: {c.get('text', '')!r})")
    # first step (by beat position) that defines each term
    order = {b.get("beat"): i for i, b in enumerate(beats)}
    defined_at: dict[str, tuple[int, str, str]] = {}
    for name, step in steps.items():
        if name not in order:
            continue
        for t in step.get("terms", []):
            term = (t.get("term") or "").strip()
            key = term.lower()
            if len(term) < 3:
                continue
            if key not in defined_at or order[name] < defined_at[key][0]:
                defined_at[key] = (order[name], name, term)
    question = plan.get("question", "")
    for key, (pos, name, term) in defined_at.items():
        if _has_phrase(term, question):
            continue
        for b in beats[:pos]:
            if not _has_phrase(term, b.get("narration", "")):
                continue
            earlier = steps.get(b.get("beat"))
            if earlier is not None and any(
                    _has_phrase(term, c.get("text", ""))
                    for c in explanation.supported_claims(earlier)):
                continue
            logger.warning(f"flow[{name}]: term {term!r} used in {b.get('beat')} before "
                           f"{name} defines it -- do not use {term!r} before {name}, or "
                           f"define it where you first use it")
            break
    return errs


# ── run_gates ────────────────────────────────────────────────────────────────
def run_gates(beats: list[dict], factsheet: dict, brand: BrandFacts,
              extra_by_beat: dict[str, str] | None = None,
              terms_by_beat: dict[str, list[dict]] | None = None, *,
              plan: dict | None = None) -> list[str]:
    """Structure check (>= MIN_BEATS beats, final purpose "cta", unique names) then the
    content gates (plus gate_flow when `plan` is given). A structural mismatch
    short-circuits with a single error."""
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
            + gate_differentiator(beats, brand)
            + gate_cta_reason(beats, brand)
            + (gate_flow(beats, plan) if plan is not None else [])
            + (gate_attribution(beats, plan) if is_strategic(plan) else []))


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
        "flow_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "revise_notes": {"type": "string"},
        # optional, machine-checkable fixes: each quotes ONE offending narration sentence
        # and names the verified claim whose wording replaces it
        "edits": {"type": "array", "items": {
            "type": "object",
            "properties": {"beat": {"type": "string"},
                           "offending_sentence": {"type": "string"},
                           "issue": {"type": "string"},
                           "replacement_claim_id": {"type": "string"}},
            "required": ["beat", "offending_sentence", "issue", "replacement_claim_id"],
            "additionalProperties": False}},
    },
    "required": ["actionable_score", "coherence_score", "hrsu_reason_score",
                 "faithfulness_score", "flow_score", "revise_notes"],
    "additionalProperties": False,
}

_NO_NUMBERS_HOOK_CTA = "The hook and the CTA must not contain any numbers."

_WRITER_SYSTEM = (
    "You write the narration for an explainer video for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype, like a good teacher. You "
    "are given a VERIFIED explanation plan: narrate it step by step, in order. The video "
    "must EXPLAIN, not just assert. Open with a hook that poses the plan's question. "
    "Narrate each step in this order: (1) open the step by connecting its BUILDS ON claims "
    "(the earlier verified claims it builds on) to this step in your own words, guided by "
    "its OPENING TRANSITION, so the step visibly follows from them (the first step follows "
    "from the question); use only those claims and this step's verified claims -- a "
    "connector may not add any cause, condition, number or fact; (2) the WHY / HOW (the "
    "mechanism), "
    "explained as cause and effect in plain words, so a viewer who knows nothing about it "
    "can follow why and how it happens, adding no cause, step or condition that its "
    "verified claims do not state -- never skip the why/how; (3) the supporting facts. "
    "Define every term the first time it "
    "is used, using ONLY the definitions provided. Never assume the viewer already knows why "
    "something happens. Convey ONLY the verified claims listed for each step -- never add a "
    "fact, number or unit that is not in the verified claims. The last item is the CTA: "
    "answer the question in one or two sentences using only verified claims you already "
    "narrated plus the approved brand statements, then cite the brand differentiator and "
    "the domain, so the video ends as it "
    "answers the question. For any statement about the company or its product the "
    "CTA may use only the cited differentiator text, the brand CTA lines and the domain; "
    "never invent additional claims about purity, QC, certifications, delivery or pricing. "
    + _NO_NUMBERS_HOOK_CTA + " card_text is at most 7 words and must not repeat the narration. "
    "Length follows the explanation: there is a 30 second floor and no maximum, and you must "
    "never pad with filler."
)

_WRITER_SYSTEM_STRATEGIC = (
    "You write the narration for a short explainer video aimed at EXECUTIVES and senior "
    "procurement decision makers who buy industrial chemicals. Voice: plain, calm, direct, "
    "zero hype and zero jargon: define every business or trade term the first time it is "
    "used, using ONLY the definitions provided. You are given a VERIFIED plan built from a "
    "strategic article: narrate it step by step, in order, as a decision story -- the "
    "situation the buyer is in, the tension or risk in it, the framework or decision "
    "criteria, what it means for the viewer's sourcing decision. Open with a hook that poses "
    "the plan's question. For every step: (1) connect it to the BUILDS ON claims in your own "
    "words, guided by its OPENING TRANSITION, adding no cause, number or fact; (2) say WHY "
    "this point matters to someone making the decision and explain the logic (the "
    "MECHANISM) as cause and effect; (3) then the supporting detail. Claims are tagged: "
    "CHECKABLE FACT = state it plainly, exactly as given; ANALYSIS = the article's / HRSU's "
    "own interpretation or recommendation -- you MUST attribute it, e.g. 'The argument HRSU "
    "makes is ...' or 'HRSU's analysis points to ...' (this is attribution, not hedging: "
    "then state the claim plainly, with no 'might' or 'could'); FRAMEWORK = a decision "
    "criterion: present it as a test the buyer can apply. Never present an ANALYSIS claim "
    "as settled fact and never add a fact, number, name, place, date or unit that is not in "
    "the verified claims. Attribute EVERY sentence that states an ANALYSIS claim, not just "
    "one per step. The last item is the CTA: its first sentence must begin 'The argument "
    "HRSU makes is' and restate, almost word for word, ONE analysis claim you already "
    "narrated (no new recommendation); then state the ONE specific HRSU "
    "reason given (in its own words) and tie it to the decision the video explained, then "
    "the domain. The CTA may use only the cited differentiator text, the brand CTA lines "
    "and the domain; never invent claims about purity, QC, certifications, delivery, "
    "pricing or capabilities. " + _NO_NUMBERS_HOOK_CTA + " card_text is at most 7 words and "
    "must not repeat the narration. Length follows the explanation: there is a 30 second "
    "floor and no maximum, and you must never pad with filler."
)

_CRITIC_SYSTEM_STRATEGIC_EXTRA = (
    " The OPENING TRANSITION lines and the TERM definitions listed in the chain are approved "
    "wording, and a CTA sentence that merely answers the question from claims already "
    "narrated is allowed: do not penalise them; penalise only a NEW fact, number, name, "
    "cause or an ANALYSIS stated as fact. When you list `edits`, quote the exact sentence."
    " This is an executive-audience STRATEGIC script and each verified claim is tagged "
    "CHECKABLE FACT, ANALYSIS or FRAMEWORK. faithfulness_score: an ANALYSIS claim narrated "
    "as established fact (no attribution to HRSU / the article's argument) scores below 5; "
    "a CHECKABLE FACT stated differently from its verified wording, or any fact not listed, "
    "scores below 5. actionable_score: could a decision maker use this to frame their "
    "sourcing decision? coherence_score also asks: is every business term defined, and does "
    "each step say why it matters to the decision? hrsu_reason_score: the CTA must state "
    "the one approved HRSU reason and tie it to the decision."
)

_CRITIC_SYSTEM = (
    "You are a skeptical procurement manager reviewing an explainer script against the "
    "verified claims it was written from. Score 0-10: actionable_score (did I learn "
    "something usable?), coherence_score (does each step follow from the last, are terms "
    "defined before use, is the chemistry described clearly?), hrsu_reason_score (is there "
    "one credible reason to consider HRSU?), faithfulness_score (does the narration assert "
    "ANYTHING not in the verified claims or the approved brand statements? any such addition "
    "scores below 5; in particular, a transition or connector that adds a cause, fact or "
    "claim not in the verified claims -- the claims a step BUILDS ON or the step's own -- "
    "scores faithfulness_score below 5). Approved brand statements in the CTA are allowed "
    "and do not count against faithfulness, but a CTA must not add brand or product claims "
    "beyond them. flow_score judges the chain (does the script EXPLAIN rather than assert? "
    "Does each step open by connecting the earlier verified claims it BUILDS ON to this "
    "step, so it visibly follows from them? Is every MECHANISM actually explained -- the "
    "why/how as cause and effect, not just asserted? Is any term used before it is defined? "
    "Does the ending answer the question?): a bare assertion with no why/how, or a step "
    "that does not connect to what it builds on, scores below 5. "
    "Give concrete revise_notes. For every sentence that adds something not in the verified "
    "claims, ALSO add an `edits` entry: `beat`, the offending sentence copied EXACTLY from the "
    "script, the `issue`, and `replacement_claim_id` = the [id] of the verified claim whose "
    "wording should replace it (empty string if the sentence should simply be removed). When "
    "the CTA lacks a specific reason to choose HRSU, name ONLY an approved brand statement "
    "as the reason; never suggest a capability, certification or claim that is not in the "
    "approved list."
)


# ── Beat construction ────────────────────────────────────────────────────────
def _labels(entities: list[str]) -> list[str]:
    # a label with a digit ("Node 1") is a number on screen that no claim states: skip it
    return [" ".join(e.split()[:3]) for e in entities if e.strip() and not re.search(r"\d", e)][:4]


def _norm_unit_text(text: str) -> str:
    """Lowercase, collapse whitespace, treat "percent" as "%"."""
    return " ".join(text.lower().replace("percent", "%").split())


def _fact_ids_for_step(step: dict, factsheet: dict) -> list[str]:
    """Ids of factsheet facts the step's verified claims actually state.

    A fact attaches only if (1) its verbatim quote overlaps a supported blog quote of the
    step, (2) its numeric value appears as a standalone number in the text of one of the
    step's supported claims, and (3) the fact's value+unit pair passes the number+unit gate
    against those claim texts. A fact that merely shares a sentence with a number-free
    claim (or whose unit the claims do not carry) is never attached, so its value/unit
    never reaches a shot payload."""
    supported = explanation.supported_claims(step)
    claim_texts = [c.get("text", "") for c in supported]
    claim_pool = " | ".join(claim_texts)
    quotes = [normalize_for_match(c["support"]["quote"]) for c in supported
              if c.get("support", {}).get("type") == "blog_quote"
              and len((c["support"]["quote"] or "").split()) >= MIN_QUOTE_WORDS]
    ids = []
    for f in factsheet.get("facts", []):
        fq = normalize_for_match(f["verbatim_quote"])
        if not any(q in fq or fq in q for q in quotes):
            continue
        value = str(f.get("value") or "")
        value_nums = explanation.standalone_numbers(value)
        if not value_nums or not any(value_nums <= explanation.standalone_numbers(t)
                                     for t in claim_texts):
            continue
        if re.search(r"[A-Za-z]", value):      # "3 kg": not a pure numeric value
            continue
        pair = f"{value} {f.get('unit') or ''}".strip()
        if explanation.gate_claim_numbers(pair, claim_pool, step.get("terms"),
                                          source_units=True):
            continue
        # The pair gate sees only the unit's first token; the WHOLE unit string is what
        # reaches the shot payload, so it must appear verbatim in the claim text.
        unit_norm = _norm_unit_text(f.get("unit") or "")
        if not unit_norm or unit_norm not in _norm_unit_text(claim_pool):
            continue
        ids.append(f["id"])
    return ids


def extra_pool_by_beat(plan: dict) -> dict[str, str]:
    """beat name -> text of the step's supported claims (every role) plus the supported
    earlier claims it BUILDS ON (number provenance: the writer may restate a builds-on
    claim when it connects to it).

    Claim TEXT only: claim texts are pair-gated against their sources in verify_claims,
    while a support quote (e.g. a whole retrieved sentence) can carry numbers the claim
    never asserted. A step's `transition` is not a claim and never widens the pool."""
    out = {}
    for pos, step in enumerate(plan["steps"]):
        claims = explanation.supported_claims(step) + _builds_on_claims(plan, pos)
        out[f"step_{pos + 1}"] = " | ".join(c["text"] for c in claims if c.get("text"))
    return out


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
_BUILDS_LABEL = "BUILDS ON (earlier verified claims):"
_TRANSITION_LABEL = "OPENING TRANSITION (guidance, words only):"
_MECH_LABEL = ("WHY / HOW (the mechanism - explain THIS claim as cause and effect in plain "
               "words; add no causes, steps, conditions or facts that are not listed):")
_FACT_LABEL = "SUPPORTING FACTS:"


_STANCE_TAG = {"checkable": "CHECKABLE FACT", "analysis": "ANALYSIS -- attribute to HRSU's "
               "argument", "framework": "FRAMEWORK"}


def _supported_by_role(step: dict, tagged: bool = False) -> dict[str, list[str]]:
    """{"mechanism": [...], "fact": [...]} of "  - claim text" lines for the step's
    supported claims. Any role other than "mechanism" (none, or a legacy stored "link")
    counts as a supporting fact. `tagged` (strategic plans) prefixes each line with the
    claim's stance tag."""
    out: dict[str, list[str]] = {"mechanism": [], "fact": []}
    for c in explanation.supported_claims(step):
        tag = _STANCE_TAG.get(effective_stance(c) or "") if tagged else None
        out["mechanism" if c.get("role") == "mechanism" else "fact"].append(
            f"  - [{tag}] {c['text']}" if tag else f"  - {c['text']}")
    return out


def _builds_on_claims(plan: dict, pos: int) -> list[dict]:
    """The supported claims of EARLIER steps that step `pos` lists in `depends_on`, in
    depends_on order (unknown, unsupported, same-step or later ids are ignored)."""
    by_id = {c.get("id"): c for s in plan["steps"][:pos]
             for c in explanation.supported_claims(s)}
    out, seen = [], set()
    for cid in explanation.depends_on_ids(plan["steps"][pos]):
        if cid in by_id and cid not in seen:
            seen.add(cid)
            out.append(by_id[cid])
    return out


def _transition(step: dict) -> str:
    t = step.get("transition")
    return t.strip() if isinstance(t, str) else ""


def _banned_words(brand: BrandFacts) -> list[str]:
    return list(dict.fromkeys(list(config.FEAR_FILLER_PATTERNS) + list(brand.banned_claims)))


def _writer_prompt(plan: dict, post_meta: dict, brand: BrandFacts, *,
                   gate_errors: list[str] | None = None, revise_notes: str = "",
                   previous_beats: list[dict] | None = None,
                   edit_instructions: list[str] | None = None) -> str:
    # Only verified claim text reaches the writer as content: the planner's step goals
    # (claim_text) and payoff.takeaway are never verified, so they are left out. The one
    # unverified planner text shown is each step's `transition`, and only under the
    # OPENING TRANSITION label as words-only guidance (validate_plan bans digits in it;
    # the writer may not add any cause, condition, number or fact through it).
    blocks = []
    for pos, step in enumerate(plan["steps"]):
        by_role = _supported_by_role(step, tagged=is_strategic(plan))
        builds = [f"  - {c['text']}" for c in _builds_on_claims(plan, pos)]
        if pos == 0:
            builds = ["  - (none: this first step follows directly from the QUESTION above; "
                      "connect it to the QUESTION using only verified claims)"]
        elif not builds:
            builds = ["  - (connect this step to the previous one using only verified claims)"]
        groups = [(_BUILDS_LABEL, builds)]
        if _transition(step):
            groups.append((_TRANSITION_LABEL, [f"  {_transition(step)}"]))
        groups += [(_MECH_LABEL, by_role["mechanism"]),
                   (_FACT_LABEL, by_role["fact"])]
        body = "".join(f"{label}\n" + "\n".join(lines) + "\n"
                       for label, lines in groups if lines)
        terms = "\n".join(f"  - {t['term']}: {t['definition']}" for t in step.get("terms", []))
        blocks.append(f"STEP {step['step_id']}\n" + body
                      + (f"Terms to define:\n{terms}\n" if terms else ""))
    diff = next((d for d in brand.differentiators
                 if d["id"] == plan["payoff"]["differentiator_id"]), None)
    prompt = (
        f"Blog: {post_meta.get('title')} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"QUESTION the video answers: {plan['question']}\n\n" + "\n".join(blocks)
        + "\nCTA: answer the QUESTION in one or two sentences using only the verified claims "
        "you already narrated, then cite the brand differentiator and the domain.\n"
        f"Brand differentiator to cite in the CTA [{plan['payoff']['differentiator_id']}]: "
        f"{diff['text'] if diff else ''}\nCTA domain: {brand.domain}\n"
        "In the CTA, make company or product statements using only the cited differentiator "
        "text, the brand CTA lines and the domain; do not invent additional claims about "
        "purity, QC, certifications, delivery or pricing.\n\n"
        f"Return a hook, exactly one narration per step using these step_ids in this order "
        f"{[s['step_id'] for s in plan['steps']]}, and a cta. Total narration must reach at "
        f"least {round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)} words (no maximum), "
        f"through explanation, never filler. {_NO_NUMBERS_HOOK_CTA}\n"
        f"Never use these words or phrases, even when a verified claim does -- restate the "
        f"idea in neutral words: {', '.join(sorted(_banned_words(brand)))}.\n\n"
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
    if edit_instructions:
        prompt += ("\n\nEDIT INSTRUCTIONS -- apply EACH one exactly; the sentence quoted is "
                   "unsupported and must not come back in any wording:\n"
                   + "\n".join(f"{i}. {t}" for i, t in enumerate(edit_instructions, 1)))
    return prompt


def _critic_prompt(plan: dict, beats: list[dict], brand: BrandFacts) -> str:
    strategic = is_strategic(plan)
    claims = "\n".join(
        f"- [{c['id']}]" + (f" ({_STANCE_TAG.get(effective_stance(c) or '', '')})"
                           if strategic and effective_stance(c) else "") + f" {c['text']}"
        for s in plan["steps"] for c in s["claims"] if c.get("verdict") == "supported")
    diff = next((d for d in brand.differentiators
                 if d["id"] == plan["payoff"]["differentiator_id"]), None)
    approved = ([f"- {diff['text']}"] if diff else []) \
        + [f"- {line}" for line in brand.cta_lines] + [f"- {brand.domain}"]
    chain = []
    for pos, step in enumerate(plan["steps"]):
        by_role = _supported_by_role(step)
        lines = [f"  BUILDS ON (earlier verified claim): {c['text']}"
                 for c in _builds_on_claims(plan, pos)]
        if _transition(step):
            lines.append(f"  OPENING TRANSITION (guidance, not a claim): {_transition(step)}")
        lines += [f"  MECHANISM (why/how): {t[4:]}" for t in by_role["mechanism"]]
        lines += [f"  TERM: {t['term']}: {t['definition']}" for t in step.get("terms", [])]
        chain.append(f"step_{pos + 1}:\n" + ("\n".join(lines) if lines else "  (none)"))
    return (f"QUESTION the video must answer: {plan['question']}\n\n"
            "EXPLANATION CHAIN to judge flow_score against (per step: the earlier verified "
            "claims it builds on, its opening guidance, and the mechanism it must "
            "explain):\n" + "\n".join(chain)
            + f"\n\nVerified claims the script may rely on:\n{claims}\n\n"
            "APPROVED BRAND STATEMENTS (the CTA beat may use these verbatim; they are "
            "pre-approved company statements, not claims to verify, so faithfulness must "
            "NOT be reduced for them; ANY other factual assertion in the CTA beat or any "
            "beat that is not in the verified claims or these approved statements still "
            "scores below 5):\n" + "\n".join(approved) + "\n\nScript:\n"
            + json.dumps([{"beat": b["beat"], "narration": b["narration"]} for b in beats],
                         ensure_ascii=False, indent=2))


# ── Writing loop ─────────────────────────────────────────────────────────────
PROVENANCE_MAX_ROUNDS = 2     # soft pre-critic rewrites per drafting episode (never a hold)


def _write_until_gates_pass(plan, post_meta, factsheet, brand, extra, local_only, *,
                            revise_notes: str = "",
                            previous_beats: list[dict] | None = None,
                            edit_instructions: list[str] | None = None,
                            forbidden: list[str] | None = None
                            ) -> tuple[list[dict], int]:
    """Writer loop: hard gates (a draft that never passes them holds), then the soft
    provenance check -- unsupported sentences, or a sentence the critic already rejected
    coming back, go straight back to the writer as edit instructions without spending a
    critic call. After PROVENANCE_MAX_ROUNDS (or on the last attempt) the draft goes on to
    the critic anyway; provenance never holds a run by itself."""
    terms = terms_by_step_beat(plan)
    errors: list[str] = []
    soft: list[str] = []
    prov_rounds = 0
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        doc = text_llm.generate_schema_json(
            _writer_prompt(plan, post_meta, brand, gate_errors=errors or None,
                           revise_notes=revise_notes, previous_beats=previous_beats,
                           edit_instructions=(edit_instructions or []) + soft or None),
            (_WRITER_SYSTEM_STRATEGIC if is_strategic(plan) else _WRITER_SYSTEM),
            SCRIPT_SCHEMA, local_only=local_only, role="writer")
        beats, errors = build_beats(doc, plan, factsheet, brand)
        if not errors:
            errors = run_gates(beats, factsheet, brand, extra_by_beat=extra,
                               terms_by_beat=terms, plan=plan)
        if not errors:
            findings = provenance.check_provenance(beats, plan, brand)
            soft = provenance.provenance_instructions(findings)
            back = provenance.reappeared(forbidden or [], beats)
            soft += [f"The sentence \"{b}\" was already rejected; it is back. Remove it."
                     for b in back]
            if not soft or prov_rounds >= PROVENANCE_MAX_ROUNDS                     or attempt == config.LLM_MAX_RETRIES:
                return beats, attempt
            prov_rounds += 1
            logger.info(f"provenance: {len(soft)} unsupported sentence(s), rewriting "
                        f"before the critic ({prov_rounds}/{PROVENANCE_MAX_ROUNDS})")
            continue
        soft = []
        logger.warning(f"script gates failed (attempt {attempt}/{config.LLM_MAX_RETRIES}): "
                       f"{errors}")
    raise HoldForReview(errors)


def run(ctx) -> dict[str, str]:
    """Run the SCRIPT stage and write script.json.

    Raises:
        EngineError: the plan on disk is not verified (run verify_claims first).
        HoldForReview: gates still failing after LLM_MAX_RETRIES attempts, or the final
            critique still below the bar after config.SCRIPT_MAX_REWRITES rewrites. The
            reasons are first written to the plan's `hold_reasons` (status unchanged).
    """
    ws = Path(ctx.workspace)
    plan = json.loads((ws / "explanation_plan.json").read_text(encoding="utf-8"))
    if plan.get("status") != "verified":
        raise EngineError("explanation_plan.json is not verified; run verify_claims first")
    post_meta = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = bool(ctx.flags.get("local_only", False))
    if is_strategic(plan):      # a transition must not carry a claim of its own
        for c in provenance.sanitize_transitions(plan):
            logger.info(f"script: cleared transition of {c['step_id']} (new material "
                        f"{c['novel']}): {c['transition']!r}")
    extra = extra_pool_by_beat(plan)

    try:
        beats, critique, attempts, rewrites = _write_and_critique(
            plan, post_meta, factsheet, brand, extra, local_only)
    except HoldForReview as hold:
        # Audit trail: record why on the plan; status stays "verified" so SCRIPT can re-run.
        plan["hold_reasons"] = list(hold.reasons)
        draft = getattr(hold, "draft", None)
        if draft:       # the best draft, for a human deciding whether to release it
            (ws / "script_held_draft.json").write_text(
                json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
        (ws / "explanation_plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        raise

    if plan.get("hold_reasons"):
        # A successful run supersedes the audit trail of an earlier hold.
        plan["hold_reasons"] = []
        (ws / "explanation_plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    payload = {"beats": beats, "critique": critique, "attempts": attempts,
               "rewrites": rewrites}
    (ws / "script.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    logger.info(f"script written: {len(beats)} beats, attempts={attempts}, rewrites={rewrites}")
    try:  # claim -> source attribution for source chips + description; never fails the stage
        from shorts_engine import references
        references.write_outputs(ws)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"references.json not written: {exc}")
    return {"script": "script.json"}


def _write_and_critique(plan, post_meta, factsheet, brand, extra, local_only):
    """Gated writing + critic/rewrite loop -> (beats, critique, attempts, rewrites).
    Raises HoldForReview (gates exhausted or critique still below the bar)."""
    beats, attempts = _write_until_gates_pass(plan, post_meta, factsheet, brand, extra,
                                              local_only)
    rewrites = 0
    surgery_done = False
    forbidden: list[str] = []
    while True:
        critique = text_llm.generate_schema_json(
            _critic_prompt(plan, beats, brand),
            _CRITIC_SYSTEM + (_CRITIC_SYSTEM_STRATEGIC_EXTRA if is_strategic(plan) else ""),
            CRITIQUE_SCHEMA,
            local_only=local_only, role="critic")
        lowest = min(critique["actionable_score"], critique["coherence_score"],
                     critique["hrsu_reason_score"], critique["faithfulness_score"],
                     critique["flow_score"])
        if lowest >= _CRITIQUE_PASS_THRESHOLD:
            break
        if rewrites >= config.SCRIPT_MAX_REWRITES and not surgery_done:
            # Last resort before holding: the writer keeps re-adding a sentence the critic
            # quoted. Deleting a quoted unsupported sentence can only raise faithfulness, so
            # cut exactly those sentences, re-run every gate, and let the critic judge once more.
            edits = provenance.resolve_critic_edits(critique.get("edits"), beats, plan)
            cut = provenance.remove_sentences(beats, edits)
            if cut is not None and not run_gates(cut, factsheet, brand, extra_by_beat=extra,
                                                 terms_by_beat=terms_by_step_beat(plan),
                                                 plan=plan):
                logger.info(f"critique still below bar: removing {len(edits)} quoted "
                            f"sentence(s) and re-scoring")
                beats, surgery_done = cut, True
                continue
        if rewrites >= config.SCRIPT_MAX_REWRITES:
            hold = HoldForReview([
                f"critique below bar after {rewrites} rewrite(s): {critique['revise_notes']}"])
            hold.draft = {"beats": beats, "critique": critique, "rewrites": rewrites}
            raise hold
        rewrites += 1
        logger.info(f"critique below bar ({lowest}), rewrite {rewrites}: "
                    f"{critique['revise_notes']}")
        edits = provenance.resolve_critic_edits(critique.get("edits"), beats, plan)
        forbidden += [e["sentence"] for e in edits]
        beats, n = _write_until_gates_pass(
            plan, post_meta, factsheet, brand, extra, local_only,
            revise_notes=critique["revise_notes"], previous_beats=beats,
            edit_instructions=provenance.critic_instructions(edits), forbidden=forbidden)
        attempts += n
    return beats, critique, attempts, rewrites
