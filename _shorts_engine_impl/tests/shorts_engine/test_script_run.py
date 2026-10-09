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


def _claim(cid, text, kind, quote="", verdict="supported", role="mechanism"):
    return {"id": cid, "text": text, "kind": kind, "role": role, "verdict": verdict,
            "needs_number": False,
            "support": {"type": "blog_quote" if kind == "blog_stated" else None,
                        "quote": quote, "url": None}}


def _plan():
    def step(sid, claims, ents):
        return {"step_id": sid, "claim_text": "goal", "claims": claims, "terms": [],
                "visual_intent": {"entities": ents, "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why model desert soil?",
        "steps": [
            step("s1", [_claim("c1", "Engineers model desert soil with a 20% calcium carbonate sand mixture.",
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
                 "faithfulness_score": 9, "flow_score": 8, "revise_notes": ""}
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
        self.critic_prompts, self.critic_systems = [], []

    def __call__(self, prompt, system, schema, **kw):
        self.roles.append(kw.get("role"))
        self.local_only.append(kw.get("local_only"))
        if schema is script_stage.SCRIPT_SCHEMA:
            self.writer_prompts.append(prompt)
            return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]
        if schema is script_stage.CRITIQUE_SCHEMA:
            self.critic_prompts.append(prompt)
            self.critic_systems.append(system)
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

    def test_extra_pool_contains_supported_claim_text_only(self):
        # claim texts are pair-gated against their sources in verify_claims; the full
        # support quote is wider than the claim and must not widen the number pool
        pool = script_stage.extra_pool_by_beat(_plan())
        assert "20% calcium carbonate sand mixture" in pool["step_1"]
        assert "80% river sand" not in pool["step_1"]
        assert "Sandy soil drains nutrients quickly." in pool["step_2"]

    def test_passage_wider_than_claim_does_not_license_its_numbers(self):
        plan = _plan()
        plan["steps"][1]["claims"] = [_claim(
            "c2", "Calcium nitrate dissolves readily.", "external_fact")]
        plan["steps"][1]["claims"][0]["support"] = {
            "type": "external", "url": "https://x.gov/a",
            "quote": "Ammonium nitrate dissolves at 1500 g/L; calcium nitrate dissolves readily."}
        doc = _doc()
        doc["steps"][1]["narration"] = ("Calcium nitrate dissolves at 1500 g/L. "
                                        + " ".join(["word"] * 12) + ".")
        brand = load_brand_facts()
        beats, errs = script_stage.build_beats(doc, plan, FACTSHEET, brand)
        assert errs == []
        gate = script_stage.run_gates(beats, FACTSHEET, brand,
                                      extra_by_beat=script_stage.extra_pool_by_beat(plan))
        assert any("1500" in e and "step_2" in e for e in gate)


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

    def test_critique_schema_requires_flow_score(self):
        jsonschema.validate(GOOD_CRITIQUE, script_stage.CRITIQUE_SCHEMA)
        bad = {k: v for k, v in GOOD_CRITIQUE.items() if k != "flow_score"}
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
    def test_prompt_omits_unverified_planner_text(self):
        plan = _plan()
        plan["steps"][0]["claim_text"] = "UNIQUE-GOAL-MARKER 9 tonnes"
        plan["payoff"]["takeaway"] = "UNIQUE-TAKEAWAY-MARKER saves a fortune"
        plan["steps"][1]["terms"] = [{"term": "EC", "definition": "conductivity", "unit": ""}]
        brand = load_brand_facts()
        prompt = script_stage._writer_prompt(plan, {"title": "T"}, brand)
        assert "UNIQUE-GOAL-MARKER" not in prompt
        assert "UNIQUE-TAKEAWAY-MARKER" not in prompt
        for s in plan["steps"]:
            for c in s["claims"]:
                assert c["text"] in prompt
        assert "EC: conductivity" in prompt
        diff = next(d for d in brand.differentiators if d["id"] == "b_purity")
        assert diff["text"] in prompt
        assert plan["question"] in prompt
        assert "STEP s1" in prompt

    def test_prompt_has_supported_claims_terms_and_no_ceiling(self):
        plan = _plan()
        plan["steps"][0]["terms"] = [{"term": "EC", "definition": "conductivity", "unit": ""}]
        prompt = script_stage._writer_prompt(plan, {"title": "T", "region": "eu", "category": "c"},
                                             load_brand_facts())
        assert "Sandy soil drains nutrients quickly." in prompt
        assert "EC: conductivity" in prompt
        assert "b_purity" in prompt
        assert "no maximum" in prompt.lower()

    LINK_LABEL = "HOW THIS FOLLOWS FROM THE PREVIOUS STEP (use as the opening transition):"
    MECH_LABEL = ("WHY / HOW (the mechanism - explain THIS claim as cause and effect in plain "
                  "words; add no causes, steps, conditions or facts that are not listed):")
    FACT_LABEL = "SUPPORTING FACTS:"

    @staticmethod
    def _role_plan():
        plan = _plan()
        plan["steps"][0]["claims"].append(_claim(
            "f1x", "Desert soil is mostly loose sand.", "external_fact", role="fact"))
        plan["steps"][1]["claims"].append(_claim(
            "l2", "Because the model soil is mostly sand, water passes through it fast.",
            "reasoning", role="link"))
        plan["steps"][1]["claims"].append(_claim(
            "f2x", "Sand grains are large compared with clay particles.", "external_fact",
            role="fact"))
        plan["steps"][1]["claims"].append(_claim(
            "u2", "UNSUPPORTED-MECH-MARKER drains everything.", "external_fact",
            verdict="unsupported"))
        plan["steps"][2]["claims"].append(_claim(
            "l3", "Since nutrients drain away, they must be kept dissolved.", "reasoning",
            role="link"))
        return plan

    @staticmethod
    def _step_section(prompt, step_id):
        body = prompt.split(f"STEP {step_id}\n", 1)[1]
        for end in ("\nSTEP ", "\nCTA:"):
            body = body.split(end, 1)[0]
        return body

    @staticmethod
    def _group(section, label):
        labels = ("HOW THIS FOLLOWS FROM", "WHY / HOW", "SUPPORTING FACTS:", "Terms to define:")
        body = section.split(label, 1)[1]
        cut = min((body.find(lb) for lb in labels if lb in body), default=len(body))
        return body[:cut]

    def test_prompt_groups_verified_claims_by_role(self):
        plan = self._role_plan()
        plan["steps"][1]["terms"] = [{"term": "EC", "definition": "conductivity", "unit": ""}]
        prompt = script_stage._writer_prompt(plan, {"title": "T"}, load_brand_facts())
        s2 = self._step_section(prompt, "s2")
        link = self._group(s2, self.LINK_LABEL)
        mech = self._group(s2, self.MECH_LABEL)
        facts = self._group(s2, self.FACT_LABEL)
        assert "water passes through it fast" in link
        assert "Sandy soil drains nutrients quickly." in mech
        assert "Sandy soil" not in link and "water passes" not in mech
        assert "Sand grains are large" in facts and "Sand grains" not in mech
        assert "Terms to define:" in s2 and "EC: conductivity" in s2
        assert "UNSUPPORTED-MECH-MARKER" not in prompt
        s3 = self._step_section(prompt, "s3")
        assert "kept dissolved" in self._group(s3, self.LINK_LABEL)
        assert "held in solution" in self._group(s3, self.MECH_LABEL)

    def test_first_step_transition_follows_from_the_question(self):
        prompt = script_stage._writer_prompt(self._role_plan(), {"title": "T"},
                                             load_brand_facts())
        s1 = self._step_section(prompt, "s1")
        transition = s1.split("\n", 1)[0] if s1.startswith("HOW THIS") else \
            next(line for line in s1.splitlines() if line.startswith("HOW THIS FOLLOWS"))
        assert "QUESTION" in transition.upper()
        assert self.LINK_LABEL not in s1
        assert "20% calcium carbonate" in self._group(s1, self.MECH_LABEL)
        assert "mostly loose sand" in self._group(s1, self.FACT_LABEL)

    def test_first_step_placeholder_uses_only_verified_claims(self):
        prompt = script_stage._writer_prompt(self._role_plan(), {"title": "T"},
                                             load_brand_facts())
        s1 = self._step_section(prompt, "s1")
        assert ("(connect this step directly to the QUESTION above using only verified "
                "claims)") in s1

    def test_mechanism_label_forbids_adding_causes_or_steps(self):
        assert "explain it fully, step by step" not in script_stage._MECH_LABEL
        assert "explain THIS claim as cause and effect" in script_stage._MECH_LABEL

    def test_role_prompt_still_omits_goals_and_takeaway(self):
        plan = self._role_plan()
        plan["steps"][1]["claim_text"] = "UNIQUE-GOAL-MARKER"
        plan["payoff"]["takeaway"] = "UNIQUE-TAKEAWAY-MARKER"
        prompt = script_stage._writer_prompt(plan, {"title": "T"}, load_brand_facts())
        assert "UNIQUE-GOAL-MARKER" not in prompt and "UNIQUE-TAKEAWAY-MARKER" not in prompt

    def test_writer_system_narrates_link_mechanism_facts_in_order(self):
        system = script_stage._WRITER_SYSTEM.lower()
        assert system.index("transition") < system.index("mechanism") < \
            system.index("supporting facts")
        assert "cause and effect" in system
        assert "never assume the viewer already knows" in system
        assert "answers the question" in system
        assert "no maximum" in system and "filler" in system

    def test_extra_pool_includes_claims_of_every_role(self):
        pool = script_stage.extra_pool_by_beat(self._role_plan())
        assert "water passes through it fast" in pool["step_2"]      # link
        assert "Sandy soil drains nutrients quickly." in pool["step_2"]  # mechanism
        assert "Sand grains are large" in pool["step_2"]              # fact
        assert "UNSUPPORTED-MECH-MARKER" not in pool["step_2"]

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

    def test_skipped_mechanism_flow_error_is_echoed_into_retry(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] = "Water moves on. " + " ".join(["word"] * 12) + "."
        router = _install(monkeypatch, [bad, _doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert len(router.writer_prompts) == 2
        assert "flow[step_2]: mechanism not narrated" in router.writer_prompts[1]

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
        plan = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert plan["hold_reasons"] == e.value.reasons
        assert plan["status"] == "verified"          # SCRIPT can be re-run on this plan

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

    def test_flow_score_alone_below_bar_triggers_rewrite(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        jumpy = dict(GOOD_CRITIQUE, flow_score=4,
                     revise_notes="Step 2 asserts drainage without explaining why.")
        router = _install(monkeypatch, [_doc()], [jumpy, GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["rewrites"] == 1
        assert "Step 2 asserts drainage without explaining why." in router.writer_prompts[1]

    def test_max_rewrites_is_three(self):
        assert config.SCRIPT_MAX_REWRITES == 3

    def test_still_low_after_max_rewrites_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [LOW_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert "critique below bar" in e.value.reasons[0]
        assert not (ctx.workspace / "script.json").exists()
        plan = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert plan["hold_reasons"] == e.value.reasons and plan["status"] == "verified"
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
        dict(GOOD_CRITIQUE, flow_score=11),
        dict(GOOD_CRITIQUE, flow_score=-1),
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

    def test_number_free_claim_does_not_attach_fact_by_quote_overlap(self):
        ec_quote = "electrical conductivity (EC) peaks at 425 during infiltration"
        fs = {"facts": FACTSHEET["facts"] + [
            {"id": "f_ec", "verbatim_quote": ec_quote, "value": "425", "unit": "EC",
             "procurement_significance": 9, "citation_marker": None}]}
        plan = _plan()
        plan["steps"][1]["claims"] = [_claim(
            "c2", "Soil conductivity rises during infiltration.", "blog_stated", ec_quote)]
        beats, errs = script_stage.build_beats(_doc(), plan, fs, load_brand_facts())
        assert errs == [] and beats[2]["fact_ids"] == []

    def test_claim_with_unitless_number_does_not_attach_fact(self):
        ec_quote = "electrical conductivity (EC) peaks at 425 during infiltration"
        fs = {"facts": [{"id": "f_ec", "verbatim_quote": ec_quote, "value": "425",
                         "unit": "EC", "procurement_significance": 9,
                         "citation_marker": None}]}
        plan = _plan()
        plan["steps"][1]["claims"] = [_claim(
            "c2", "Conductivity peaks at 425 during infiltration.", "blog_stated", ec_quote)]
        beats, _ = script_stage.build_beats(_doc(), plan, fs, load_brand_facts())
        assert beats[2]["fact_ids"] == []      # "EC" is neither a known unit nor a term

    def test_range_fact_attaches_when_claim_states_value_and_unit(self):
        quote = "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume"
        fs = {"facts": [{"id": "f_dose", "verbatim_quote": quote, "value": "1.5-3",
                         "unit": "kg", "procurement_significance": 9,
                         "citation_marker": None}]}
        plan = _plan()
        plan["steps"][1]["claims"] = [_claim(
            "c2", "Plants dose 1.5 to 3 kg per cubic meter.", "blog_stated", quote)]
        beats, _ = script_stage.build_beats(_doc(), plan, fs, load_brand_facts())
        assert beats[2]["fact_ids"] == ["f_dose"]
        fs["facts"][0]["unit"] = "kg/m3"     # unit the claim does not state with 1.5-3
        beats, _ = script_stage.build_beats(_doc(), plan, fs, load_brand_facts())
        assert beats[2]["fact_ids"] == []

    def test_unsupported_claim_never_attaches_fact(self):
        plan = _plan()
        plan["steps"][0]["claims"][0]["verdict"] = "unsupported"
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        assert beats[1]["fact_ids"] == []

    def test_diagram_labels_capped_at_four(self):
        plan = _plan()
        plan["steps"][1]["visual_intent"]["entities"] = ["a", "b", "c", "d", "e"]
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        assert beats[2]["diagram_labels"] == ["a", "b", "c", "d"]

    def test_writer_prompt_forbids_numbers_in_hook_and_cta(self):
        prompt = script_stage._writer_prompt(_plan(), {"title": "T"}, load_brand_facts())
        assert "The hook and the CTA must not contain any numbers." in prompt

    def _dose_fact(self, value, unit):
        quote = "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume"
        fs = {"facts": [{"id": "f_dose", "verbatim_quote": quote, "value": value,
                         "unit": unit, "procurement_significance": 9,
                         "citation_marker": None}]}
        plan = _plan()
        plan["steps"][1]["claims"] = [_claim(
            "c2", "Plants dose 1.5 to 3 kg per cubic meter.", "blog_stated", quote)]
        return script_stage.build_beats(_doc(), plan, fs, load_brand_facts())[0]

    def test_multiword_wrong_unit_does_not_attach_fact(self):
        assert self._dose_fact("1.5-3", "kg per tonne")[2]["fact_ids"] == []

    def test_value_with_letters_does_not_attach_fact(self):
        assert self._dose_fact("3 kg", "m3")[2]["fact_ids"] == []

    def test_numeric_fact_with_empty_unit_does_not_attach(self):
        assert self._dose_fact("1.5-3", "")[2]["fact_ids"] == []

    def test_full_unit_in_claim_still_attaches(self):
        assert self._dose_fact("1.5-3", "kg")[2]["fact_ids"] == ["f_dose"]

    def test_percent_forms_still_attach(self):
        for claim in ("Engineers use a 20% calcium carbonate sand mixture.",
                      "Engineers use a 20 percent calcium carbonate sand mixture."):
            plan = _plan()
            plan["steps"][0]["claims"][0]["text"] = claim
            beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
            assert beats[1]["fact_ids"] == ["f1"], claim


class TestStaleHoldReasons:
    def test_successful_run_clears_stale_hold_reasons(self, tmp_path, monkeypatch):
        plan = _plan()
        plan["hold_reasons"] = ["old"]
        ctx = _ctx(tmp_path, plan)
        _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        saved = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert saved["hold_reasons"] == [] and saved["status"] == "verified"


class TestCtaBrandStatements:
    def _critic_prompt_text(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        return router, router.critic_prompts[0]

    def test_critic_prompt_lists_approved_brand_statements(self, tmp_path, monkeypatch):
        brand = load_brand_facts()
        diff = next(d for d in brand.differentiators if d["id"] == "b_purity")
        _, prompt = self._critic_prompt_text(tmp_path, monkeypatch)
        assert "APPROVED BRAND STATEMENTS" in prompt
        section = prompt.split("APPROVED BRAND STATEMENTS", 1)[1]
        assert diff["text"] in section
        assert brand.cta_lines[0] in section
        assert brand.domain in section

    def test_critic_prompt_still_has_verified_claims(self, tmp_path, monkeypatch):
        _, prompt = self._critic_prompt_text(tmp_path, monkeypatch)
        for step in _plan()["steps"]:
            for c in step["claims"]:
                if c.get("verdict") == "supported":
                    assert c["text"] in prompt

    def test_critic_prompt_shows_question_and_labelled_chain(self):
        plan = _plan()
        for s in plan["steps"]:
            for c in s["claims"]:
                c["role"] = "fact"
        plan["steps"][0]["claims"].append(_claim(
            "m1", "Carbonate grains hold little water, so the mixture dries fast.",
            "external_fact", role="mechanism"))
        plan["steps"][1]["claims"].append(_claim(
            "l2", "Because the mixture dries fast, nutrients wash through it.", "reasoning",
            role="link"))
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        prompt = script_stage._critic_prompt(plan, beats, load_brand_facts())
        assert plan["question"] in prompt
        assert "LINK (how it follows from the previous step): Because the mixture dries " \
               "fast, nutrients wash through it." in prompt
        assert "MECHANISM (why/how): Carbonate grains hold little water" in prompt
        assert "LINK (how it follows from the previous step): Carbonate" not in prompt
        assert "APPROVED BRAND STATEMENTS" in prompt
        for s in plan["steps"]:
            for c in s["claims"]:
                assert c["text"] in prompt

    def test_critic_system_explains_flow_score(self):
        system = script_stage._CRITIC_SYSTEM.lower()
        assert "flow_score" in system
        assert "defined" in system and "answer the question" in system
        assert "below 5" in system and "why/how" in system

    def test_critic_system_exempts_approved_brand_statements(self, tmp_path, monkeypatch):
        router, _ = self._critic_prompt_text(tmp_path, monkeypatch)
        system = router.critic_systems[0].lower()
        assert "approved brand statements" in system
        assert "do not count against faithfulness" in system

    def test_writer_limits_company_claims_to_approved_text(self):
        prompt = script_stage._writer_prompt(_plan(), {"title": "T"}, load_brand_facts())
        text = (script_stage._WRITER_SYSTEM + prompt).lower()
        assert "only the cited differentiator text, the brand cta lines and the domain" in text
        assert "purity, qc, certifications, delivery or pricing" in text
        assert "The hook and the CTA must not contain any numbers." in prompt
