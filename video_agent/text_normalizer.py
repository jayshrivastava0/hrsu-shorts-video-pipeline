"""
Convert blog narration text into TTS-friendly form.
Handles chemistry notation, units, citations, markdown.
Order of replacements matters — formulas first, then units, then strip.
"""
import re

# (pattern, replacement) — order matters: most-specific first.
_REPLACEMENTS = [
    # Formulas (specific → general)
    (re.compile(r"Ca\(NO[₃3]\)[₂2]"), "calcium nitrate"),
    (re.compile(r"H₂S"), "H 2 S"),
    (re.compile(r"\bH2S\b"), "H 2 S"),
    (re.compile(r"CO₂"), "C O 2"),
    (re.compile(r"\bCO2\b"), "C O 2"),
    # Units
    (re.compile(r"\bmg/L\b"), "milligrams per liter"),
    (re.compile(r"\bkg/t\b"), "kilograms per tonne"),
    (re.compile(r"%"), " percent"),
    (re.compile(r"°C"), " degrees Celsius"),
    # Domain
    (re.compile(r"hrsuindore\.com", re.IGNORECASE), "H R S U Indore dot com"),
    # Strip citation markers like [1], [12]
    (re.compile(r"\[\d+\]"), ""),
    # Strip markdown emphasis (bold first, then italic)
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    # Collapse whitespace (last)
    (re.compile(r"\s+"), " "),
]


def normalize_for_tts(text: str) -> str:
    """Return TTS-safe version of `text`."""
    out = text
    for pattern, repl in _REPLACEMENTS:
        out = pattern.sub(repl, out)
    return out.strip()
