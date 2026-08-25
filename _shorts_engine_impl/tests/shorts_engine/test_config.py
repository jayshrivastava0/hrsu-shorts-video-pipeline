"""Test config constants for Purpose-based pacing and duration."""
from __future__ import annotations
from shorts_engine import config


def test_purpose_template_covers_all_purposes():
    for purpose in ("hook", "stakes", "mechanism", "proof", "cta", "other"):
        assert purpose in config.PURPOSE_TEMPLATE
        spec = config.PURPOSE_TEMPLATE[purpose]
        assert spec["min_s"] > 0
        assert spec["max_s"] >= spec["min_s"]


def test_total_min_s_is_30_and_no_ceiling_constant_remains():
    assert config.TOTAL_MIN_S == 30.0
    assert not hasattr(config, "TOTAL_MAX_S")
    assert not hasattr(config, "BEAT_TEMPLATE")


def test_min_beats_is_three():
    assert config.MIN_BEATS == 3
