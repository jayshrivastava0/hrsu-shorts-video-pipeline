"""Tests for the FACTS stage -- miner, normalizer, verbatim locator, LLM wrap,
gate, and run(). Pins the never-unverified invariant: every kept fact must be
string-locatable in canonical.txt, and every digit in its value must appear
inside its own verbatim quote."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from shorts_engine.errors import EngineError
from shorts_engine.llm import text_llm
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import facts
from shorts_engine.stages.facts import (
    FACT_WRAP_SCHEMA,
    gate_facts,
    locate_verbatim,
    mine_candidate_sentences,
    normalize_for_match,
    run,
    split_sentences,
    wrap_sentences_into_facts,
)
from shorts_engine.stages.ingest import isolate_post

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

CANON = (
    "Current industry best practice suggests a dosage range of 1.5 to 3 kg "
    "per cubic meter of wastewater volume, though this will vary. "
    "The EU’s stringent water quality directives mandate effective "
    "reduction. A denitrifying filter removes suspended solids alongside "
    "nitrate reduction."
)


class TestNormalizeForMatch:
    """Normalization: lowercase, collapse whitespace, unify dashes/quotes/nbsp."""

    def test_unifies_en_dash_and_curly_quote_with_whitespace_collapse(self) -> None:
        assert normalize_for_match("A – B’s  test") == "a - b's test"

    def test_unifies_dash_in_numeric_range(self) -> None:
        assert normalize_for_match("1.5–3 kg/m³") == "1.5-3 kg/m³"

    def test_unifies_em_dash(self) -> None:
        assert normalize_for_match("A—B") == "a-b"

    def test_unifies_minus_sign(self) -> None:
        assert normalize_for_match("5−3") == "5-3"

    def test_unifies_double_curly_quotes(self) -> None:
        assert normalize_for_match("“Hello”") == '"hello"'

    def test_unifies_nonbreaking_space_unicode(self) -> None:
        assert normalize_for_match("A B") == "a b"

    def test_unifies_literal_nbsp_entity(self) -> None:
        assert normalize_for_match("A&nbsp;B") == "a b"

    def test_lowercases(self) -> None:
        assert normalize_for_match("HELLO World") == "hello world"

    def test_collapses_internal_whitespace_and_strips_ends(self) -> None:
        assert normalize_for_match("  Hello   World  ") == "hello world"

    def test_collapses_newlines_and_tabs(self) -> None:
        assert normalize_for_match("Hello\n\tWorld") == "hello world"

    def test_is_idempotent(self) -> None:
        s = "A – B’s “Test”"
        once = normalize_for_match(s)
        twice = normalize_for_match(once)
        assert once == twice

    def test_empty_string_returns_empty_string(self) -> None:
        assert normalize_for_match("") == ""


class TestLocateVerbatim:
    """Char-offset lookup inside the normalized canonical text."""

    def test_finds_exact_substring(self) -> None:
        assert locate_verbatim("dosage range of 1.5 to 3 kg", CANON) is not None

    def test_finds_with_unified_quotes_and_case(self) -> None:
        quote = "the eu's stringent water quality directives"
        assert locate_verbatim(quote, CANON) is not None

    def test_returns_none_for_fabricated_text(self) -> None:
        assert locate_verbatim("reduces nitrate by 150 mg/L", CANON) is None

    def test_returns_none_for_empty_quote(self) -> None:
        assert locate_verbatim("", CANON) is None

    def test_returns_none_for_whitespace_only_quote(self) -> None:
        assert locate_verbatim("   \n\t", CANON) is None

    def test_handles_whitespace_differences(self) -> None:
        messy = "dosage   range of\n1.5 to 3 kg"
        assert locate_verbatim(messy, CANON) is not None

    def test_unifies_dash_variants_between_quote_and_canonical(self) -> None:
        dash_canon = "The mixture ratio is 1.5–3 by weight."
        assert locate_verbatim("1.5-3 by weight", dash_canon) is not None

    def test_finds_quote_at_start_of_canonical(self) -> None:
        assert locate_verbatim("Current industry best practice", CANON) == 0

    def test_finds_quote_at_end_of_canonical(self) -> None:
        assert locate_verbatim("nitrate reduction", CANON) is not None

    def test_offset_matches_normalized_canonical_find(self) -> None:
        quote = "The EU's stringent water quality directives"
        expected = normalize_for_match(CANON).find(normalize_for_match(quote))
        assert expected >= 0
        assert locate_verbatim(quote, CANON) == expected


class TestSplitSentences:
    """Lightweight sentence splitter used by the candidate miner."""

    def test_splits_canon_into_three_sentences(self) -> None:
        sents = split_sentences(CANON)
        assert len(sents) == 3
        assert sents[0].startswith("Current industry")

    def test_second_and_third_sentence_content(self) -> None:
        sents = split_sentences(CANON)
        assert sents[1].startswith("The EU")
        assert sents[2].startswith("A denitrifying filter")

    def test_does_not_split_on_decimal_point(self) -> None:
        text = "The dose is 3.5 kg per liter. This sentence is separate."
        sents = split_sentences(text)
        assert sents == ["The dose is 3.5 kg per liter.", "This sentence is separate."]

    def test_splits_on_exclamation_mark(self) -> None:
        sents = split_sentences("Watch out! The tank is overflowing.")
        assert sents == ["Watch out!", "The tank is overflowing."]

    def test_splits_on_question_mark(self) -> None:
        sents = split_sentences("Is this safe? Yes, it is rated for use.")
        assert sents == ["Is this safe?", "Yes, it is rated for use."]

    def test_single_sentence_no_split(self) -> None:
        assert split_sentences("Just one sentence here.") == ["Just one sentence here."]

    def test_empty_string_returns_empty_list(self) -> None:
        assert split_sentences("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert split_sentences("   \n\t  ") == []

    def test_strips_whitespace_from_each_sentence(self) -> None:
        text = "First sentence.   Second sentence.\nThird sentence."
        sents = split_sentences(text)
        assert sents == ["First sentence.", "Second sentence.", "Third sentence."]

    def test_splits_before_opening_quote(self) -> None:
        text = 'He said hello. "How are you?" She smiled.'
        sents = split_sentences(text)
        assert sents == ["He said hello.", '"How are you?" She smiled.']

    def test_splits_before_opening_paren(self) -> None:
        text = "See the chart above. (Figure 2 shows the trend.)"
        sents = split_sentences(text)
        assert sents == ["See the chart above.", "(Figure 2 shows the trend.)"]


class TestMineCandidateSentences:
    """Deterministic numeric-sentence miner (the only sentences the LLM ever sees)."""

    def test_only_numeric_sentence_from_canon(self) -> None:
        cands = mine_candidate_sentences(CANON)
        assert len(cands) == 1
        assert "1.5 to 3 kg" in cands[0]

    def test_returns_empty_when_no_digits_anywhere(self) -> None:
        text = "Our product is reliable. It performs well in the field. Customers like it."
        assert mine_candidate_sentences(text) == []

    def test_preserves_document_order(self) -> None:
        text = "Alpha has 1 unit. Beta has no numbers here. Gamma has 3 units."
        cands = mine_candidate_sentences(text)
        assert cands == ["Alpha has 1 unit.", "Gamma has 3 units."]

    def test_dedups_exact_duplicate_sentences(self) -> None:
        text = "The dose is 5 kg. Something else happens. The dose is 5 kg."
        cands = mine_candidate_sentences(text)
        assert cands == ["The dose is 5 kg."]

    def test_keeps_near_duplicates_that_differ_in_case(self) -> None:
        text = "The dose is 5 kg. THE DOSE IS 5 KG."
        cands = mine_candidate_sentences(text)
        assert len(cands) == 2

    def test_includes_percentage_and_year_sentences(self) -> None:
        text = "Revenue grew by 12%. In 2026 this changed. Our product is reliable."
        cands = mine_candidate_sentences(text)
        assert cands == ["Revenue grew by 12%.", "In 2026 this changed."]


class TestFactWrapSchema:
    """Structural correctness of the LLM response schema (validated with jsonschema,
    even though text_llm.generate_schema_json does not currently enforce it itself --
    this pins the contract for when it does, and for the script stage's reuse)."""

    GOOD_FACT = {
        "id": "f1",
        "verbatim_quote": "some quote",
        "value": "1.5-3",
        "unit": "kg/m3",
        "claim_summary": "summary",
        "tags": ["spec"],
        "procurement_significance": 5,
        "citation_marker": 1,
    }

    def test_requires_facts_key(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({}, FACT_WRAP_SCHEMA)

    def test_validates_a_well_formed_payload(self) -> None:
        jsonschema.validate({"facts": [self.GOOD_FACT]}, FACT_WRAP_SCHEMA)

    def test_allows_null_citation_marker(self) -> None:
        fact = {**self.GOOD_FACT, "citation_marker": None}
        jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)

    def test_rejects_missing_required_field(self) -> None:
        fact = {k: v for k, v in self.GOOD_FACT.items() if k != "value"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)

    def test_rejects_unknown_tag_enum_value(self) -> None:
        fact = {**self.GOOD_FACT, "tags": ["not_a_real_tag"]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)

    def test_rejects_out_of_range_procurement_significance(self) -> None:
        fact = {**self.GOOD_FACT, "procurement_significance": 6}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)

    def test_rejects_procurement_significance_below_minimum(self) -> None:
        fact = {**self.GOOD_FACT, "procurement_significance": 0}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)

    def test_rejects_additional_top_level_key(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [], "extra": 1}, FACT_WRAP_SCHEMA)

    def test_rejects_additional_fact_key(self) -> None:
        fact = {**self.GOOD_FACT, "extra_field": "nope"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"facts": [fact]}, FACT_WRAP_SCHEMA)


class TestWrapSentencesIntoFacts:
    """LLM wrapper: turns mined sentences into structured (ungated) fact entries."""

    def test_calls_llm_and_returns_entries(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["prompt"] = prompt
            assert schema is FACT_WRAP_SCHEMA
            return {
                "facts": [{
                    "id": "f1",
                    "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                    "value": "1.5-3", "unit": "kg/m3",
                    "claim_summary": "dosing window", "tags": ["spec"],
                    "procurement_significance": 5, "citation_marker": 1,
                }]
            }

        out = wrap_sentences_into_facts(
            ["Current industry best practice suggests a dosage range of "
             "1.5 to 3 kg per cubic meter of wastewater volume."],
            {"title": "T", "region": "eu", "category": "wastewater_treatment"},
            llm=fake_llm,
        )
        assert out[0]["id"] == "f1"
        assert "1.5 to 3 kg" in captured["prompt"]

    def test_prompt_includes_post_meta(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["prompt"] = prompt
            return {"facts": []}

        wrap_sentences_into_facts(
            ["A widget costs 5 dollars."],
            {"title": "Great Post", "region": "au", "category": "mining"},
            llm=fake_llm,
        )
        assert "Great Post" in captured["prompt"]
        assert "au" in captured["prompt"]
        assert "mining" in captured["prompt"]

    def test_passes_local_only_through_to_llm(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["local_only"] = kw.get("local_only")
            return {"facts": []}

        wrap_sentences_into_facts(
            ["A widget costs 5 dollars."], {"title": "T"},
            local_only=True, llm=fake_llm,
        )
        assert captured["local_only"] is True

    def test_defaults_local_only_to_false(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["local_only"] = kw.get("local_only")
            return {"facts": []}

        wrap_sentences_into_facts(["A widget costs 5 dollars."], {"title": "T"}, llm=fake_llm)
        assert captured["local_only"] is False

    def test_empty_sentence_list_returns_empty_without_calling_llm(self) -> None:
        def fake_llm(*args, **kwargs):
            raise AssertionError("llm should not be called with no sentences")

        assert wrap_sentences_into_facts([], {"title": "T"}, llm=fake_llm) == []

    def test_includes_every_sentence_with_bullet_prefix(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["prompt"] = prompt
            return {"facts": []}

        sentences = ["First sentence has 1 fact.", "Second sentence has 2 facts."]
        wrap_sentences_into_facts(sentences, {"title": "T"}, llm=fake_llm)
        assert "- First sentence has 1 fact." in captured["prompt"]
        assert "- Second sentence has 2 facts." in captured["prompt"]

    def test_system_prompt_instructs_verbatim_extraction(self) -> None:
        captured = {}

        def fake_llm(prompt, system, schema, **kw):
            captured["system"] = system
            return {"facts": []}

        wrap_sentences_into_facts(["A widget costs 5 dollars."], {"title": "T"}, llm=fake_llm)
        assert "verbatim" in captured["system"].lower()

    def test_default_llm_resolves_text_llm_generate_schema_json_at_call_time(
        self, monkeypatch
    ) -> None:
        """The default llm= must be resolved fresh at call time (not captured as a
        stale default at def time), so that monkeypatching
        text_llm.generate_schema_json -- as the eventual full-pipeline integration
        test does -- is actually honored."""
        calls = []

        def fake(prompt, system, schema, **kw):
            calls.append((prompt, system, schema, kw))
            return {"facts": []}

        monkeypatch.setattr(text_llm, "generate_schema_json", fake)
        out = wrap_sentences_into_facts(["A widget costs 5 dollars."], {"title": "T"})
        assert out == []
        assert len(calls) == 1


class TestGateFacts:
    """The never-unverified invariant: drop anything not string-locatable, or
    whose value digits don't appear inside its own quote."""

    def test_keeps_located_drops_fabricated(self) -> None:
        entries = [
            {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
             "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
             "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1},
            {"id": "f2", "verbatim_quote": "reduces nitrate by 150 mg/L",
             "value": "150", "unit": "mg/L", "claim_summary": "fabricated",
             "tags": ["metric"], "procurement_significance": 5, "citation_marker": None},
            {"id": "f3",
             "verbatim_quote": "A denitrifying filter removes suspended solids "
                                "alongside nitrate reduction",
             "value": "0", "unit": "", "claim_summary": "value not in quote",
             "tags": ["benefit"], "procurement_significance": 3, "citation_marker": None},
        ]
        kept, dropped = gate_facts(entries, CANON)
        assert [e["id"] for e in kept] == ["f1"]
        assert kept[0]["char_offset"] is not None
        reasons = {d["id"]: d["reason"] for d in dropped}
        assert "not located" in reasons["f2"]
        assert "value" in reasons["f3"]

    def test_empty_entries_returns_empty_lists(self) -> None:
        kept, dropped = gate_facts([], CANON)
        assert kept == [] and dropped == []

    def test_kept_char_offset_matches_locate_verbatim(self) -> None:
        entry = {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                 "value": "1.5-3", "unit": "kg/m3", "claim_summary": "x",
                 "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1}
        kept, _ = gate_facts([entry], CANON)
        assert kept[0]["char_offset"] == locate_verbatim(entry["verbatim_quote"], CANON)

    def test_dropped_entries_preserve_original_fields(self) -> None:
        entry = {"id": "f2", "verbatim_quote": "reduces nitrate by 150 mg/L",
                 "value": "150", "unit": "mg/L", "claim_summary": "fabricated",
                 "tags": ["metric"], "procurement_significance": 5, "citation_marker": None}
        _, dropped = gate_facts([entry], CANON)
        assert dropped[0]["value"] == "150"
        assert dropped[0]["claim_summary"] == "fabricated"
        assert dropped[0]["reason"] == "not located in canonical text"

    def test_kept_entries_preserve_original_fields(self) -> None:
        entry = {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                 "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
                 "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1}
        kept, _ = gate_facts([entry], CANON)
        assert kept[0]["claim_summary"] == "dosing window"
        assert kept[0]["unit"] == "kg/m3"

    def test_preserves_input_order_for_kept(self) -> None:
        e1 = {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
              "value": "1.5", "unit": "kg", "claim_summary": "a", "tags": ["spec"],
              "procurement_significance": 1, "citation_marker": None}
        e2 = {"id": "f2",
              "verbatim_quote": "The EU's stringent water quality directives "
                                 "mandate effective reduction",
              "value": "", "unit": "", "claim_summary": "b", "tags": ["compliance"],
              "procurement_significance": 2, "citation_marker": None}
        kept, _ = gate_facts([e1, e2], CANON)
        assert [k["id"] for k in kept] == ["f1", "f2"]

    def test_preserves_input_order_for_dropped(self) -> None:
        e1 = {"id": "d1", "verbatim_quote": "fabricated one", "value": "1", "unit": "",
              "claim_summary": "x", "tags": ["metric"], "procurement_significance": 1,
              "citation_marker": None}
        e2 = {"id": "d2", "verbatim_quote": "fabricated two", "value": "2", "unit": "",
              "claim_summary": "y", "tags": ["metric"], "procurement_significance": 1,
              "citation_marker": None}
        _, dropped = gate_facts([e1, e2], CANON)
        assert [d["id"] for d in dropped] == ["d1", "d2"]

    def test_empty_value_string_bypasses_digit_check(self) -> None:
        entry = {"id": "f3",
                 "verbatim_quote": "A denitrifying filter removes suspended solids "
                                    "alongside nitrate reduction",
                 "value": "", "unit": "", "claim_summary": "qualitative benefit",
                 "tags": ["benefit"], "procurement_significance": 3, "citation_marker": None}
        kept, dropped = gate_facts([entry], CANON)
        assert [k["id"] for k in kept] == ["f3"]
        assert dropped == []

    def test_case_and_quote_variant_location_still_gates_on_value(self) -> None:
        entry = {"id": "f4", "verbatim_quote": "THE EU'S STRINGENT WATER QUALITY DIRECTIVES",
                 "value": "", "unit": "", "claim_summary": "regulatory context",
                 "tags": ["compliance"], "procurement_significance": 2, "citation_marker": None}
        kept, _ = gate_facts([entry], CANON)
        assert [k["id"] for k in kept] == ["f4"]

    def test_multiple_digit_groups_all_required(self) -> None:
        entry = {"id": "f5", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                 "value": "1.5-3.0", "unit": "kg/m3", "claim_summary": "x",
                 "tags": ["spec"], "procurement_significance": 1, "citation_marker": None}
        kept, dropped = gate_facts([entry], CANON)
        assert kept == []
        assert dropped[0]["reason"] == "value digits not inside the quote"

    def test_partial_digit_match_dropped(self) -> None:
        entry = {"id": "f6", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                 "value": "1.5-99", "unit": "kg/m3", "claim_summary": "x",
                 "tags": ["spec"], "procurement_significance": 1, "citation_marker": None}
        kept, dropped = gate_facts([entry], CANON)
        assert kept == []
        assert dropped[0]["id"] == "f6"
        assert dropped[0]["reason"] == "value digits not inside the quote"

    def test_reason_string_for_unlocated_quote_is_exact(self) -> None:
        entries = [{"id": "a", "verbatim_quote": "not in canon at all", "value": "1",
                    "unit": "", "claim_summary": "x", "tags": ["metric"],
                    "procurement_significance": 1, "citation_marker": None}]
        _, dropped = gate_facts(entries, CANON)
        assert dropped[0]["reason"] == "not located in canonical text"


class TestRunFunction:
    """Stage entry point: canonical.txt + post.json -> factsheet.json."""

    @staticmethod
    def _ctx(tmp_path, canonical_text, post_meta=None, flags=None) -> StageContext:
        manifest = RunManifest.create(blog_url=URL, workspace_root=tmp_path)
        ws = Path(manifest.workspace)
        ws.joinpath("canonical.txt").write_text(canonical_text, encoding="utf-8")
        meta = post_meta or {
            "url": URL, "title": "Optimizing Nitrate Removal", "region": "eu",
            "category": "wastewater_treatment", "citations": [], "images": [],
        }
        ws.joinpath("post.json").write_text(json.dumps(meta), encoding="utf-8")
        return StageContext(manifest=manifest, workspace=ws, flags=flags or {})

    @staticmethod
    def _good_fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
        return [{
            "id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
            "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
            "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1,
        }]

    def test_writes_factsheet_json_and_returns_artifact_dict(self, tmp_path, monkeypatch) -> None:
        ctx = self._ctx(tmp_path, CANON)
        monkeypatch.setattr(facts, "wrap_sentences_into_facts", self._good_fake_wrap)
        result = run(ctx)
        assert result == {"factsheet": "factsheet.json"}
        payload = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
        assert payload["facts"][0]["id"] == "f1"
        assert payload["dropped"] == []

    def test_reads_post_meta_and_forwards_to_wrap(self, tmp_path, monkeypatch) -> None:
        captured = {}

        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            captured["post_meta"] = post_meta
            return self._good_fake_wrap(sentences, post_meta)

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON, post_meta={
            "url": URL, "title": "Custom Title", "region": "gulf",
            "category": "mining", "citations": [], "images": [],
        })
        run(ctx)
        assert captured["post_meta"]["title"] == "Custom Title"
        assert captured["post_meta"]["region"] == "gulf"

    def test_passes_local_only_flag_from_ctx(self, tmp_path, monkeypatch) -> None:
        captured = {}

        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            captured["local_only"] = local_only
            return self._good_fake_wrap(sentences, post_meta)

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON, flags={"local_only": True})
        run(ctx)
        assert captured["local_only"] is True

    def test_defaults_local_only_false_when_flag_absent(self, tmp_path, monkeypatch) -> None:
        captured = {}

        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            captured["local_only"] = local_only
            return self._good_fake_wrap(sentences, post_meta)

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        run(ctx)
        assert captured["local_only"] is False

    def test_raises_engine_error_when_no_numeric_sentences(self, tmp_path) -> None:
        text = "Our product is reliable. It performs well in the field."
        ctx = self._ctx(tmp_path, text)
        with pytest.raises(EngineError, match="no numeric sentences"):
            run(ctx)

    def test_raises_engine_error_when_all_facts_fail_gate(self, tmp_path, monkeypatch) -> None:
        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            return [{"id": "f1", "verbatim_quote": "totally fabricated content",
                     "value": "999", "unit": "", "claim_summary": "poison",
                     "tags": ["metric"], "procurement_significance": 1,
                     "citation_marker": None}]

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        with pytest.raises(EngineError, match="failed the verbatim gate"):
            run(ctx)

    def test_caps_candidate_sentences_at_max(self, tmp_path, monkeypatch) -> None:
        many = " ".join(f"Fact number {i} states value {i}." for i in range(1, 46))
        captured = {}

        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            captured["count"] = len(sentences)
            return [{"id": "f1", "verbatim_quote": sentences[0], "value": "1",
                     "unit": "", "claim_summary": "x", "tags": ["metric"],
                     "procurement_significance": 1, "citation_marker": None}]

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, many)
        run(ctx)
        assert captured["count"] == 40

    def test_dropped_facts_included_in_factsheet(self, tmp_path, monkeypatch) -> None:
        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            return [
                self._good_fake_wrap(sentences, post_meta)[0],
                {"id": "f2", "verbatim_quote": "fabricated poison stat",
                 "value": "150000", "unit": "t", "claim_summary": "poison",
                 "tags": ["metric"], "procurement_significance": 3,
                 "citation_marker": None},
            ]

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        run(ctx)
        payload = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
        assert [f["id"] for f in payload["facts"]] == ["f1"]
        assert payload["dropped"][0]["id"] == "f2"
        assert "not located" in payload["dropped"][0]["reason"]

    def test_brand_facts_empty_when_brand_module_missing(self, tmp_path, monkeypatch) -> None:
        # Force ImportError deterministically, regardless of whether Task 10's
        # shorts_engine/brand.py exists on disk in this environment.
        monkeypatch.setitem(sys.modules, "shorts_engine.brand", None)
        monkeypatch.setattr(facts, "wrap_sentences_into_facts", self._good_fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        run(ctx)
        payload = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
        assert payload["brand_facts"] == []

    def test_brand_facts_populated_when_brand_module_available(self, tmp_path, monkeypatch) -> None:
        fake_module = types.ModuleType("shorts_engine.brand")

        def load_brand_facts():
            return SimpleNamespace(
                differentiators=[{"id": "b_purity", "text": "High purity powder"}],
                cta_lines=["Visit hrsuindore.com"],
            )

        fake_module.load_brand_facts = load_brand_facts
        monkeypatch.setitem(sys.modules, "shorts_engine.brand", fake_module)
        monkeypatch.setattr(facts, "wrap_sentences_into_facts", self._good_fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        run(ctx)
        payload = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
        assert payload["brand_facts"] == [
            {"id": "b_purity", "text": "High purity powder", "kind": "differentiator"},
            {"id": "cta1", "text": "Visit hrsuindore.com", "kind": "cta"},
        ]

    def test_full_chain_through_real_wrap_with_monkeypatched_text_llm(
        self, tmp_path, monkeypatch
    ) -> None:
        """Mirrors the eventual full-pipeline integration test: monkeypatch
        text_llm.generate_schema_json (not wrap_sentences_into_facts itself) and
        let run() drive the real wrap -> gate pipeline end to end."""

        def router(prompt, system, schema, **kw):
            assert schema is FACT_WRAP_SCHEMA
            return {"facts": [{
                "id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
                "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1,
            }]}

        monkeypatch.setattr(text_llm, "generate_schema_json", router)
        ctx = self._ctx(tmp_path, CANON)
        result = run(ctx)
        assert result == {"factsheet": "factsheet.json"}
        payload = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
        assert payload["facts"][0]["id"] == "f1"

    def test_factsheet_json_preserves_non_ascii_characters(self, tmp_path, monkeypatch) -> None:
        def fake_wrap(sentences, post_meta, *, local_only=False, llm=None):
            return [{"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                     "value": "1.5-3", "unit": "kg/m3",
                     "claim_summary": "Café quality – verified",
                     "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1}]

        monkeypatch.setattr(facts, "wrap_sentences_into_facts", fake_wrap)
        ctx = self._ctx(tmp_path, CANON)
        run(ctx)
        raw = (ctx.workspace / "factsheet.json").read_text(encoding="utf-8")
        assert "Café" in raw
        assert "\\u00e9" not in raw


class TestFactsAgainstRealFixture:
    """Sanity checks tying the FACTS-stage pure functions to what INGEST actually
    produces from the real (poisoned) Blogger fixture."""

    FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"

    def _real_canonical(self) -> str:
        html = self.FIXTURE.read_text(encoding="utf-8")
        return isolate_post(html, URL).canonical_text

    def test_mine_candidate_sentences_finds_the_real_dosage_sentence(self) -> None:
        cands = mine_candidate_sentences(self._real_canonical())
        assert any("1.5 to 3 kg" in c for c in cands)

    def test_mine_candidate_sentences_never_surfaces_sibling_poison(self) -> None:
        cands = mine_candidate_sentences(self._real_canonical())
        assert all("150,000 metric tons" not in c for c in cands)

    def test_locate_verbatim_against_real_canonical_text(self) -> None:
        canonical = self._real_canonical()
        quote = "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume"
        assert locate_verbatim(quote, canonical) is not None

    def test_gate_facts_drops_poison_and_keeps_real_fact_against_real_canonical(self) -> None:
        canonical = self._real_canonical()
        entries = [
            {"id": "f1",
             "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume",
             "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
             "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1},
            {"id": "f2", "verbatim_quote": "approximately 150,000 metric tons",
             "value": "150000", "unit": "t", "claim_summary": "sibling poison",
             "tags": ["metric"], "procurement_significance": 3, "citation_marker": None},
        ]
        kept, dropped = gate_facts(entries, canonical)
        assert [k["id"] for k in kept] == ["f1"]
        assert [d["id"] for d in dropped] == ["f2"]
