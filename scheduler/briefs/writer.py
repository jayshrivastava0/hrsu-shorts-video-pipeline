"""Script writer for brief-driven videos: beats, phrase rewrites, support files."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from scheduler.briefs.facts import Facts
from scheduler.briefs.model import Brief
from scheduler.briefs.research import Source
from scheduler.briefs.types import Recipe

IN_PROGRESS_KEYWORDS = (
    "electric",
    "other boilers",
    "all boilers",
    "other reaction vessels",
    "other vessels",
    "raised chimney",
    "taller chimney",
)

NEVER_CLAIMED_KEYWORDS = ("csr", "corporate social responsibility")
_CSR_WORD = re.compile(r"\bcsr\b", re.IGNORECASE)

MAX_ATTEMPTS = 3
_KINDS = ("fact", "opinion", "story")
_MALFORMED = "malformed beat (not an object)"

Llm = Callable[[str, str, dict], dict]


class WriterFailed(Exception):
    """Raised when the model keeps using banned or in-progress phrases."""

    def __init__(self, phrases: list[str]):
        self.phrases = list(phrases)
        super().__init__("script writer failed; offending phrases: " + ", ".join(self.phrases))


_BEAT_SCHEMA = {
    "type": "object",
    "properties": {
        "beat": {"type": "string"},
        "purpose": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
        "narration": {"type": "string"},
        "card_text": {"type": "string"},
        "broll_wish": {"type": "string"},
        "kind": {"type": "string", "enum": list(_KINDS)},
    },
    "required": ["beat", "purpose", "narration", "card_text", "broll_wish", "kind"],
}

_SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": ["string", "null"]},
        "beats": {"type": "array", "minItems": 5, "maxItems": 8, "items": _BEAT_SCHEMA},
    },
    "required": ["beats"],
}

_FACTS_RULES = {
    "narration": "State only the facts listed below and what the source snippets say.",
    "documents": "State only the facts listed below; they come from the supplied documents.",
    "facts_and_sources": "State only the facts listed below, backed by the source snippets.",
    "sources": "State only what the source snippets support.",
    "facts_only": "State only the facts listed below. Do not add claims beyond them.",
}

_TYPE_RULES = {
    "marketing": "This is marketing: beats may be story or opinion, thesis must be null.",
}


def _system_prompt(recipe: Recipe) -> str:
    rules = _FACTS_RULES.get(recipe.facts_source, _FACTS_RULES["facts_only"])
    lines = [
        "You write the script for one short video for HRSU, a calcium nitrate supplier.",
        f"Video type: {recipe.name}.",
        rules,
        _TYPE_RULES.get(recipe.name, "Each fact beat must be a claim the sources support."),
        "Never state a fact that is not in the facts list or the source snippets.",
        "Do not use hedging words. Do not add disclaimers or preamble.",
    ]
    return "\n".join(lines)


def _user_prompt(brief: Brief, recipe: Recipe, facts: Facts, sources: list[Source], feedback: str) -> str:
    lines = [f"Brief: {brief.text}", "", "Facts you may state:"]
    lines += [f"- {f}" for f in facts.in_place]
    lines += ["", "Source snippets:"]
    for s in sources:
        lines.append(f"[{s.id}] {s.title} ({s.publisher}): {s.snippet}")
    lines += [
        "",
        "Write 5-8 beats. The first beat is a hook with purpose \"hook\".",
        f"The last beat must end with this exact line: {recipe.cta_line}",
        "Each beat has kind \"fact\", \"opinion\" or \"story\".",
        "thesis is the single core claim, equal to the narration of one fact beat; null for marketing.",
    ]
    if feedback:
        lines += ["", feedback]
    return "\n".join(lines)


def _normalise_beats(raw: list | None, recipe: Recipe) -> tuple[list[dict], bool]:
    """Return (beats, malformed). Non-dict items are dropped and flagged, never trusted."""
    beats: list[dict] = []
    malformed = False
    for i, b in enumerate(raw or [], 1):
        if not isinstance(b, dict):
            malformed = True
            continue
        kind = str(b.get("kind") or "fact")
        beats.append({
            "beat": str(b.get("beat") or f"beat_{i:02d}"),
            "purpose": str(b.get("purpose") or "mechanism"),
            "fact_ids": [str(x) for x in b.get("fact_ids") or []],
            "narration": str(b.get("narration") or "").strip(),
            "card_text": str(b.get("card_text") or ""),
            "broll_wish": str(b.get("broll_wish") or ""),
            "kind": kind if kind in _KINDS else "fact",
        })
    if not beats or malformed:
        return beats, malformed
    beats[0]["purpose"] = "hook"
    last = beats[-1]
    if len(beats) > 1:
        last["purpose"] = "cta"
    if not last["narration"].endswith(recipe.cta_line):
        last["narration"] = (last["narration"] + " " + recipe.cta_line).strip()
    return beats, False


def _offending(beats: list[dict], banned: list[str]) -> list[str]:
    """Phrases that must not appear in any beat's narration, card_text or broll_wish."""
    needles = [n for n in [*banned, *IN_PROGRESS_KEYWORDS, *NEVER_CLAIMED_KEYWORDS] if n and n.strip()]
    found: list[str] = []
    for b in beats:
        text = " ".join(b.get(k, "") for k in ("narration", "card_text", "broll_wish")).lower()
        for n in needles:
            hit = bool(_CSR_WORD.search(text)) if n == "csr" else n.lower() in text
            if hit and n not in found:
                found.append(n)
    return found


def _thesis(raw_thesis, beats: list[dict], recipe: Recipe) -> str | None:
    if recipe.name == "marketing":
        return None
    fact_lines = [b["narration"] for b in beats if b["kind"] == "fact"]
    if not fact_lines:
        return None
    if raw_thesis in fact_lines:
        return raw_thesis
    return fact_lines[0]


def write_script(
    brief: Brief,
    recipe: Recipe,
    facts: Facts,
    sources: list[Source],
    llm: Llm,
) -> dict:
    """Ask `llm` for a beat script, rewriting on banned or in-progress phrases (max 3 calls)."""
    system = _system_prompt(recipe)
    feedback = ""
    offending: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = llm(system, _user_prompt(brief, recipe, facts, sources, feedback), _SCRIPT_SCHEMA)
        if not isinstance(result, dict):
            result = {}
        raw_beats = result.get("beats")
        if not isinstance(raw_beats, list):
            raw_beats = []
        beats, malformed = _normalise_beats(raw_beats, recipe)
        if malformed:
            offending = [_MALFORMED]
        elif not beats:
            offending = ["no beats returned"]
        else:
            offending = _offending(beats, facts.banned_claims)
        if not offending:
            return {
                "beats": beats,
                "critique": {},
                "attempts": attempt,
                "rewrites": attempt - 1,
                "thesis": _thesis(result.get("thesis"), beats, recipe),
            }
        feedback = (
            "Rewrite the script. Remove these phrases entirely, they are not allowed: "
            + ", ".join(offending)
        )
    raise WriterFailed(offending)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return seen


def write_support_files(run_dir: Path, brief: Brief, script: dict, sources: list[Source]) -> None:
    """Write script.json, references.json, video_description.txt and explanation_plan.json."""
    run_dir.mkdir(parents=True, exist_ok=True)
    beats = script.get("beats") or []
    fact_beats = [b for b in beats if b.get("kind") == "fact"]

    (run_dir / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    claims = {
        f"c{i}": {
            "text": b["narration"],
            "sources": [],
            "tier": "unverified",
            "chip": None,
            "stance": "checkable",
        }
        for i, b in enumerate(fact_beats, 1)
    }
    chips = _unique([s.publisher for s in sources])
    references = {
        "claims": claims,
        "sources": [
            {"id": s.id, "title": s.title, "publisher": s.publisher, "url": s.url,
             "kind": "web", "tier": "unverified"}
            for s in sources
        ],
        "post_kind": brief.type,
        "chips": chips,
    }
    (run_dir / "references.json").write_text(
        json.dumps(references, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    hook = beats[0]["narration"] if beats else ""
    cta = beats[-1]["narration"] if beats else ""
    lines = [hook, "", "Sources"]
    lines += [f"{i}. {s.publisher} - {s.url}" for i, s in enumerate(sources, 1)]
    lines += ["", "This video presents HRSU's analysis.", "", cta]
    (run_dir / "video_description.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    title = next((ln.strip() for ln in brief.text.splitlines() if ln.strip()), brief.id)
    plan = {"question": title, "post_kind": brief.type}
    (run_dir / "explanation_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
