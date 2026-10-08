"""End-to-end integration test for shorts_engine
(INGEST -> FACTS -> EXPLAIN -> VERIFY_CLAIMS -> SCRIPT).

Drives the real (poisoned) Blogger fixture through the real stage modules --
ingest.run(), facts.run(), explain.run(), verify_claims.run(), script.run() /
run_gates() -- sharing a single RunManifest/StageContext exactly as
shorts_engine.runner.run() does, with only the LLM boundary
(shorts_engine.llm.text_llm.generate_schema_json) replaced by a fake that answers
by schema identity, and verify_claims' web Retriever replaced by one that finds
nothing. No network, no real Ollama.

Pins the two invariants end to end:

  1. isolation: the sibling post's teaser numbers ("150,000 metric tons")
     never survive INGEST into canonical.txt, so a "poison" fact built from
     that teaser can never verify -- FACTS' verbatim gate must drop it.
  2. never-unverified: a beat narration containing a fabricated number that
     is not backed by any referenced fact or verified claim fails run_gates(),
     and when the writer LLM persistently returns such a narration, SCRIPT's
     run() raises HoldForReview instead of ever writing script.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine import explanation
from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import explain as explain_stage
from shorts_engine.stages import facts as facts_stage
from shorts_engine.stages import ingest as ingest_stage
from shorts_engine.stages import script as script_stage
from shorts_engine.stages import verify_claims as vc_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"
FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"

REAL_QUOTE = "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume"
SIBLING_POISON_MARKER = "150,000 metric tons"

# Planner output: every blog_stated quote is a real passage of the fixture's
# canonical.txt (numbers only where the quote carries the same number+unit), so
# VERIFY supports them with no web access; the reasoning claim follows from them.
PLAN_RAW = {
    "question": "Why do EU plants dose calcium nitrate to cut nitrate discharge?",
    "steps": [
        {"step_id": "s1", "claim_text": "There is an established dosing window.",
         "claims": [{"id": "c1", "kind": "blog_stated", "quote": REAL_QUOTE,
                     "text": "Best practice uses a dosage range of 1.5 to 3 kg per cubic "
                             "meter of wastewater."}],
         "terms": [],
         "visual_intent": {"entities": ["Calcium nitrate", "Wastewater"],
                           "relationship": "dosed into", "quantity": "dose"}},
        {"step_id": "s2", "claim_text": "Bacteria turn nitrate into nitrogen gas.",
         "claims": [{"id": "c2", "kind": "blog_stated",
                     "quote": "This bacteria utilizes nitrate as an electron acceptor, "
                              "converting it into harmless nitrogen gas",
                     "text": "Denitrifying bacteria use nitrate as an electron acceptor "
                             "and convert it into harmless nitrogen gas."}],
         "terms": [{"term": "denitrification",
                    "definition": "bacteria converting nitrate into nitrogen gas",
                    "unit": ""}],
         "visual_intent": {"entities": ["Nitrate", "Denitrifying bacteria", "Nitrogen gas"],
                           "relationship": "converted by", "quantity": "nitrate level"}},
        {"step_id": "s3", "claim_text": "The dose must be tuned per site.",
         "claims": [{"id": "c3", "kind": "blog_stated",
                     "quote": "The amount of chemicals required for treatment depends on "
                              "the pH and alkalinity of the wastewater",
                     "text": "The amount needed depends on the pH and alkalinity of the "
                             "wastewater."},
                    {"id": "c4", "kind": "reasoning", "quote": "",
                     "text": "So the dose has to be tuned to each site."}],
         "terms": [],
         "visual_intent": {"entities": ["pH", "Alkalinity", "Dose"],
                           "relationship": "sets", "quantity": "dose"}},
    ],
    "payoff": {"takeaway": "Tune a consistent calcium nitrate dose to your wastewater.",
               "differentiator_id": "b_purity"},
}

GOOD_DOC = {
    "hook": {"narration": "Why do EU plants dose calcium nitrate to meet tightening "
                          "nitrate discharge limits?",
             "card_text": "Why dose calcium nitrate?", "broll_wish": ""},
    "steps": [
        {"step_id": "s1", "narration": "Current best practice suggests a dosage range of "
                                       "1.5 to 3 kg per cubic meter of wastewater.",
         "card_text": "The dosing window", "broll_wish": ""},
        {"step_id": "s2", "narration": "That dose feeds denitrifying bacteria, which use "
                                       "nitrate as an electron acceptor and convert it "
                                       "into harmless nitrogen gas.",
         "card_text": "Bacteria make nitrogen gas", "broll_wish": ""},
        {"step_id": "s3", "narration": "How much you need depends on the pH and alkalinity "
                                       "of the wastewater, so the dose has to be tuned to "
                                       "each site.",
         "card_text": "Tune the dose per site", "broll_wish": ""},
    ],
    "cta": {"narration": "HRSU supplies consistent high-purity calcium nitrate powder with "
                         "batch-level QC. Visit hrsuindore.com for the guide.",
            "card_text": "hrsuindore.com", "broll_wish": ""},
}

CRITIQUE = {"actionable_score": 8, "coherence_score": 9, "hrsu_reason_score": 8,
            "faithfulness_score": 9, "revise_notes": ""}


def _fact_router(prompt, system, schema, **kw):
    """Fake FACTS-stage LLM: returns one real, verbatim-quoted fact plus one
    'poison' fact whose quote is the sibling post's teaser. The verbatim gate
    must reject the poison fact because isolation already removed that text
    from canonical.txt before FACTS ever ran."""
    assert schema is facts_stage.FACT_WRAP_SCHEMA
    return {"facts": [
        {"id": "f1", "verbatim_quote": REAL_QUOTE, "value": "1.5-3",
         "unit": "kg/m3", "claim_summary": "dosing window", "tags": ["spec"],
         "procurement_significance": 5, "citation_marker": 1},
        {"id": "f2", "verbatim_quote": "approximately " + SIBLING_POISON_MARKER,
         "value": "150000", "unit": "t",
         "claim_summary": "POISON from sibling post -- must never verify",
         "tags": ["metric"], "procurement_significance": 3,
         "citation_marker": None},
    ]}


def _make_router(doc: dict):
    """One fake LLM for the whole flow, answering by schema identity."""
    def router(prompt, system, schema, **kw):
        if schema is facts_stage.FACT_WRAP_SCHEMA:
            return _fact_router(prompt, system, schema, **kw)
        if schema is explanation.PLAN_SCHEMA:
            return json.loads(json.dumps(PLAN_RAW))
        if schema is vc_stage.VERDICT_SCHEMA:
            return {"verdict": "supported", "passage_index": 0}
        if schema is vc_stage.REPAIR_SCHEMA:
            return {"claims": []}
        if schema is script_stage.SCRIPT_SCHEMA:
            return json.loads(json.dumps(doc))
        if schema is script_stage.CRITIQUE_SCHEMA:
            return CRITIQUE
        raise AssertionError(f"unexpected schema: {schema}")
    return router


class _EmptyRetriever:
    def retrieve(self, claim_text):
        return []


def _make_ctx(tmp_path: Path) -> StageContext:
    manifest = RunManifest.create(URL, workspace_root=tmp_path)
    return StageContext(manifest=manifest, workspace=Path(manifest.workspace), flags={})


def _run_to_verified_plan(ctx: StageContext, monkeypatch, router) -> None:
    """INGEST -> FACTS -> EXPLAIN -> VERIFY_CLAIMS on the real fixture."""
    ctx.flags["html_override"] = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", router)
    monkeypatch.setattr(vc_stage, "Retriever", lambda urls: _EmptyRetriever())
    ctx.manifest.checkpoint("ingested", **ingest_stage.run(ctx))
    ctx.manifest.checkpoint("facts", **facts_stage.run(ctx))
    ctx.manifest.checkpoint("explained", **explain_stage.run(ctx))
    ctx.manifest.checkpoint("claims_verified", **vc_stage.run(ctx))


def _verified_plan_offline() -> dict:
    """PLAN_RAW as verify_claims leaves it when every claim is supported."""
    plan = explanation.to_plan_document(PLAN_RAW, attempts=1)
    for step in plan["steps"]:
        for c in step["claims"]:
            c["verdict"] = "supported"
    plan["status"] = "verified"
    return plan


class TestIsolationInvariant:
    """Sibling-post content must be structurally unreachable past INGEST, and
    that must transitively keep a sibling-derived fact out of factsheet.json."""

    def test_sibling_content_excluded_from_canonical_and_factsheet(
        self, tmp_path, monkeypatch
    ) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        ctx = _make_ctx(tmp_path)
        ctx.flags["html_override"] = html

        ingest_artifacts = ingest_stage.run(ctx)
        ctx.manifest.checkpoint("ingested", **ingest_artifacts)

        canonical = (ctx.workspace / "canonical.txt").read_text(encoding="utf-8")
        assert SIBLING_POISON_MARKER not in canonical, (
            "sibling-post teaser leaked into canonical.txt -- isolation broke"
        )
        assert "1.5 to 3 kg" in canonical, "target post's own content is missing"

        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", _fact_router)
        facts_artifacts = facts_stage.run(ctx)
        ctx.manifest.checkpoint("facts", **facts_artifacts)

        factsheet = json.loads(
            (ctx.workspace / "factsheet.json").read_text(encoding="utf-8")
        )
        kept_ids = [f["id"] for f in factsheet["facts"]]
        assert "f1" in kept_ids
        assert "f2" not in kept_ids, (
            "sibling-post poison fact must be dropped by the verbatim gate"
        )
        dropped_reasons = [d["reason"] for d in factsheet["dropped"] if d["id"] == "f2"]
        assert dropped_reasons and "not located" in dropped_reasons[0]


class TestNeverUnverifiedInvariant:
    """A fabricated numeric token must fail run_gates() and must block
    SCRIPT's run() with a raised HoldForReview -- it must never reach
    script.json."""

    @staticmethod
    def _factsheet_with_only_real_fact() -> dict:
        return {
            "facts": [{
                "id": "f1", "verbatim_quote": REAL_QUOTE, "char_offset": 0,
                "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
                "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1,
            }],
            "brand_facts": [], "dropped": [],
        }

    @staticmethod
    def _bad_doc() -> dict:
        bad = json.loads(json.dumps(GOOD_DOC))
        bad["steps"][1]["narration"] = (
            "Bacteria then cut nitrate levels by 150 mg per liter of wastewater flow, "
            "converting it into harmless nitrogen gas."
        )
        return bad

    def test_fabricated_number_fails_run_gates(self) -> None:
        factsheet = self._factsheet_with_only_real_fact()
        brand = load_brand_facts()
        plan = _verified_plan_offline()

        beats, errs = script_stage.build_beats(self._bad_doc(), plan, factsheet, brand)
        assert errs == []
        errors = script_stage.run_gates(beats, factsheet, brand,
                                        extra_by_beat=script_stage.extra_pool_by_beat(plan))
        assert errors, "a fabricated '150 mg/L' must fail at least one gate"
        assert any("150" in e and "does not trace" in e for e in errors), errors

    def test_fabricated_number_holds_for_review_through_run(
        self, tmp_path, monkeypatch
    ) -> None:
        ctx = _make_ctx(tmp_path)
        _run_to_verified_plan(ctx, monkeypatch, _make_router(self._bad_doc()))

        with pytest.raises(HoldForReview) as exc_info:
            script_stage.run(ctx)
        assert any("150" in e for e in exc_info.value.reasons)
        assert not (ctx.workspace / "script.json").exists(), (
            "an ungrounded script must never be written to disk"
        )


class TestFullPipelineGroundedScript:
    """The full happy path across all five stages, sharing one manifest/workspace
    exactly like shorts_engine.runner.run() does: isolation holds, only the real
    fact survives the verbatim gate, every planned claim verifies against the
    article, and the resulting script.json is fully traceable back to them."""

    def test_full_run_produces_grounded_script(self, tmp_path, monkeypatch) -> None:
        ctx = _make_ctx(tmp_path)
        _run_to_verified_plan(ctx, monkeypatch, _make_router(GOOD_DOC))

        plan = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert plan["status"] == "verified"
        assert [s["step_id"] for s in plan["steps"]] == ["s1", "s2", "s3"]
        assert plan["dropped_claims"] == []

        ctx.manifest.checkpoint("scripted", **script_stage.run(ctx))

        factsheet = json.loads(
            (ctx.workspace / "factsheet.json").read_text(encoding="utf-8")
        )
        assert [f["id"] for f in factsheet["facts"]] == ["f1"]

        script = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        beats = script["beats"]
        assert [b["beat"] for b in beats] == ["hook", "step_1", "step_2", "step_3", "cta"]
        assert beats[1]["fact_ids"] == ["f1"]
        assert beats[-1]["purpose"] == "cta"
        assert beats[-1]["fact_ids"] == ["b_purity"]
        assert script["critique"] == CRITIQUE
        assert ctx.manifest.status == "scripted"

        # Every numeric token in every beat must trace to the surviving fact, a
        # verified plan claim, a brand differentiator/CTA, or the domain -- the
        # never-unverified invariant holding on the *accepted* script.
        brand = load_brand_facts()
        assert script_stage.run_gates(
            beats, factsheet, brand,
            extra_by_beat=script_stage.extra_pool_by_beat(plan)) == []
