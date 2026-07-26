"""Tests for AssetCandidate vision-judge fields (Task B-1)."""
from __future__ import annotations
import json
from pathlib import Path

from video_agent.storyboard import (
    AssetCandidate, Scene, VisualConcept, Storyboard,
    save_storyboard, load_storyboard, _scene_from_dict,
)


def _make_scene(chosen_asset: AssetCandidate | None = None) -> Scene:
    return Scene(
        index=0, beat="hook",
        narration="Calcium nitrate cuts H2S 90%.",
        on_screen_text="90% H2S CUT",
        visual_concept=VisualConcept(
            subject="wastewater plant", modifier="aerial",
            type="photo", mood="problem", style_hint="",
        ),
        duration_target_s=4.0,
        chosen_asset=chosen_asset,
    )


def test_round_trip_with_vision_fields(tmp_path):
    """Vision-judge fields survive save/load round-trip."""
    ac = AssetCandidate(
        source="google_images", url="http://example.com/img.jpg",
        score=80, local_path="/tmp/img.jpg",
        vision_score=8, vision_reason="shows wastewater plant clearly",
        focus_x=0.3, focus_y=0.7, subject_fills_frame=True,
    )
    sb = Storyboard(
        version="2.0",
        blog={"id": "b1", "url": "u", "title": "t", "region": "usa",
              "category": "wastewater_treatment", "persona": "procurement"},
        scenes=[_make_scene(chosen_asset=ac)],
    )
    path = tmp_path / "sb.json"
    save_storyboard(sb, path)
    sb2 = load_storyboard(path)

    ac2 = sb2.scenes[0].chosen_asset
    assert ac2 is not None
    assert ac2.vision_score == 8
    assert ac2.vision_reason == "shows wastewater plant clearly"
    assert abs(ac2.focus_x - 0.3) < 1e-6
    assert abs(ac2.focus_y - 0.7) < 1e-6
    assert ac2.subject_fills_frame is True


def test_backward_compat_missing_vision_fields():
    """Old storyboard dicts without vision fields load with correct defaults."""
    old_dict = {
        "index": 0, "beat": "hook",
        "narration": "test narration",
        "on_screen_text": "TEST",
        "visual_concept": {
            "subject": "plant", "modifier": "aerial",
            "type": "photo", "mood": "problem", "style_hint": "",
        },
        "duration_target_s": 3.0,
        "transition_in": "cut",
        "asset_candidates": [],
        "chosen_asset": {
            "source": "google_images",
            "url": "http://example.com/img.jpg",
            "score": 70,
            "local_path": "/tmp/img.jpg",
            # no vision fields — old storyboard
        },
        "motion": {"type": "ken_burns", "direction": "in", "speed_px_per_frame": 0.6},
        "critic_notes": {"alignment_score": 10, "flags": [], "revision": None},
        "degraded": False,
    }
    scene = _scene_from_dict(old_dict)
    ac = scene.chosen_asset
    assert ac is not None
    assert ac.vision_score == -1
    assert ac.vision_reason == ""
    assert abs(ac.focus_x - 0.5) < 1e-6
    assert abs(ac.focus_y - 0.5) < 1e-6
    assert ac.subject_fills_frame is False


def test_default_vision_score_is_negative_one():
    """Default vision_score == -1 means 'not judged'."""
    ac = AssetCandidate(
        source="bing", url="http://x.com/img.jpg",
        score=50, local_path="/tmp/x.jpg",
    )
    assert ac.vision_score == -1
    assert ac.focus_x == 0.5
    assert ac.focus_y == 0.5
    assert ac.subject_fills_frame is False
