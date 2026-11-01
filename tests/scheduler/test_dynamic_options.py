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
    assert "Lead with this angle: An India-based alternative" in led
    assert "ONE point" in led and "never a backup" in led


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


def test_fast_pace_changes_beat_range_prompt_and_id():
    recipe = get_recipe("marketing")
    fast = _brief(duration_s=150, pace="fast")
    assert fast.id != _brief(duration_s=150).id
    assert writer._beat_range(recipe, fast) == (15, 25)
    assert "Pace is FAST" in writer._user_prompt(fast, recipe, load_facts(), [], "")
    assert "Pace is FAST" not in writer._user_prompt(_brief(duration_s=150), recipe, load_facts(), [], "")


def test_fast_pace_rules_reach_director_prompt_only_when_asked(tmp_path):
    run, proj = tmp_path / "run", tmp_path / "proj"
    run.mkdir(); proj.mkdir()
    marketing = get_recipe("marketing").prompt_path
    assert "Pace: FAST" not in director.build_prompt(proj, run, template=marketing)
    (run / "pace.txt").write_text("fast", encoding="utf-8")
    text = director.build_prompt(proj, run, template=marketing)
    assert "Pace: FAST" in text and "{{PACE_RULES}}" not in text


def test_backup_and_second_choice_phrases_are_banned():
    banned = [b.lower() for b in load_facts().banned_claims]
    for phrase in ("second supplier", "backup supplier", "second choice", "plan b"):
        assert phrase in banned
    beats = [{"narration": "HRSU can be your second supplier.", "card_text": "", "broll_wish": ""}]
    assert "second supplier" in writer._offending(beats, load_facts().banned_claims)


def test_marketing_prompt_has_no_label_text_and_demands_one_message():
    text = get_recipe("marketing").prompt_path.read_text(encoding="utf-8")
    assert 'carries the word "illustration"' not in text
    assert "One message per video" in text and "THE choice, never a backup" in text
    fast = (get_recipe("marketing").prompt_path.parent / "pace_fast.md").read_text(encoding="utf-8")
    assert "stock photo" not in fast.lower().replace('"stock photo" (or', "")  # only the ban mention remains
    assert "Backgrounds must vary" in fast
