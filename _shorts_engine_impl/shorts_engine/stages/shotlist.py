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


def plan_beat_shots(beat: dict, facts: dict, cites: dict, brand) -> list[dict]:
    name = beat["beat"]
    narration = beat["narration"]
    spans = pack_phrases(split_phrases(narration)) or [narration]
    est_total = max(estimate_s(narration), config.SHOT_MIN_S)
    shots: list[dict] = []

    def add(type_, payload, span, fallback=None):
        shots.append({"id": "", "beat": name, "type": type_, "duration_s": 0.0,
                      "narration_span": span, "payload": payload,
                      "fallback": fallback})

    if name == "hook":
        headline_payload = {"text": beat["card_text"],
                            "wish": beat.get("broll_wish", "")}
        wish = (beat.get("broll_wish") or "").strip()
        if wish and len(spans) >= 2:
            add("BROLL", {"wish": wish, "layout": "auto"}, spans[0],
                fallback={"type": "HEADLINE_CARD", "payload": headline_payload})
            add("HEADLINE_CARD", headline_payload, ", ".join(spans[1:]))
        else:
            add("HEADLINE_CARD", headline_payload, narration)
    elif name == "stakes":
        fact = _first_numeric_fact(beat, facts)
        if fact:
            add("STAT_CARD", _stat_payload(fact, beat["card_text"], cites), spans[0])
        else:
            add("HEADLINE_CARD", {"text": beat["card_text"]}, spans[0])
        if len(spans) > 1:
            rest = ", ".join(spans[1:])
            add("HEADLINE_CARD", {"text": beat["card_text"]}, rest)
    elif name == "mechanism":
        labels = beat.get("diagram_labels") or _fallback_labels(narration)
        n_shots = max(1, min(3, len(spans)))
        span_groups = spans[:n_shots - 1] + [", ".join(spans[n_shots - 1:])] \
            if n_shots > 1 else [narration]
        for k, span in enumerate(span_groups, start=1):
            add("DIAGRAM", {"template": "flow", "labels": labels,
                            "reveal_stage": k, "reveal_total": n_shots}, span)
    elif name == "proof":
        fact = _first_numeric_fact(beat, facts) or next(
            (facts[f] for f in beat.get("fact_ids", []) if f in facts), None)
        paper_fact = None
        for fid in beat.get("fact_ids", []):
            f = facts.get(fid)
            m = f.get("citation_marker") if f else None
            if m in cites and cites[m]["kind"] == "paper":
                paper_fact = f
                break
        wish = (beat.get("broll_wish") or "").strip()
        if paper_fact is not None:
            m = paper_fact["citation_marker"]
            quote_fb = {"type": "QUOTE_CARD",
                        "payload": {"quote": paper_fact["verbatim_quote"],
                                    "source": _chip(m, cites)}}
            add("PAPER_CARD", {"marker": m, "url": cites[m]["url"],
                               "highlight": beat["card_text"],
                               "wish": beat.get("broll_wish", "")},
                spans[0], fallback=quote_fb)
            stat = paper_fact if extract_numeric_tokens(str(paper_fact.get("value", ""))) \
                else (fact or paper_fact)
            add("STAT_CARD", _stat_payload(stat, beat["card_text"], cites),
                ", ".join(spans[1:]) or narration)
        elif wish and len(spans) >= 2 and fact is not None:
            stat_payload = _stat_payload(fact, beat["card_text"], cites)
            add("BROLL", {"wish": wish, "layout": "auto"}, spans[0],
                fallback={"type": "STAT_CARD", "payload": stat_payload})
            add("QUOTE_CARD", {"quote": fact["verbatim_quote"],
                               "source": _chip(fact.get("citation_marker"), cites)},
                ", ".join(spans[1:]) or narration)
        elif fact is not None:
            add("STAT_CARD", _stat_payload(fact, beat["card_text"], cites), spans[0])
            add("QUOTE_CARD", {"quote": fact["verbatim_quote"],
                               "source": _chip(fact.get("citation_marker"), cites)},
                ", ".join(spans[1:]) or narration)
        else:
            add("HEADLINE_CARD", {"text": beat["card_text"]}, narration)
    elif name == "cta":
        diff_text = ""
        for fid in beat.get("fact_ids", []):
            for dd in brand.differentiators:
                if dd["id"] == fid:
                    diff_text = dd["text"]
        add("LOGO_CTA", {"differentiator": diff_text,
                         "cta_line": brand.cta_lines[0] if brand.cta_lines else "",
                         "domain": brand.domain}, narration)
    else:
        add("HEADLINE_CARD", {"text": beat.get("card_text", "")}, narration)

    # distribute the beat's estimated duration across its shots, clamped
    per = est_total / len(shots)
    cap = config.LOGO_CTA_MAX_S if name == "cta" else config.SHOT_MAX_S
    for s in shots:
        s["duration_s"] = round(min(max(per, config.SHOT_MIN_S), cap), 2)
    return shots


def lint_shotlist(shots: list[dict], factsheet: dict) -> list[str]:
    errors: list[str] = []
    facts = {f["id"]: f for f in factsheet.get("facts", [])}
    known = {"HEADLINE_CARD", "STAT_CARD", "DIAGRAM", "QUOTE_CARD",
             "PAPER_CARD", "LOGO_CTA", "BROLL"}
    total = 0.0
    for s in shots:
        total += s["duration_s"]
        if s["type"] not in known:
            errors.append(f"{s['id']}: unknown shot type {s['type']}")
        cap = config.LOGO_CTA_MAX_S if s["type"] == "LOGO_CTA" else config.SHOT_MAX_S
        if not (config.SHOT_MIN_S <= s["duration_s"] <= cap):
            errors.append(f"{s['id']}: duration {s['duration_s']} outside "
                          f"[{config.SHOT_MIN_S}, {cap}]")
        if s["type"] in ("PAPER_CARD", "BROLL") and not s.get("fallback"):
            errors.append(f"{s['id']}: {s['type']} requires a declared fallback")
        if s["type"] == "STAT_CARD":
            fact = facts.get(s["payload"].get("fact_id", ""))
            quote_digits = set(extract_numeric_tokens(
                fact["verbatim_quote"])) if fact else set()
            for tok in extract_numeric_tokens(str(s["payload"].get("value", ""))):
                if tok not in quote_digits:
                    errors.append(f"{s['id']}: STAT value token '{tok}' not in "
                                  f"referenced fact quote")
        if s["type"] == "DIAGRAM" and s["payload"].get("template") == "flow":
            n = len(s["payload"].get("labels") or [])
            if not 2 <= n <= 4:
                errors.append(f"{s['id']}: flow diagram needs 2-4 labels, has {n}")
    # Per-shot durations are rounded to 2 decimals in plan_beat_shots, so a
    # script sitting exactly on the boundary can lose up to 0.005s per shot
    # to accumulated rounding (observed live: a 91-word script -> exactly
    # 35.0s estimated -> 9 shots summing to 34.99, failing a strict check
    # whose {:.1f}-formatted message then displayed the impossible-looking
    # "35.0s outside [35.0, 50.0]"). This total is a plan sanity check, not
    # a precision contract -- ASSEMBLE re-flows every duration against the
    # real measured voice audio anyway -- so absorb rounding with an epsilon
    # sized for ~20 shots.
    eps = 0.1
    if not (config.TOTAL_MIN_S - eps <= total <= config.TOTAL_MAX_S + eps):
        errors.append(f"total duration {total:.2f}s outside "
                      f"[{config.TOTAL_MIN_S}, {config.TOTAL_MAX_S}]")
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
