"""Type gate for brief-driven videos: runs the base gate, then checks on-screen text."""
from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path

from scheduler.briefs.facts import Facts
from scheduler.briefs.model import Brief
from scheduler.briefs.types import Recipe
from scheduler.briefs.writer import IN_PROGRESS_KEYWORDS, NEVER_CLAIMED_KEYWORDS
from scheduler.gate import MIN_DURATION_S, GateResult, probe_duration, run_gate, run_lint

# Explainers need at least 30 s (the base gate); a festive marketing ad is deliberately shorter.
MIN_DURATION_BY_TYPE = {"marketing": 15.0}

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_CSR_WORD = re.compile(r"\bcsr\b", re.IGNORECASE)
_NUMBER_FACT_SOURCES = ("documents", "narration", "sources")
_MAX_LISTED_NUMBERS = 10


def on_screen_text(project_dir: Path) -> str:
    """Visible text of the composition HTML, one line per text element."""
    project_dir = Path(project_dir)
    files: list[Path] = []
    comp = project_dir / "compositions"
    if comp.is_dir():
        files.extend(sorted(comp.rglob("*.html")))
    index = project_dir / "index.html"
    if index.is_file() and index not in files:
        files.append(index)

    lines: list[str] = []
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = _COMMENT_RE.sub(" ", raw)
        raw = _SCRIPT_STYLE_RE.sub(" ", raw)
        raw = _TAG_RE.sub("\n", raw)
        raw = html_lib.unescape(raw)
        for line in raw.split("\n"):
            collapsed = " ".join(line.split())
            if collapsed:
                lines.append(collapsed)
    return "\n".join(lines)


def numbers_in(text: str) -> set[str]:
    """Normalised numeric strings in text (thousands commas stripped, trailing % dropped)."""
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        is_percent = token.endswith("%")
        if is_percent:
            token = token[:-1]
        token = token.replace(",", "")
        has_decimal = "." in token
        if token in ("0", "0.0", "0.00"):
            # the static HTML of an animated counter reads "0%" until its script runs; a start value, not a claim
            continue
        if not has_decimal and not is_percent and token.isdigit() and int(token) <= 12:
            # list numbering and small step counts ("step 3") are not claims
            continue
        found.add(token)
    return found


def _script_beats(run_dir: Path) -> list[dict]:
    """Beat dicts from run_dir/script.json; empty when the file is missing."""
    path = Path(run_dir) / "script.json"
    if not path.is_file():
        return []
    beats = json.loads(path.read_text(encoding="utf-8")).get("beats", [])
    return [b for b in beats if isinstance(b, dict)]


def _narration_text(run_dir: Path) -> str:
    """Narration of every beat in run_dir/script.json; empty when the file is missing."""
    return "\n".join(str(b.get("narration") or "") for b in _script_beats(run_dir))


def _beat_texts(run_dir: Path) -> list[str]:
    """Narration, card text and b-roll wish of every beat, one entry per non-empty text."""
    out: list[str] = []
    for b in _script_beats(run_dir):
        for key in ("narration", "card_text", "broll_wish"):
            value = str(b.get(key) or "").strip()
            if value:
                out.append(value)
    return out


def _add(reasons: list[str], message: str) -> None:
    if message not in reasons:
        reasons.append(message)


def run_type_gate(brief: Brief, recipe: Recipe, facts: Facts, run_dir: Path, project_dir: Path, *,
                  allowed_text: str,
                  ffprobe=probe_duration,
                  lint=run_lint) -> GateResult:
    base = run_gate(run_dir, project_dir, ffprobe=ffprobe, lint=lint,
                    min_duration_s=MIN_DURATION_BY_TYPE.get(recipe.name, MIN_DURATION_S))
    reasons: list[str] = list(base.reasons)

    lines = on_screen_text(project_dir).splitlines()
    screen_flat = " ".join(lines).lower()

    # (a) banned phrases and never-claimed terms
    for phrase in facts.banned_claims:
        needle = phrase.strip().lower()
        if needle and needle in screen_flat:
            reasons.append(f"banned claim on screen: {phrase!r}")
    for term in NEVER_CLAIMED_KEYWORDS:
        if term == "csr":
            hit = any(_CSR_WORD.search(line) for line in lines)
        else:
            hit = term in screen_flat
        if hit:
            reasons.append(f"never-claimed term on screen: {term!r}")

    # (b) number tracing against the source text
    if recipe.facts_source in _NUMBER_FACT_SOURCES:
        allowed = numbers_in(allowed_text)
        offending = sorted(numbers_in("\n".join(lines)) - allowed)
        if offending:
            shown = ", ".join(offending[:_MAX_LISTED_NUMBERS])
            reasons.append(f"numbers on screen not found in source text: {shown}")
        if recipe.facts_source == "documents":
            # proof: the narration must not state a number the supplied documents do not contain
            offending_n = sorted(numbers_in(_narration_text(run_dir)) - allowed)
            if offending_n:
                shown = ", ".join(offending_n[:_MAX_LISTED_NUMBERS])
                reasons.append(f"numbers in narration not found in source text: {shown}")

    # (c) in-progress wording must say planned or pending on the same line
    for line in lines:
        low = line.lower()
        for keyword in IN_PROGRESS_KEYWORDS:
            if keyword in low and "planned" not in low and "pending" not in low:
                reasons.append(f"in-progress claim without 'planned' or 'pending': {keyword!r}")
                break

    # (d) the spoken script (narration, card text, b-roll wishes) gets the same banned, never-claimed and
    # in-progress checks as the screen; each beat text counts as one line for the planned/pending exemption
    for text in _beat_texts(run_dir):
        low = text.lower()
        for phrase in facts.banned_claims:
            needle = phrase.strip().lower()
            if needle and needle in low:
                _add(reasons, f"banned claim in script: {phrase!r}")
        for term in NEVER_CLAIMED_KEYWORDS:
            hit = bool(_CSR_WORD.search(text)) if term == "csr" else term in low
            if hit:
                _add(reasons, f"never-claimed term in script: {term!r}")
        for keyword in IN_PROGRESS_KEYWORDS:
            if keyword in low and "planned" not in low and "pending" not in low:
                _add(reasons, f"in-progress claim in script without 'planned' or 'pending': {keyword!r}")
                break

    return GateResult(ok=not reasons, reasons=reasons, video_path=base.video_path,
                      duration_s=base.duration_s, voice_s=base.voice_s)
