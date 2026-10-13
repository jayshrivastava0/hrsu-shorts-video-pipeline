# tests/shorts_engine/test_script_strategic.py
"""Strategic / executive track of the SCRIPT stage (offline, fakes only)."""
from __future__ import annotations

import json

from shorts_engine.brand import load_brand_facts
from shorts_engine.stages import script as script_stage

from tests.shorts_engine.test_script_run import (
    FACTSHEET, GOOD_CRITIQUE, _ctx, _doc, _install, _plan)

WORDS = " ".join(["word"] * 12)


def _strategic_plan():
    plan = _plan()
    plan["post_kind"] = "strategic"
    plan["steps"][0]["claims"][0]["stance"] = "checkable"
    plan["steps"][1]["claims"][0]["stance"] = "analysis"
    plan["steps"][2]["claims"][0]["stance"] = "framework"
    plan["payoff"]["differentiator_id"] = "b_supply"
    return plan


def _attributed_doc():
    d = _doc()
    d["steps"][1]["narration"] = (
        f"The argument HRSU makes is that sandy soil drains nutrients quickly. {WORDS}.")
    d["cta"]["narration"] = (
        f"HRSU offers flexible minimum order quantities and responsive quoting for trial "
        f"orders. Visit hrsuindore.com. {WORDS}.")
    return d


class TestAttributionGate:
    def test_analysis_narrated_as_fact_is_rejected_for_strategic_plans(self):
        plan = _strategic_plan()
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        errs = script_stage.gate_attribution(beats, plan)
        assert len(errs) == 1 and "attribution[step_2]" in errs[0]
        assert "HRSU" in errs[0]

    def test_attributed_analysis_passes(self):
        plan = _strategic_plan()
        beats, _ = script_stage.build_beats(_attributed_doc(), plan, FACTSHEET, load_brand_facts())
        assert script_stage.gate_attribution(beats, plan) == []

    def test_attribution_phrases(self):
        plan = _strategic_plan()
        for phrase in ("HRSU's analysis points to this:", "According to the article,",
                       "The article argues that", "In HRSU's view,"):
            d = _doc()
            d["steps"][1]["narration"] = f"{phrase} sandy soil drains nutrients quickly. {WORDS}."
            beats, _ = script_stage.build_beats(d, plan, FACTSHEET, load_brand_facts())
            assert script_stage.gate_attribution(beats, plan) == [], phrase

    def test_a_figure_labelled_analysis_does_not_need_attribution(self):
        plan = _strategic_plan()            # c1 is checkable (carries 20%): plain statement ok
        beats, _ = script_stage.build_beats(_attributed_doc(), plan, FACTSHEET, load_brand_facts())
        assert not [e for e in script_stage.gate_attribution(beats, plan) if "step_1" in e]

    def test_technical_plan_is_not_checked(self):
        plan = _plan()
        beats, _ = script_stage.build_beats(_doc(), plan, FACTSHEET, load_brand_facts())
        errs = script_stage.run_gates(beats, FACTSHEET, load_brand_facts(), plan=plan)
        assert not [e for e in errs if e.startswith("attribution")]


class TestStrategicPrompts:
    def test_writer_uses_strategic_system_with_tags_and_structure(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, _strategic_plan())
        systems = []

        def fake(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                systems.append(system)
                fake.prompt = prompt
                return _attributed_doc()
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake)
        script_stage.run(ctx)
        sys_ = systems[0]
        assert sys_ is script_stage._WRITER_SYSTEM_STRATEGIC
        low = sys_.lower()
        assert "situation" in low and "tension" in low and "framework" in low
        assert "sourcing decision" in low and "attribut" in low and "no jargon" not in low
        assert "[ANALYSIS -- attribute to HRSU's argument]" in fake.prompt
        assert "[CHECKABLE FACT]" in fake.prompt and "[FRAMEWORK]" in fake.prompt

    def test_technical_plan_keeps_the_original_writer_prompt(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "CHECKABLE FACT" not in router.writer_prompts[0]
        assert "ANALYSIS" not in router.writer_prompts[0]

    def test_critic_gets_tags_and_the_strategic_rubric(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, _strategic_plan())
        router = _install(monkeypatch, [_attributed_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "(ANALYSIS -- attribute to HRSU's argument)" in router.critic_prompts[0]
        assert "(CHECKABLE FACT)" in router.critic_prompts[0]
        assert "ANALYSIS claim narrated" in router.critic_systems[0]

    def test_unattributed_draft_is_sent_back_by_the_gate_before_the_critic(
            self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, _strategic_plan())
        router = _install(monkeypatch, [_doc(), _attributed_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "attribution[step_2]" in router.writer_prompts[1]
        assert router.roles == ["writer", "writer", "critic"]
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["attempts"] == 2

    def test_writer_is_told_the_banned_words_up_front(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "catastrophic" in router.writer_prompts[0]
