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

    def __call__(self, prompt, system, schema, **kw):
        self.calls.append((schema, kw.get("role"), prompt))
        if schema is vc.VERDICT_SCHEMA:
            if self.logic is not None and system is vc._LOGIC_SYSTEM:
                return self.logic(prompt)
            if self.verdicts:
                return self.verdicts.pop(0)
            return {"verdict": "supported", "passage_index": 0}
        if schema is vc.REPAIR_SCHEMA:
            return self.repair or {"claims": []}
        raise AssertionError(schema)


def _claim(cid, text, kind, quote="", verdict=None, role="fact"):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "role": role,
                           "quote": quote})
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


def _good_steps():
    """Three steps, each with a blog-quoted mechanism; s2 and s3 also link back."""
    q = "Calcium nitrate dissolves readily in water."
    steps = [_step(f"s{i}", [_claim(f"c{i}", "Calcium nitrate dissolves in water.",
                                    "blog_stated", q, role="mechanism")]) for i in (1, 2, 3)]
    for i in (2, 3):
        steps[i - 1]["claims"].append(_claim(
            f"l{i}", "Because it dissolves, the next effect follows.", "reasoning",
            role="link"))
    return steps


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
        assert e.value.reasons == ["only 2 verified step(s) survived; need at least 3"]
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
        # s1's mechanism fails; a supported fact stays, so s2's link still has a premise
        steps[0]["claims"] = [_claim("cx", "Invented fact.", "blog_stated", "not in article",
                                     role="mechanism"),
                              _claim("f1", "Calcium nitrate dissolves in water.", "blog_stated",
                                     "Calcium nitrate dissolves readily in water.")]
        llm.repair = {"claims": [{"id": "new", "text": "Calcium nitrate dissolves in water.",
                                  "kind": "blog_stated", "role": "mechanism",
                                  "quote": "Calcium nitrate dissolves readily in water."}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in doc["steps"][0]["claims"]]
        assert ids == ["f1", "s1_r1_1"]
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert any(s is vc.REPAIR_SCHEMA and role == "planner" for s, role, _ in llm.calls)

    def test_repair_is_bounded_by_max_rounds(self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        # only s1 fails (a supported fact keeps s2's link premise alive)
        steps[0]["claims"] = [_claim("cx", "Invented.", "blog_stated", "nope",
                                     role="mechanism"),
                              _claim("f1", "Calcium nitrate dissolves in water.", "blog_stated",
                                     "Calcium nitrate dissolves readily in water.")]
        llm.repair = {"claims": [{"id": "n", "text": "Still invented.", "kind": "blog_stated",
                                  "role": "mechanism", "quote": "nope again"}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        repairs = [c for c in llm.calls if c[0] is vc.REPAIR_SCHEMA]
        assert len(repairs) == config.PLAN_MAX_REPAIR_ROUNDS


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
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s1"]
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


GOOD_QUOTE = "Calcium nitrate dissolves readily in water."
GOOD_MECHANISM = {"id": "m", "text": "Calcium nitrate dissolves in water.",
                  "kind": "blog_stated", "role": "mechanism", "quote": GOOD_QUOTE}
GOOD_LINK = {"id": "l", "text": "Because it dissolves, the solution stays clear.",
             "kind": "reasoning", "role": "link", "quote": ""}


def _run_doc(tmp_path, monkeypatch, steps, *, hold=False):
    ctx = _ctx(tmp_path, _plan(steps))
    monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
    if hold:
        with pytest.raises(HoldForReview):
            vc.run(ctx)
    else:
        vc.run(ctx)
    return json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))


def _repair_prompts(llm):
    return [p for s, role, p in llm.calls if s is vc.REPAIR_SCHEMA and role == "planner"]


class TestExplainTheWhy:
    """Spec section 12: a step survives only with a verified mechanism and (unless first)
    a verified link; a missing one is repaired (bounded rounds) before the step is dropped."""

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
        llm.repair = {"claims": [GOOD_MECHANISM]}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        prompts = _repair_prompts(llm)
        assert len(prompts) == 1
        assert "mechanism" in prompts[0] and CANONICAL in prompts[0]
        assert doc["dropped_steps"] == [] and doc["status"] == "verified"
        s2 = doc["steps"][1]
        assert any(c["role"] == "mechanism" and c["verdict"] == "supported"
                   and c["id"] == "s2_r1_1" for c in s2["claims"])

    def test_step_lacking_link_triggers_repair_even_with_no_failed_claim(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["claims"] = steps[2]["claims"][:1]  # s3: mechanism only, no link
        llm.repair = {"claims": [GOOD_LINK]}
        doc = _run_doc(tmp_path, monkeypatch, steps)
        prompts = _repair_prompts(llm)
        assert len(prompts) == 1 and "link" in prompts[0]
        assert doc["dropped_steps"] == []
        assert any(c["role"] == "link" and c["verdict"] == "supported"
                   for c in doc["steps"][2]["claims"])

    def test_first_step_needs_no_link(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        assert not any(c["role"] == "link" for c in steps[0]["claims"])
        _run_doc(tmp_path, monkeypatch, steps)
        assert _repair_prompts(llm) == []

    def test_repair_prompt_keeps_supported_claims_and_explains_failures(
            self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[1]["claims"].append(_claim("bad", "Invented.", "blog_stated", "nope nope nope"))
        llm.repair = {"claims": []}
        _run_doc(tmp_path, monkeypatch, steps)
        prompt = _repair_prompts(llm)[0]
        assert "Calcium nitrate dissolves in water." in prompt      # kept claim listed
        assert "Invented." in prompt and "quote not found" in prompt  # failed claim + reason

    def test_exhausted_repair_drops_the_step_with_a_reason(self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        steps[1]["claims"][0]["role"] = "fact"
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert len(_repair_prompts(llm)) == config.PLAN_MAX_REPAIR_ROUNDS
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s2"]
        assert doc["dropped_steps"][0]["reason"] == "no verified mechanism"

    def test_missing_link_after_exhausted_repair_drops_step(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["claims"] = steps[2]["claims"][:1]
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert [(s["step_id"], s["reason"]) for s in doc["dropped_steps"]] == \
            [("s3", "no verified link")]

    def test_illustrative_mechanism_does_not_count(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[1]["claims"][0] = _claim("ill", "Like sugar in tea.", "illustrative",
                                       role="mechanism")
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s2"]

    def test_link_premises_come_only_from_earlier_steps(self, llm):
        own = _claim("m2", "OWN-STEP-MECHANISM dissolves.", "blog_stated", GOOD_QUOTE,
                     role="mechanism")
        link = _claim("l2", "Because of it, so this.", "reasoning", role="link")
        plan = _plan([_step("s1", [_claim("m1", "EARLIER-STEP-FACT dissolves.",
                                          "blog_stated", GOOD_QUOTE, role="mechanism")]),
                      _step("s2", [own, link])])
        vc.verify_plan(plan, canonical=CANONICAL, retriever=FakeRetriever())
        logic = [p for s, _r, p in llm.calls if "Conclusion: Because of it" in p]
        assert len(logic) == 1
        assert "EARLIER-STEP-FACT" in logic[0] and "OWN-STEP-MECHANISM" not in logic[0]

    def test_link_whose_premise_step_is_dropped_is_not_left_supported(
            self, tmp_path, monkeypatch, llm):
        # s2 holds the only premise s3's link relies on, but s2 has no mechanism, so it
        # is dropped after the repair rounds; s3's link must then be re-checked against
        # the surviving premises (s1 only) and fail.
        steps = _good_steps()
        steps.append(_step("s4", [_claim("m4", "Calcium nitrate dissolves in water.",
                                         "blog_stated", GOOD_QUOTE, role="mechanism"),
                                  _claim("l4", "Because it dissolves, s4 follows.",
                                         "reasoning", role="link")]))
        steps[1]["claims"] = [
            _claim("f2", "PREMISE-ONLY-IN-S2 water carries it.", "blog_stated", GOOD_QUOTE),
            _claim("l2", "Because it dissolves, water carries it.", "reasoning",
                   role="link")]
        steps[2]["claims"][1] = _claim("l3", "Because water carries it, s3 follows.",
                                       "reasoning", role="link")

        def logic(prompt):
            if "Conclusion: Because water carries it" in prompt:
                ok = "PREMISE-ONLY-IN-S2" in prompt
                return {"verdict": "supported" if ok else "unsupported", "passage_index": None}
            return {"verdict": "supported", "passage_index": None}

        llm.logic = logic
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        dropped = {s["step_id"]: s["reason"] for s in doc["dropped_steps"]}
        assert dropped == {"s2": "no verified mechanism", "s3": "no verified link"}
        assert [s["step_id"] for s in doc["steps"]] == ["s1", "s4"]
        assert all(c["id"] != "l3" for s in doc["steps"] for c in s["claims"])
        l3 = [c for c in doc["dropped_claims"] if c["id"] == "l3"]
        assert l3 and l3[-1]["verdict"] != "supported"
        # s3 was verified while s2 still existed (supported), then re-checked once
        l3_checks = [p for s, _r, p in llm.calls if "Conclusion: Because water carries it" in p]
        assert len(l3_checks) == 2

    def test_legacy_claims_without_role_are_facts(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        for s in steps:
            for c in s["claims"]:
                del c["role"]
        llm.repair = {"claims": []}
        doc = _run_doc(tmp_path, monkeypatch, steps, hold=True)
        assert len(doc["dropped_steps"]) == 3
