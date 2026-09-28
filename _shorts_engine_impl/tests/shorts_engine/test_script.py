"""Tests for the SCRIPT stage's flexible, purpose-tagged beat structure
(2026-08-26 creative-flow redesign): beats are free-form in count, order,
and naming -- only the `purpose` tag (hook/stakes/mechanism/proof/cta/other)
carries narrative-role meaning forward, and the only hard structural rules
are a MIN_BEATS floor and a final beat whose purpose is "cta"."""
from __future__ import annotations

from shorts_engine.stages.script import (
    apply_word_topup,
    gate_total_duration,
    gate_word_budget,
    run_gates,
    SCRIPT_SCHEMA,
)


def _beat(beat, purpose, narration, fact_ids=None, card_text="c", broll_wish=""):
    return {"beat": beat, "purpose": purpose, "narration": narration,
            "fact_ids": fact_ids or [], "card_text": card_text, "broll_wish": broll_wish}


def _fake_brand():
    from shorts_engine.brand import BrandFacts
    return BrandFacts(
        company="HRSU", domain="hrsuindore.com", tagline="t",
        differentiators=[{"id": "b_purity", "text": "High-purity calcium nitrate"}],
        cta_lines=["Visit hrsuindore.com"], banned_claims=[],
    )


class TestFlexibleBeatStructure:
    def test_run_gates_rejects_fewer_than_min_beats(self):
        beats = [_beat("hook", "hook", "word " * 5), _beat("cta", "cta", "word " * 10)]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert any("at least" in e for e in errors)

    def test_run_gates_rejects_final_beat_not_cta_purpose(self):
        beats = [_beat("a", "hook", "w " * 5), _beat("b", "stakes", "w " * 8),
                 _beat("c", "proof", "w " * 8)]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert any('purpose "cta"' in e for e in errors)

    def test_run_gates_accepts_nonstandard_beat_count_and_names(self):
        # 7 beats, none named hook/stakes/mechanism/proof -- only purposes matter.
        beats = [
            _beat("cold-open", "hook", "word " * 3),
            _beat("problem", "stakes", "word " * 4),
            _beat("twist", "other", "word " * 5),
            _beat("how-it-works", "mechanism", "word " * 8),
            _beat("evidence-1", "proof", "word " * 6),
            _beat("evidence-2", "proof", "word " * 6),
            _beat("close", "cta", "word " * 6,
                  fact_ids=[_fake_brand().differentiators[0]["id"]]),
        ]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert not any(e.startswith("structure:") for e in errors)

    def test_gate_word_budget_keys_off_purpose_not_position(self):
        # A beat named "twist" with purpose "mechanism" must be judged against
        # mechanism's word range, not against whatever beat sits at that index.
        beats = [_beat("twist", "mechanism", "word " * 2)]  # far under mechanism's floor
        errors = gate_word_budget(beats)
        assert any("budget[twist]" in e for e in errors)

    def test_gate_total_duration_only_checks_floor_no_ceiling(self):
        # 200 words is far over the old 50s/85-word ceiling -- must NOT error.
        beats = [_beat("a", "cta", "word " * 200)]
        assert gate_total_duration(beats) == []

    def test_apply_word_topup_uses_purpose_keyed_headroom(self):
        beats = [_beat("twist", "mechanism", "short narration here")]
        padded = apply_word_topup(beats)
        assert len(padded[0]["narration"].split()) > len(beats[0]["narration"].split())

    def test_schema_has_no_max_items_and_requires_purpose(self):
        beats_schema = SCRIPT_SCHEMA["properties"]["beats"]
        assert "maxItems" not in beats_schema
        assert beats_schema["minItems"] == 3
        assert "purpose" in beats_schema["items"]["required"]
