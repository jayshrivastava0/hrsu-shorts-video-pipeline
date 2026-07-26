import pytest
from unittest.mock import patch, MagicMock
from video_agent.script_builder import (
    extract_facts, ScriptBuilderError, _tier2_regex_facts,
)


BLOG_NUMERIC = {
    "blog_id": "n1",
    "category": "wastewater_treatment",
    "content_html": "Calcium nitrate cuts H₂S by 90%. Doses of 50 mg/L work in 24 hours.",
    "summary": "summary",
}

BLOG_THIN = {
    "blog_id": "thin1",
    "category": "wastewater_treatment",
    "content_html": "<p>Wastewater is treated. Outcomes vary.</p>",
    "summary": "thin",
}

BLOG_NO_CATEGORY = {
    "blog_id": "x",
    "category": "non_existent_category",
    "content_html": "<p>nothing</p>",
    "summary": "x",
}


def test_tier2_regex_finds_numerics():
    text = "We saw 90% improvement, dosing 50 mg/L over 24 hours and 5°C drop."
    facts = _tier2_regex_facts(text)
    assert len(facts) >= 4
    assert any("90" in f["claim"] for f in facts)
    assert any("mg/L" in f["claim"] for f in facts)


def test_tier1_used_when_ollama_returns_three_plus():
    fake = [
        {"value": "90", "unit": "%", "claim": "H2S cut 90%", "source_quote": "..."},
        {"value": "50", "unit": "mg/L", "claim": "dose 50 mg/L", "source_quote": "..."},
        {"value": "24", "unit": "hours", "claim": "in 24 hours", "source_quote": "..."},
    ]
    with patch("video_agent.script_builder._tier1_ollama_numeric",
               return_value=fake):
        facts, meta = extract_facts(BLOG_NUMERIC)
    assert meta["tier_used"] == 1
    assert meta["numeric_count"] == 3
    assert len(facts) >= 3


def test_tier2_kicks_in_when_tier1_too_few():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]):
        facts, meta = extract_facts(BLOG_NUMERIC)
    assert meta["tier_used"] == 2
    assert len(facts) >= 3


def test_tier3_qualitative_when_no_numerics():
    qualitative = ["a fact", "b fact", "c fact", "d fact"]
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative",
               return_value=qualitative):
        facts, meta = extract_facts(BLOG_THIN)
    assert meta["tier_used"] == 3
    assert meta["punch_points_count"] >= 3


def test_tier4_template_fallback():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative", return_value=[]):
        facts, meta = extract_facts(BLOG_THIN)
    assert meta["tier_used"] == 4
    assert meta["fell_back_to_template"] is True
    assert len(facts) >= 3


def test_tier5_aborts_when_no_template():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative", return_value=[]):
        with pytest.raises(ScriptBuilderError, match="no fact extraction tier"):
            extract_facts(BLOG_NO_CATEGORY)
