"""End-to-end integration test for shorts_engine Plan 1 (INGEST -> FACTS -> SCRIPT).

Drives the real (poisoned) Blogger fixture through the real stage modules --
ingest.run(), facts.run(), script.run()/run_gates() -- sharing a single
RunManifest/StageContext exactly as shorts_engine.runner.run() does, with only
the LLM boundary (shorts_engine.llm.text_llm.generate_schema_json) replaced by
a small schema-routed fake. No network, no real Ollama.

Pins the two Plan-1 invariants end to end:

  1. isolation: the sibling post's teaser numbers ("150,000 metric tons")
     never survive INGEST into canonical.txt, so a "poison" fact built from
     that teaser can never verify -- FACTS' verbatim gate must drop it.
  2. never-unverified: a beat narration/card_text containing a fabricated
     number that is not backed by any referenced fact fails run_gates(), and
     when the writer LLM persistently returns such a beat, SCRIPT's run()
     raises GateFailure instead of ever writing script.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import GateFailure
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import facts as facts_stage
from shorts_engine.stages import ingest as ingest_stage
from shorts_engine.stages import script as script_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"
FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"

REAL_QUOTE = "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume"
SIBLING_POISON_MARKER = "150,000 metric tons"

# Narration lengths sized for WORDS_PER_SECOND=1.7: per-beat integer bounds
# hook [3,8], stakes [6,12], mechanism [11,24], proof [9,20], cta [9,16];
# total 62 words, inside the aggregate [60, 85] window.
GOOD_BEATS = [
    {"beat": "hook",
     "narration": "EU nitrate discharge limits are tightening fast.",
     "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": ""},
    {"beat": "stakes",
     "narration": "Non-compliance risks steep penalties and unplanned production "
                  "downtime this quarter.",
     "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
    {"beat": "mechanism",
     "narration": "Dosing calcium nitrate feeds denitrifying bacteria, converting "
                  "nitrate into harmless nitrogen gas within the treatment train "
                  "without any retrofit.",
     "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
    {"beat": "proof",
     "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic "
                  "meter.",
     "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
    {"beat": "cta",
     "narration": "HRSU supplies high-purity powder with batch QC. Visit "
                  "hrsuindore.com for the guide.",
     "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
]

CRITIQUE = {"actionable_score": 8, "coherence_score": 9,
            "hrsu_reason_score": 8, "revise_notes": ""}


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


def _make_ctx(tmp_path: Path) -> StageContext:
    manifest = RunManifest.create(URL, workspace_root=tmp_path)
    return StageContext(manifest=manifest, workspace=Path(manifest.workspace), flags={})


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
    SCRIPT's run() with a raised GateFailure -- it must never reach
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

    def test_fabricated_number_fails_run_gates(self) -> None:
        factsheet = self._factsheet_with_only_real_fact()
        brand = load_brand_facts()

        bad_beats = json.loads(json.dumps(GOOD_BEATS))
        bad_beats[3]["narration"] = (
            "Reduces nitrate levels by 150 mg per liter of wastewater flow."
        )

        errors = script_stage.run_gates(bad_beats, factsheet, brand)
        assert errors, "a fabricated '150 mg/L' must fail at least one gate"
        assert any("150" in e and "does not trace" in e for e in errors), errors

    def test_fabricated_number_raises_gate_failure_through_run(
        self, tmp_path, monkeypatch
    ) -> None:
        bad_beats = json.loads(json.dumps(GOOD_BEATS))
        bad_beats[3]["narration"] = (
            "Reduces nitrate levels by 150 mg per liter of wastewater flow."
        )

        ctx = _make_ctx(tmp_path)
        (ctx.workspace / "post.json").write_text(json.dumps({
            "url": URL, "title": "Optimizing Nitrate Removal", "region": "eu",
            "category": "wastewater_treatment", "citations": [], "images": [],
        }), encoding="utf-8")
        (ctx.workspace / "factsheet.json").write_text(
            json.dumps(self._factsheet_with_only_real_fact()), encoding="utf-8"
        )

        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": bad_beats}
            return CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)

        with pytest.raises(GateFailure) as exc_info:
            script_stage.run(ctx)
        assert any("150" in e for e in exc_info.value.errors)
        assert not (ctx.workspace / "script.json").exists(), (
            "an ungrounded script must never be written to disk"
        )


class TestFullPipelineGroundedScript:
    """The full happy path across all three Plan-1 stages, sharing one
    manifest/workspace exactly like shorts_engine.runner.run() does: isolation
    holds, only the real fact survives the verbatim gate, and the resulting
    script.json is fully traceable back to it."""

    def test_full_run_produces_grounded_script(self, tmp_path, monkeypatch) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        ctx = _make_ctx(tmp_path)
        ctx.flags["html_override"] = html

        ctx.manifest.checkpoint("ingested", **ingest_stage.run(ctx))

        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", _fact_router)
        ctx.manifest.checkpoint("facts", **facts_stage.run(ctx))

        def script_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            if schema is script_stage.CRITIQUE_SCHEMA:
                return CRITIQUE
            raise AssertionError(f"unexpected schema in script phase: {schema}")

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", script_llm)
        ctx.manifest.checkpoint("scripted", **script_stage.run(ctx))

        factsheet = json.loads(
            (ctx.workspace / "factsheet.json").read_text(encoding="utf-8")
        )
        assert [f["id"] for f in factsheet["facts"]] == ["f1"]

        script = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert len(script["beats"]) == 5
        assert script["beats"][3]["fact_ids"] == ["f1"]
        assert script["beats"][4]["fact_ids"] == ["b_purity"]
        assert ctx.manifest.status == "scripted"

        # Every numeric token in every beat must trace to the surviving fact,
        # a brand differentiator/CTA, or the domain -- the never-unverified
        # invariant holding on the *accepted* script, not just on a rejected one.
        brand = load_brand_facts()
        assert script_stage.run_gates(script["beats"], factsheet, brand) == []
