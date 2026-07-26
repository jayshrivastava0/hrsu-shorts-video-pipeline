"""
5-tier fact extraction -> narration -> scenes.
"""
import re
import logging
from typing import Tuple
from video_agent.config import (
    SCRIPT_MIN_NUMERICS, CATEGORY_PUNCH_POINTS,
)
from video_agent.ollama_client import OllamaClient, OllamaError

log = logging.getLogger(__name__)


class ScriptBuilderError(RuntimeError):
    pass


_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub("", html or "")


def _find_all_numerics(text: str) -> list:
    results = []
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(%)", text):
        results.append((m.group(1), m.group(2), m.group(0), m.start()))
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(mg/L)\b", text, re.IGNORECASE):
        results.append((m.group(1), m.group(2), m.group(0), m.start()))
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(kg(?:/t)?)\b", text, re.IGNORECASE):
        results.append((m.group(1), m.group(2), m.group(0), m.start()))
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(hours?|days?|years?)\b", text, re.IGNORECASE):
        results.append((m.group(1), m.group(2), m.group(0), m.start()))
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(?:degrees?\s*C|[°]C)\b", text, re.IGNORECASE):
        results.append((m.group(1), "C", m.group(0), m.start()))
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(ppm|tonnes?)\b", text, re.IGNORECASE):
        results.append((m.group(1), m.group(2), m.group(0), m.start()))
    seen = set()
    out = []
    for item in sorted(results, key=lambda x: x[3]):
        if item[3] not in seen:
            seen.add(item[3])
            out.append(item)
    return out


def _tier1_ollama_numeric(blog_record: dict, client=None) -> list:
    client = client or OllamaClient()
    text = _strip_html(blog_record.get("content_html", ""))[:4000]
    system = (
        "You extract surprising NUMERIC facts from technical chemistry articles. "
        "Each fact must contain a number and a unit. "
        "Return a JSON array. Each item: "
        '{"value": "<number>", "unit": "<unit>", "claim": "<one sentence <= 20 words>", '
        '"source_quote": "<short quote from article>"}'
    )
    prompt = "Extract up to 5 surprising numeric facts from this article:\n\n" + text
    try:
        out = client.generate_json(prompt, system=system)
    except OllamaError as e:
        log.warning("Tier 1 Ollama failed: %s", e)
        return []
    if not isinstance(out, list):
        return []
    return [f for f in out if isinstance(f, dict) and f.get("claim")]


def _tier2_regex_facts(text: str) -> list:
    plain = _strip_html(text)
    found = []
    seen_keys = set()
    for sent in _SENTENCE_RE.findall(plain):
        sent = sent.strip()
        matches = _find_all_numerics(sent)
        for value, unit, match_str, _ in matches:
            key = (sent[:200], match_str)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append({
                "value": value,
                "unit": unit,
                "claim": sent[:200],
                "source_quote": sent[:200],
            })
    return found


def _tier3_ollama_qualitative(blog_record: dict, client=None) -> list:
    client = client or OllamaClient()
    text = _strip_html(blog_record.get("content_html", ""))[:4000]
    system = (
        "You distill qualitative punch points from technical articles. "
        "Look for: named regulations, named processes, contrarian claims, "
        "named risks, named entities. Each point must be <= 15 words. "
        'Return a JSON array of strings: ["point one", "point two", ...]'
    )
    prompt = "Give me 5 surprising non-numeric punch points from:\n\n" + text
    try:
        out = client.generate_json(prompt, system=system)
    except OllamaError as e:
        log.warning("Tier 3 Ollama failed: %s", e)
        return []
    return [str(p)[:200] for p in out if p] if isinstance(out, list) else []


def _tier4_template(category: str) -> list:
    points = CATEGORY_PUNCH_POINTS.get(category, [])
    return [{"claim": p, "value": "", "unit": "", "source_quote": ""} for p in points]


def extract_facts(blog_record: dict) -> Tuple[list, dict]:
    meta = {
        "tier_used": 0,
        "numeric_count": 0,
        "punch_points_count": 0,
        "fell_back_to_template": False,
    }

    facts = _tier1_ollama_numeric(blog_record)
    meta["numeric_count"] = len(facts)
    if len(facts) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 1
        return facts, meta

    regex_facts = _tier2_regex_facts(blog_record.get("content_html", ""))
    if facts and regex_facts:
        combined = facts + [r for r in regex_facts
                            if r["claim"] not in {f["claim"] for f in facts}]
    else:
        combined = facts or regex_facts
    if len(combined) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 2 if not facts else 1
        meta["numeric_count"] = len(combined)
        return combined, meta

    qual = _tier3_ollama_qualitative(blog_record)
    qual_facts = [{"claim": p, "value": "", "unit": "", "source_quote": ""} for p in qual]
    pool = combined + qual_facts
    meta["punch_points_count"] = len(qual_facts)
    if len(pool) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 3
        return pool, meta

    cat = blog_record.get("category", "")
    template_facts = _tier4_template(cat)
    if template_facts:
        meta["tier_used"] = 4
        meta["fell_back_to_template"] = True
        log.warning("Tier 4 template fallback for blog %s (category=%s)",
                    blog_record.get("blog_id"), cat)
        return pool + template_facts, meta

    raise ScriptBuilderError(
        "no fact extraction tier produced output for blog "
        + str(blog_record.get("blog_id"))
        + " (category=" + repr(cat) + ")"
    )


