"""Tests for the SCRIPT stage's writer/critique/run() orchestration:
write -> gate (retry with the gate errors echoed back into the prompt, up to
config.LLM_MAX_RETRIES attempts) -> critique once -> one conditional rewrite
if any critique score is below the pass bar -> save script.json. A final
gate failure (either exhausted writer retries or a failed rewrite) raises
GateFailure -- the engine never ships an unverified or off-template script
(spec §4 Stage 3)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from shorts_engine import config
from shorts_engine.errors import GateFailure
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import script as script_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

# Narration lengths sized for WORDS_PER_SECOND=1.7: per-beat integer bounds
# hook [3,8], stakes [6,12], mechanism [11,24], proof [9,20], cta [9,16];
# total 62 words, inside the aggregate [60, 85] window.
GOOD_BEATS = [
    {"beat": "hook", "narration": "EU nitrate discharge limits are tightening fast.",
     "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": "wastewater aeration basin"},
    {"beat": "stakes", "narration": "Non-compliance risks steep penalties and unplanned production downtime this quarter.",
     "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Dosing calcium nitrate feeds denitrifying bacteria, converting nitrate into harmless nitrogen gas within the treatment train without any retrofit.",
     "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
    {"beat": "proof", "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic meter.",
     "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies high-purity powder with batch QC. Visit hrsuindore.com for the guide.",
     "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
]
BAD_BEATS = json.loads(json.dumps(GOOD_BEATS))
BAD_BEATS[3]["narration"] = "Reduces nitrate levels by 150 mg per liter of wastewater flow."

GOOD_CRITIQUE = {"actionable_score": 8, "coherence_score": 9,
                 "hrsu_reason_score": 8, "revise_notes": ""}


def _ctx(tmp_path: Path) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu",
         "category": "wastewater_treatment", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({
        "facts": [{"id": "f1",
                   "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume",
                   "char_offset": 0, "value": "1.5-3", "unit": "kg/m3",
                   "claim_summary": "dosing", "tags": ["spec"],
                   "procurement_significance": 5, "citation_marker": 1}],
        "brand_facts": [], "dropped": []}), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestHappyPath:
    """A clean first draft that also critiques well needs exactly one
    writer call and one critique call, and writes script.json."""

    def test_writes_script_json_and_returns_artifact_dict(self, tmp_path, monkeypatch) -> None:
        calls = []

        def fake_llm(prompt, system, schema, **kw):
            calls.append(schema)
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        out = script_stage.run(ctx)
        assert out == {"script": "script.json"}

        written = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert written["attempts"] == 1
        assert written["critique"]["coherence_score"] == 9
        assert len(written["beats"]) == 5
        assert len(calls) == 2  # one write + one critique

    def test_script_json_beats_match_writer_output(self, tmp_path, monkeypatch) -> None:
        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        script_stage.run(ctx)
        written = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert written["beats"][3]["fact_ids"] == ["f1"]
        assert written["beats"][4]["fact_ids"] == ["b_purity"]

    def test_local_only_flag_is_forwarded_to_every_llm_call(self, tmp_path, monkeypatch) -> None:
        captured = []

        def fake_llm(prompt, system, schema, **kw):
            captured.append(kw.get("local_only"))
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        ctx.flags["local_only"] = True
        script_stage.run(ctx)
        assert captured and all(v is True for v in captured)

    def test_defaults_local_only_false_when_flag_absent(self, tmp_path, monkeypatch) -> None:
        captured = []

        def fake_llm(prompt, system, schema, **kw):
            captured.append(kw.get("local_only"))
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert captured and all(v is False for v in captured)


class TestGateRetry:
    """A gate-failing first draft is retried with the gate errors echoed
    into the writer prompt; a clean second draft still needs one critique
    call to finish."""

    def test_gate_errors_are_echoed_into_the_retry_prompt(self, tmp_path, monkeypatch) -> None:
        prompts = []
        responses = [{"beats": BAD_BEATS}, {"beats": GOOD_BEATS}, GOOD_CRITIQUE]

        def fake_llm(prompt, system, schema, **kw):
            prompts.append(prompt)
            return responses.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert "150" in prompts[1] and "does not trace" in prompts[1]

    def test_second_attempt_succeeds_after_one_gate_retry(self, tmp_path, monkeypatch) -> None:
        responses = [{"beats": BAD_BEATS}, {"beats": GOOD_BEATS}, GOOD_CRITIQUE]

        def fake_llm(prompt, system, schema, **kw):
            return responses.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        out = script_stage.run(ctx)
        assert out == {"script": "script.json"}
        written = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert written["attempts"] == 2

    def test_first_attempt_prompt_has_no_gate_errors_echoed(self, tmp_path, monkeypatch) -> None:
        prompts = []
        responses = [{"beats": GOOD_BEATS}, GOOD_CRITIQUE]

        def fake_llm(prompt, system, schema, **kw):
            prompts.append(prompt)
            return responses.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert "FAILED these checks" not in prompts[0]


class TestExhaustedRetries:
    """Three consecutive gate-failing drafts raise GateFailure with the
    final gate errors attached, without ever reaching the critique step."""

    def test_raises_gate_failure(self, tmp_path, monkeypatch) -> None:
        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": BAD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        with pytest.raises(GateFailure):
            script_stage.run(_ctx(tmp_path))

    def test_gate_failure_carries_the_final_gate_errors(self, tmp_path, monkeypatch) -> None:
        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": BAD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        with pytest.raises(GateFailure) as ei:
            script_stage.run(_ctx(tmp_path))
        assert any("150" in e for e in ei.value.errors)

    def test_writer_is_called_exactly_max_retries_times(self, tmp_path, monkeypatch) -> None:
        calls = {"writer": 0}

        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                calls["writer"] += 1
                return {"beats": BAD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        with pytest.raises(GateFailure):
            script_stage.run(_ctx(tmp_path))
        assert calls["writer"] == config.LLM_MAX_RETRIES

    def test_critique_is_never_called_when_writer_never_passes_gates(self, tmp_path, monkeypatch) -> None:
        calls = {"critique": 0}

        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": BAD_BEATS}
            calls["critique"] += 1
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        with pytest.raises(GateFailure):
            script_stage.run(_ctx(tmp_path))
        assert calls["critique"] == 0

    def test_script_json_is_not_written_on_failure(self, tmp_path, monkeypatch) -> None:
        def fake_llm(prompt, system, schema, **kw):
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": BAD_BEATS}
            return GOOD_CRITIQUE

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        with pytest.raises(GateFailure):
            script_stage.run(ctx)
        assert not (ctx.workspace / "script.json").exists()


class TestLowCritiqueRewrite:
    """A below-bar critique score triggers exactly one rewrite, with the
    reviewer's revise_notes folded into the rewrite prompt; the rewrite is
    re-gated before being accepted."""

    def test_one_rewrite_when_any_score_is_below_seven(self, tmp_path, monkeypatch) -> None:
        seq = [{"beats": GOOD_BEATS},
               {"actionable_score": 4, "coherence_score": 9, "hrsu_reason_score": 8,
                "revise_notes": "hook is vague"},
               {"beats": GOOD_BEATS}]
        prompts = []

        def fake_llm(prompt, system, schema, **kw):
            prompts.append(prompt)
            return seq.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert seq == []                      # all three calls consumed
        assert "hook is vague" in prompts[2]  # rewrite saw the notes

    def test_rewrite_result_is_what_gets_saved(self, tmp_path, monkeypatch) -> None:
        rewritten = json.loads(json.dumps(GOOD_BEATS))
        rewritten[0]["card_text"] = "Rewritten hook card"
        seq = [{"beats": GOOD_BEATS},
               {"actionable_score": 4, "coherence_score": 9, "hrsu_reason_score": 8,
                "revise_notes": "hook is vague"},
               {"beats": rewritten}]

        def fake_llm(prompt, system, schema, **kw):
            return seq.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        script_stage.run(ctx)
        written = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert written["beats"][0]["card_text"] == "Rewritten hook card"

    def test_exactly_at_threshold_score_of_seven_does_not_trigger_rewrite(self, tmp_path, monkeypatch) -> None:
        calls = []
        borderline_critique = {"actionable_score": 7, "coherence_score": 7,
                                "hrsu_reason_score": 7, "revise_notes": ""}

        def fake_llm(prompt, system, schema, **kw):
            calls.append(schema)
            if schema is script_stage.SCRIPT_SCHEMA:
                return {"beats": GOOD_BEATS}
            return borderline_critique

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert len(calls) == 2  # no rewrite call

    def test_rewrite_that_fails_gates_raises_gate_failure(self, tmp_path, monkeypatch) -> None:
        # The rewrite is itself gate-retried up to LLM_MAX_RETRIES times
        # (mirrors the initial writer loop) -- BAD_BEATS fails every one of
        # those attempts, so the sequence needs one BAD_BEATS per attempt.
        seq = ([{"beats": GOOD_BEATS},
                {"actionable_score": 4, "coherence_score": 9, "hrsu_reason_score": 8,
                 "revise_notes": "hook is vague"}]
               + [{"beats": BAD_BEATS}] * config.LLM_MAX_RETRIES)

        def fake_llm(prompt, system, schema, **kw):
            return seq.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        with pytest.raises(GateFailure):
            script_stage.run(_ctx(tmp_path))
        assert seq == []  # every rewrite attempt was actually used

    def test_rewrite_recovers_after_a_gate_retry(self, tmp_path, monkeypatch) -> None:
        """Regression: a live run showed the critique-triggered rewrite
        introducing a total_duration violation the original draft didn't
        have, and (before this fix) that failure was unrecoverable --
        single-shot, no retry. The rewrite must now get the same
        gate-retry-with-echo treatment as the initial write: a first
        rewrite attempt that fails gates should retry (with the gate
        errors, not revise_notes, echoed in) and can still succeed."""
        seq = [{"beats": GOOD_BEATS},
               {"actionable_score": 4, "coherence_score": 9, "hrsu_reason_score": 8,
                "revise_notes": "hook is vague"},
               {"beats": BAD_BEATS},   # rewrite attempt 1: fails (untraceable "150")
               {"beats": GOOD_BEATS}]  # rewrite attempt 2: passes
        prompts = []

        def fake_llm(prompt, system, schema, **kw):
            prompts.append(prompt)
            return seq.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        ctx = _ctx(tmp_path)
        out = script_stage.run(ctx)
        assert out == {"script": "script.json"}
        assert seq == []
        # rewrite attempt 2's prompt echoes the gate failure, not revise_notes
        assert "150" in prompts[3] and "does not trace" in prompts[3]

    def test_only_one_rewrite_attempted_even_if_it_would_critique_low_again(self, tmp_path, monkeypatch) -> None:
        # run() critiques only once; a rewrite is gated but never re-critiqued,
        # so exactly 3 llm calls happen (write, critique, rewrite) -- never 4+.
        seq = [{"beats": GOOD_BEATS},
               {"actionable_score": 2, "coherence_score": 9, "hrsu_reason_score": 8,
                "revise_notes": "still vague"},
               {"beats": GOOD_BEATS}]
        calls = {"n": 0}

        def fake_llm(prompt, system, schema, **kw):
            calls["n"] += 1
            return seq.pop(0)

        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
        script_stage.run(_ctx(tmp_path))
        assert calls["n"] == 3


class TestWriterPromptAndBeatRules:
    """Direct unit coverage of the prompt-building helpers."""

    def _factsheet(self):
        return {"facts": [{"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                           "value": "1.5-3", "unit": "kg/m3", "citation_marker": 1}]}

    def _brand(self):
        from shorts_engine.brand import BrandFacts
        return BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                          differentiators=[{"id": "b_purity", "text": "High purity"}],
                          cta_lines=["Visit hrsuindore.com"], banned_claims=[])

    def test_beat_rules_lists_all_five_beats_in_order(self) -> None:
        rules = script_stage._beat_rules()
        lines = rules.splitlines()
        assert len(lines) == 5
        assert lines[0].startswith("- hook:")
        assert lines[4].startswith("- cta:")

    def test_beat_rules_show_the_true_tolerance_widened_word_ranges(self) -> None:
        """Regression: the prompt showed each beat's no-tolerance nominal
        word range, understating the real ceiling gate_word_budget accepts
        -- once the aggregate total-duration floor forced beats toward
        their upper bounds, the model needed the true room. hook's real
        integer-feasible range is 3-8 (2-4s x 1.7 w/s +/-20%, ceil/floor),
        stakes' is 6-12."""
        rules = script_stage._beat_rules()
        lines = rules.splitlines()
        assert "3-8 words allowed" in lines[0]    # hook
        assert "6-12 words allowed" in lines[1]   # stakes

    def test_writer_prompt_includes_post_meta_facts_and_differentiators(self) -> None:
        prompt = script_stage._writer_prompt(
            {"title": "Great Post", "region": "eu", "category": "wastewater_treatment"},
            self._factsheet(), self._brand(),
        )
        assert "Great Post" in prompt
        assert "dosage range of 1.5 to 3 kg per cubic meter" in prompt
        assert "b_purity" in prompt
        assert "hrsuindore.com" in prompt

    def test_writer_prompt_tells_writer_where_to_put_differentiator_id(self) -> None:
        """Regression: two live runs showed the writer inventing its own
        field (e.g. 'brand_differentiator') for the cta beat's
        differentiator instead of using fact_ids, because the prompt only
        said "cite" without saying where. The prompt must now say this
        explicitly, using one of the brand's own differentiator ids as the
        example so the instruction is concrete."""
        prompt = script_stage._writer_prompt(
            {"title": "T"}, self._factsheet(), self._brand())
        assert "fact_ids" in prompt
        assert "b_purity" in prompt  # first differentiator id, used as the example
        assert "brand_differentiator" in prompt  # named as what NOT to invent

    def test_writer_prompt_tells_writer_to_aim_for_the_total_duration_window(self) -> None:
        """Regression: a live run showed gate_total_duration rejecting
        three consecutive drafts for being too short in aggregate (26.9s,
        33.1s, 34.2s -- each beat individually legal, but every beat trended
        toward the short end of its own range). The prompt must tell the
        writer the aggregate word-count target up front, not just react
        after the first failure."""
        prompt = script_stage._writer_prompt(
            {"title": "T"}, self._factsheet(), self._brand())
        assert "SUM" in prompt
        assert "60" in prompt  # TOTAL_MIN_S(35) * WORDS_PER_SECOND(1.7), rounded
        assert "85" in prompt  # TOTAL_MAX_S(50) * WORDS_PER_SECOND(1.7)

    def test_writer_prompt_omits_gate_errors_block_when_none_given(self) -> None:
        prompt = script_stage._writer_prompt(
            {"title": "T"}, self._factsheet(), self._brand())
        assert "FAILED these checks" not in prompt

    def test_writer_prompt_includes_gate_errors_when_given(self) -> None:
        prompt = script_stage._writer_prompt(
            {"title": "T"}, self._factsheet(), self._brand(),
            gate_errors=["numbers[proof]: '150' does not trace"],
        )
        assert "FAILED these checks" in prompt
        assert "150" in prompt

    def test_writer_prompt_includes_revise_notes_when_given(self) -> None:
        prompt = script_stage._writer_prompt(
            {"title": "T"}, self._factsheet(), self._brand(),
            revise_notes="tighten the hook",
        )
        assert "tighten the hook" in prompt


class TestScriptSchema:
    """Structural correctness of the writer LLM response schema."""

    GOOD_BEAT = {"beat": "hook", "narration": "n", "fact_ids": [],
                 "card_text": "c", "broll_wish": ""}

    def _five(self, **overrides):
        beats = [{**self.GOOD_BEAT, "beat": s} for s in
                 ("hook", "stakes", "mechanism", "proof", "cta")]
        for idx, patch in overrides.items():
            beats[int(idx)].update(patch)
        return beats

    def test_validates_five_well_formed_beats(self) -> None:
        jsonschema.validate({"beats": self._five()}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_fewer_than_five_beats(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": self._five()[:4]}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_more_than_five_beats(self) -> None:
        six = self._five() + [dict(self.GOOD_BEAT)]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": six}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_unknown_beat_name(self) -> None:
        beats = self._five(**{"0": {"beat": "outro"}})
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": beats}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_missing_required_field(self) -> None:
        beats = self._five()
        del beats[0]["card_text"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": beats}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_additional_beat_key(self) -> None:
        beats = self._five(**{"0": {"extra": "nope"}})
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": beats}, script_stage.SCRIPT_SCHEMA)

    def test_rejects_non_string_fact_id(self) -> None:
        beats = self._five(**{"3": {"fact_ids": [1]}})
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"beats": beats}, script_stage.SCRIPT_SCHEMA)


class TestCritiqueSchema:
    """Structural correctness of the critic LLM response schema."""

    def test_validates_a_well_formed_payload(self) -> None:
        jsonschema.validate(GOOD_CRITIQUE, script_stage.CRITIQUE_SCHEMA)

    def test_rejects_score_above_ten(self) -> None:
        bad = {**GOOD_CRITIQUE, "actionable_score": 11}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)

    def test_rejects_score_below_zero(self) -> None:
        bad = {**GOOD_CRITIQUE, "coherence_score": -1}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)

    def test_rejects_missing_revise_notes(self) -> None:
        bad = {k: v for k, v in GOOD_CRITIQUE.items() if k != "revise_notes"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)

    def test_rejects_additional_top_level_key(self) -> None:
        bad = {**GOOD_CRITIQUE, "extra": 1}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)
