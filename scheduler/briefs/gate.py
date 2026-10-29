"""Type gate for brief-driven videos: runs the base gate, then checks on-screen text."""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

from scheduler.briefs.facts import Facts
from scheduler.briefs.model import Brief
from scheduler.briefs.types import Recipe
from scheduler.briefs.writer import IN_PROGRESS_KEYWORDS, NEVER_CLAIMED_KEYWORDS
from scheduler.gate import GateResult, probe_duration, run_gate, run_lint

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
        if not has_decimal and not is_percent and token.isdigit() and int(token) <= 12:
            # list numbering and small step counts ("step 3") are not claims
            continue
        found.add(token)
    return found


def run_type_gate(brief: Brief, recipe: Recipe, facts: Facts, run_dir: Path, project_dir: Path, *,
                  allowed_text: str,
                  ffprobe=probe_duration,
                  lint=run_lint) -> GateResult:
    base = run_gate(run_dir, project_dir, ffprobe=ffprobe, lint=lint)
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

    # (c) in-progress wording must say planned or pending on the same line
    for line in lines:
        low = line.lower()
        for keyword in IN_PROGRESS_KEYWORDS:
            if keyword in low and "planned" not in low and "pending" not in low:
                reasons.append(f"in-progress claim without 'planned' or 'pending': {keyword!r}")
                break

    return GateResult(ok=not reasons, reasons=reasons, video_path=base.video_path,
                      duration_s=base.duration_s, voice_s=base.voice_s)
