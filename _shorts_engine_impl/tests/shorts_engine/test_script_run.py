"""SCRIPT stage: narrate the verified explanation plan; gates; final-script critic loop."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from shorts_engine import config
from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import script as script_stage

URL = "https://hrsuindore.com/blog/x/"
FACTSHEET = {"facts": [{"id": "f1", "verbatim_quote": "a 20% calcium carbonate and 80% river sand mixture",
                        "value": "20", "unit": "%", "procurement_significance": 5,
                        "citation_marker": None}]}


def _claim(cid, text, kind, quote="", verdict="supported"):
    return {"id": cid, "text": text, "kind": kind, "verdict": verdict, "needs_number": False,
            "support": {"type": "blog_quote" if kind == "blog_stated" else None,
                        "quote": quote, "url": None}}


def _plan():
    def step(sid, claims, ents):
        return {"step_id": sid, "claim_text": "goal", "claims": claims, "terms": [],
                "visual_intent": {"entities": ents, "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why model desert soil?",
        "steps": [
            step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                               "blog_stated", "a 20% calcium carbonate and 80% river sand mixture")],
                 ["calcium carbonate", "river sand", "mixture of the two soils here"]),
            step("s2", [_claim("c2", "Sandy soil drains nutrients quickly.", "external_fact")],
                 ["sand", "water"]),
            step("s3", [_claim("c3", "So nutrients must be held in solution.", "reasoning")],
                 ["nutrient"]),
        ],
        "payoff": {"takeaway": "Pick a soluble powder.", "differentiator_id": "b_purity"},
        "status": "verified", "attempts": 1, "dropped_claims": [], "dropped_steps": [],
        "hold_reasons": [],
    }


def _doc(**over):
    words = " ".join(["word"] * 12)
    doc = {
        "hook": {"narration": f"Why do engineers model desert soil? {words}.",
                 "card_text": "Why model sand?", "broll_wish": "desert"},
        "steps": [
            {"step_id": "s1", "narration": f"Engineers model desert soil with sand. {words}.",
             "card_text": "Sand mixture", "broll_wish": ""},
            {"step_id": "s2", "narration": f"Sandy soil drains nutrients quickly. {words}.",
             "card_text": "Fast drainage", "broll_wish": ""},
            {"step_id": "s3", "narration": f"So nutrients must be held in solution. {words}.",
             "card_text": "Hold in solution", "broll_wish": ""},
        ],
        "cta": {"narration": f"Consistent high-purity calcium nitrate powder helps. Visit hrsuindore.com. {words}.",
                "card_text": "hrsuindore.com", "broll_wish": ""},
    }
    doc.update(over)
    return doc


GOOD_CRITIQUE = {"actionable_score": 8, "coherence_score": 9, "hrsu_reason_score": 8,
                 "faithfulness_score": 9, "revise_notes": ""}
LOW_CRITIQUE = dict(GOOD_CRITIQUE, coherence_score=3, revise_notes="Step 2 does not follow.")


def _ctx(tmp_path: Path, plan=None) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu", "category": "c", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps(FACTSHEET), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan or _plan()), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class Router:
    def __init__(self, docs, critiques):
        self.docs, self.critiques = list(docs), list(critiques)
        self.writer_prompts, self.roles, self.local_only = [], [], []

    def __call__(self, prompt, system, schema, **kw):
        self.roles.append(kw.get("role"))
        self.local_only.append(kw.get("local_only"))
        if schema is script_stage.SCRIPT_SCHEMA:
            self.writer_prompts.append(prompt)
            return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]
        if schema is script_stage.CRITIQUE_SCHEMA:
            return self.critiques.pop(0) if len(self.critiques) > 1 else self.critiques[0]
        raise AssertionError(schema)


def _install(monkeypatch, docs, critiques):
    r = Router(docs, critiques)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", r)
    return r


class TestBuildBeats:
    def test_beat_shape_names_and_purposes(self):
        beats, errs = script_stage.build_beats(_doc(), _plan(), FACTSHEET, load_brand_facts())
        assert errs == []
        assert [b["beat"] for b in beats] == ["hook", "step_1", "step_2", "step_3", "cta"]
        assert [b["purpose"] for b in beats] == ["hook", "mechanism", "mechanism", "mechanism", "cta"]
        assert beats[4]["fact_ids"] == ["b_purity"]
        assert beats[1]["step_id"] == "s1" and beats[1]["fact_ids"] == ["f1"]
        assert beats[1]["diagram_labels"] == ["calcium carbonate", "river sand", "mixture of the"]
        assert beats[1]["visual_intent"]["entities"][0] == "calcium carbonate"

    def test_build_beats_rejects_step_id_mismatch(self):
        doc = _doc()
        doc["steps"][0]["step_id"], doc["steps"][1]["step_id"] = "s2", "s1"
        beats, errs = script_stage.build_beats(doc, _plan(), FACTSHEET, load_brand_facts())
        assert beats == [] and any("steps must be exactly" in e for e in errs)

    def test_extra_pool_contains_supported_claim_text_and_quotes(self):
        pool = script_stage.extra_pool_by_beat(_plan())
        assert "80% river sand" in pool["step_1"]
        assert "Sandy soil drains nutrients quickly." in pool["step_2"]


class TestSchemas:
    def test_script_schema_accepts_well_formed_doc(self):
        jsonschema.validate(_doc(), script_stage.SCRIPT_SCHEMA)

    def test_script_schema_rejects_extra_key(self):
        bad = _doc()
        bad["hook"]["surprise"] = "x"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.SCRIPT_SCHEMA)

    def test_critique_schema_requires_faithfulness_score(self):
        bad = {k: v for k, v in GOOD_CRITIQUE.items() if k != "faithfulness_score"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)


class TestRunHappyPath:
    def test_writes_script_json_with_final_critique(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        assert script_stage.run(ctx) == {"script": "script.json"}
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["critique"] == GOOD_CRITIQUE
        assert out["attempts"] == 1 and out["rewrites"] == 0
        assert out["beats"][0]["beat"] == "hook"
        assert "writer" in router.roles and "critic" in router.roles

    def test_accepted_script_passes_every_gate(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        extra = script_stage.extra_pool_by_beat(_plan())
        assert script_stage.run_gates(out["beats"], FACTSHEET, load_brand_facts(),
                                      extra_by_beat=extra) == []

    def test_not_verified_plan_is_refused(self, tmp_path, monkeypatch):
        from shorts_engine.errors import EngineError
        plan = _plan()
        plan["status"] = "planned"
        ctx = _ctx(tmp_path, plan)
        _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        with pytest.raises(EngineError, match="not verified"):
            script_stage.run(ctx)


class TestWriterPrompt:
    def test_prompt_has_supported_claims_terms_and_no_ceiling(self):
        plan = _plan()
        plan["steps"][0]["terms"] = [{"term": "EC", "definition": "conductivity", "unit": ""}]
        prompt = script_stage._writer_prompt(plan, {"title": "T", "region": "eu", "category": "c"},
                                             load_brand_facts())
        assert "Sandy soil drains nutrients quickly." in prompt
        assert "EC: conductivity" in prompt
        assert "b_purity" in prompt
        assert "no maximum" in prompt.lower()

    def test_prompt_echoes_gate_errors_and_revise_notes(self):
        prompt = script_stage._writer_prompt(
            _plan(), {"title": "T"}, load_brand_facts(),
            gate_errors=["numbers[step_1]: '9' bad"], revise_notes="Fix step 2.")
        assert "numbers[step_1]" in prompt and "Fix step 2." in prompt


class TestGateRetryAndHold:
    def test_gate_errors_are_echoed_into_retry(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [bad, _doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert len(router.writer_prompts) == 2
        assert "'150'" in router.writer_prompts[1] and "'150'" not in router.writer_prompts[0]

    def test_exhausted_writer_retries_hold_and_write_nothing(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [bad], [GOOD_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert len(router.writer_prompts) == config.LLM_MAX_RETRIES
        assert any("150" in r for r in e.value.reasons)
        assert not (ctx.workspace / "script.json").exists()

    def test_short_script_is_not_padded_it_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        tiny = _doc()
        for key in ("hook", "cta"):
            tiny[key]["narration"] = tiny[key]["narration"].split(".")[0] + "."
        for s in tiny["steps"]:
            s["narration"] = s["narration"].split(".")[0] + "."
        _install(monkeypatch, [tiny], [GOOD_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert any("total_duration" in r for r in e.value.reasons)


class TestCriticLoop:
    def test_low_critique_triggers_rewrite_and_final_script_is_rescored(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [LOW_CRITIQUE, GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["rewrites"] == 1
        assert out["critique"] == GOOD_CRITIQUE          # the FINAL script's critique
        assert "Step 2 does not follow." in router.writer_prompts[1]

    def test_threshold_score_of_seven_passes_without_rewrite(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        seven = dict(GOOD_CRITIQUE, actionable_score=7)
        _install(monkeypatch, [_doc()], [seven])
        script_stage.run(ctx)
        assert json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))["rewrites"] == 0

    def test_faithfulness_score_gates_too(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        unfaithful = dict(GOOD_CRITIQUE, faithfulness_score=2, revise_notes="Adds unsupported facts.")
        router = _install(monkeypatch, [_doc()], [unfaithful, GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "Adds unsupported facts." in router.writer_prompts[1]

    def test_still_low_after_max_rewrites_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [LOW_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert "critique below bar" in e.value.reasons[0]
        assert not (ctx.workspace / "script.json").exists()
        assert router.roles.count("critic") == config.SCRIPT_MAX_REWRITES + 1
        assert len(router.writer_prompts) == 1 + config.SCRIPT_MAX_REWRITES

    def test_critic_never_called_when_writer_never_passes_gates(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [bad], [GOOD_CRITIQUE])
        with pytest.raises(HoldForReview):
            script_stage.run(ctx)
        assert "critic" not in router.roles

    def test_rewrite_that_always_fails_gates_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [_doc(), bad], [LOW_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert any("150" in r for r in e.value.reasons)
        assert router.roles.count("critic") == 1
        assert not (ctx.workspace / "script.json").exists()

    def test_rewrite_that_fails_a_gate_once_then_recovers(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        _install(monkeypatch, [_doc(), bad, _doc()], [LOW_CRITIQUE, GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["rewrites"] == 1
        assert out["critique"] == GOOD_CRITIQUE
        assert out["attempts"] == 3

    def test_rewrite_prompt_includes_previous_draft_narration(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        first = _doc()
        first["steps"][1]["narration"] = ("Sandy soil drains nutrients quickly, UNIQUE-DRAFT-MARKER. "
                                          + " ".join(["word"] * 12) + ".")
        router = _install(monkeypatch, [first, _doc()], [LOW_CRITIQUE, GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "UNIQUE-DRAFT-MARKER" not in router.writer_prompts[0]
        assert "UNIQUE-DRAFT-MARKER" in router.writer_prompts[1]
        assert "step_2" in router.writer_prompts[1]


class TestLocalOnlyFlag:
    def test_local_only_forwarded_to_every_llm_call(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        ctx.flags["local_only"] = True
        router = _install(monkeypatch, [_doc()], [LOW_CRITIQUE, GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert len(router.local_only) >= 3
        assert all(v is True for v in router.local_only)

    def test_local_only_defaults_to_false(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert router.local_only and all(v is False for v in router.local_only)


class TestCritiqueSchemaBounds:
    @pytest.mark.parametrize("bad", [
        dict(GOOD_CRITIQUE, coherence_score=11),
        dict(GOOD_CRITIQUE, actionable_score=-1),
        dict(GOOD_CRITIQUE, surprise="x"),
    ])
    def test_rejects_out_of_range_and_extra_keys(self, bad):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)


class TestBeatDetails:
    def test_short_blog_quote_attaches_no_fact(self):
        plan = _plan()
        fs = {"facts": FACTSHEET["facts"] + [
            {"id": "f2", "verbatim_quote": "river sand", "value": "", "unit": "",
             "procurement_significance": 1, "citation_marker": None}]}
        plan["steps"][0]["claims"][0]["support"]["quote"] = "river sand"
        beats, errs = script_stage.build_beats(_doc(), plan, fs, load_brand_facts())
        assert errs == [] and beats[1]["fact_ids"] == []

    def test_diagram_labels_capped_at_four(self):
        plan = _plan()
        plan["steps"][1]["visual_intent"]["entities"] = ["a", "b", "c", "d", "e"]
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        assert beats[2]["diagram_labels"] == ["a", "b", "c", "d"]

    def test_writer_prompt_forbids_numbers_in_hook_and_cta(self):
        prompt = script_stage._writer_prompt(_plan(), {"title": "T"}, load_brand_facts())
        assert "The hook and the CTA must not contain any numbers." in prompt
