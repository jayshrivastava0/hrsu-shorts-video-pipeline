# tests/shorts_engine/test_explanation.py
from __future__ import annotations

import copy

import jsonschema

from shorts_engine import explanation as ex
from shorts_engine.brand import BrandFacts

CANONICAL = (
    "In sandy soils, electrical conductivity (EC) peaks at 425 during infiltration. "
    "Engineers often use a 20% calcium carbonate and 80% river sand mixture to model "
    "desert soil. Calcium nitrate dissolves readily in water."
)
BRAND = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                   differentiators=[{"id": "b_purity", "text": "High-purity powder"}],
                   cta_lines=["Visit hrsuindore.com"], banned_claims=[])


def _claim(cid, text, kind="blog_stated", quote=""):
    return {"id": cid, "text": text, "kind": kind, "quote": quote}


def _step(sid, claims, entities=("sand",)):
    return {"step_id": sid, "claim_text": f"goal {sid}", "claims": claims, "terms": [],
            "visual_intent": {"entities": list(entities), "relationship": "r", "quantity": "q"}}


def _plan():
    return {
        "question": "Why does sand drain nutrients?",
        "steps": [
            _step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                                quote="a 20% calcium carbonate and 80% river sand mixture")]),
            _step("s2", [_claim("c2", "Calcium nitrate dissolves readily in water.",
                                kind="external_fact")]),
            _step("s3", [_claim("c3", "So nutrients move with the water.", kind="reasoning")]),
        ],
        "payoff": {"takeaway": "Pick a consistent powder.", "differentiator_id": "b_purity"},
    }


class TestSchema:
    def test_valid_plan_matches_schema(self):
        jsonschema.validate(_plan(), ex.PLAN_SCHEMA)

    def test_unknown_kind_is_rejected(self):
        p = _plan()
        p["steps"][0]["claims"][0]["kind"] = "guess"
        try:
            jsonschema.validate(p, ex.PLAN_SCHEMA)
        except jsonschema.ValidationError:
            return
        raise AssertionError("expected ValidationError")


class TestGateClaimNumbers:
    def test_number_with_known_unit_in_source_passes(self):
        assert ex.gate_claim_numbers("Mix is 20% carbonate", "a 20% calcium carbonate mix") == []

    def test_number_without_unit_fails(self):
        errs = ex.gate_claim_numbers("EC peaked at 425", "EC peaking at 425")
        assert any("unit" in e and "425" in e for e in errs)

    def test_unknown_unit_word_fails(self):
        errs = ex.gate_claim_numbers("EC peaked at 425 EC", "EC peaking at 425 EC")
        assert any("unit" in e for e in errs)

    def test_unit_must_appear_in_source(self):
        errs = ex.gate_claim_numbers("EC peaked at 425 dS/m", "EC peaking at 425")
        assert any("dS/m" in e or "ds/m" in e for e in errs)

    def test_untraced_number_fails(self):
        errs = ex.gate_claim_numbers("Dose is 5 kg", "the dose is 3 kg")
        assert any("'5'" in e and "trace" in e for e in errs)

    def test_term_defined_unit_is_allowed_when_in_source(self):
        terms = [{"term": "EC", "definition": "x", "unit": "mS/cm"}]
        assert ex.gate_claim_numbers("EC is 4 mS/cm", "EC is 4 mS/cm here", terms) == []

    def test_range_checks_unit_on_last_number(self):
        assert ex.gate_claim_numbers("Use 1.5 to 3 kg", "dose of 1.5 to 3 kg per m3") == []

    def test_years_ph_and_chemical_names_are_not_flagged(self):
        assert ex.gate_claim_numbers("In 2026 pH 7 water with H2S", "2026 pH 7 H2S") == []

    def test_no_numbers_no_errors(self):
        assert ex.gate_claim_numbers("Water dissolves salt.", "") == []


class TestValidatePlan:
    def test_valid_plan_has_no_errors(self):
        assert ex.validate_plan(_plan(), CANONICAL, BRAND) == []

    def test_fewer_than_min_steps(self):
        p = _plan()
        p["steps"] = p["steps"][:2]
        assert any("structure" in e and "3" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_blog_stated_quote_must_exist_verbatim(self):
        p = _plan()
        p["steps"][0]["claims"][0]["quote"] = "a 30% mixture that is not in the article"
        assert any("c1" in e and "verbatim" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_duplicate_step_and_claim_ids(self):
        p = _plan()
        p["steps"][1]["step_id"] = "s1"
        p["steps"][2]["claims"][0]["id"] = "c1"
        errs = ex.validate_plan(p, CANONICAL, BRAND)
        assert any("duplicate step" in e for e in errs)
        assert any("duplicate claim" in e for e in errs)

    def test_illustrative_claim_may_not_contain_numbers(self):
        p = _plan()
        p["steps"][2]["claims"][0] = _claim("c3", "Like 3 sugar cubes in tea", kind="illustrative")
        assert any("c3" in e and "illustrative" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_blog_stated_number_without_unit_is_rejected_at_plan_time(self):
        p = _plan()
        p["steps"][0]["claims"][0] = _claim(
            "c1", "EC peaks at 425 in sand.",
            quote="electrical conductivity (EC) peaks at 425 during infiltration")
        assert any("c1" in e and "unit" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_payoff_differentiator_must_exist(self):
        p = _plan()
        p["payoff"]["differentiator_id"] = "b_nope"
        assert any("differentiator" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_step_needs_visual_entities(self):
        p = _plan()
        p["steps"][0]["visual_intent"]["entities"] = []
        assert any("visual_intent" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_question_and_takeaway_may_not_contain_numbers(self):
        p = _plan()
        p["question"] = "Why do 3 things happen?"
        assert any("question" in e for e in ex.validate_plan(p, CANONICAL, BRAND))


class TestDocumentHelpers:
    def test_finalize_claim_shape(self):
        c = ex.finalize_claim(_claim("c1", "Mix is 20% carbonate", quote="q"))
        assert c == {"id": "c1", "text": "Mix is 20% carbonate", "kind": "blog_stated",
                     "support": {"type": "blog_quote", "quote": "q", "url": None},
                     "verdict": None, "needs_number": True}

    def test_to_plan_document_finalizes_every_claim_and_does_not_mutate_input(self):
        raw = _plan()
        before = copy.deepcopy(raw)
        doc = ex.to_plan_document(raw, attempts=2)
        assert raw == before
        assert doc["status"] == "planned" and doc["attempts"] == 2
        assert doc["steps"][1]["claims"][0]["support"]["type"] is None
        assert doc["dropped_claims"] == [] and doc["dropped_steps"] == []

    def test_supported_claims_filters_by_verdict(self):
        step = {"claims": [{"id": "a", "verdict": "supported"},
                           {"id": "b", "verdict": "unsupported"},
                           {"id": "c", "verdict": None}]}
        assert [c["id"] for c in ex.supported_claims(step)] == ["a"]
