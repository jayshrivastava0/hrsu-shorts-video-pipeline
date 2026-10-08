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
    def step(sid, claims):
        return {"step_id": sid, "claim_text": f"goal {sid}", "claims": claims, "terms": [],
                "visual_intent": {"entities": ["water"], "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why does calcium nitrate suit foliar sprays?",
        "steps": [
            step("s1", [{"id": "c1", "text": "Calcium nitrate dissolves readily in water.",
                         "kind": "blog_stated",
                         "quote": "Calcium nitrate dissolves readily in water."}]),
            step("s2", [{"id": "c2", "text": "Dissolved salt passes through spray nozzles.",
                         "kind": "reasoning", "quote": ""}]),
            step("s3", [{"id": "c3", "text": "Soluble powder avoids clogged nozzles.",
                         "kind": "reasoning", "quote": ""}]),
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
    assert not (ctx.workspace / "explanation_plan.json").exists()
