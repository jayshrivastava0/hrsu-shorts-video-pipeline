"""Provenance check + critic edit instructions (pure, offline)."""
from __future__ import annotations

from shorts_engine import provenance as pv
from shorts_engine.brand import BrandFacts

BRAND = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                   differentiators=[{"id": "b_supply", "text": "Flexible minimum order quantities"}],
                   cta_lines=["Visit hrsuindore.com"], banned_claims=[])


def _c(cid, text, role="mechanism", kind="blog_stated"):
    return {"id": cid, "text": text, "kind": kind, "role": role, "verdict": "supported",
            "support": {"type": "blog_quote", "quote": text, "url": None}}


def _plan():
    return {
        "question": "Why does procurement strategy change supply risk?",
        "steps": [
            {"step_id": "s1", "claim_text": "g", "depends_on": [], "transition": "",
             "terms": [{"term": "OpEx", "definition": "recurring operating expense", "unit": ""}],
             "claims": [_c("m1", "Traditional procurement is an OpEx model where costs "
                                 "fluctuate with market demand and transport logistics.")],
             "visual_intent": {"entities": ["a"], "relationship": "r", "quantity": "q"}},
            {"step_id": "s2", "claim_text": "g", "depends_on": ["m1"], "transition": "",
             "terms": [],
             "claims": [_c("m2", "Vertical integration creates dependence on spot market "
                                 "prices only for the inputs it does not make itself.")],
             "visual_intent": {"entities": ["a"], "relationship": "r", "quantity": "q"}},
        ],
        "payoff": {"takeaway": "t", "differentiator_id": "b_supply"},
    }


def _beats(s1, s2="Vertical integration creates dependence on spot market prices only for inputs."):
    return [{"beat": "hook", "narration": "Why does procurement strategy change supply risk?"},
            {"beat": "step_1", "narration": s1}, {"beat": "step_2", "narration": s2},
            {"beat": "cta", "narration": "Flexible minimum order quantities help. Visit hrsuindore.com."}]


class TestCheckProvenance:
    def test_clean_narration_has_no_findings(self):
        b = _beats("Traditional procurement is an OpEx model, so costs fluctuate with market "
                   "demand and transport logistics.")
        assert pv.check_provenance(b, _plan(), BRAND) == []

    def test_invented_named_entity_is_flagged_with_the_closest_claim(self):
        b = _beats("Traditional procurement often involves the Spot Market, so costs "
                   "fluctuate with market demand.")
        f = pv.check_provenance(b, _plan(), BRAND)
        assert len(f) == 1 and f[0]["beat"] == "step_1"
        assert "spot" in f[0]["why"] and f[0]["claim_id"] == "m1"

    def test_claim_of_a_later_step_is_not_support_for_an_earlier_step(self):
        # "spot market prices" is claim m2's wording (step 2): step 1 may not use it
        b = _beats("Traditional procurement leaves buyers exposed to Spot Market prices.")
        assert pv.check_provenance(b, _plan(), BRAND)

    def test_mostly_new_material_is_flagged(self):
        b = _beats("Geopolitical tensions escalate shipping insurance premiums across "
                   "congested harbours worldwide.")
        f = pv.check_provenance(b, _plan(), BRAND)
        assert f and "new material" in f[0]["why"]

    def test_short_connector_is_not_flagged(self):
        b = _beats("That brings us to the next question.")
        assert pv.check_provenance(b, _plan(), BRAND) == []

    def test_defined_term_and_question_words_count_as_support(self):
        b = _beats("An OpEx cost recurring as operating expense follows demand and logistics.")
        assert pv.check_provenance(b, _plan(), BRAND) == []

    def test_builds_on_claim_is_support_for_the_next_step(self):
        s2 = "Because traditional procurement is an OpEx model, integration changes it."
        assert pv.check_provenance(_beats("Costs fluctuate with market demand.", s2),
                                   _plan(), BRAND) == []

    def test_cta_may_use_brand_statement_and_domain(self):
        assert pv.check_provenance(_beats("Costs fluctuate with market demand."),
                                   _plan(), BRAND) == []


class TestInstructions:
    def test_instruction_quotes_sentence_gives_claim_and_forbids_readding(self):
        f = pv.check_provenance(_beats("Traditional procurement often involves the Spot "
                                       "Market, so costs fluctuate."), _plan(), BRAND)
        text = pv.provenance_instructions(f)[0]
        assert "Traditional procurement often involves the Spot Market" in text
        assert "[m1]" in text and "Do NOT re-add" in text

    def test_no_matching_claim_means_remove(self):
        f = [{"beat": "step_1", "sentence": "Zzz qqq.", "why": "is odd", "claim_id": None,
              "claim_text": None}]
        assert "REMOVE" in pv.provenance_instructions(f)[0]


class TestCriticEdits:
    def test_edit_uses_the_plans_claim_text_not_the_critics(self):
        beats = _beats("Traditional procurement often involves the Spot Market.")
        edits = pv.resolve_critic_edits(
            [{"beat": "step_1", "offending_sentence": "Traditional procurement often involves "
              "the Spot Market.", "issue": "unsupported", "replacement_claim_id": "m2",
              "replacement_text": "CRITIC-INVENTED"}], beats, _plan())
        assert edits[0]["claim_text"].startswith("Vertical integration creates dependence")
        assert "CRITIC-INVENTED" not in pv.critic_instructions(edits)[0]

    def test_phantom_sentence_is_dropped(self):
        edits = pv.resolve_critic_edits(
            [{"beat": "step_1", "offending_sentence": "Not in the script at all.",
              "issue": "x", "replacement_claim_id": "m1"}], _beats("Something else."), _plan())
        assert edits == []

    def test_unknown_claim_id_means_remove(self):
        beats = _beats("Traditional procurement often involves the Spot Market.")
        edits = pv.resolve_critic_edits(
            [{"beat": "step_1", "offending_sentence": "Traditional procurement often involves "
              "the Spot Market", "issue": "x", "replacement_claim_id": "zz"}], beats, _plan())
        assert edits[0]["claim_id"] is None and "REMOVE" in pv.critic_instructions(edits)[0]


class TestReappeared:
    def test_flagged_sentence_returning_is_detected(self):
        old = "Traditional procurement often involves the Spot Market."
        assert pv.reappeared([old], _beats("Traditional procurement often involves the spot market!"))
        assert pv.reappeared([old], _beats("Costs fluctuate with demand.")) == []


def test_brand_name_used_for_attribution_is_not_an_invented_entity():
    b = _beats("The argument HRSU makes is that costs fluctuate with market demand and logistics.")
    assert pv.check_provenance(b, _plan(), BRAND) == []


class TestSanitizeTransitions:
    def test_transition_smuggling_a_new_claim_is_cleared(self):
        plan = _plan()
        plan["steps"][1]["transition"] = "This shift transforms spending into a strategic advantage."
        cleared = pv.sanitize_transitions(plan)
        assert [c["step_id"] for c in cleared] == ["s2"] and plan["steps"][1]["transition"] == ""

    def test_connective_transition_built_from_claim_words_is_kept(self):
        plan = _plan()
        plan["steps"][1]["transition"] = "With costs tied to market demand, look at integration."
        assert pv.sanitize_transitions(plan) == []
        assert plan["steps"][1]["transition"].startswith("With costs")
