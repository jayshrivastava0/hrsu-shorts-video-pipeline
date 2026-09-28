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
# clears the TOTAL_MIN_S floor (62 words ~= 36.5s estimated; no ceiling exists).
BEATS = [
    {"beat": "hook", "purpose": "hook",
     "narration": "Your effluent nitrate is creeping toward the limit.",
     "fact_ids": [], "card_text": "Nitrate limits are tightening", "broll_wish": "aeration basin"},
    {"beat": "stakes", "purpose": "stakes",
     "narration": "Plants dose one point five to three kilograms per cubic meter.",
     "fact_ids": ["f1"],
     "card_text": "The dosing window that works", "broll_wish": ""},
    {"beat": "mechanism", "purpose": "mechanism",
     "narration": "Calcium nitrate feeds denitrifying bacteria, so they strip oxygen "
     "from nitrate, releasing harmless nitrogen gas before discharge without any retrofit.",
     "fact_ids": ["f1"], "card_text": "Bacteria do the removal",
     "broll_wish": "", "diagram_labels": ["Effluent in", "Calcium nitrate dosing",
                                          "Denitrifying bacteria", "N2 out"]},
    {"beat": "proof", "purpose": "proof",
     "narration": "Published trials report ninety two percent nitrate removal with this "
     "approach across municipal plants.",
     "fact_ids": ["f2"],
     "card_text": "92 percent removal", "broll_wish": ""},
    {"beat": "cta", "purpose": "cta",
     "narration": "HRSU ships high-purity powder with batch QC. The dosing guide is at "
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

    def test_hook_without_fact_suggests_headline(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[0], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["suggested_type"] == "HEADLINE_CARD" for s in shots)
        assert all(s["beat_purpose"] == "hook" for s in shots)
        assert all(s["payload"]["text"] == "Nitrate limits are tightening" for s in shots)

    def test_stakes_uses_stat_from_fact(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[1], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["suggested_type"] == "STAT_CARD" for s in shots)
        assert shots[0]["payload"]["fact_value"] == "1.5–3"
        assert "mdpi.com" in shots[0]["payload"]["fact_citation"]

    def test_mechanism_suggests_diagram_with_labels(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[2], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["suggested_type"] == "DIAGRAM" for s in shots)
        assert all(s["beat_purpose"] == "mechanism" for s in shots)
        assert all(s["payload"]["diagram_labels"] == BEATS[2]["diagram_labels"] for s in shots)

    def test_proof_with_numeric_fact_suggests_stat_card(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[3], self._facts_by_id(), self._cites(),
                                         self._brand())  # f2 cites web
        assert all(s["suggested_type"] == "STAT_CARD" for s in shots)
        assert shots[0]["payload"]["fact_text"] == \
            "denitrifying filters removed 92 percent of nitrate"

    def test_cta_shots_carry_brand_payload(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[4], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["suggested_type"] == "LOGO_CTA" for s in shots)
        assert all(s["payload"]["differentiator"] == "high-purity powder" for s in shots)
        assert all(s["payload"]["domain"] == "hrsuindore.com" for s in shots)
        assert all(s["duration_s"] <= 10.0 for s in shots)  # LOGO_CTA_MAX_S


class TestLinter:
    def test_duration_bounds_flagged(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "hook", "beat_purpose": "hook",
                  "duration_s": 9.0, "narration_span": "x",
                  "payload": {"text": "t"}}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("9.0" in e for e in errs)

    def test_logo_cta_exempt_up_to_10s(self):
        from shorts_engine.stages import shotlist
        shots = [
            {"id": "s00", "beat": "hook", "beat_purpose": "hook", "duration_s": 3.0,
             "narration_span": "x", "payload": {"text": "t"}},
            {"id": "s01", "beat": "stakes", "beat_purpose": "stakes", "duration_s": 5.0,
             "narration_span": "x", "payload": {"fact_value": "1.5", "fact_unit": "kg"}},
            {"id": "s02", "beat": "mechanism", "beat_purpose": "mechanism", "duration_s": 10.0,
             "narration_span": "x", "payload": {"diagram_labels": ["a", "b"]}},
            {"id": "s03", "beat": "proof", "beat_purpose": "proof", "duration_s": 10.0,
             "narration_span": "x", "payload": {"fact_value": "92", "fact_unit": "%"}},
            {"id": "s04", "beat": "cta", "beat_purpose": "cta", "duration_s": 8.0,
             "narration_span": "x", "payload": {}}
        ]
        # LOGO_CTA should not be flagged for its 8.0s duration (cap is 10.0s)
        errs = shotlist.lint_shotlist(shots, FACTS)
        duration_errors = [e for e in errs if e.startswith("s04:")]
        assert not duration_errors

    def _shots_with_durations(self, durations):
        """Otherwise lint-clean shots (hook cards + final cta) carrying the
        given per-shot durations."""
        shots = [
            {"id": f"s{i:02d}", "beat": "hook", "beat_purpose": "hook",
             "duration_s": d, "narration_span": "x", "payload": {"text": "t"}}
            for i, d in enumerate(durations[:-1])
        ]
        shots.append({"id": f"s{len(durations)-1:02d}", "beat": "cta",
                      "beat_purpose": "cta", "duration_s": durations[-1],
                      "narration_span": "x", "payload": {}})
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
        """The epsilon only absorbs rounding (~0.1s) -- a real shortfall
        below the TOTAL_MIN_S floor (30s) must still be rejected, with
        2-decimal honesty. (The ceiling check no longer exists -- Task 3
        dropped it -- so only the floor is exercised here.)"""
        from shorts_engine.stages import shotlist
        durations = [2.85, 1.69, 1.69, 2.46, 2.46, 2.46, 3.23, 3.23, 4.40]
        assert abs(sum(durations) - 24.47) < 1e-9
        errs = shotlist.lint_shotlist(self._shots_with_durations(durations), FACTS)
        total_errs = [e for e in errs if "total duration" in e]
        assert len(total_errs) == 1
        assert "24.47" in total_errs[0]


class TestFreeformShotPlanning:
    def test_shots_carry_suggested_type_not_type(self):
        from shorts_engine.stages.shotlist import plan_beat_shots
        shots = plan_beat_shots(_beat("cold-open", "hook", "Cold weather pours."),
                                 {}, {}, _fake_brand())
        assert "suggested_type" in shots[0]
        assert "type" not in shots[0]

    def test_shots_carry_beat_purpose(self):
        from shorts_engine.stages.shotlist import plan_beat_shots
        shots = plan_beat_shots(_beat("twist", "mechanism", "How it works, step one."),
                                 {}, {}, _fake_brand())
        assert all(s["beat_purpose"] == "mechanism" for s in shots)

    def test_fact_resolved_verbatim_into_payload(self):
        from shorts_engine.stages.shotlist import plan_beat_shots
        facts = {"f1": {"id": "f1", "verbatim_quote": "425 mS/cm peak conductivity",
                        "value": 425, "unit": "mS/cm", "citation_marker": 1}}
        cites = {1: {"url": "https://epa.gov/x", "kind": "standard"}}
        shots = plan_beat_shots(
            _beat("evidence", "proof", "Peak conductivity reached 425.", fact_ids=["f1"]),
            facts, cites, _fake_brand())
        assert shots[0]["payload"]["fact_text"] == "425 mS/cm peak conductivity"
        assert shots[0]["payload"]["fact_value"] == 425

    def test_cta_purpose_gets_cta_length_cap(self):
        from shorts_engine.stages.shotlist import plan_beat_shots
        shots = plan_beat_shots(
            _beat("close", "cta", "Visit us today for pricing and technical support now.",
                  fact_ids=["b_purity"]),
            {}, {}, _fake_brand())
        assert all(s["duration_s"] <= 10.0 for s in shots)  # config.LOGO_CTA_MAX_S

    def test_lint_shotlist_has_no_ceiling(self):
        from shorts_engine.stages.shotlist import lint_shotlist
        shots = [{"id": "s00", "duration_s": 4.0, "beat_purpose": "hook"}] * 20  # far over old 50s
        assert lint_shotlist(shots, {"facts": []}) == []

    def test_lint_shotlist_still_enforces_floor(self):
        from shorts_engine.stages.shotlist import lint_shotlist
        shots = [{"id": "s00", "duration_s": 2.0, "beat_purpose": "hook"}]  # well under 30s
        errors = lint_shotlist(shots, {"facts": []})
        assert any("floor" in e or "under" in e for e in errors)


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
        assert data["shots"][-1]["suggested_type"] == "LOGO_CTA"
        assert data["shots"][-1]["beat_purpose"] == "cta"
        from shorts_engine import config
        assert data["total_s"] >= config.TOTAL_MIN_S
