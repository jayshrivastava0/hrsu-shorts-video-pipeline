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


def plan_beat_shots(beat: dict, facts: dict, cites: dict, brand) -> list[dict]:
    narration = beat["narration"]
    purpose = beat.get("purpose", "other")
    spans = pack_phrases(split_phrases(narration)) or [narration]
    est_total = max(estimate_s(narration), config.SHOT_MIN_S)

    fact = _first_numeric_fact(beat, facts) or next(
        (facts[f] for f in beat.get("fact_ids", []) if f in facts), None)

    shots: list[dict] = []
    for span in spans:
        payload = {"text": beat.get("card_text", "")}
        if fact is not None:
            payload["fact_id"] = fact["id"]
            payload["fact_text"] = fact["verbatim_quote"]
            payload["fact_value"] = fact.get("value")
            payload["fact_unit"] = fact.get("unit")
            payload["fact_citation"] = _chip(fact.get("citation_marker"), cites)
        if purpose == "mechanism":
            payload["diagram_labels"] = beat.get("diagram_labels") or _fallback_labels(narration)
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
            "suggested_type": _suggest_type(purpose, fact), "duration_s": 0.0,
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
