"""The grading rubric: the written definition-of-done contract the verifier
grades against (Anthropic 'contract negotiation' principle). Emitted during
PLAN as rubric.json so it exists before any artifact does."""
from __future__ import annotations
import json
from pathlib import Path

DEFAULT_CRITERIA: list[dict] = [
    {"key": "visual_match",
     "description": ("Does the image plausibly illustrate what the narration "
                     "for this scene is saying? Generic stock that could "
                     "accompany any topic scores <=4.")},
    {"key": "readability",
     "description": ("Is all on-screen text fully visible, uncropped, and "
                     "readable at a glance on a phone?")},
    {"key": "framing",
     "description": ("Is the subject well-framed for 9:16 vertical? No "
                     "squashed/stretched imagery, no accidental empty bands.")},
    {"key": "brand_safety",
     "description": ("Professional B2B tone. No watermarks from other brands, "
                     "no people in unsafe/unprofessional situations, nothing "
                     "that would embarrass an industrial-chemistry company.")},
    {"key": "coherence",
     "description": ("Does this frame look like it belongs to the same video "
                     "as the hero claim (industrial, technical, consistent "
                     "color treatment)?")},
]


def write_rubric(workspace: Path, hero_claim: str = "") -> Path:
    p = Path(workspace) / "rubric.json"
    p.write_text(json.dumps(
        {"hero_claim": hero_claim, "criteria": DEFAULT_CRITERIA},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_rubric(workspace: Path) -> dict:
    p = Path(workspace) / "rubric.json"
    if not p.exists():
        return {"hero_claim": "", "criteria": DEFAULT_CRITERIA}
    return json.loads(p.read_text(encoding="utf-8"))
