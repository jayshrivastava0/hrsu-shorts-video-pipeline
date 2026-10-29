import hashlib

import pytest

from scheduler import director
from scheduler.briefs.types import get_recipe

TYPES = ["proof", "esg", "market", "marketing"]


def _dirs(tmp_path):
    proj, run = tmp_path / "proj", tmp_path / "run"
    proj.mkdir()
    run.mkdir()
    return proj, run


def _prompt(type_name, tmp_path):
    proj, run = _dirs(tmp_path)
    return director.build_prompt(proj, run, template=get_recipe(type_name).prompt_path)


@pytest.mark.parametrize("type_name", TYPES)
def test_no_unreplaced_placeholders(type_name, tmp_path):
    assert "{{" not in _prompt(type_name, tmp_path)


def test_market_prompt_requires_source_chips_and_hrsu_view(tmp_path):
    text = _prompt("market", tmp_path)
    assert "HRSU's view" in text
    assert "source chip" in text


def test_esg_prompt_forbids_in_progress_as_done(tmp_path):
    text = _prompt("esg", tmp_path)
    assert "in progress" in text
    assert "in_place" in text


def test_marketing_prompt_allows_story_but_not_invented_claims(tmp_path):
    text = _prompt("marketing", tmp_path)
    assert "story" in text.lower()
    assert "in_place" in text
    assert "price" in text.lower()
    assert get_recipe("marketing").cta_line in text


def test_proof_prompt_only_printed_facts_quoted_as_printed(tmp_path):
    text = _prompt("proof", tmp_path)
    assert "assets/" in text
    assert "as printed" in text


def test_review_note_appended(tmp_path):
    proj, run = _dirs(tmp_path)
    note = "Move the CTA card to the left and cut the second diagram."
    text = director.build_prompt(proj, run, review_note=note)
    assert "## Owner review note (fix this)" in text
    assert note in text
    assert text.index("## Owner review note (fix this)") > text.index("Finish (mandatory)")


# update this hash deliberately when the explainer prompt is edited
EXPLAINER_PROMPT_SHA256 = "949c2b20ed37732ad17d7039e0e9762981d9fff81c5eb8d6cfb79f94e8ae33e5"


def test_explainer_prompt_file_pinned():
    digest = hashlib.sha256(director.PROMPT_PATH.read_bytes()).hexdigest()
    assert digest == EXPLAINER_PROMPT_SHA256


def test_explainer_prompt_has_no_review_note_without_note(tmp_path):
    proj, run = _dirs(tmp_path)
    assert "Owner review note" not in director.build_prompt(proj, run)


@pytest.mark.parametrize("type_name", ["esg", "marketing"])
def test_facts_file_path_uses_run_dir(type_name, tmp_path):
    proj, run = _dirs(tmp_path)
    text = director.build_prompt(proj, run, template=get_recipe(type_name).prompt_path)
    assert f"{run}/brand_facts.yaml" in text
    assert " brand_facts.yaml" not in text
    assert "`brand_facts.yaml`" not in text
