"""Authority corroboration: offline (fake search/fetch/verifier)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine import corroboration as co
from shorts_engine import explanation as ex
from shorts_engine import references as R
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.retrieval import Passage, Retriever, authority_queries
from shorts_engine.retrieval.search import SearchHit
from shorts_engine.runner import StageContext
from shorts_engine.stages import verify_claims as vc

CLAIM = "Hydrogen sulfide reacts with moisture to form sulfuric acid."
EPA = "https://www.epa.gov/sewer/h2s"
WIKI = "https://en.wikipedia.org/wiki/Hydrogen_sulfide"
VENDOR = "https://www.somevendor.com/h2s"
EPA_PAGE = ("Intro words. Hydrogen sulfide gas reacts with moisture on pipe walls to form "
            "sulfuric acid that corrodes concrete. Other unrelated sentence.")


class TestAuthorityQueries:
    def test_site_groups_and_bounded(self):
        qs = authority_queries(CLAIM)
        assert 3 <= len(qs) <= 4
        for q in qs:
            assert "site:" in q and len(q) < 220
            assert "hydrogen" in q and "sulfide" in q
        assert any("site:epa.gov" in q for q in qs)
        assert any("site:echa.europa.eu" in q for q in qs)
        assert any("site:pubchem.ncbi.nlm.nih.gov" in q for q in qs)
        assert len(set(qs)) == len(qs)

    def test_empty_claim(self):
        assert authority_queries("") == []


def _retriever(hits=None, pages=None, search_exc=False, fetch_exc=False):
    queries = []

    def search(q):
        queries.append(q)
        if search_exc:
            raise RuntimeError("down")
        return hits or []

    def fetch(url):
        if fetch_exc:
            raise RuntimeError("boom")
        return (pages or {}).get(url, "")

    r = Retriever([], search_fn=search, fetch_fn=fetch)
    r.queries = queries
    return r


class TestRetrieveAuthoritative:
    def test_filters_non_authoritative_hosts(self):
        r = _retriever([SearchHit(WIKI, "w", ""), SearchHit(VENDOR, "v", ""),
                        SearchHit(EPA, "e", "")],
                       {WIKI: EPA_PAGE, VENDOR: EPA_PAGE, EPA: EPA_PAGE})
        ps = r.retrieve_authoritative(CLAIM)
        assert ps and all(p.url == EPA and p.kind == "authoritative" for p in ps)

    def test_never_raises_search_or_fetch_fail(self):
        assert _retriever(search_exc=True).retrieve_authoritative(CLAIM) == []
        r = _retriever([SearchHit(EPA, "e", "")], fetch_exc=True)
        assert r.retrieve_authoritative(CLAIM) == []

    def test_stops_at_first_query_with_passages_and_caches(self):
        r = _retriever([SearchHit(EPA, "e", "")], {EPA: EPA_PAGE})
        r.retrieve_authoritative(CLAIM)
        assert len(r.queries) == 1
        r.retrieve_authoritative(CLAIM)
        assert len(r.queries) == 1

    def test_tries_all_queries_when_empty(self):
        r = _retriever([SearchHit(WIKI, "w", "")], {WIKI: EPA_PAGE})
        assert r.retrieve_authoritative(CLAIM) == []
        assert len(r.queries) == len(authority_queries(CLAIM))


class FakeAuthRetriever:
    def __init__(self, passages=None, exc=False):
        self.passages, self.exc, self.calls = passages or [], exc, []

    def retrieve_authoritative(self, text):
        self.calls.append(text)
        if self.exc:
            raise RuntimeError("x")
        return self.passages


def _claim(cid="c1", text=CLAIM, kind="external_fact", role="mechanism", verdict="supported",
           support=None):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "role": role, "quote": ""})
    c["verdict"] = verdict
    if support is not None:
        c["support"] = support
    return c


def P(url=EPA, text="Hydrogen sulfide gas reacts with moisture on pipe walls to form "
                    "sulfuric acid that corrodes concrete."):
    return Passage(url, text, "authoritative")


@pytest.fixture
def verifier(monkeypatch):
    state = {"res": {"verdict": "supported", "passage_index": 0}, "calls": []}

    def fake(prompt, system, schema, **kw):
        state["calls"].append((prompt, system, schema, kw))
        if isinstance(state["res"], Exception):
            raise state["res"]
        return state["res"]

    monkeypatch.setattr(co.text_llm, "generate_schema_json", fake)
    return state


class TestCorroborateClaim:
    def test_supported(self, verifier):
        out = co.corroborate_claim(_claim(), FakeAuthRetriever([P()]))
        assert out["url"] == EPA and out["publisher"] == "US EPA"
        assert out["tier"] == "authoritative" and "sulfuric acid" in out["quote"]
        assert out["kind"] == "standard"
        prompt, system, schema, kw = verifier["calls"][0]
        assert schema is vc.VERDICT_SCHEMA and kw["role"] == "verifier"
        assert "strict" in system.lower() and "outside knowledge" in system.lower()

    def test_unsupported(self, verifier):
        verifier["res"] = {"verdict": "unsupported", "passage_index": None}
        assert co.corroborate_claim(_claim(), FakeAuthRetriever([P()])) is None

    def test_contradicted(self, verifier):
        verifier["res"] = {"verdict": "contradicted", "passage_index": 0}
        assert co.corroborate_claim(_claim(), FakeAuthRetriever([P()])) is None

    def test_bad_index(self, verifier):
        verifier["res"] = {"verdict": "supported", "passage_index": 7}
        assert co.corroborate_claim(_claim(), FakeAuthRetriever([P()])) is None

    def test_verifier_error(self, verifier):
        verifier["res"] = RuntimeError("ollama down")
        assert co.corroborate_claim(_claim(), FakeAuthRetriever([P()])) is None

    def test_retriever_error(self, verifier):
        assert co.corroborate_claim(_claim(), FakeAuthRetriever(exc=True)) is None

    def test_no_passages_no_llm(self, verifier):
        assert co.corroborate_claim(_claim(), FakeAuthRetriever([])) is None
        assert verifier["calls"] == []

    def test_number_gate(self, verifier):
        c = _claim(text="Dosing is 50 mg/L of nitrate.")
        bad = [P(text="Dosing is typically 20 mg/L of nitrate in sewers.")]
        assert co.corroborate_claim(c, FakeAuthRetriever(bad)) is None
        ok = [P(text="Dosing is typically 50 mg/L of nitrate in sewers.")]
        assert co.corroborate_claim(c, FakeAuthRetriever(ok)) is not None

    def test_weak_host_excluded(self, verifier):
        for u in (WIKI, VENDOR):
            assert co.corroborate_claim(_claim(), FakeAuthRetriever([P(url=u)])) is None
        assert verifier["calls"] == []

    def test_local_only_passed(self, verifier):
        co.corroborate_claim(_claim(), FakeAuthRetriever([P()]), local_only=True)
        assert verifier["calls"][0][3]["local_only"] is True


def _plan(*steps):
    return {"steps": [{"step_id": f"s{i+1}", "claims": cl, "terms": [], "depends_on": []}
                      for i, cl in enumerate(steps)]}


class TestSelect:
    def test_skips_reasoning_illustrative_unsupported_and_limits(self):
        plan = _plan(
            [_claim("a1", kind="reasoning", role="fact"), _claim("a2", role="fact"),
             _claim("a3", kind="illustrative", role="fact"),
             _claim("a4", verdict="unsupported")],
            [_claim("b1", kind="blog_stated", role="mechanism"), _claim("b2", role="fact")],
            [_claim("c1", role="mechanism")])
        ids = [c["id"] for c in co.select_key_claims(plan, limit=10)]
        assert set(ids) == {"a2", "b1", "b2", "c1"}
        assert len(co.select_key_claims(plan, limit=2)) == 2

    def test_spread_across_steps(self):
        plan = _plan([_claim(f"a{i}") for i in range(4)], [_claim("b1")], [_claim("c1")])
        ids = [c["id"] for c in co.select_key_claims(plan, limit=3)]
        assert sorted(i[0] for i in ids) == ["a", "b", "c"]

    def test_mechanism_preferred(self):
        plan = _plan([_claim("f", role="fact"), _claim("m", role="mechanism")])
        assert co.select_key_claims(plan, limit=1)[0]["id"] == "m"


class TestCorroboratePlan:
    def test_marks_claims_and_keeps_verdicts(self, verifier):
        plan = _plan([_claim("a1")], [_claim("b1", kind="reasoning", role="fact")])
        snap = lambda: json.dumps([[(c["verdict"], c.get("support")) for c in s["claims"]]  # noqa: E731
                                   for s in plan["steps"]])
        before = snap()
        counts = co.corroborate_plan(plan, FakeAuthRetriever([P()]))
        assert counts["corroborated"] == 1 and counts["attempted"] == 1
        assert plan["steps"][0]["claims"][0]["corroboration"]["url"] == EPA
        assert "corroboration" not in plan["steps"][1]["claims"][0]
        assert before == snap()

    def test_failures_leave_no_field(self, verifier):
        verifier["res"] = RuntimeError("x")
        plan = _plan([_claim("a1")])
        counts = co.corroborate_plan(plan, FakeAuthRetriever([P()]))
        assert counts["corroborated"] == 0
        assert "corroboration" not in plan["steps"][0]["claims"][0]


# -- verify_claims.run integration --------------------------------------------
CANON = "Calcium nitrate dissolves readily in water."


class _VR:
    def retrieve(self, t):
        return []


def _ctx(tmp_path):
    steps = []
    for i in (1, 2, 3):
        c = ex.finalize_claim({"id": f"c{i}", "text": "Calcium nitrate dissolves in water.",
                               "kind": "blog_stated", "role": "mechanism", "quote": CANON})
        steps.append({"step_id": f"s{i}", "claim_text": "g", "claims": [c], "terms": [],
                      "depends_on": [f"c{i-1}"] if i > 1 else [],
                      "transition": "With that settled, next." if i > 1 else "",
                      "visual_intent": {"entities": ["x"], "relationship": "r", "quantity": "q"}})
    plan = {"question": "q?", "steps": steps, "payoff": {}, "status": "planned",
            "attempts": 1, "dropped_claims": [], "dropped_steps": [], "hold_reasons": []}
    m = RunManifest.create("https://hrsuindore.com/blog/x/", workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANON, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"citations": []}), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestRunIntegration:
    def _setup(self, monkeypatch, corroborate):
        monkeypatch.setattr(vc, "Retriever", lambda urls: _VR())
        monkeypatch.setattr(vc.text_llm, "generate_schema_json",
                            lambda *a, **k: {"verdict": "supported", "passage_index": 0})
        monkeypatch.setattr(vc.corroboration, "corroborate_plan", corroborate)

    def test_corroboration_written(self, tmp_path, monkeypatch):
        def fake(plan, retriever, **kw):
            plan["steps"][0]["claims"][0]["corroboration"] = {"url": EPA}
            return {"attempted": 1, "corroborated": 1}
        self._setup(monkeypatch, fake)
        ctx = _ctx(tmp_path)
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "verified"
        assert doc["steps"][0]["claims"][0]["corroboration"]["url"] == EPA

    def test_raising_corroboration_does_not_affect_run(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("kaboom")
        self._setup(monkeypatch, boom)
        ctx = _ctx(tmp_path)
        assert vc.run(ctx) == {"explanation_plan": "explanation_plan.json"}
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "verified" and len(doc["steps"]) == 3

    def test_switch_off(self, tmp_path, monkeypatch):
        called = []
        self._setup(monkeypatch, lambda *a, **k: called.append(1))
        monkeypatch.setattr(vc.config, "CORROBORATE_CLAIMS", False)
        vc.run(_ctx(tmp_path))
        assert not called

    def test_not_run_when_held(self, tmp_path, monkeypatch):
        called = []
        self._setup(monkeypatch, lambda *a, **k: called.append(1))
        monkeypatch.setattr(vc.config, "MIN_VERIFIED_STEPS", 9)
        with pytest.raises(HoldForReview):
            vc.run(_ctx(tmp_path))
        assert not called


# -- references ------------------------------------------------------------------
class TestReferences:
    CORR = {"url": EPA, "publisher": "US EPA", "quote": "x", "tier": "authoritative",
            "kind": "standard"}

    def _plan(self, corr):
        c = _claim("m1", kind="blog_stated", role="mechanism",
                   support={"type": "blog_quote", "quote": "No marker here", "url": None})
        if corr:
            c["corroboration"] = corr
        r = _claim("r1", kind="reasoning", role="fact",
                   support={"type": None, "quote": "", "url": None})
        return {"question": "q", "steps": [{"step_id": "s1", "claims": [c, r],
                                            "depends_on": []}]}

    def test_corroboration_gives_chip(self):
        plan = self._plan(self.CORR)
        refs = R.build_references(plan, {"citations": []}, "No marker here.")
        c = refs["claims"]["m1"]
        assert c["tier"] == "authoritative" and c["chip"] == "US EPA"
        assert refs["sources"][0]["url"] == EPA
        assert refs["claims"]["r1"]["chip"] == "US EPA"
        assert R.pick_chip_claims(refs, plan)[0]["label"] == "US EPA"

    def test_no_corroboration_no_chip(self):
        refs = R.build_references(self._plan(None), {"citations": []}, "No marker here.")
        assert refs["claims"]["m1"]["chip"] is None

    def test_weak_corroboration_never_chip(self):
        bad = dict(self.CORR, url=WIKI)
        refs = R.build_references(self._plan(bad), {"citations": []}, "No marker here.")
        assert refs["claims"]["m1"]["chip"] is None
        assert refs["claims"]["m1"]["tier"] != "authoritative"
