"""Per-type recipes for brief-driven videos."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from scheduler.briefs.model import VIDEO_TYPES

_BRIEFS_DIR = Path(__file__).resolve().parent
_PROMPTS_DIR = _BRIEFS_DIR / "prompts"
_DIRECTOR_PROMPT = _BRIEFS_DIR.parent / "director_prompt.md"

_FACTS_SOURCES = (
    "narration",
    "documents",
    "facts_and_sources",
    "sources",
    "facts_only",
)


@dataclass(frozen=True)
class Recipe:
    name: str
    needs_research: bool
    facts_source: str
    prompt_path: Path
    cta_line: str
    tags: list[str]


_CTA = "Sourcing calcium nitrate? Visit hrsuindore.com"

_RECIPES: dict[str, Recipe] = {
    "explainer": Recipe(
        name="explainer",
        needs_research=True,
        facts_source="narration",
        prompt_path=_DIRECTOR_PROMPT,
        cta_line=_CTA,
        tags=["calcium nitrate", "HRSU", "explainer"],
    ),
    "proof": Recipe(
        name="proof",
        needs_research=False,
        facts_source="documents",
        prompt_path=_PROMPTS_DIR / "proof.md",
        cta_line=_CTA,
        tags=["calcium nitrate", "HRSU", "proof"],
    ),
    "esg": Recipe(
        name="esg",
        needs_research=True,
        facts_source="facts_and_sources",
        prompt_path=_PROMPTS_DIR / "esg.md",
        cta_line=_CTA,
        tags=["calcium nitrate", "HRSU", "esg"],
    ),
    "market": Recipe(
        name="market",
        needs_research=True,
        facts_source="sources",
        prompt_path=_PROMPTS_DIR / "market.md",
        cta_line=_CTA,
        tags=["calcium nitrate", "HRSU", "market"],
    ),
    "marketing": Recipe(
        name="marketing",
        needs_research=False,
        facts_source="facts_only",
        prompt_path=_PROMPTS_DIR / "marketing.md",
        cta_line="Ask for a quotation at hrsuindore.com",
        tags=["calcium nitrate", "HRSU", "marketing"],
    ),
}


def get_recipe(type_name: str) -> Recipe:
    """Return the recipe for a video type; KeyError lists the valid names."""
    try:
        return _RECIPES[type_name]
    except KeyError:
        valid = ", ".join(VIDEO_TYPES)
        raise KeyError(f"unknown video type {type_name!r}; valid types: {valid}") from None


def recipe_for(brief) -> Recipe:
    """The recipe of the brief's type, with the brief's own call to action when it set one."""
    recipe = get_recipe(brief.type)
    cta = getattr(brief, "cta", None)
    return replace(recipe, cta_line=cta) if cta else recipe
