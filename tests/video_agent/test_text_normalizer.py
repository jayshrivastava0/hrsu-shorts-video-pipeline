import pytest
from video_agent.text_normalizer import normalize_for_tts


def test_h2s_with_subscript():
    assert normalize_for_tts("H₂S levels") == "H 2 S levels"

def test_h2s_plain():
    assert normalize_for_tts("H2S levels") == "H 2 S levels"

def test_calcium_nitrate_formula():
    assert normalize_for_tts("Use Ca(NO3)2 today") == "Use calcium nitrate today"

def test_calcium_nitrate_subscript():
    assert normalize_for_tts("Ca(NO₃)₂ dosing") == "calcium nitrate dosing"

def test_co2_variants():
    assert normalize_for_tts("CO₂ rose; CO2 fell") == "C O 2 rose; C O 2 fell"

def test_units_replaced():
    assert normalize_for_tts("50 mg/L and 2 kg/t") == \
        "50 milligrams per liter and 2 kilograms per tonne"

def test_percent():
    assert normalize_for_tts("90% reduction") == "90 percent reduction"

def test_celsius():
    assert normalize_for_tts("at 25°C exactly") == "at 25 degrees Celsius exactly"

def test_domain_spelled_out():
    assert "H R S U Indore dot com" in normalize_for_tts("Visit hrsuindore.com today")

def test_strips_citations():
    assert normalize_for_tts("This[1] is fact[2].") == "This is fact."

def test_strips_markdown_bold_italic():
    assert normalize_for_tts("**bold** and *italic*") == "bold and italic"

def test_collapses_whitespace():
    assert normalize_for_tts("a   b\t\tc") == "a b c"

def test_combined():
    src = "Use **Ca(NO3)2** at 50 mg/L[1] to cut H₂S by 90%."
    out = normalize_for_tts(src)
    assert "calcium nitrate" in out
    assert "milligrams per liter" in out
    assert "[1]" not in out
    assert "**" not in out
    assert "H 2 S" in out
    assert "90 percent" in out
