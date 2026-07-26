"""Tests for vision-indexed footage source (Task B-4)."""
from __future__ import annotations
from pathlib import Path
import pytest

from video_agent.vision.footage_index import (
    FootageClip, discover_clips, best_footage_for_scene,
)
from video_agent.vision.judge import VisionVerdict


def _make_clip(tmp_path, name: str, idx: int) -> FootageClip:
    p = tmp_path / f"{name}.mp4"
    p.write_bytes(b"fake video")
    rep = tmp_path / f"{name}.jpg"
    rep.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return FootageClip(path=p, duration_s=10.0, rep_frame=rep)


class TestBestFootageForScene:

    def test_returns_highest_scoring_clip(self, monkeypatch, tmp_path):
        """Returns the clip with the highest vision score."""
        clips = [_make_clip(tmp_path, f"clip{i}", i) for i in range(2)]

        scores = iter([4, 8])
        def fake_judge(image_path, narration, beat="", hero_claim="", visual_subject="", **kw):
            s = next(scores)
            return VisionVerdict(score=s, reason=f"score {s}")
        monkeypatch.setattr("video_agent.vision.footage_index.judge_image", fake_judge)

        result = best_footage_for_scene("narration text", "mechanism", "hero", "plant", clips=clips)
        assert result is not None
        clip, verdict = result
        assert verdict.score == 8

    def test_tiebreak_favors_earlier_dir(self, monkeypatch, tmp_path):
        """When two clips tie on vision score, the earlier-indexed clip wins."""
        clips = [_make_clip(tmp_path, f"clip{i}", i) for i in range(2)]

        def fake_judge(image_path, narration, beat="", hero_claim="", visual_subject="", **kw):
            return VisionVerdict(score=7, reason="tied")
        monkeypatch.setattr("video_agent.vision.footage_index.judge_image", fake_judge)

        result = best_footage_for_scene("narration", "hook", "hero", "plant", clips=clips)
        assert result is not None
        clip, verdict = result
        # clip at idx=0 wins the tie (lower idx = earlier dir = higher trust)
        assert clip.path == clips[0].path

    def test_returns_none_when_no_clips(self, monkeypatch):
        """Returns None when clip list is empty."""
        result = best_footage_for_scene("narration", "hook", "hero", "plant", clips=[])
        assert result is None

    def test_returns_none_when_all_verdicts_none(self, monkeypatch, tmp_path):
        """Returns None when judge_image returns None for all clips."""
        clips = [_make_clip(tmp_path, "clip0", 0)]
        monkeypatch.setattr("video_agent.vision.footage_index.judge_image",
                            lambda *a, **kw: None)
        result = best_footage_for_scene("narration", "hook", "hero", "plant", clips=clips)
        assert result is None

    def test_discover_clips_returns_empty_when_dirs_missing(self, monkeypatch):
        """discover_clips() returns [] when FOOTAGE_DIRS don't exist."""
        import video_agent.vision.footage_index as fi
        monkeypatch.setattr(fi, "FOOTAGE_DIRS", [Path("/nonexistent/factory")])
        result = discover_clips()
        assert result == []
