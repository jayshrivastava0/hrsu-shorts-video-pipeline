# tests/shorts_engine/test_regression_foliar_soil.py
"""Replay of run-e721c494: a foliar-blend post whose old script jumped to desert soil and
printed '425 EC' with no unit. The new pipeline must not ship that."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from shorts_engine import explanation as ex
from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import explain, verify_claims as vc

FIX = Path(__file__).parent / "fixtures" / "regression_run_e721c494"
# verbatim substring of the fixture's canonical.txt; the number 425 carries no unit there
EC_QUOTE = "electrical conductivity (EC) peaking at 425"
SAND_QUOTE = "a 20% calcium carbonate and 80% river sand mixture"
NOZZLE_QUOTE = "solubility issues lead to nutrient precipitation, nozzle clogging, and uneven crop application"
BLEND_QUOTE = "Maintaining the stability of precision foliar blends is a constant challenge"
GYPSUM_QUOTE = ("the reaction between calcium ions and sulfates or phosphates, which creates "
                "insoluble salts like calcium sulfate (gypsum)")


def _ctx(tmp_path):
    m = RunManifest.create("https://hrsuindore.com/blog/calcium-nitrate-solubility-in-precision/",
                           workspace_root=tmp_path)
    ws = Path(m.workspace)
    for name in ("canonical.txt", "post.json", "factsheet.json"):
        shutil.copy(FIX / name, ws / name)
    return StageContext(manifest=m, workspace=ws, flags={})


def _step(sid, claims, depends_on=(), transition=""):
    return {"step_id": sid, "claim_text": "goal", "claims": claims, "terms": [],
            "depends_on": list(depends_on), "transition": transition,
            "visual_intent": {"entities": ["soil"], "relationship": "r", "quantity": "q"}}


def _claim(cid, text, kind, quote="", role="mechanism"):
    return {"id": cid, "text": text, "kind": kind, "role": role, "quote": quote}


def _plan(steps):
    return {"question": "Why does soil retention matter for foliar blends?", "steps": steps,
            "payoff": {"takeaway": "Choose a soluble powder.", "differentiator_id": "b_purity"}}


class NoWeb:
    def retrieve(self, claim_text):
        return []


def test_unitless_ec_number_is_rejected_at_plan_time():
    canonical = (FIX / "canonical.txt").read_text(encoding="utf-8")
    assert EC_QUOTE in canonical
    plan = _plan([_step(f"s{i}", [_claim(f"c{i}", "EC peaks at 425.",
                                         "blog_stated", EC_QUOTE)]) for i in (1, 2, 3)])
    errs = ex.validate_plan(plan, canonical, load_brand_facts())
    assert any("425" in e and "unit" in e for e in errs)


def test_soil_to_foliar_leap_is_dropped_when_nothing_supports_it(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    steps = [
        _step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                            "blog_stated", SAND_QUOTE)]),
        # the leap: nothing in the article or on the web supports soil data -> foliar
        _step("s2", [_claim("c2", "Soil infiltration data proves foliar solubility.",
                            "external_fact"),
                     _claim("f2", "So soil data carries over to foliar sprays.", "reasoning",
                            role="fact")],
              depends_on=["c1"], transition="From the soil model, turn to the spray tank."),
        # built only on the leap
        _step("s3", [_claim("m3", "Fully dissolved salts pass through spray nozzles.",
                            "external_fact"),
                     _claim("c3", "So foliar sprays never clog nozzles.", "reasoning",
                            role="fact")],
              depends_on=["c2"], transition="That brings us to the nozzle itself."),
    ]
    monkeypatch.setattr(explain.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: _plan(steps))
    explain.run(ctx)
    monkeypatch.setattr(vc, "Retriever", lambda urls: NoWeb())

    def fake_llm(prompt, system, schema, **kw):
        if schema is vc.VERDICT_SCHEMA:
            if prompt.startswith("Claim: Engineers model desert soil"):  # c1 quote entailment
                return {"verdict": "supported", "passage_index": 0}
            return {"verdict": "unsupported", "passage_index": None}   # premises don't carry it
        return {"claims": []}                                          # planner repair: nothing

    monkeypatch.setattr(vc.text_llm, "generate_schema_json", fake_llm)
    with pytest.raises(HoldForReview):
        vc.run(ctx)
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert doc["status"] == "held"
    dropped = {c["id"] for c in doc["dropped_claims"]}
    assert {"c2", "c3"} <= dropped            # the unsupported leap and the claim built on it
    reasons = {s["step_id"]: s["reason"] for s in doc["dropped_steps"]}
    assert reasons["s2"] == "no verified mechanism"
    assert "depends on dropped claims" in reasons["s3"]   # cascades from the dropped leap
    surviving = " ".join(c["text"] for s in doc["steps"] for c in s["claims"])
    assert "foliar" not in surviving.lower()


def test_well_supported_blog_claims_are_not_over_held(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    steps = [
        _step("s1", [_claim("c1", "Foliar blends need stable solutions.",
                            "blog_stated", BLEND_QUOTE)]),
        _step("s2", [_claim("c2", "Poor solubility causes nozzle clogging.",
                            "blog_stated", NOZZLE_QUOTE)],
              depends_on=["c1"],
              transition="When a blend is not stable, the trouble shows up at the nozzle."),
        # s3 stays on the foliar blend (no jump to soil) and builds on s2's clogging claim
        _step("s3", [_claim("c3", "Calcium ions react with sulfates or phosphates into "
                                  "insoluble salts.", "blog_stated", GYPSUM_QUOTE)],
              depends_on=["c2"],
              transition="To see why nozzles clog, look at what forms in the tank."),
    ]
    monkeypatch.setattr(explain.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: _plan(steps))
    explain.run(ctx)
    monkeypatch.setattr(vc, "Retriever", lambda urls: NoWeb())
    monkeypatch.setattr(vc.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: {"verdict": "supported", "passage_index": 0}
                        if sc is vc.VERDICT_SCHEMA else {"claims": []})
    vc.run(ctx)
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert doc["status"] == "verified"
    assert len(doc["steps"]) == 3 and doc["dropped_claims"] == []
    assert [s["depends_on"] for s in doc["steps"]] == [[], ["c1"], ["c2"]]
