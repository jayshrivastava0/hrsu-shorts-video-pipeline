"""Tests for Cinematographer — flat {"scenes":[...]} JSON schema."""
import pytest
from unittest.mock import MagicMock
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, HeroClaim, CriticNotes,
)
from video_agent.agents.cinematographer import Cinematographer


def _make_sb(n_scenes: int = 3) -> Storyboard:
    sb = Storyboard(version="2.2", blog={"region": "australia", "category": "mining"})
    sb.hero_claim = HeroClaim(stat="$2B/yr", claim_text="Calcium nitrate cuts AMD costs")
    beats = ["hook", "mechanism", "cta"]
    moods = ["problem", "mechanism", "brand"]
    for i in range(n_scenes):
        s = Scene(
            index=i,
            beat=beats[i % len(beats)],
            narration=f"Scene {i} narration about calcium nitrate treatment.",
            on_screen_text="",
            duration_target_s=6.0,
            visual_concept=VisualConcept(
                subject="calcium nitrate", modifier="treatment",
                type="photo", mood=moods[i % len(moods)],
            ),
            critic_notes=CriticNotes(),
        )
        sb.scenes.append(s)
    return sb


def _mock_client(json_response: dict):
    client = MagicMock()
    client.generate_json.return_value = json_response
    return client


def test_valid_flat_schema_applied():
    """Cinematographer applies color_grade, transition_in, motion, voice_prosody from flat schema."""
    sb = _make_sb(3)
    json_resp = {
        "scenes": [
            {"index": 0, "color_grade": "red_tension",   "transition_in": "hard_cut",
             "motion": "fast_zoom",  "voice_prosody": "hook_emphasis"},
            {"index": 1, "color_grade": "cold_blue",     "transition_in": "slow_fade",
             "motion": "hold",       "voice_prosody": "matter_of_fact"},
            {"index": 2, "color_grade": "warm_brand",    "transition_in": "slide_left",
             "motion": "slow_push",  "voice_prosody": "warm_cta"},
        ]
    }
    client = _mock_client(json_resp)
    Cinematographer(client=client).run(sb)

    assert sb.scenes[0].cinematography.color_grade   == "red_tension"
    assert sb.scenes[0].cinematography.transition_in == "hard_cut"
    assert sb.scenes[0].cinematography.motion        == "fast_zoom"
    assert sb.scenes[0].cinematography.voice_prosody == "hook_emphasis"

    assert sb.scenes[1].cinematography.color_grade   == "cold_blue"
    assert sb.scenes[2].cinematography.color_grade   == "warm_brand"
    assert sb.scenes[2].cinematography.transition_in == "slide_left"


def test_invalid_value_ignored_keeps_default():
    """Out-of-vocabulary values are silently ignored; field stays at default."""
    sb = _make_sb(1)
    json_resp = {
        "scenes": [
            {"index": 0, "color_grade": "not_a_palette", "transition_in": "slow_fade",
             "motion": "hold", "voice_prosody": "conversational"},
        ]
    }
    Cinematographer(client=_mock_client(json_resp)).run(sb)
    assert sb.scenes[0].cinematography.color_grade == "neutral_doc"   # stayed default
    assert sb.scenes[0].cinematography.transition_in == "slow_fade"   # valid field applied


def test_llm_failure_leaves_defaults():
    """If generate_json raises OllamaError, all scenes keep default Cinematography."""
    from video_agent.ollama_client import OllamaError
    sb = _make_sb(2)
    client = MagicMock()
    client.generate_json.side_effect = OllamaError("timeout")
    Cinematographer(client=client).run(sb)
    for s in sb.scenes:
        assert s.cinematography is not None
        assert s.cinematography.color_grade == "neutral_doc"


def test_malformed_response_leaves_defaults():
    """If response has no 'scenes' key, all scenes keep default Cinematography."""
    sb = _make_sb(2)
    Cinematographer(client=_mock_client({"wrong_key": []})).run(sb)
    for s in sb.scenes:
        assert s.cinematography.color_grade == "neutral_doc"


def test_out_of_range_index_ignored():
    """Scene index outside storyboard range is silently dropped."""
    sb = _make_sb(2)
    json_resp = {
        "scenes": [
            {"index": 99, "color_grade": "red_tension", "transition_in": "cut",
             "motion": "hold", "voice_prosody": "conversational"},
        ]
    }
    Cinematographer(client=_mock_client(json_resp)).run(sb)
    # No crash, defaults unchanged
    for s in sb.scenes:
        assert s.cinematography.color_grade == "neutral_doc"
