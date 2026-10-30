# tests/shorts_engine/test_verify_strategic.py
"""Strategic-track claim handling in verify_claims (offline, fakes only)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine import explanation as ex
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import verify_claims as vc

CANONICAL = (
    "Free trade agreements reduce reliance on single source corridors for buyers3. "
    "Our analysis is that shifting to long term contracts lets a firm stabilise its margins. "
    "The agreement lowers the tariff to 5% on nitrate imports. "
    "Resilience depends on diversifying suppliers across regions."
)
Q_CITED = "Free trade agreements reduce reliance on single source corridors for buyers3."
Q_ANALYSIS = "Our analysis is that shifting to long term contracts lets a firm stabilise its margins."
Q_UNCITED_NUM = "The agreement lowers the tariff to 5% on nitrate imports."
Q_DIVERSIFY = "Resilience depends on diversifying suppliers across regions."
CITES = [{"marker": 3, "title": "Trade review", "url": "https://example.org/trade-review",
          "kind": "web"}]


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def retrieve(self, text):
        self.calls.append(text)
        return []

    def retrieve_authoritative(self, text):
        return []


class Router:
    def __init__(self, repair=None):
        self.repair, self.calls = repair, []

    def __call__(self, prompt, system, schema, **kw):
        self.calls.append((schema, system, prompt))
        if schema is vc.VERDICT_SCHEMA:
            return {"verdict": "supported", "passage_index": 0}
        if schema in (vc.REPAIR_SCHEMA, vc.STRATEGIC_REPAIR_SCHEMA):
            return self.repair or {"claims": []}
        raise AssertionError(schema)


def _claim(cid, text, quote, stance, role="mechanism", kind="blog_stated"):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "role": role,
                           "quote": quote, "stance": stance})
    return c


def _step(sid, claims, depends_on=()):
    return {"step_id": sid, "claim_text": f"GOAL-{sid}", "claims": claims, "terms": [],
            "depends_on": list(depends_on),
            "transition": "Next." if depends_on else "",
            "visual_intent": {"entities": ["x"], "relationship": "r", "quantity": "q"}}


def _plan(steps, kind="strategic"):
    return {"question": "q?", "steps": steps, "post_kind": kind,
            "payoff": {"takeaway": "t", "differentiator_id": "b_supply"},
            "status": "planned", "attempts": 1, "dropped_claims": [], "dropped_steps": [],
            "hold_reasons": []}


def _good_steps(extra=None):
    s1 = _step("s1", [_claim("c1", "Free trade agreements reduce reliance on single source "
                                    "corridors.", Q_CITED, "checkable")])
    s2 = _step("s2", [_claim("c2", "Shifting to long term contracts lets a firm stabilise "
                                    "its margins.", Q_ANALYSIS, "analysis")], ["c1"])
    s3 = _step("s3", [_claim("c3", "Resilience depends on diversifying suppliers across "
                                    "regions.", Q_DIVERSIFY, "framework")], ["c2"])
    steps = [s1, s2, s3]
    if extra:
        steps[0]["claims"].append(extra)
    return steps


def _run(tmp_path, monkeypatch, plan, router=None):
    m = RunManifest.create("https://hrsuindore.com/blog/x/", workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANONICAL, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"citations": CITES}), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    router = router or Router()
    monkeypatch.setattr(vc.text_llm, "generate_schema_json", router)
    monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
    monkeypatch.setattr(vc.config, "CORROBORATE_CLAIMS", False)
    ctx = StageContext(manifest=m, workspace=ws, flags={})
    vc.run(ctx)
    doc = json.loads((ws / "explanation_plan.json").read_text(encoding="utf-8"))
    return doc, ctx, router


class TestEffectiveStance:
    def test_number_acronym_or_named_agreement_is_checkable_whatever_it_was_called(self):
        assert vc.effective_stance({"stance": "analysis", "text": "Tariffs fall to 5%."}) == "checkable"
        assert vc.effective_stance({"stance": "analysis", "text": "CPTPP members gain access."}) == "checkable"
        assert vc.effective_stance({"stance": "framework", "text": "The Trade Agreement matters."}) == "checkable"

    def test_plain_argument_stays_analysis_and_technical_claims_have_no_stance(self):
        assert vc.effective_stance({"stance": "analysis", "text": "CapEx locks in margins."}) == "analysis"
        assert vc.effective_stance({"text": "Tariffs fall to 5%."}) is None


class TestCheckableRule:
    def test_cited_checkable_claim_is_kept_and_carries_the_citation(self, tmp_path, monkeypatch):
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps()))
        c1 = doc["steps"][0]["claims"][0]
        assert c1["verdict"] == "supported"
        assert c1["support"]["cited"] == [
            {"marker": 3, "title": "Trade review", "url": "https://example.org/trade-review"}]

    def test_uncited_checkable_claim_is_dropped_never_asserted(self, tmp_path, monkeypatch):
        extra = _claim("cx", "The agreement lowers the tariff to 5% on nitrate imports.",
                       Q_UNCITED_NUM, "checkable", role="fact")
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps(extra)))
        assert "cx" not in [c["id"] for s in doc["steps"] for c in s["claims"]]
        dropped = {c["id"]: c for c in doc["dropped_claims"]}
        assert "no citation" in dropped["cx"]["reason"]

    def test_a_figure_called_analysis_is_still_held_to_the_checkable_rule(self, tmp_path, monkeypatch):
        extra = _claim("cy", "The agreement lowers the tariff to 5% on nitrate imports.",
                       Q_UNCITED_NUM, "analysis", role="fact")
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps(extra)))
        assert "cy" in [c["id"] for c in doc["dropped_claims"]]

    def test_analysis_and_framework_need_only_the_blog_quote(self, tmp_path, monkeypatch):
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps()))
        assert [c["verdict"] for s in doc["steps"][1:] for c in s["claims"]] == ["supported"] * 2

    def test_technical_plan_ignores_the_rule(self, tmp_path, monkeypatch):
        extra = _claim("cx", "The agreement lowers the tariff to 5% on nitrate imports.",
                       Q_UNCITED_NUM, "checkable", role="fact")
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps(extra), kind="technical"))
        assert "cx" in [c["id"] for c in doc["steps"][0]["claims"]]
        assert "trust_summary" not in doc


class TestTrustSummary:
    def test_counts_are_in_plan_and_manifest(self, tmp_path, monkeypatch):
        extra = _claim("cx", "The agreement lowers the tariff to 5% on nitrate imports.",
                       Q_UNCITED_NUM, "checkable", role="fact")
        doc, ctx, _ = _run(tmp_path, monkeypatch, _plan(_good_steps(extra)))
        ts = doc["trust_summary"]
        assert ts["checkable_verified"] == 1 and ts["checkable_dropped"] == 1
        assert ts["analysis_attributed"] == 1 and ts["framework"] == 1 and ts["steps"] == 3
        saved = json.loads((Path(ctx.workspace) / "run_manifest.json").read_text())
        assert saved["trust_summary"] == ts


class TestStrategicRepair:
    def test_repair_uses_strategic_schema_and_tells_planner_to_attribute_or_drop(
            self, tmp_path, monkeypatch):
        extra = _claim("cx", "The agreement lowers the tariff to 5% on nitrate imports.",
                       Q_UNCITED_NUM, "checkable", role="fact")
        _, _, router = _run(tmp_path, monkeypatch, _plan(_good_steps(extra)))
        repairs = [c for c in router.calls if c[0] is vc.STRATEGIC_REPAIR_SCHEMA]
        assert repairs and "STRATEGIC post" in repairs[0][1]
        assert "analysis" in repairs[0][1] and "citation" in repairs[0][1]
        assert not [c for c in router.calls if c[0] is vc.REPAIR_SCHEMA]

    def test_technical_repair_is_unchanged(self, tmp_path, monkeypatch):
        steps = _good_steps()
        steps[0]["claims"].append(_claim("cz", "Unsourced.", "", None, kind="external_fact",
                                         role="fact"))
        _, _, router = _run(tmp_path, monkeypatch, _plan(steps, kind="technical"))
        assert [c for c in router.calls if c[0] is vc.REPAIR_SCHEMA]
        assert not [c for c in router.calls if c[0] is vc.STRATEGIC_REPAIR_SCHEMA]


class TestRelevance:
    def test_off_topic_claim_is_dropped_with_reason(self, tmp_path, monkeypatch):
        class R(Router):
            def __call__(self, prompt, system, schema, **kw):
                if system is vc._RELEVANCE_SYSTEM and "Safety perimeter" in prompt:
                    return {"verdict": "unsupported", "passage_index": None}
                return super().__call__(prompt, system, schema, **kw)
        extra = _claim("cx", "Safety perimeter planning matters.", Q_ANALYSIS, "analysis",
                       role="fact")
        doc, _, _ = _run(tmp_path, monkeypatch, _plan(_good_steps(extra)), R())
        dropped = {c["id"]: c["reason"] for c in doc["dropped_claims"]}
        assert "off-topic" in dropped["cx"] and len(doc["steps"]) == 3

    def test_relevance_is_not_asked_on_the_technical_track(self, tmp_path, monkeypatch):
        _, _, router = _run(tmp_path, monkeypatch, _plan(_good_steps(), kind="technical"))
        assert not [c for c in router.calls if c[1] is vc._RELEVANCE_SYSTEM]
