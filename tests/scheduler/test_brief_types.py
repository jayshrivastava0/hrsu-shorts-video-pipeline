from pathlib import Path

import pytest

from scheduler.briefs.model import VIDEO_TYPES, brief_id
from scheduler.briefs.types import get_recipe


def test_brief_id_is_stable_and_whitespace_insensitive():
    a = brief_id("How  calcium nitrate\n\tcuts H2S in sewers")
    b = brief_id("How calcium nitrate cuts H2S in sewers")
    assert a == b
    assert a.startswith("how-calcium-nitrate-cuts-h2s-in-")
    assert len(a.rsplit("-", 1)[1]) == 6


def test_brief_id_differs_for_different_text():
    assert brief_id("calcium nitrate for sewers") != brief_id("calcium nitrate for concrete")


def test_get_recipe_unknown_lists_valid_types():
    with pytest.raises(KeyError) as exc:
        get_recipe("nonsense")
    msg = str(exc.value)
    for name in VIDEO_TYPES:
        assert name in msg


def test_explainer_recipe_uses_existing_prompt():
    recipe = get_recipe("explainer")
    assert recipe.prompt_path.name == "director_prompt.md"


def test_market_recipe_needs_research():
    assert get_recipe("market").needs_research is True


def test_marketing_recipe_facts_only():
    recipe = get_recipe("marketing")
    assert recipe.facts_source == "facts_only"
    assert recipe.needs_research is False


def test_every_prompt_file_exists():
    for name in VIDEO_TYPES:
        assert get_recipe(name).prompt_path.exists(), name


def test_marketing_cta_is_a_generic_quotation_line():
    assert get_recipe("marketing").cta_line == "Ask for a quotation at hrsuindore.com"
