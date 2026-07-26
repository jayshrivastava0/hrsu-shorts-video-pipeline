"""Tests for the SHOTLIST stage."""
from __future__ import annotations
import json
import pytest

FACTS = {
    "facts": [
        {"id": "f1", "verbatim_quote": "optimal dosage range of 1.5 to 3 kg per cubic meter",
         "value": "1.5–3", "unit": "kg/m³", "citation_marker": 2},
        {"id": "f2", "verbatim_quote": "denitrifying filters removed 92 percent of nitrate",
         "value": "92", "unit": "%", "citation_marker": 5},
    ],
    "brand_facts": {"differentiators": [{"id": "b_purity", "text": "high-purity powder"}],
                    "cta_lines": ["Full guide on the HRSU blog"], "domain": "hrsuindore.com"},
}
CITES = [{"marker": 2, "url": "https://www.mdpi.com/2073-4441/12/5/1234", "kind": "paper"},
         {"marker": 5, "url": "https://example.com/report", "kind": "web"}]

# Narration lengths sized for WORDS_PER_SECOND=1.7 so the planned total
# lands inside [TOTAL_MIN_S, TOTAL_MAX_S] (62 words ~= 36.5s estimated).
BEATS = [
    {"beat": "hook", "narration": "Your effluent nitrate is creeping toward the limit.",
     "fact_ids": [], "card_text": "Nitrate limits are tightening", "broll_wish": "aeration basin"},
    {"beat": "stakes", "narration": "Plants dose one point five to three kilograms per cubic meter.",
     "fact_ids": ["f1"],
     "card_text": "The dosing window that works", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Calcium nitrate feeds denitrifying bacteria, so they strip oxygen "
     "from nitrate, releasing harmless nitrogen gas before discharge without any retrofit.",
     "fact_ids": ["f1"], "card_text": "Bacteria do the removal",
     "broll_wish": "", "diagram_labels": ["Effluent in", "Calcium nitrate dosing",
                                          "Denitrifying bacteria", "N2 out"]},
    {"beat": "proof", "narration": "Published trials report ninety two percent nitrate removal with this "
     "approach across municipal plants.",
     "fact_ids": ["f2"],
     "card_text": "92 percent removal", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU ships high-purity powder with batch QC. The dosing guide is at "
     "hrsuindore dot com.",
     "fact_ids": ["b_purity"],
     "card_text": "Get the dosing guide", "broll_wish": ""},
]


class TestPhrasePacking:
    def test_split_phrases(self):
        from shorts_engine.stages import shotlist
        ph = shotlist.split_phrases("One, two. Three; four")
        assert ph == ["One", "two", "Three", "four"]

    def test_estimate_uses_words_per_second(self):
        from shorts_engine.stages import shotlist
        assert abs(shotlist.estimate_s("one two three four five") - 5 / 1.7) < 1e-6

    def test_pack_respects_target_bounds(self):
        from shorts_engine.stages import shotlist
        from shorts_engine import config
        words = "word " * 26  # 10s of narration
        spans = shotlist.pack_phrases(shotlist.split_phrases(
            ", ".join([words[:30]] * 6)))
        for s in spans:
            assert shotlist.estimate_s(s) <= config.SHOT_MAX_S + 0.01

    def test_single_unpunctuated_long_sentence_is_subdivided(self):
        """Regression: a live run showed a beat's narration as one long,
        comma-free sentence collapsing to a single span whose estimate
        (5.38s) exceeded SHOT_MAX_S (4.5s) -- plan_beat_shots's final
        duration clamp then silently truncated it, losing ~0.9s of
        narration time from the total. split_phrases finds no natural
        punctuation to split on, so pack_phrases itself must subdivide an
        overlong single phrase by word count."""
        from shorts_engine.stages import shotlist
        from shorts_engine import config
        sentence = ("Failure to meet the 50 mg/L guideline risks regulatory "
                    "non-compliance across European water bodies")
        assert shotlist.split_phrases(sentence + ".") == [sentence]
        assert shotlist.estimate_s(sentence) > config.SHOT_TARGET_MAX_S

        spans = shotlist.pack_phrases([sentence])
        assert len(spans) > 1
        for s in spans:
            assert shotlist.estimate_s(s) <= config.SHOT_TARGET_MAX_S + 0.01

    def test_subdivided_spans_preserve_total_duration(self):
        """The whole point of subdividing is to NOT lose seconds -- the
        summed estimate across all returned spans must equal the original
        phrase's estimate (word-for-word repackaging, no truncation)."""
        from shorts_engine.stages import shotlist
        sentence = ("Failure to meet the 50 mg/L guideline risks regulatory "
                    "non-compliance across European water bodies")
        spans = shotlist.pack_phrases([sentence])
        assert sum(len(s.split()) for s in spans) == len(sentence.split())

    def test_short_single_phrase_is_not_subdivided(self):
        """A phrase that already fits under SHOT_TARGET_MAX_S must pass
        through pack_phrases unchanged (no unnecessary splitting)."""
        from shorts_engine.stages import shotlist
        short = "Nitrate limits are tightening"
        assert shotlist.pack_phrases([short]) == [short]


class TestBeatMapping:
    def _facts_by_id(self):
        return {f["id"]: f for f in FACTS["facts"]}

    def _cites(self):
        return {c["marker"]: c for c in CITES}

    def _brand(self):
        from shorts_engine.brand import BrandFacts
        return BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                          differentiators=[{"id": "b_purity", "text": "high-purity powder"}],
                          cta_lines=["Full guide on the HRSU blog"], banned_claims=[])

    def test_hook_is_headline(self):
        # BEATS[0]'s narration is a single unpunctuated 9-word sentence whose
        # estimate (5.29s) exceeds SHOT_TARGET_MAX_S (3.5s), so pack_phrases
        # subdivides it into 2 spans (see TestPhrasePacking regression test).
        # Combined with a non-empty broll_wish, that now correctly triggers
        # the new BROLL-first emission (Task 9) with a HEADLINE_CARD fallback
        # carrying the beat's card_text -- not a HEADLINE_CARD directly.
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[0], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "BROLL"
        assert shots[0]["payload"]["wish"] == "aeration basin"
        fb = shots[0]["fallback"]
        assert fb["type"] == "HEADLINE_CARD"
        assert fb["payload"]["text"] == "Nitrate limits are tightening"

    def test_stakes_uses_stat_from_fact(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[1], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "STAT_CARD"
        assert shots[0]["payload"]["value"] == "1.5–3"
        assert "mdpi.com" in shots[0]["payload"]["citation"]

    def test_mechanism_flow_reveal_stages(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[2], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["type"] == "DIAGRAM" for s in shots)
        assert 1 <= len(shots) <= 3
        stages = [s["payload"]["reveal_stage"] for s in shots]
        assert stages == list(range(1, len(shots) + 1))
        assert all(s["payload"]["reveal_total"] == len(shots) for s in shots)
        assert shots[0]["payload"]["labels"] == BEATS[2]["diagram_labels"]

    def test_proof_paper_card_with_quote_fallback(self):
        from shorts_engine.stages import shotlist
        beat = dict(BEATS[3], fact_ids=["f1"])  # f1 cites marker 2 = paper
        shots = shotlist.plan_beat_shots(beat, self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "PAPER_CARD"
        fb = shots[0]["fallback"]
        assert fb["type"] == "QUOTE_CARD"
        assert "1.5 to 3 kg" in fb["payload"]["quote"]

    def test_proof_without_paper_is_stat_plus_quote(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[3], self._facts_by_id(), self._cites(),
                                         self._brand())  # f2 cites web
        assert [s["type"] for s in shots] == ["STAT_CARD", "QUOTE_CARD"]

    def test_cta_single_logo_shot(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[4], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert len(shots) == 1 and shots[0]["type"] == "LOGO_CTA"
        assert shots[0]["payload"]["differentiator"] == "high-purity powder"
        assert shots[0]["payload"]["domain"] == "hrsuindore.com"


class TestLinter:
    def test_paper_card_without_fallback_flagged(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                  "narration_span": "x", "payload": {}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("fallback" in e for e in errs)

    def test_stat_digits_must_trace_to_fact(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "proof", "type": "STAT_CARD", "duration_s": 3.0,
                  "narration_span": "x", "fallback": None,
                  "payload": {"value": "97", "unit": "%", "label": "l",
                              "fact_id": "f2"}}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("97" in e for e in errs)

    def test_duration_bounds_flagged(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "hook", "type": "HEADLINE_CARD",
                  "duration_s": 9.0, "narration_span": "x",
                  "payload": {"text": "t"}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("9.0" in e for e in errs)

    def test_logo_cta_exempt_up_to_10s(self):
        from shorts_engine.stages import shotlist
        shots = [
            {"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 3.0,
             "narration_span": "x", "payload": {"text": "t"}, "fallback": None},
            {"id": "s01", "beat": "stakes", "type": "STAT_CARD", "duration_s": 5.0,
             "narration_span": "x", "payload": {"value": "1.5", "unit": "kg", "label": "l"}, "fallback": None},
            {"id": "s02", "beat": "mechanism", "type": "DIAGRAM", "duration_s": 10.0,
             "narration_span": "x", "payload": {"template": "flow", "labels": ["a", "b"]}, "fallback": None},
            {"id": "s03", "beat": "proof", "type": "STAT_CARD", "duration_s": 10.0,
             "narration_span": "x", "payload": {"value": "92", "unit": "%", "label": "l"}, "fallback": None},
            {"id": "s04", "beat": "cta", "type": "LOGO_CTA", "duration_s": 8.0,
             "narration_span": "x", "payload": {}, "fallback": None}
        ]
        # LOGO_CTA should not be flagged for its 8.0s duration (cap is 10.0s)
        errs = shotlist.lint_shotlist(shots, FACTS)
        duration_errors = [e for e in errs if e.startswith("s04:")]
        assert not duration_errors

    def _shots_with_durations(self, durations):
        """Otherwise lint-clean shots (HEADLINE_CARDs + final LOGO_CTA)
        carrying the given per-shot durations."""
        shots = [
            {"id": f"s{i:02d}", "beat": "hook", "type": "HEADLINE_CARD",
             "duration_s": d, "narration_span": "x",
             "payload": {"text": "t"}, "fallback": None}
            for i, d in enumerate(durations[:-1])
        ]
        shots.append({"id": f"s{len(durations)-1:02d}", "beat": "cta",
                      "type": "LOGO_CTA", "duration_s": durations[-1],
                      "narration_span": "x", "payload": {}, "fallback": None})
        return shots

    def test_total_rounding_loss_at_the_floor_is_tolerated(self):
        """Regression: a live run's script estimated exactly 35.0s (91
        words), but per-shot 2-decimal rounding in plan_beat_shots summed
        to 34.99 -- and the strict floor check rejected it with a message
        that (via {:.1f} formatting) displayed the impossible-looking
        '35.0s outside [35.0, 50.0]'. A within-epsilon rounding loss at the
        boundary must pass; ASSEMBLE re-flows against real audio anyway."""
        from shorts_engine.stages import shotlist
        durations = [3.85, 2.69, 2.69, 3.46, 3.46, 3.46, 4.23, 4.23, 6.92]
        assert abs(sum(durations) - 34.99) < 1e-9  # the exact live case
        errs = shotlist.lint_shotlist(self._shots_with_durations(durations), FACTS)
        assert not [e for e in errs if "total duration" in e]

    def test_genuinely_short_total_is_still_flagged(self):
        """The epsilon only absorbs rounding (~0.1s) -- a real half-second
        shortfall must still be rejected, with 2-decimal honesty."""
        from shorts_engine.stages import shotlist
        durations = [3.85, 2.69, 2.69, 3.46, 3.46, 3.46, 4.23, 4.23, 6.40]
        errs = shotlist.lint_shotlist(self._shots_with_durations(durations), FACTS)
        total_errs = [e for e in errs if "total duration" in e]
        assert len(total_errs) == 1
        assert "34.47" in total_errs[0]


class TestBrollEmission:
    def _fixtures(self):
        facts = {f["id"]: f for f in FACTS["facts"]}
        cites = {c["marker"]: c for c in CITES}
        from shorts_engine.brand import BrandFacts
        brand = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                           differentiators=[{"id": "b_purity", "text": "high-purity powder"}],
                           cta_lines=["Full guide on the HRSU blog"], banned_claims=[])
        return facts, cites, brand

    def test_hook_with_wish_and_two_spans_emits_broll_first(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook",
                "narration": "Your effluent nitrate is creeping toward the limit, "
                             "and the discharge clock is already running.",
                "fact_ids": [], "card_text": "Nitrate limits are tightening",
                "broll_wish": "wastewater aeration basin"}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert shots[0]["type"] == "BROLL"
        assert shots[0]["payload"]["wish"] == "wastewater aeration basin"
        fb = shots[0]["fallback"]
        assert fb["type"] == "HEADLINE_CARD"
        assert fb["payload"]["text"] == "Nitrate limits are tightening"

    def test_hook_without_wish_stays_headline(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook", "narration": "Your effluent nitrate is rising fast.",
                "fact_ids": [], "card_text": "Limits tightening", "broll_wish": ""}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert all(s["type"] != "BROLL" for s in shots)

    def test_single_span_hook_keeps_designed_card_despite_wish(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook", "narration": "Nitrate limits are rising.",
                "fact_ids": [], "card_text": "Limits tightening",
                "broll_wish": "aeration basin"}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert shots[0]["type"] == "HEADLINE_CARD"

    def test_linter_flags_broll_without_fallback(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 3.0,
                  "narration_span": "x", "payload": {"wish": "w"}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("fallback" in e for e in errs)


class TestRun:
    def test_run_writes_shotlist(self, tmp_path):
        from pathlib import Path
        from shorts_engine.stages import shotlist
        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = Path(m.workspace)
        (ws / "script.json").write_text(json.dumps({"beats": BEATS}), encoding="utf-8")
        (ws / "factsheet.json").write_text(json.dumps(FACTS), encoding="utf-8")
        (ws / "post.json").write_text(json.dumps({"citations": CITES}), encoding="utf-8")
        ctx = StageContext(manifest=m, workspace=ws, flags={})
        arts = shotlist.run(ctx)
        data = json.loads((ws / arts["shotlist"]).read_text(encoding="utf-8"))
        assert data["shots"][0]["beat"] == "hook"
        assert data["shots"][-1]["type"] == "LOGO_CTA"
        from shorts_engine import config
        assert config.TOTAL_MIN_S <= data["total_s"] <= config.TOTAL_MAX_S
