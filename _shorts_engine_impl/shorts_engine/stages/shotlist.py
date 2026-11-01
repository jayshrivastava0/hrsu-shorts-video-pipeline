"""Stage 4 — SHOTLIST: deterministic beat→shots expansion + linter. No LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.errors import GateFailure
from shorts_engine.stages.script import extract_numeric_tokens

logger = logging.getLogger(__name__)

_PHRASE_SPLIT = re.compile(r"[,.;:]+")


def split_phrases(text: str) -> list[str]:
    return [p.strip() for p in _PHRASE_SPLIT.split(text) if p.strip()]


def estimate_s(text: str) -> float:
    return len(text.split()) / config.WORDS_PER_SECOND


def _split_long_phrase(phrase: str) -> list[str]:
    """Subdivide a single phrase whose own estimate exceeds SHOT_TARGET_MAX_S
    into word-count chunks that each fit. Without this, a long sentence with
    no internal comma/period/semicolon/colon (so split_phrases returns it as
    one piece) would pass through pack_phrases unchanged, and
    plan_beat_shots's final per-shot hard-cap clamp would then silently
    truncate -- and lose -- the excess seconds instead of carrying them into
    a second shot."""
    words = phrase.split()
    max_words = max(1, int(config.SHOT_TARGET_MAX_S * config.WORDS_PER_SECOND))
    chunks = [" ".join(words[i:i + max_words])
              for i in range(0, len(words), max_words)]
    return chunks or [phrase]


def pack_phrases(phrases: list[str]) -> list[str]:
    """Greedy-pack phrases into spans targeting SHOT_TARGET_MIN..MAX seconds.
    Phrases that individually exceed SHOT_TARGET_MAX_S are first subdivided
    by word count (see _split_long_phrase) so no narration time is silently
    lost to the downstream per-shot hard-cap clamp."""
    expanded: list[str] = []
    for ph in phrases:
        if estimate_s(ph) > config.SHOT_TARGET_MAX_S:
            expanded.extend(_split_long_phrase(ph))
        else:
            expanded.append(ph)

    spans, cur = [], ""
    for ph in expanded:
        trial = (cur + ", " + ph).strip(", ") if cur else ph
        if estimate_s(trial) <= config.SHOT_TARGET_MAX_S or not cur:
            cur = trial
        else:
            spans.append(cur)
            cur = ph
    if cur:
        if spans and estimate_s(cur) < config.SHOT_TARGET_MIN_S / 2:
            spans[-1] = spans[-1] + ", " + cur
        else:
            spans.append(cur)
    return spans


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _chip(marker: int | None, cites: dict) -> str:
    if marker is None or marker not in cites:
        return "Source — HRSU blog"
    return f"Source [{marker}] — {_domain(cites[marker]['url'])}"


def _first_numeric_fact(beat: dict, facts: dict) -> dict | None:
    for fid in beat.get("fact_ids", []):
        f = facts.get(fid)
        if f and extract_numeric_tokens(str(f.get("value", ""))):
            return f
    return None


def _stat_payload(fact: dict, label: str, cites: dict) -> dict:
    return {"value": str(fact["value"]), "unit": str(fact.get("unit") or ""),
            "label": label, "citation": _chip(fact.get("citation_marker"), cites),
            "fact_id": fact["id"]}


def _fallback_labels(narration: str) -> list[str]:
    phrases = split_phrases(narration)[:3]
    labels = [" ".join(p.split()[:4]) for p in phrases if p]
    return labels if len(labels) >= 2 else (labels + ["Result"])[:2]


def _suggest_type(purpose: str, fact: dict | None) -> str:
    """Advisory only -- Task 4's authoring subagent may override this. Never
    read by lint_shotlist or any gate."""
    if purpose == "cta":
        return "LOGO_CTA"
    if purpose == "mechanism":
        return "DIAGRAM"
    if fact is not None:
        return "STAT_CARD"
    return "HEADLINE_CARD"


def _continuation_text(narration_span: str) -> str:
    """On-screen text for a shot that is NOT the first one produced from a
    beat split by pack_phrases (see plan_beat_shots). Every shot built from
    one beat used to get the identical `payload["text"]` (the beat's static
    `card_text`) regardless of how many on-screen cuts the beat's narration
    was split into -- confirmed in real runs as literal back-to-back
    duplicate cards (e.g. "Soil Retention Risks" shown twice in a row,
    "Substrate Simulation Setup" twice, "425 EC" three times -- the latter
    two needed the fact/diagram_labels handling below too, not just this
    function, since pack_phrases only varies `narration_span`, never the
    rest of the payload). Splitting itself is correct and necessary --
    SHOT_MAX_S caps how long any one card can hold the screen, so long
    narration must still cut -- but repeating the exact same card for every
    cut wastes the cut instead of using it. Draws this shot's on-screen text
    from what THIS shot's own narration is actually saying (first few
    words) instead of the beat's static headline, so a multi-shot beat
    reads as a short sequence of related points rather than the same card
    shown again. `.title()` mangles acronyms/units (e.g. "mg/l" ->
    "Mg/L") but every composition's own CSS already applies
    `text-transform: uppercase` to headline text (confirmed in the real
    compositions this bug came from), so the exact input casing never
    reaches the screen -- this only has to be reasonable for any
    non-visual consumer (the vision judge's `_expected_text`, logs). A
    continuation shot never carries a `fact_id` (see plan_beat_shots), so a
    number pulled straight from its own narration would land on screen with
    no citation behind it -- contradicts the persona's own verbatim-facts
    rule and the project's never-unverified rule alike. Numeric words are
    filtered out of the candidate words before taking the first six, rather
    than filtered out of the final slice, so a dropped number doesn't just
    shorten the card -- the next real word slides in instead."""
    words = [w for w in narration_span.split() if not extract_numeric_tokens(w)]
    if not words:
        words = narration_span.split()  # every word was numeric -- fall back rather than go blank
    return " ".join(words[:6]).rstrip(",;:.").title()


def plan_beat_shots(beat: dict, facts: dict, cites: dict, brand) -> list[dict]:
    narration = beat["narration"]
    purpose = beat.get("purpose", "other")
    spans = pack_phrases(split_phrases(narration)) or [narration]
    est_total = max(estimate_s(narration), config.SHOT_MIN_S)

    fact = _first_numeric_fact(beat, facts) or next(
        (facts[f] for f in beat.get("fact_ids", []) if f in facts), None)
    diagram_labels = beat.get("diagram_labels") or _fallback_labels(narration)

    shots: list[dict] = []
    for i, span in enumerate(spans):
        # Only the FIRST shot of a split beat carries the beat's own static
        # headline/fact -- see _continuation_text's doc comment for why.
        # `shot_fact` (not the beat-level `fact`) drives BOTH the payload
        # fields below AND suggested_type, so a continuation shot of a
        # numeric-fact beat degrades from a repeated STAT_CARD to a
        # HEADLINE_CARD carrying this shot's own narration instead --
        # matching the scene-author-persona's own rule ("no fact_text in
        # the brief -> do not display a specific number at all").
        shot_fact = fact if i == 0 else None
        card_text = beat.get("card_text", "") if i == 0 else _continuation_text(span)
        payload = {"text": card_text}
        if shot_fact is not None:
            payload["fact_id"] = shot_fact["id"]
            payload["fact_text"] = shot_fact["verbatim_quote"]
            payload["fact_value"] = shot_fact.get("value")
            payload["fact_unit"] = shot_fact.get("unit")
            payload["fact_citation"] = _chip(shot_fact.get("citation_marker"), cites)
        if purpose == "mechanism":
            payload["diagram_labels"] = diagram_labels
            if len(spans) > 1:
                # A multi-shot mechanism beat would otherwise render the
                # IDENTICAL diagram (same labels, same implied layout) on
                # every cut. `focus_label` cycles through the beat's own
                # labels one per shot so the authoring subagent has a
                # concrete reason to vary each cut (e.g. highlight that
                # one step) instead of redrawing the same diagram.
                payload["focus_label"] = diagram_labels[i % len(diagram_labels)]
        if purpose == "cta":
            diff_text = ""
            for fid in beat.get("fact_ids", []):
                for dd in brand.differentiators:
                    if dd["id"] == fid:
                        diff_text = dd["text"]
            payload["differentiator"] = diff_text
            payload["cta_line"] = brand.cta_lines[0] if brand.cta_lines else ""
            payload["domain"] = brand.domain
        shots.append({
            "id": "", "beat": beat.get("beat", purpose), "beat_purpose": purpose,
            "suggested_type": _suggest_type(purpose, shot_fact), "duration_s": 0.0,
            "narration_span": span, "payload": payload,
            "broll_wish": beat.get("broll_wish", ""),
        })

    per = est_total / len(shots)
    cap = config.LOGO_CTA_MAX_S if purpose == "cta" else config.SHOT_MAX_S
    for s in shots:
        s["duration_s"] = round(min(max(per, config.SHOT_MIN_S), cap), 2)
    return shots


def lint_shotlist(shots: list[dict], factsheet: dict) -> list[str]:
    errors: list[str] = []
    total = 0.0
    for s in shots:
        total += s["duration_s"]
        cap = config.LOGO_CTA_MAX_S if s.get("beat_purpose") == "cta" else config.SHOT_MAX_S
        if not (config.SHOT_MIN_S <= s["duration_s"] <= cap):
            errors.append(f"{s['id']}: duration {s['duration_s']} outside "
                          f"[{config.SHOT_MIN_S}, {cap}]")
    eps = 0.1
    if total < config.TOTAL_MIN_S - eps:
        errors.append(f"total duration {total:.2f}s under the {config.TOTAL_MIN_S}s floor")
    return errors


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    script_doc = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    from shorts_engine.brand import load_brand_facts
    brand = load_brand_facts()
    facts = {f["id"]: f for f in factsheet.get("facts", [])}
    cites = {c["marker"]: c for c in post.get("citations", [])}

    shots: list[dict] = []
    for beat in script_doc["beats"]:
        shots.extend(plan_beat_shots(beat, facts, cites, brand))
    for i, s in enumerate(shots):
        s["id"] = f"s{i:02d}"
    errors = lint_shotlist(shots, factsheet)
    if errors:
        raise GateFailure(errors)
    total = round(sum(s["duration_s"] for s in shots), 2)
    out = {"shots": shots, "total_s": total}
    (ws / "shotlist.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    logger.info("shotlist: %d shots, %.1fs", len(shots), total)
    return {"shotlist": "shotlist.json"}
