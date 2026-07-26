from pathlib import Path
from video_agent.harness.rubric import (
    DEFAULT_CRITERIA, write_rubric, load_rubric,
)


def test_default_criteria_complete():
    keys = {c["key"] for c in DEFAULT_CRITERIA}
    assert {"visual_match", "readability", "framing",
            "brand_safety", "coherence"} <= keys
    for c in DEFAULT_CRITERIA:
        assert c["description"]          # every criterion is explained


def test_write_then_load_roundtrip(tmp_path: Path):
    p = write_rubric(tmp_path, hero_claim="25% strength boost")
    assert p.exists()
    rub = load_rubric(tmp_path)
    assert rub["hero_claim"] == "25% strength boost"
    assert rub["criteria"] == DEFAULT_CRITERIA


def test_load_missing_returns_default(tmp_path: Path):
    rub = load_rubric(tmp_path)               # nothing written
    assert rub["criteria"] == DEFAULT_CRITERIA
    assert rub["hero_claim"] == ""
