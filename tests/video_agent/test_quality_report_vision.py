"""Tests for D-2: quality_report.json includes vision provenance fields."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, AssetCandidate, HeroClaim,
)


def _make_storyboard(tmp_path: Path, vision_score: int = 7,
                     source: str = "unsplash") -> Storyboard:
    scene = Scene(
        index=0, beat="mechanism",
        narration="Calcium nitrate prevents H2S.",
        on_screen_text="H2S CONTROL",
        visual_concept=VisualConcept(
            subject="wastewater plant", modifier="aerial",
            type="photo", mood="mechanism", style_hint="",
        ),
        duration_target_s=5.0,
    )
    scene.chosen_asset = AssetCandidate(
        source=source, url=f"http://fake.com/img.jpg", score=80,
        local_path=str(tmp_path / "img.jpg"),
        caption="wastewater treatment plant",
        width=1920, height=1080, is_clip=False, duration_s=None,
        vision_score=vision_score, vision_reason="good industrial shot",
    )
    scene._framing = "kenburns"
    sb = Storyboard(version="2", blog={"category": "wastewater_treatment"},
                    hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"))
    sb.scenes = [scene]
    return sb


class TestQualityReportVisionProvenance:

    def test_vision_score_in_report(self, tmp_path):
        """quality_report includes vision_score per scene."""
        from video_agent.composer import _write_quality_report
        sb = _make_storyboard(tmp_path, vision_score=8)
        _write_quality_report(sb, tmp_path, voice_duration=30.0,
                              video_duration=32.0, outro_concatenated=True)
        report = json.loads((tmp_path / "quality_report.json").read_text())
        scene_data = report["scenes"][0]
        assert scene_data["vision_score"] == 8

    def test_is_own_footage_true_for_own_footage(self, tmp_path):
        """is_own_footage=True when source is 'own_footage'."""
        from video_agent.composer import _write_quality_report
        sb = _make_storyboard(tmp_path, source="own_footage")
        _write_quality_report(sb, tmp_path, voice_duration=30.0,
                              video_duration=32.0, outro_concatenated=True)
        report = json.loads((tmp_path / "quality_report.json").read_text())
        assert report["scenes"][0]["is_own_footage"] is True

    def test_is_own_footage_false_for_stock(self, tmp_path):
        """is_own_footage=False when source is not 'own_footage'."""
        from video_agent.composer import _write_quality_report
        sb = _make_storyboard(tmp_path, source="pexels")
        _write_quality_report(sb, tmp_path, voice_duration=30.0,
                              video_duration=32.0, outro_concatenated=True)
        report = json.loads((tmp_path / "quality_report.json").read_text())
        assert report["scenes"][0]["is_own_footage"] is False

    def test_framing_field_present(self, tmp_path):
        """framing field reflects scene._framing ('kenburns', 'blurfill', etc.)."""
        from video_agent.composer import _write_quality_report
        sb = _make_storyboard(tmp_path)
        sb.scenes[0]._framing = "blurfill"
        _write_quality_report(sb, tmp_path, voice_duration=30.0,
                              video_duration=32.0, outro_concatenated=True)
        report = json.loads((tmp_path / "quality_report.json").read_text())
        assert report["scenes"][0]["framing"] == "blurfill"

    def test_vision_score_null_for_degraded_scene(self, tmp_path):
        """vision_score is null when scene has no chosen_asset."""
        from video_agent.composer import _write_quality_report
        sb = _make_storyboard(tmp_path)
        sb.scenes[0].chosen_asset = None
        sb.scenes[0].degraded = True
        _write_quality_report(sb, tmp_path, voice_duration=30.0,
                              video_duration=32.0, outro_concatenated=True)
        report = json.loads((tmp_path / "quality_report.json").read_text())
        assert report["scenes"][0]["vision_score"] is None
        assert report["scenes"][0]["is_own_footage"] is False
