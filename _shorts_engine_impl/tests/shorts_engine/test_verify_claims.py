# tests/shorts_engine/test_verify_claims.py
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shorts_engine import explanation as ex
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.retrieval import Passage
from shorts_engine.runner import StageContext
from shorts_engine.stages import verify_claims as vc

CANONICAL = ("Calcium nitrate dissolves readily in water. A 20% calcium carbonate and "
             "80% river sand mixture models desert soil.")


class FakeRetriever:
    def __init__(self, passages=None):
        self.passages, self.calls = passages or [], []

    def retrieve(self, claim_text):
        self.calls.append(claim_text)
        return self.passages


class Router:
    """Fake text_llm.generate_schema_json: answers by schema identity."""

    def __init__(self, verdicts=None, repair=None):
        self.verdicts = list(verdicts or [])
        self.repair = repair
        self.calls = []

    def __call__(self, prompt, system, schema, **kw):
        self.calls.append((schema, kw.get("role"), prompt))
        if schema is vc.VERDICT_SCHEMA:
            return self.verdicts.pop(0)
        if schema is vc.REPAIR_SCHEMA:
            return self.repair or {"claims": []}
        raise AssertionError(schema)


def _claim(cid, text, kind, quote="", verdict=None):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "quote": quote})
    c["verdict"] = verdict
    return c


def _step(sid, claims):
    return {"step_id": sid, "claim_text": "g", "claims": claims, "terms": [],
            "visual_intent": {"entities": ["x"], "relationship": "r", "quantity": "q"}}


@pytest.fixture
def llm(monkeypatch):
    router = Router()
    monkeypatch.setattr(vc.text_llm, "generate_schema_json", router)
    return router


class TestVerifyClaim:
    def test_blog_stated_supported_when_quote_found(self, llm):
        c = _claim("c1", "Calcium nitrate dissolves in water.", "blog_stated",
                   "Calcium nitrate dissolves readily in water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "supported" and llm.calls == []

    def test_blog_stated_unsupported_when_quote_missing(self, llm):
        c = _claim("c1", "x", "blog_stated", "not in the article")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and "quote" in out["reason"]

    def test_illustrative_with_number_is_unsupported(self, llm):
        c = _claim("c1", "Like 3 sugar cubes", "illustrative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported"

    def test_external_fact_without_retrieval_is_unsupported_and_makes_no_llm_call(self, llm):
        c = _claim("c2", "Calcium nitrate is very soluble.", "external_fact")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([]))
        assert out["verdict"] == "unsupported" and out["reason"] == "no_retrieval"
        assert llm.calls == []

    def test_external_fact_supported_records_source(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 0}]
        c = _claim("c2", "Calcium nitrate is very soluble in water.", "external_fact")
        p = Passage("https://www.epa.gov/x", "Calcium nitrate is very soluble in water.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "supported"
        assert out["support"] == {"type": "source", "quote": p.text, "url": p.url}
        assert llm.calls[0][1] == "verifier"

    def test_external_fact_contradicted(self, llm):
        llm.verdicts = [{"verdict": "contradicted", "passage_index": 0}]
        c = _claim("c2", "Calcium nitrate is insoluble.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "contradicted"

    def test_supported_with_bad_passage_index_is_unsupported(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 7}]
        c = _claim("c2", "Calcium nitrate is very soluble.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "unsupported"

    def test_external_number_must_trace_to_the_passage(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 0}]
        c = _claim("c2", "It dissolves 50 g per 100 ml of water.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "unsupported" and "trace" in out["reason"]

    def test_reasoning_needs_premises(self, llm):
        c = _claim("c3", "So nozzles stay clear.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and llm.calls == []

    def test_reasoning_supported_by_logic_check(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": None}]
        c = _claim("c3", "So nozzles stay clear.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=["It dissolves fully."],
                              canonical=CANONICAL, retriever=FakeRetriever())
        assert out["verdict"] == "supported"
        assert "It dissolves fully." in llm.calls[0][2]

    def test_does_not_mutate_input(self, llm):
        c = _claim("c1", "x", "blog_stated", "not in the article")
        before = copy.deepcopy(c)
        vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                        retriever=FakeRetriever())
        assert c == before


def _plan(steps):
    return {"question": "q?", "steps": steps,
            "payoff": {"takeaway": "t", "differentiator_id": "b_purity"},
            "status": "planned", "attempts": 1, "dropped_claims": [], "dropped_steps": [],
            "hold_reasons": []}


def _ctx(tmp_path, plan):
    m = RunManifest.create("https://hrsuindore.com/blog/x/", workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANONICAL, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"citations": []}), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


def _good_steps():
    q = "Calcium nitrate dissolves readily in water."
    return [_step(f"s{i}", [_claim(f"c{i}", "Calcium nitrate dissolves in water.",
                                   "blog_stated", q)]) for i in (1, 2, 3)]


class TestRun:
    def test_all_supported_writes_verified_plan(self, tmp_path, monkeypatch, llm):
        ctx = _ctx(tmp_path, _plan(_good_steps()))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        assert vc.run(ctx) == {"explanation_plan": "explanation_plan.json"}
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "verified"
        assert all(c["verdict"] == "supported" for s in doc["steps"] for c in s["claims"])

    def test_unsupported_claims_are_dropped_and_recorded(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"].append(_claim("cx", "Calcium nitrate is miraculous.", "external_fact"))
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever([]))  # empty search
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert len(doc["steps"]) == 3 and doc["status"] == "verified"

    def test_holds_when_fewer_than_three_steps_survive(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["claims"] = [_claim("cz", "Unsourceable claim.", "external_fact")]
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever([]))
        with pytest.raises(HoldForReview) as e:
            vc.run(ctx)
        assert any("3" in r for r in e.value.reasons)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "held" and doc["hold_reasons"]
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s3"]

    def test_step_with_only_illustrative_claims_is_dropped(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps.append(_step("s4", [_claim("ci", "Like sugar in tea.", "illustrative")]))
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [s["step_id"] for s in doc["steps"]] == ["s1", "s2", "s3"]

    def test_repair_round_replaces_a_failed_claim_and_reverifies(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented fact.", "blog_stated", "not in article")]
        llm.repair = {"claims": [{"id": "new", "text": "Calcium nitrate dissolves in water.",
                                  "kind": "blog_stated",
                                  "quote": "Calcium nitrate dissolves readily in water."}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in doc["steps"][0]["claims"]]
        assert len(ids) == 1 and ids[0].startswith("s1_r1_")
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert any(s is vc.REPAIR_SCHEMA and role == "planner" for s, role, _ in llm.calls)

    def test_repair_is_bounded_by_max_rounds(self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented.", "blog_stated", "nope")]
        llm.repair = {"claims": [{"id": "n", "text": "Still invented.", "kind": "blog_stated",
                                  "quote": "nope again"}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        repairs = [c for c in llm.calls if c[0] is vc.REPAIR_SCHEMA]
        assert len(repairs) == config.PLAN_MAX_REPAIR_ROUNDS
