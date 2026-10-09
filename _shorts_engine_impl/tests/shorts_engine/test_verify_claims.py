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
        self.logic = None  # optional fn(prompt) -> verdict dict for the logic checker
        self.calls = []
        self.systems = []  # system prompt of each call, parallel to `calls`

    def __call__(self, prompt, system, schema, **kw):
        self.calls.append((schema, kw.get("role"), prompt))
        self.systems.append(system)
        if schema is vc.VERDICT_SCHEMA:
            if self.logic is not None and system is vc._LOGIC_SYSTEM:
                return self.logic(prompt)
            if self.verdicts:
                return self.verdicts.pop(0)
            return {"verdict": "supported", "passage_index": 0}
        if schema is vc.REPAIR_SCHEMA:
            if callable(self.repair):
                return self.repair(prompt)
            return self.repair or {"claims": []}
        raise AssertionError(schema)


def _claim(cid, text, kind, quote="", verdict=None, role="fact"):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "role": role,
                           "quote": quote})
    c["verdict"] = verdict
    return c


def _step(sid, claims, depends_on=(), transition=""):
    return {"step_id": sid, "claim_text": f"GOAL-{sid}", "claims": claims, "terms": [],
            "depends_on": list(depends_on), "transition": transition,
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
        assert out["verdict"] == "supported"
        assert len(llm.calls) == 1 and llm.calls[0][1] == "verifier"
        assert out["support"] == {"type": "blog_quote", "quote": c["support"]["quote"], "url": None}

    def test_blog_stated_text_quote_mismatch_rejected(self, llm):
        llm.verdicts = [{"verdict": "unsupported", "passage_index": None}]
        c = _claim("c1", "Calcium nitrate eliminates H2S completely", "blog_stated",
                   "Calcium nitrate dissolves readily in water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and out["reason"] == "claim not supported by its quote"

    def test_blog_stated_contradicted_by_quote_keeps_verdict(self, llm):
        llm.verdicts = [{"verdict": "contradicted", "passage_index": 0}]
        c = _claim("c1", "Calcium nitrate does not dissolve", "blog_stated",
                   "Calcium nitrate dissolves readily in water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "contradicted"

    def test_blog_stated_verifier_wrong_index_rejected(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 3}]
        c = _claim("c1", "Calcium nitrate dissolves in water.", "blog_stated",
                   "Calcium nitrate dissolves readily in water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported"

    def test_blog_stated_short_quote_rejected_without_llm(self, llm):
        c = _claim("c1", "It is water.", "blog_stated", "water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and llm.calls == []

    def test_blog_stated_number_without_unit_in_quote_rejected(self, llm):
        c = _claim("c1", "The mixture is 20 g calcium carbonate.", "blog_stated",
                   "A 20% calcium carbonate and 80% river sand mixture models desert soil.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and llm.calls == []

    def test_blog_stated_unsupported_when_quote_missing(self, llm):
        c = _claim("c1", "x", "blog_stated", "not in the article")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and "quote" in out["reason"]

    def test_illustrative_with_number_is_unsupported(self, llm):
        c = _claim("c1", "Like 3 sugar cubes", "illustrative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and llm.calls == []

    def test_illustrative_pure_analogy_supported_via_verifier(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": None}]
        c = _claim("c1", "Like sugar dissolving in tea.", "illustrative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "supported"
        assert len(llm.calls) == 1 and llm.calls[0][1] == "verifier"

    def test_illustrative_factual_claim_rejected(self, llm):
        llm.verdicts = [{"verdict": "unsupported", "passage_index": None}]
        c = _claim("c1", "Calcium nitrate is REACH-registered.", "illustrative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported"
        assert out["reason"] == "illustrative claim asserts a fact"

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

    def test_reasoning_number_must_trace_to_premises(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": None}]
        c = _claim("c3", "So it dissolves 50 g per 100 ml.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=["It dissolves fully."],
                              canonical=CANONICAL, retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and "trace" in out["reason"]

    def test_reasoning_contradicted(self, llm):
        llm.verdicts = [{"verdict": "contradicted", "passage_index": None}]
        c = _claim("c3", "So nozzles clog.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=["It dissolves fully."],
                              canonical=CANONICAL, retriever=FakeRetriever())
        assert out["verdict"] == "contradicted"

    def test_supported_reasoning_clears_stale_quote(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": None}]
        c = _claim("c3", "So nozzles stay clear.", "reasoning", quote="stale planner quote")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=["It dissolves fully."],
                              canonical=CANONICAL, retriever=FakeRetriever())
        assert out["verdict"] == "supported"
        assert out["support"] == {"type": None, "quote": "", "url": None}

    def test_bool_passage_index_rejected(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": True}]
        c = _claim("c2", "Calcium nitrate is very soluble.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p, p]))
        assert out["verdict"] == "unsupported"

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


GOOD_QUOTE = "Calcium nitrate dissolves readily in water."


def _good_steps(n=3):
    """n chained steps, each with a blog-quoted mechanism c<i>; step i>1 builds on c<i-1>."""
    steps = [_step(f"s{i}", [_claim(f"c{i}", "Calcium nitrate dissolves in water.",
                                    "blog_stated", GOOD_QUOTE, role="mechanism")],
                   depends_on=[f"c{i - 1}"] if i > 1 else [],
                   transition="With that settled, look at the next effect." if i > 1 else "")
             for i in range(1, n + 1)]
    return steps


def _run_doc(tmp_path, monkeypatch, steps, *, hold=False):
    ctx = _ctx(tmp_path, _plan(steps))
    monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
    if hold:
        with pytest.raises(HoldForReview):
            vc.run(ctx)
    else:
        vc.run(ctx)
    return json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))


def _repair_prompts(llm, sid=None):
    out = [p for s, role, p in llm.calls if s is vc.REPAIR_SCHEMA and role == "planner"]
    return out if sid is None else [p for p in out if f"Step goal: GOAL-{sid}\n" in p]


GOOD_MECHANISM = {"id": "m", "text": "Calcium nitrate dissolves in water.",
                  "kind": "blog_stated", "role": "mechanism", "quote": GOOD_QUOTE}


class TestRun:
    def test_all_supported_writes_verified_plan(self, tmp_path, monkeypatch, llm):
        ctx = _ctx(tmp_path, _plan(_good_steps()))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        assert vc.run(ctx) == {"explanation_plan": "explanation_plan.json"}
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "verified"
        assert all(c["verdict"] == "supported" for s in doc["steps"] for c in s["claims"])
        assert [s["depends_on"] for s in doc["steps"]] == [[], ["c1"], ["c2"]]

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
        assert e.value.reasons == ["only 2 verified step(s) survived; need at least 3"]
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "held" and doc["hold_reasons"]
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s3"]

    def test_step_with_only_illustrative_claims_is_dropped(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps.append(_step("s4", [_claim("ci", "Like sugar in tea.", "illustrative")],
                           depends_on=["c3"]))
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [s["step_id"] for s in doc["steps"]] == ["s1", "s2", "s3"]

    def test_repair_round_replaces_a_failed_claim_and_reverifies(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        # s1's mechanism fails. While s1 does not hold, s2 (building on s1's fact) and s3
        # (building on s2) do not hold either, so all three get a repair in round 1.
        steps[0]["claims"] = [_claim("cx", "Invented fact.", "blog_stated", "not in article",
                                     role="mechanism"),
                              _claim("f1", "Calcium nitrate dissolves in water.", "blog_stated",
                                     GOOD_QUOTE)]
        steps[1]["depends_on"] = ["f1"]
        llm.repair = lambda prompt: {"claims": [GOOD_MECHANISM] if "Step goal: GOAL-s1\n" in prompt
                                     else []}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [c["id"] for c in doc["steps"][0]["claims"]] == ["f1", "s1_r1_1"]
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert doc["status"] == "verified" and len(doc["steps"]) == 3
        assert doc["steps"][1]["depends_on"] == ["f1"]
        assert len(_repair_prompts(llm)) == 3        # round 1 only; round 2 has nothing to do

    def test_repair_is_bounded_by_max_rounds(self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        # s1's mechanism never verifies, so s2 and s3 (built on it) never hold either
        steps[0]["claims"] = [_claim("cx", "Invented.", "blog_stated", "nope",
                                     role="mechanism"),
                              _claim("f1", "Calcium nitrate dissolves in water.", "blog_stated",
                                     GOOD_QUOTE)]
        steps[1]["depends_on"] = ["f1"]
        llm.repair = {"claims": [{"id": "n", "text": "Still invented.", "kind": "blog_stated",
                                  "role": "mechanism", "quote": "nope again"}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        repairs = [c for c in llm.calls if c[0] is vc.REPAIR_SCHEMA]
        assert len(repairs) == 3 * config.PLAN_MAX_REPAIR_ROUNDS     # every step, every round
        assert len(repairs) == vc.max_repair_calls(3)

    def test_max_repair_calls_bound(self):
        from shorts_engine import config
        assert vc.max_repair_calls(5) == config.PLAN_MAX_REPAIR_ROUNDS * 5

    def test_repair_calls_stay_within_the_bound_when_everything_fails(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        for s in steps:
            for c in s["claims"]:
                c["support"]["quote"] = "never in the article"
            s["depends_on"] = ["nope"] if s["step_id"] != "s1" else []
        llm.repair = {"claims": [{"id": "n", "text": "Still invented.", "kind": "blog_stated",
                                  "role": "mechanism", "quote": "nope again"}],
                      "depends_on": ["also-nope"], "transition": "Still 3 words."}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        repairs = [c for c in llm.calls if c[0] is vc.REPAIR_SCHEMA]
        assert 0 < len(repairs) <= vc.max_repair_calls(3)


class TestVerifyPlanPremises:
    def test_illustrative_claim_never_becomes_a_premise(self, llm):
        ill = _claim("ci", "Like sugar in tea.", "illustrative")
        rea = _claim("cr", "So nozzles stay clear.", "reasoning")
        plan = _plan([_step("s1", [ill, rea])])
        vc.verify_plan(plan, canonical=CANONICAL, retriever=FakeRetriever())
        out = plan["steps"][0]["claims"]
        assert out[0]["verdict"] == "supported"
        assert out[1]["verdict"] == "unsupported"
        assert out[1]["reason"] == "no verified premises to reason from"
        assert len(llm.calls) == 1  # only the illustrative check


class TestRunReset:
    def test_repaired_claim_with_preset_verdict_is_reverified(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented fact.", "blog_stated", "not in article")]
        llm.repair = {"claims": [{"id": "new", "text": "Invented again.", "kind": "blog_stated",
                                  "quote": "also not in the article", "verdict": "supported"}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["dropped_steps"][0]["step_id"] == "s1"
        assert doc["dropped_steps"][0]["reason"] == "no verified mechanism"
        assert "s1_r1_1" in [c["id"] for c in doc["dropped_claims"]]

    def test_repair_prompt_includes_article(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented.", "blog_stated", "nope nope nope nope")]
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        prompts = [p for s, _r, p in llm.calls if s is vc.REPAIR_SCHEMA]
        assert prompts and all(CANONICAL in p for p in prompts)


class TestExplainTheWhy:
    """Spec section 12: a step survives only with a verified mechanism and (unless first)
    a depends_on naming a verified claim of an earlier surviving step; a missing one is
    repaired (bounded rounds) before the step is dropped."""

    def test_max_repair_rounds_is_four(self):
        from shorts_engine import config
        assert config.PLAN_MAX_REPAIR_ROUNDS == 4

    def test_fully_explained_plan_needs_no_repair(self, tmp_path, monkeypatch, llm):
        doc = _run_doc(tmp_path, monkeypatch, _good_steps())
        assert _repair_prompts(llm) == []
        assert doc["dropped_steps"] == [] and len(doc["steps"]) == 3

    def test_step_lacking_mechanism_triggers_repair_even_with_no_failed_claim(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[1]["claims"][0]["role"] = "fact"     # supported, but no why/how
        llm.repair = lambda p: {"claims": [GOOD_MECHANISM] if "Step goal: GOAL-s2\n" in p else []}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        prompts = _repair_prompts(llm, "s2")
        assert len(prompts) == 1
        assert "MISSING MECHANISM" in prompts[0] and CANONICAL in prompts[0]
        assert doc["dropped_steps"] == [] and doc["status"] == "verified"
        s2 = doc["steps"][1]
        assert any(c["role"] == "mechanism" and c["verdict"] == "supported"
                   and c["id"] == "s2_r1_1" for c in s2["claims"])

    def test_missing_depends_on_triggers_repair_and_repaired_depends_on_is_applied(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["depends_on"] = []
        steps[2]["transition"] = ""
        llm.repair = {"claims": [], "depends_on": ["c2"],
                      "transition": "With the salt dissolved, follow the water."}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        prompts = _repair_prompts(llm)
        assert len(prompts) == 1 and "NO VALID DEPENDS_ON" in prompts[0]
        assert "[c2]" in prompts[0] and "[c1]" in prompts[0]    # earlier claim ids to choose
        assert doc["dropped_steps"] == [] and doc["status"] == "verified"
        assert doc["steps"][2]["depends_on"] == ["c2"]
        assert doc["steps"][2]["transition"] == "With the salt dissolved, follow the water."
        assert [c["id"] for c in doc["steps"][2]["claims"]] == ["c3"]   # claims kept

    def test_invalid_depends_on_triggers_repair_and_invalid_repairs_are_not_applied(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["depends_on"] = ["nope"]
        llm.repair = {"claims": [], "depends_on": ["c3"],          # same step: invalid
                      "transition": "After 2 rounds, look again."}  # digits: invalid
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        from shorts_engine import config
        assert len(_repair_prompts(llm)) == config.PLAN_MAX_REPAIR_ROUNDS
        dropped = {s["step_id"]: s for s in doc["dropped_steps"]}
        assert dropped["s3"]["reason"] == "no valid depends_on"
        assert dropped["s3"]["depends_on"] == ["nope"]
        assert "2 rounds" not in dropped["s3"]["transition"]

    def test_first_step_needs_no_depends_on_and_is_never_asked_for_one(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"][0]["role"] = "fact"
        llm.repair = lambda p: {"claims": [GOOD_MECHANISM] if "Step goal: GOAL-s1\n" in p else [],
                                "depends_on": ["c2"]}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        s1_prompt = _repair_prompts(llm, "s1")[0]
        assert "NO VALID DEPENDS_ON" not in s1_prompt
        assert doc["steps"][0]["depends_on"] == []            # returned value ignored
        assert doc["status"] == "verified"

    def test_repair_prompt_keeps_supported_claims_and_explains_failures(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[1]["claims"].append(_claim("bad", "Invented.", "blog_stated", "nope nope nope"))
        llm.repair = {"claims": []}
        _run_doc(tmp_path, monkeypatch, steps)
        prompt = _repair_prompts(llm)[0]
        assert "Calcium nitrate dissolves in water." in prompt      # kept claim listed
        assert "Invented." in prompt and "quote not found" in prompt  # failed claim + reason
        assert "Current depends_on: ['c1']" in prompt
        assert "Current transition: With that settled" in prompt

    def test_exhausted_repair_drops_the_step_and_its_dependents(
            self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        steps[1]["claims"][0]["role"] = "fact"
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert len(_repair_prompts(llm, "s2")) == config.PLAN_MAX_REPAIR_ROUNDS
        assert len(_repair_prompts(llm)) <= vc.max_repair_calls(3)
        assert [(s["step_id"], s["reason"]) for s in doc["dropped_steps"]] == \
            [("s2", "no verified mechanism"), ("s3", "depends on dropped claims")]

    def test_illustrative_mechanism_does_not_count(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[1]["claims"][0] = _claim("ill", "Like sugar in tea.", "illustrative",
                                       role="mechanism")
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert doc["dropped_steps"][0]["step_id"] == "s2"

    def test_step_survives_on_its_remaining_dependency(self, tmp_path, monkeypatch, llm):
        steps = _good_steps(4)
        steps[1]["claims"][0]["role"] = "fact"           # s2 never gets a mechanism
        steps[2]["depends_on"] = ["c1", "c2"]            # s3 also builds on s1
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        assert [s["step_id"] for s in doc["steps"]] == ["s1", "s3", "s4"]
        assert doc["steps"][1]["depends_on"] == ["c1"]   # the dropped c2 is trimmed
        assert [(s["step_id"], s["reason"]) for s in doc["dropped_steps"]] == \
            [("s2", "no verified mechanism")]
        assert doc["status"] == "verified"

    def test_a_chain_of_dependent_steps_cascades_and_terminates(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps(6)
        steps[1]["claims"][0]["role"] = "fact"
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert [s["step_id"] for s in doc["steps"]] == ["s1"]
        assert [(s["step_id"], s["reason"]) for s in doc["dropped_steps"]] == \
            [("s2", "no verified mechanism")] + \
            [(f"s{i}", "depends on dropped claims") for i in range(3, 7)]
        assert len(_repair_prompts(llm)) <= vc.max_repair_calls(6)

    def test_prune_is_stable_and_trims_dead_dependencies(self, llm):
        steps = _good_steps(3)
        for s in steps:
            for c in s["claims"]:
                c["verdict"] = "supported"
        steps[2]["depends_on"] = ["c2", "ghost"]
        plan = _plan(steps)
        vc._prune(plan, first=steps[0])
        assert [s["step_id"] for s in plan["steps"]] == ["s1", "s2", "s3"]
        assert plan["steps"][2]["depends_on"] == ["c2"]
        before = json.dumps(plan)
        vc._prune(plan, first=steps[0])                 # already a fixed point
        assert json.dumps(plan) == before

    def test_legacy_claims_without_role_are_facts(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        for s in steps:
            for c in s["claims"]:
                del c["role"]
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert len(doc["dropped_steps"]) == 3

    def test_legacy_plan_with_link_claims_and_no_depends_on_is_repaired_not_crashed(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        for s in steps:
            del s["depends_on"], s["transition"]
        legacy = _claim("l2", "Because it dissolves, so on.", "reasoning")
        legacy["role"] = "link"                  # as stored on disk by the old planner
        steps[1]["claims"].append(legacy)
        prev = {"s2": ["c1"], "s3": ["c2"]}
        llm.repair = lambda p: {"claims": [], "depends_on": next(
            (v for k, v in prev.items() if f"Step goal: GOAL-{k}\n" in p), [])}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        assert doc["status"] == "verified" and len(doc["steps"]) == 3
        assert [s["depends_on"] for s in doc["steps"][1:]] == [["c1"], ["c2"]]
        l2 = next(c for c in doc["steps"][1]["claims"] if c["id"] == "l2")
        assert l2["verdict"] == "supported"
        assert llm.systems[[p for _s, _r, p in llm.calls].index(next(
            p for _s, _r, p in llm.calls if "Conclusion: Because it dissolves" in p))] \
            is vc._LOGIC_SYSTEM


class TestNoLinkVerification:
    """Connections between steps are deterministic depends_on references now: there is no
    link-specific prompt, premise ordering or re-link pass left in VERIFY."""

    def test_link_machinery_is_gone(self):
        for name in ("_LINK_SYSTEM", "_prune_and_relink"):
            assert not hasattr(vc, name)
        assert "link" not in vc._REPAIR_SYSTEM.lower()

    def test_repair_schema_has_optional_depends_on_and_transition(self):
        props = vc.REPAIR_SCHEMA["properties"]
        assert props["depends_on"] == {"type": "array", "items": {"type": "string"}}
        assert props["transition"] == {"type": "string"}
        assert vc.REPAIR_SCHEMA["required"] == ["claims"]

    EARLIER = "Calcium nitrate draws moisture from the air."
    OWN = "A damp surface layer binds fine dust particles together."

    def test_reasoning_sees_earlier_premises_and_own_supported_facts_whatever_the_order(
            self, llm):
        s1 = _step("s1", [_claim("m1", self.EARLIER, "blog_stated", GOOD_QUOTE,
                                 role="mechanism")])
        s2 = _step("s2", [_claim("m2", "So the dust stays bound.", "reasoning",
                                 role="mechanism"),
                          _claim("f2", self.OWN, "blog_stated", GOOD_QUOTE)],
                   depends_on=["m1"])
        plan = _plan([s1, s2])
        vc.verify_plan(plan, canonical=CANONICAL, retriever=FakeRetriever())
        idx = next(i for i, (_s, _r, p) in enumerate(llm.calls)
                   if "Conclusion: So the dust stays bound." in p)
        prompt = llm.calls[idx][2]
        assert self.EARLIER in prompt and self.OWN in prompt
        assert llm.systems[idx] is vc._LOGIC_SYSTEM
        assert [c["id"] for c in plan["steps"][1]["claims"]] == ["m2", "f2"]  # order kept

    def test_own_unsupported_claim_is_not_a_premise(self, llm):
        s1 = _step("s1", [_claim("m1", self.EARLIER, "blog_stated", GOOD_QUOTE,
                                 role="mechanism")])
        s2 = _step("s2", [_claim("f2", "INVENTED-FACT.", "blog_stated", "not in it"),
                          _claim("m2", "So the dust stays bound.", "reasoning",
                                 role="mechanism")], depends_on=["m1"])
        plan = _plan([s1, s2])
        vc.verify_plan(plan, canonical=CANONICAL, retriever=FakeRetriever())
        prompt = next(p for _s, _r, p in llm.calls if "Conclusion: So the dust" in p)
        assert "INVENTED-FACT" not in prompt

    def test_step_without_mechanism_contributes_no_premises(self, llm):
        s1 = _step("s1", [_claim("f1", "PREMISE-OF-UNEXPLAINED-STEP.", "blog_stated",
                                 GOOD_QUOTE)])
        s2 = _step("s2", [_claim("m2", "So it follows.", "reasoning", role="mechanism")],
                   depends_on=["f1"])
        plan = _plan([s1, s2])
        vc.verify_plan(plan, canonical=CANONICAL, retriever=FakeRetriever())
        m2 = plan["steps"][1]["claims"][0]
        assert m2["verdict"] == "unsupported" and "no verified premises" in m2["reason"]
