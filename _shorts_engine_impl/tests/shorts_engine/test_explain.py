# tests/shorts_engine/test_explain.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine import config
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import explain

URL = "https://hrsuindore.com/blog/x/"
CANONICAL = ("Calcium nitrate dissolves readily in water. In sandy soils, electrical "
             "conductivity (EC) peaks at 425 during infiltration. A 20% calcium carbonate "
             "and 80% river sand mixture models desert soil.")


def _ctx(tmp_path: Path) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANONICAL, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu", "category": "c", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({"facts": [], "brand_facts": [], "dropped": []}),
                                       encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


def _good_plan():
    def step(sid, claims, depends_on=(), transition=""):
        return {"step_id": sid, "claim_text": f"goal {sid}", "claims": claims, "terms": [],
                "depends_on": list(depends_on), "transition": transition,
                "visual_intent": {"entities": ["water"], "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why does calcium nitrate suit foliar sprays?",
        "steps": [
            step("s1", [{"id": "c1", "text": "Calcium nitrate dissolves readily in water.",
                         "kind": "blog_stated", "role": "mechanism",
                         "quote": "Calcium nitrate dissolves readily in water."}]),
            step("s2", [{"id": "c2", "text": "Dissolved salt passes through spray nozzles.",
                         "kind": "reasoning", "role": "mechanism", "quote": ""},
                        {"id": "f2", "text": "So nothing solid reaches the nozzle.",
                         "kind": "reasoning", "role": "fact", "quote": ""}],
                 depends_on=["c1"],
                 transition="Once the salt is in solution, follow it to the sprayer."),
            step("s3", [{"id": "c3", "text": "Soluble powder avoids clogged nozzles.",
                         "kind": "reasoning", "role": "mechanism", "quote": ""},
                        {"id": "f3", "text": "So the nozzle does not clog.",
                         "kind": "reasoning", "role": "fact", "quote": ""}],
                 depends_on=["c2", "f2"],
                 transition="That leaves the question of what reaches the nozzle."),
        ],
        "payoff": {"takeaway": "Choose a consistently soluble powder.",
                   "differentiator_id": "b_purity"},
    }


def test_writes_plan_document_and_returns_artifact(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen = {}

    def fake(prompt, system, schema, **kw):
        seen["role"] = kw.get("role")
        return _good_plan()

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", fake)
    assert explain.run(ctx) == {"explanation_plan": "explanation_plan.json"}
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert seen["role"] == "planner"
    assert doc["status"] == "planned" and doc["attempts"] == 1
    assert doc["steps"][0]["claims"][0]["support"]["type"] == "blog_quote"
    assert doc["steps"][0]["claims"][0]["verdict"] is None
    assert doc["steps"][1]["depends_on"] == ["c1"] and doc["steps"][0]["depends_on"] == []
    assert doc["steps"][1]["transition"].startswith("Once the salt")


def test_validation_errors_are_echoed_into_the_retry_prompt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    bad = _good_plan()
    bad["steps"][0]["claims"][0]["quote"] = "a sentence that is not in the article"
    prompts = []

    def fake(prompt, system, schema, **kw):
        prompts.append(prompt)
        return bad if len(prompts) == 1 else _good_plan()

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", fake)
    explain.run(ctx)
    assert len(prompts) == 2
    assert "not found verbatim" in prompts[1]
    assert "not found verbatim" not in prompts[0]


def test_prompt_contains_full_article_and_differentiators(tmp_path):
    from shorts_engine.brand import load_brand_facts
    prompt = explain._planner_prompt({"title": "T", "region": "eu", "category": "c"},
                                     CANONICAL, {"facts": []}, load_brand_facts())
    assert CANONICAL in prompt
    assert "b_purity" in prompt


def test_planner_is_told_to_explain_the_mechanism_and_chain_the_steps():
    system = explain._PLANNER_SYSTEM.lower()
    assert "mechanism" in system and "depends_on" in system and "define" in system
    assert "role" in system and "transition" in system
    assert "link" not in system
    # worked examples use a generic illustration, never the blog under test
    assert "oxide film" in system


def test_planner_is_told_depends_on_and_transition_rules():
    system = explain._PLANNER_SYSTEM
    assert "ids of claims in EARLIER steps" in system
    assert "first step" in system and "depends_on: []" in system
    # a transition is words only: no facts, no numbers, and it is not a claim
    assert "no facts" in system and "no numbers" in system and "not a claim" in system
    assert "Good transition" in system and "Bad transition" in system
    # the article's own mechanism is quoted, otherwise an external_fact
    assert "blog_stated" in system and "external_fact" in system
    prompt = explain._planner_prompt({"title": "T"}, CANONICAL, {"facts": []},
                                     __import__("shorts_engine.brand", fromlist=["x"])
                                     .load_brand_facts())
    assert "depends_on" in prompt and "link" not in prompt.split("Remember:", 1)[1]


def test_schema_role_enum_has_no_link():
    from shorts_engine import explanation
    item = explanation.PLAN_SCHEMA["properties"]["steps"]["items"]
    assert item["properties"]["claims"]["items"]["properties"]["role"]["enum"] == \
        ["fact", "mechanism"]
    assert {"depends_on", "transition"} <= set(item["required"])


def test_missing_mechanism_and_depends_on_errors_are_echoed_into_the_retry_prompt(
        tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    bad = _good_plan()
    bad["steps"][1]["claims"] = [dict(bad["steps"][1]["claims"][0], role="fact")]
    bad["steps"][1]["depends_on"] = []
    prompts = []

    def fake(prompt, system, schema, **kw):
        prompts.append(prompt)
        return bad if len(prompts) == 1 else _good_plan()

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", fake)
    explain.run(ctx)
    assert len(prompts) == 2
    assert "step[s2]: missing a 'mechanism' claim" in prompts[1]
    assert "step[s2]: missing depends_on" in prompts[1]


def test_exhausted_retries_hold_for_review_and_write_no_plan(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    bad = _good_plan()
    bad["steps"] = bad["steps"][:1]

    calls = []
    monkeypatch.setattr(explain.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: calls.append(1) or bad)
    with pytest.raises(HoldForReview) as e:
        explain.run(ctx)
    assert len(calls) == config.LLM_MAX_RETRIES
    assert any("structure" in r for r in e.value.reasons)
    # the audit trail exists: a minimal held plan carrying the hold reasons
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert doc["status"] == "held" and doc["steps"] == []
    assert doc["hold_reasons"] == e.value.reasons
    assert doc["dropped_claims"] == [] and doc["dropped_steps"] == []
    from shorts_engine.review.plan_report import format_plan
    assert "HELD" in format_plan(doc)


def test_thin_plan_holds_instead_of_crashing_on_schema(tmp_path, monkeypatch):
    """A planner returning 2 steps must HOLD (spec 7.3/11), not fail the run with a schema
    EngineLLMError: the step count is checked by validate_plan, not by PLAN_SCHEMA."""
    import jsonschema
    from shorts_engine.errors import EngineLLMError
    ctx = _ctx(tmp_path)
    thin = _good_plan()
    thin["steps"] = thin["steps"][:2]
    calls = []

    def schema_checked(prompt, system, schema, **kw):
        calls.append(1)
        try:  # what the real generate_schema_json does with every model response
            jsonschema.validate(instance=thin, schema=schema)
        except jsonschema.ValidationError as exc:
            raise EngineLLMError(f"schema failure: {exc.message}") from exc
        return thin

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", schema_checked)
    with pytest.raises(HoldForReview) as e:
        explain.run(ctx)
    assert not isinstance(e.value, EngineLLMError)
    assert len(calls) == config.LLM_MAX_RETRIES
    assert any("structure" in r for r in e.value.reasons)
