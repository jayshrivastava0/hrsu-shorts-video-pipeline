import pytest

from scheduler import director
from scheduler.briefs import formats, writer
from scheduler.briefs.facts import load_facts
from scheduler.briefs.intake import build_brief
from scheduler.briefs.types import get_recipe, recipe_for

TEXT = "Christmas story about trust in a gift"


def _brief(**kw):
    return build_brief(TEXT, "marketing", **kw)


def test_formats_resolve_defaults_and_overrides():
    assert formats.resolve("long", None, None) == ("landscape", 180)
    assert formats.resolve("short", "1:1", 20) == ("square", 20)
    assert formats.resolve(None, None, None) == (None, None)
    with pytest.raises(ValueError):
        formats.resolve("epic", None, None)


def test_options_change_the_brief_id_but_defaults_do_not():
    base = _brief()
    assert _brief(fmt="long").id != base.id
    assert _brief(angles=["dual_source"]).id != base.id
    assert _brief(cta="Talk to us").id != base.id
    assert _brief().id == base.id
    assert _brief(orientation="portrait").id == base.id   # legacy flag keeps old ids


def test_cta_override_reaches_the_recipe():
    assert recipe_for(_brief()).cta_line == get_recipe("marketing").cta_line
    assert recipe_for(_brief(cta="Book a call at example.com")).cta_line == "Book a call at example.com"


def test_buyer_reasons_load_and_angles_resolve():
    facts = load_facts()
    ids = {r["id"] for r in facts.buyer_reasons}
    assert {"dual_source", "alt_to_china", "powder_form", "low_impurities", "nearby"} <= ids
    brief = _brief(angles=["dual_source", "something custom"])
    resolved = writer._resolve_angles(brief, facts)
    assert resolved[0].startswith("Helps buyers reduce") and resolved[1] == "something custom"


def test_marketing_prompt_leads_with_angle_and_does_not_default_to_qc():
    facts = load_facts()
    recipe = get_recipe("marketing")
    plain = writer._user_prompt(_brief(), recipe, facts, [], "")
    assert "Do not default to batch-level QC" in plain
    led = writer._user_prompt(_brief(angles=["alt_to_china"]), recipe, facts, [], "")
    assert "Lead with these angles: An India-based alternative" in led


def test_duration_sets_length_rule_and_beat_range():
    recipe = get_recipe("marketing")
    long = _brief(fmt="long")
    assert writer._beat_range(recipe, long) == (12, 30)
    assert "180 seconds" in writer._length_rule(recipe, long)
    assert writer._script_schema(recipe, long)["properties"]["beats"]["maxItems"] == 30
    assert writer._beat_range(recipe, _brief()) == (3, 5)   # unchanged default


def test_square_canvas_and_cta_in_director_prompt(tmp_path):
    run, proj = tmp_path / "run", tmp_path / "proj"
    run.mkdir(); proj.mkdir()
    (run / "orientation.txt").write_text("square", encoding="utf-8")
    (run / "cta.txt").write_text("Talk to us at example.com", encoding="utf-8")
    text = director.build_prompt(proj, run, template=get_recipe("marketing").prompt_path)
    assert "1080x1080" in text and "Talk to us at example.com" in text
    assert "{{CTA}}" not in text and "Ask for a quotation" not in text
