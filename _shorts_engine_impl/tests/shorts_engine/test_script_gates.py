"""Tests for the SCRIPT stage's deterministic gates -- pure functions that
enforce the never-unverified invariant, the free-form purpose-tagged beat
structure (2026-08-26 creative-flow redesign, spec §4 Stage 3), the
banned-phrase/fear-filler/brand-banned-claims ban, card_text hygiene, and
the exactly-one-differentiator-in-the-cta-beat rule (spec §7).

These gates are the last line of defense before a script.json is written:
no LLM output reaches disk without passing every one of them (see Task 12 /
test_script_run.py for the writer/critique/run() orchestration that calls
run_gates())."""
from __future__ import annotations

from shorts_engine import config
from shorts_engine.brand import BrandFacts
from shorts_engine.stages.script import (
    extract_numeric_tokens,
    gate_banned,
    gate_card_text,
    gate_differentiator,
    gate_numbers,
    gate_total_duration,
    run_gates,
)

BRAND = BrandFacts(
    company="HRSU",
    domain="hrsuindore.com",
    tagline="t",
    differentiators=[
        {"id": "b_purity", "text": "High-purity powder with batch QC"},
        {"id": "b_supply", "text": "Flexible MOQs"},
        {"id": "b_esg", "text": "Solar power at the plant"},
    ],
    cta_lines=["Visit hrsuindore.com"],
    banned_claims=["REACH registered", "certified"],
)

FACTSHEET = {
    "facts": [
        {
            "id": "f1",
            "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
            "value": "1.5-3", "unit": "kg/m3", "tags": ["spec"], "citation_marker": 1,
        },
    ],
}


def _beats(**over):
    """Five spec-clean beats (hook/stakes/mechanism/proof/cta), each tagged
    with a `purpose` matching its beat name; `over` patches beats by their
    positional index (as a string key, e.g. `_beats(**{"3": {...}})` patches
    the proof beat) so each test only states what it changes. Beat naming
    and count are otherwise free-form since the 2026-08-26 creative-flow
    redesign -- only `purpose` carries narrative-role meaning."""
    # Narration lengths sized for WORDS_PER_SECOND=1.7: per-beat integer
    # bounds hook [3,8], stakes [6,12], mechanism [11,24], proof [9,20],
    # cta [9,16]; total 62 words, comfortably over the 51-word (30s) floor.
    beats = [
        {"beat": "hook", "purpose": "hook", "narration": "EU nitrate discharge limits are tightening fast.",
         "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": ""},
        {"beat": "stakes", "purpose": "stakes", "narration": "Non-compliance risks steep penalties and unplanned production downtime this quarter.",
         "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
        {"beat": "mechanism", "purpose": "mechanism", "narration": "Dosing calcium nitrate feeds denitrifying bacteria, converting nitrate into harmless nitrogen gas within the treatment train without any retrofit.",
         "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
        {"beat": "proof", "purpose": "proof", "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic meter.",
         "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
        {"beat": "cta", "purpose": "cta", "narration": "HRSU supplies high-purity powder with batch QC. Visit hrsuindore.com for the guide.",
         "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
    ]
    for i, patch in over.items():
        beats[int(i)].update(patch)
    return beats


class TestExtractNumericTokens:
    """Numeric tokens are extracted with thousands-separator commas
    stripped, so they compare cleanly against fact/differentiator text that
    may or may not use commas."""

    def test_extracts_decimals_and_strips_thousands_comma(self) -> None:
        assert extract_numeric_tokens("range of 1.5 to 3 kg, 10,000 tons") == ["1.5", "3", "10000"]

    def test_no_numbers_returns_empty_list(self) -> None:
        assert extract_numeric_tokens("no numbers here") == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert extract_numeric_tokens("") == []

    def test_percentage_number_extracted_without_percent_sign(self) -> None:
        assert extract_numeric_tokens("grew by 12%") == ["12"]

    def test_multiple_decimal_tokens(self) -> None:
        assert extract_numeric_tokens("1.5-3.0 ratio") == ["1.5", "3.0"]

    def test_year_like_integer_extracted(self) -> None:
        assert extract_numeric_tokens("since 2026") == ["2026"]


class TestGateNumbers:
    """The never-unverified invariant: every numeric token in narration or
    card_text must trace back to a referenced fact's verbatim quote, a
    referenced brand differentiator's text, a CTA line, or the domain."""

    def test_passes_when_every_number_traces_to_a_fact(self) -> None:
        assert gate_numbers(_beats(), FACTSHEET, BRAND) == []

    def test_fails_on_untraced_number_in_narration(self) -> None:
        bad = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg per liter.",
                              "fact_ids": ["f1"]}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 1
        assert "150" in errs[0] and "proof" in errs[0]

    def test_checks_card_text_too(self) -> None:
        bad = _beats(**{"3": {"card_text": "42 percent better"}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 1 and "42" in errs[0]

    def test_error_message_names_beat_and_source(self) -> None:
        bad = _beats(**{"0": {"narration": "There are 99 reasons to comply."}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 1
        assert "hook" in errs[0] and "narration" in errs[0]

    def test_multiple_untraced_numbers_each_produce_an_error(self) -> None:
        bad = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg and 60 percent overall."}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 2

    def test_lone_digit_substring_of_a_larger_traced_number_is_not_traced(self) -> None:
        # "3" appears verbatim in the fact quote ("... to 3 kg ..."), but "5"
        # only ever appears glued inside "1.5" -- it must NOT be treated as
        # traced merely because it is a textual substring of that token.
        bad = _beats(**{"3": {"narration": "Read the number 5 on the gauge."}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 1 and "'5'" in errs[0]

    def test_number_traced_via_differentiator_text_is_allowed(self) -> None:
        brand = BrandFacts(
            company="HRSU", domain="hrsuindore.com", tagline="t",
            differentiators=[{"id": "b_purity", "text": "99.9% purity powder"}],
            cta_lines=["Visit hrsuindore.com"], banned_claims=[],
        )
        beats = _beats(**{"4": {"narration": "HRSU delivers 99.9% purity powder. Visit hrsuindore.com today.",
                                "fact_ids": ["b_purity"]}})
        assert gate_numbers(beats, FACTSHEET, brand) == []

    def test_number_traced_via_cta_line_is_allowed_in_any_beat(self) -> None:
        brand = BrandFacts(
            company="HRSU", domain="hrsuindore.com", tagline="t",
            differentiators=[{"id": "b_purity", "text": "High purity"}],
            cta_lines=["Save 20% on your first order at hrsuindore.com"],
            banned_claims=[],
        )
        beats = _beats(**{"0": {"narration": "Save 20% on your first order today."}})
        assert gate_numbers(beats, FACTSHEET, brand) == []

    def test_untraced_number_not_masked_by_an_unrelated_fact_id(self) -> None:
        # fact_ids references f1, but the number itself (150) isn't in f1's
        # quote -- referencing *some* fact must not be enough on its own.
        bad = _beats(**{"3": {"narration": "Confirmed at 150 client sites.",
                              "fact_ids": ["f1"]}})
        errs = gate_numbers(bad, FACTSHEET, BRAND)
        assert len(errs) == 1

    def test_empty_beats_list_produces_no_errors(self) -> None:
        assert gate_numbers([], FACTSHEET, BRAND) == []


class TestGateNumberUnits:
    """Narration numbers must also carry a unit (known, or defined in the beat's
    plan-step terms) that the beat's allowed pool ties to that same number."""

    FS = {"facts": FACTSHEET["facts"] + [
        {"id": "f9", "verbatim_quote": "add 5 kg of powder per batch", "value": "5",
         "unit": "kg", "tags": [], "citation_marker": None},
        {"id": "f10", "verbatim_quote": "readings of 425 EC were recorded", "value": "425",
         "unit": "EC", "tags": [], "citation_marker": None},
    ]}

    def _proof(self, narration, fact_ids=("f9",)):
        return _beats(**{"3": {"narration": narration, "fact_ids": list(fact_ids)}})

    def test_unit_not_tied_to_number_in_source_fails(self) -> None:
        errs = gate_numbers(self._proof("Add 5 g of powder per batch."), self.FS, BRAND)
        assert len(errs) == 1
        assert errs[0].startswith("numbers[proof]:") and "'g'" in errs[0]

    def test_bare_number_without_unit_fails(self) -> None:
        errs = gate_numbers(self._proof("The batch count is 5."), self.FS, BRAND)
        assert len(errs) == 1
        assert errs[0].startswith("numbers[proof]:") and "no unit" in errs[0]

    def test_correct_range_with_unit_passes(self) -> None:
        beats = self._proof("A dosage range of 1.5 to 3 kg per cubic meter works.", ["f1"])
        assert gate_numbers(beats, self.FS, BRAND) == []

    def test_correct_number_and_unit_passes(self) -> None:
        assert gate_numbers(self._proof("Add 5 kg of powder per batch."), self.FS, BRAND) == []

    def test_unknown_unit_fails_without_term(self) -> None:
        errs = gate_numbers(self._proof("Readings reach 425 EC.", ["f10"]), self.FS, BRAND)
        assert len(errs) == 1 and "unknown unit" in errs[0]

    def test_term_defined_unit_passes_when_pair_is_in_pool(self) -> None:
        terms = {"proof": [{"term": "EC", "definition": "conductivity", "unit": "EC"}]}
        beats = self._proof("Readings reach 425 EC.", ["f10"])
        assert gate_numbers(beats, self.FS, BRAND, terms_by_beat=terms) == []

    def test_term_defined_unit_fails_when_pair_is_not_in_pool(self) -> None:
        fs = {"facts": [{"id": "f11", "verbatim_quote": "readings of 425 dS/m were recorded",
                         "value": "425", "unit": "dS/m", "tags": [], "citation_marker": None}]}
        terms = {"proof": [{"term": "EC", "definition": "conductivity", "unit": "EC"}]}
        beats = self._proof("Readings reach 425 EC.", ["f11"])
        errs = gate_numbers(beats, fs, BRAND, terms_by_beat=terms)
        assert len(errs) == 1 and "'ec'" in errs[0]

    def test_unit_check_covers_hook_and_cta(self) -> None:
        brand = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                           differentiators=[{"id": "b_purity", "text": "99.9% purity powder"}],
                           cta_lines=["Visit hrsuindore.com"], banned_claims=[])
        beats = _beats(**{"4": {"narration": "HRSU delivers 99.9 kg purity powder. Visit "
                                             "hrsuindore.com today."}})
        errs = gate_numbers(beats, FACTSHEET, brand)
        assert len(errs) == 1 and errs[0].startswith("numbers[cta]:")

    def test_untraced_number_reported_once(self) -> None:
        errs = gate_numbers(self._proof("Reduces nitrate by 150 mg per liter."), self.FS, BRAND)
        assert len(errs) == 1 and "150" in errs[0]

    def test_unitless_number_in_card_text_fails(self) -> None:
        # 425 traces to f10, but the card shows it with no unit
        beats = _beats(**{"3": {"card_text": "Peaks at 425", "fact_ids": ["f1", "f10"]}})
        errs = gate_numbers(beats, self.FS, BRAND)
        assert len(errs) == 1
        assert errs[0].startswith("numbers[proof]:") and "no unit" in errs[0]
        assert "card_text" in errs[0]

    def test_unitless_number_in_diagram_label_fails(self) -> None:
        beats = _beats(**{"3": {"fact_ids": ["f1", "f10"], "diagram_labels": ["425", "soil"]}})
        errs = gate_numbers(beats, self.FS, BRAND)
        assert len(errs) == 1
        assert "no unit" in errs[0] and "diagram label" in errs[0]

    def test_card_text_with_traced_unit_passes(self) -> None:
        beats = _beats(**{"3": {"card_text": "Add 5 kg per batch", "fact_ids": ["f9"],
                                "narration": "Add 5 kg of powder per batch."}})
        assert gate_numbers(beats, self.FS, BRAND) == []

    def test_untraced_card_number_reported_once(self) -> None:
        beats = _beats(**{"3": {"card_text": "Peaks at 777"}})
        errs = gate_numbers(beats, self.FS, BRAND)
        assert len(errs) == 1 and "777" in errs[0]

    def test_run_gates_forwards_terms_by_beat(self) -> None:
        beats = self._proof("Readings reach 425 EC. " + "More words here. " * 3, ["f10"])
        terms = {"proof": [{"term": "EC", "definition": "conductivity", "unit": "EC"}]}
        assert any("unknown unit" in e for e in run_gates(beats, self.FS, BRAND))
        assert run_gates(beats, self.FS, BRAND, terms_by_beat=terms) == []


class TestGateBanned:
    """Rejects SCRIPT_BANNED_PHRASES (AI-isms), FEAR_FILLER_PATTERNS (hype /
    fear marketing), and brand.banned_claims (hard-blocked claims) anywhere
    in narration or card_text, case-insensitively."""

    def test_catches_fear_filler_phrase(self) -> None:
        errs = gate_banned(_beats(**{"1": {"narration": "Compliance is everything for plants."}}), BRAND)
        assert any("is everything" in e for e in errs)

    def test_catches_brand_banned_claim(self) -> None:
        errs = gate_banned(
            _beats(**{"4": {"narration": "We are REACH registered suppliers. Visit hrsuindore.com now."}}),
            BRAND,
        )
        assert any("reach registered" in e.lower() for e in errs)

    def test_catches_script_banned_phrase(self) -> None:
        phrase = config.SCRIPT_BANNED_PHRASES[0]
        errs = gate_banned(_beats(**{"0": {"narration": f"{phrase.capitalize()}, we cover dosing."}}), BRAND)
        assert any(phrase in e for e in errs)

    def test_clean_beats_produce_no_errors(self) -> None:
        assert gate_banned(_beats(), BRAND) == []

    def test_catches_banned_phrase_in_card_text(self) -> None:
        errs = gate_banned(_beats(**{"2": {"card_text": "certified process"}}), BRAND)
        assert any("certified" in e for e in errs)

    def test_match_is_case_insensitive(self) -> None:
        errs = gate_banned(_beats(**{"1": {"narration": "This Is Everything for compliance."}}), BRAND)
        assert any("is everything" in e for e in errs)

    def test_error_message_names_the_beat(self) -> None:
        errs = gate_banned(_beats(**{"2": {"narration": "This is a game-changer for plants."}}), BRAND)
        assert any("mechanism" in e for e in errs)

    def test_empty_beats_list_produces_no_errors(self) -> None:
        assert gate_banned([], BRAND) == []


class TestGateTotalDuration:
    """Aggregate duration vs. the config.TOTAL_MIN_S floor -- no ceiling
    (2026-08-26 creative-flow redesign: longer videos are fine). gate_word_
    budget only bounds each beat individually -- PURPOSE_TEMPLATE's per-
    purpose min_s values sum to well under TOTAL_MIN_S, so a script where
    every beat independently passes its own word budget can still, in
    aggregate, be too short for a valid video. A live run hit exactly this:
    five gate-compliant beats totaling only ~29s of narration, discovered
    only at the SHOTLIST stage (which has no LLM and no retry path of its
    own) with no way to recover. This gate catches it at SCRIPT time
    instead, where the existing writer retry-with-echo loop can act on it."""

    def test_default_beats_are_within_total_window(self) -> None:
        assert gate_total_duration(_beats()) == []

    def test_all_beats_at_their_minimum_is_flagged_too_short(self) -> None:
        # Mirrors the real failure: every beat individually legal (at or
        # near its own purpose's min_s), but the sum falls under the floor.
        short = _beats(**{
            "0": {"narration": "EU nitrate limits are tightening fast."},  # 6 words
            "1": {"narration": "Non-compliance risks penalties for your plant."},  # 6 words
            "2": {"narration": "Dosing calcium nitrate turns nitrate into harmless nitrogen gas in the treatment train."},  # 14 words
            "3": {"narration": "A dosage range of 1.5 to 3 kg per cubic meter works."},  # 12 words
            "4": {"narration": "HRSU supplies high-purity powder. Visit hrsuindore.com for the guide."},  # 9 words
        })
        # total = 6+6+13+12+9 = 46 words -> 27.1s at 1.7 w/s, under the
        # 51-word (30s) floor while every beat is individually legal.
        errs = gate_total_duration(short)
        assert len(errs) == 1
        assert "total_duration" in errs[0]

    def test_too_short_message_names_exact_deficit(self) -> None:
        """The error does the arithmetic for the writer: exact words needed."""
        short = _beats(**{
            "0": {"narration": " ".join(["word"] * 5)},
            "1": {"narration": " ".join(["word"] * 8)},
            "2": {"narration": " ".join(["word"] * 15)},
            "3": {"narration": " ".join(["word"] * 12)},
            "4": {"narration": " ".join(["word"] * 10)},
        })  # total = 50 words -> 29.4s at 1.7 w/s
        errs = gate_total_duration(short)
        assert len(errs) == 1
        assert "AT LEAST" in errs[0]
        assert "4" in errs[0]  # 51 - 50 + 3 buffer = 4

    def test_run_gates_includes_total_duration_errors(self) -> None:
        short = _beats(**{
            "0": {"narration": "EU nitrate limits are tightening fast."},
            "1": {"narration": "Non-compliance risks penalties for your plant."},
            "2": {"narration": "Dosing calcium nitrate turns nitrate into harmless nitrogen gas in the treatment train."},
            "3": {"narration": "A dosage range of 1.5 to 3 kg per cubic meter works."},
            "4": {"narration": "HRSU supplies high-purity powder. Visit hrsuindore.com for the guide."},
        })
        errs = run_gates(short, FACTSHEET, BRAND)
        assert any("total_duration" in e for e in errs)


class TestGateCardText:
    """card_text must be <=7 words and must not echo a 5-gram of its own
    beat's narration (so cards add information instead of repeating it)."""

    def test_default_beats_have_no_errors(self) -> None:
        assert gate_card_text(_beats()) == []

    def test_rejects_narration_echo_five_gram(self) -> None:
        echo = _beats(**{"1": {"card_text": "steep penalties and unplanned production"}})
        errs = gate_card_text(echo)
        assert len(errs) == 1 and "duplicates narration" in errs[0]

    def test_rejects_card_text_over_seven_words(self) -> None:
        long_card = _beats(**{"0": {"card_text": "one two three four five six seven eight"}})
        errs = gate_card_text(long_card)
        assert any("longer than 7 words" in e for e in errs)

    def test_short_card_text_bypasses_the_echo_check(self) -> None:
        # Fewer than 5 words never triggers the sliding 5-gram echo check.
        beats = _beats(**{"1": {"card_text": "risks penalties"}})
        assert gate_card_text(beats) == []

    def test_empty_beats_list_produces_no_errors(self) -> None:
        assert gate_card_text([]) == []


class TestGateDifferentiator:
    """Exactly one brand differentiator id must appear, and only in the cta
    beat's fact_ids (spec §7)."""

    def test_default_beats_have_exactly_one_in_cta(self) -> None:
        assert gate_differentiator(_beats(), BRAND) == []

    def test_missing_differentiator_in_cta_is_flagged(self) -> None:
        none_ = _beats(**{"4": {"fact_ids": []}})
        assert len(gate_differentiator(none_, BRAND)) == 1

    def test_differentiator_outside_cta_is_flagged(self) -> None:
        early = _beats(**{"0": {"fact_ids": ["b_esg"]}})
        assert len(gate_differentiator(early, BRAND)) == 1

    def test_two_differentiators_in_cta_is_flagged(self) -> None:
        two = _beats(**{"4": {"fact_ids": ["b_purity", "b_esg"]}})
        errs = gate_differentiator(two, BRAND)
        assert len(errs) == 1 and "cta" in errs[0]

    def test_differentiator_in_two_early_beats_flags_each(self) -> None:
        bad = _beats(**{"0": {"fact_ids": ["b_esg"]}, "1": {"fact_ids": ["b_supply"]}})
        errs = gate_differentiator(bad, BRAND)
        assert len(errs) == 2

    def test_non_differentiator_fact_ids_in_cta_do_not_count(self) -> None:
        # f1 is a regular fact, not a brand differentiator -- it must not
        # satisfy the "exactly one differentiator" requirement on its own.
        beats = _beats(**{"4": {"fact_ids": ["f1"]}})
        assert len(gate_differentiator(beats, BRAND)) == 1


class TestRunGates:
    """Aggregation: structural checks (beat count/order) short-circuit
    before the five individual gates run."""

    def test_aggregates_multiple_gate_failures(self) -> None:
        bad = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg per liter."},
                        "4": {"fact_ids": []}})
        errs = run_gates(bad, FACTSHEET, BRAND)
        assert len(errs) >= 2

    def test_clean_script_produces_no_errors(self) -> None:
        assert run_gates(_beats(), FACTSHEET, BRAND) == []

    def test_fewer_than_min_beats_short_circuits_with_structure_error(self) -> None:
        # Beats are free-form in count since the 2026-08-26 redesign, but a
        # count below config.MIN_BEATS is still a hard structural failure.
        errs = run_gates(_beats()[:2], FACTSHEET, BRAND)
        assert len(errs) == 1 and "structure" in errs[0]

    def test_final_beat_not_cta_purpose_short_circuits_with_structure_error(self) -> None:
        # Beat naming/order is free-form now, but the FINAL beat's purpose
        # must still be "cta".
        beats = _beats()
        beats[-1]["purpose"] = "proof"
        errs = run_gates(beats, FACTSHEET, BRAND)
        assert len(errs) == 1 and "structure" in errs[0]

    def test_reordered_beats_no_longer_short_circuit_on_structure(self) -> None:
        # Beat order is no longer checked -- only MIN_BEATS and the final
        # beat's purpose matter structurally.
        beats = _beats()
        beats[0], beats[1] = beats[1], beats[0]
        errs = run_gates(beats, FACTSHEET, BRAND)
        assert not any(e.startswith("structure:") for e in errs)

    def test_nonstandard_beat_name_no_longer_short_circuits_on_structure(self) -> None:
        # Beat names are free-form strings now -- only `purpose` matters.
        beats = _beats()
        beats[0]["beat"] = "intro"
        errs = run_gates(beats, FACTSHEET, BRAND)
        assert not any(e.startswith("structure:") for e in errs)

    def test_duplicate_beat_names_short_circuit_with_structure_error(self) -> None:
        # Free-form beat naming means two beats can plausibly get the same name (e.g. an
        # LLM naming two different beats "proof"). assemble.reflow() keys a dict by beat
        # name, so a duplicate silently collides -- one beat's audio span vanishes and both
        # beats' shots get grouped under the survivor's span. This must fail loudly and
        # clearly at script-gate time, not surface later as a confusing assemble-finalize
        # duration-law failure.
        beats = _beats()
        beats[2]["beat"] = beats[1]["beat"]
        errs = run_gates(beats, FACTSHEET, BRAND)
        assert len(errs) == 1
        assert "structure" in errs[0]
        assert "unique" in errs[0]
        assert beats[1]["beat"] in errs[0]

    def test_duplicate_beat_names_short_circuits_before_content_gates(self) -> None:
        # The structural checks run BEFORE the five content gates (per run_gates' own
        # docstring) -- a script with both a duplicate beat name AND a content-gate
        # failure (e.g. an untraceable number) must report only the structure error.
        beats = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg per liter."}})
        beats[2]["beat"] = beats[1]["beat"]
        errs = run_gates(beats, FACTSHEET, BRAND)
        assert len(errs) == 1
        assert errs[0].startswith("structure:")


class TestDiagramLabelsGate:
    def test_gate_numbers_scans_diagram_labels(self):
        from shorts_engine.stages import script
        from shorts_engine.brand import BrandFacts
        brand = BrandFacts(company="c", domain="hrsuindore.com", tagline="t",
                           differentiators=[{"id": "b_purity", "text": "pure"}],
                           cta_lines=["cta"], banned_claims=[])
        factsheet = {"facts": [{"id": "f1", "verbatim_quote": "uses 2 stages",
                                "value": "2", "unit": ""}]}
        beats = [{"beat": "mechanism", "narration": "no numbers here",
                  "fact_ids": ["f1"], "card_text": "clean",
                  "diagram_labels": ["Stage 99 boost", "output"]}]
        errs = script.gate_numbers(beats, factsheet, brand)
        assert any("99" in e for e in errs)
