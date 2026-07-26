"""Coverage for the data-coercion / footage-routing changes in dispatcher."""
from pathlib import Path
from PIL import Image
from video_agent.visual_engine.dispatcher import (
    generate_visual, _normalize_chart_data, _coerce_number,
)


def test_coerce_number_handles_units_and_strings():
    assert _coerce_number("15%") == 15.0
    assert _coerce_number("1 Billion AUD") == 1.0
    assert _coerce_number(42) == 42.0
    assert _coerce_number("nope") is None


def test_normalize_bar_accepts_labelN_valueN():
    data = {"label1": "Cost Savings", "value1": "15%",
            "label2": "Industry Impact", "value2": "1 Billion AUD"}
    out = _normalize_chart_data("bar", data)
    assert out is not None
    assert out["labels"] == ["Cost Savings", "Industry Impact"]
    assert out["values"] == [15.0, 1.0]


def test_normalize_bar_rejects_empty():
    assert _normalize_chart_data("bar", {}) is None
    assert _normalize_chart_data("bar", {"labels": [], "values": []}) is None


def test_normalize_callout_stat_maps_aliases():
    data = {"value1": "90%", "unit": "% Reduction", "concentration": "50 mg/L"}
    out = _normalize_chart_data("callout_stat", data)
    assert out is not None
    assert out["value"] == "90%"
    assert "Reduction" in out["label"]


def test_infographic_with_garbage_data_falls_back_to_text_card(tmp_path):
    scene = {"index": 0, "visual_type": "infographic",
             "visual_spec": {"chart_type": "bar", "data": {"foo": "bar"}},
             "narration": "x", "on_screen_text": "FALLBACK"}
    out = generate_visual(scene, tmp_path / "x.png")
    assert out["generator_used"] == "text_card"
    assert Image.open(out["asset_path"]).size == (1080, 1920)


def test_text_card_result_has_is_static_true(tmp_path):
    scene = {"index": 0, "visual_type": "text_card",
             "visual_spec": {"layout": "hook"}, "on_screen_text": "HOOK"}
    out = generate_visual(scene, tmp_path / "x.png")
    assert out.get("is_static") is True


def test_infographic_bar_result_has_is_static_true(tmp_path):
    # Bar charts have axes+labels that break under pan — must be static.
    scene = {"index": 0, "visual_type": "infographic",
             "visual_spec": {"chart_type": "bar",
                             "data": {"labels": ["A", "B"], "values": [10, 20]}},
             "narration": "x", "on_screen_text": "STATS"}
    out = generate_visual(scene, tmp_path / "x.png")
    assert out["generator_used"] == "infographic"
    assert out.get("is_static") is True


def test_infographic_callout_result_has_is_static_true(tmp_path):
    scene = {"index": 0, "visual_type": "infographic",
             "visual_spec": {"chart_type": "callout_stat",
                             "data": {"value": "90%", "label": "Reduction"}},
             "narration": "x", "on_screen_text": "90%"}
    out = generate_visual(scene, tmp_path / "x.png")
    assert out["generator_used"] == "infographic"
    assert out.get("is_static") is True


def test_callout_accepts_arbitrary_first_numeric_key():
    data = {"h2s_reduction": "90%", "volume": "1 liter", "concentration": "50 mg/L"}
    out = _normalize_chart_data("callout_stat", data)
    assert out is not None
    assert "90" in out["value"]
    assert "reduction" in out["label"].lower()


def test_bar_extracts_pairs_from_arbitrary_keys():
    data = {"chemical_cost_savings": "15%", "industry_impact": "1 Billion AUD"}
    out = _normalize_chart_data("bar", data)
    assert out is not None
    assert len(out["values"]) == 2
    assert out["labels"][0] == "Chemical Cost Savings"


def test_bar_rejects_single_pair_via_arbitrary_keys():
    data = {"only_one_metric": "42%"}
    out = _normalize_chart_data("bar", data)
    assert out is None


def test_callout_rejects_when_no_numeric_value_anywhere():
    assert _normalize_chart_data("callout_stat", {"foo": "bar"}) is None


def test_comparison_falls_back_to_first_two_pairs():
    data = {"before_treatment": "100 ppm", "after_treatment": "10 ppm"}
    out = _normalize_chart_data("comparison", data)
    assert out is not None
    assert "left_value" in out
    assert "right_value" in out


def test_stock_visual_no_manifest_falls_back_to_text_card(tmp_path, monkeypatch):
    # Force factory_broll to return None.
    monkeypatch.setattr("video_agent.visual_engine.dispatcher.find_footage",
                        lambda scene: None)
    scene = {"index": 0, "visual_type": "stock",
             "visual_spec": {"query": "factory shot"},
             "on_screen_text": "FACTORY"}
    out = generate_visual(scene, tmp_path / "x.png")
    assert out["generator_used"] == "text_card"
