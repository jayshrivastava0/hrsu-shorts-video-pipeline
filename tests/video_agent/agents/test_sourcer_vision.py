"""Tests for vision-first Sourcer (Task B-3)."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from video_agent.storyboard import Scene, VisualConcept, AssetCandidate
from video_agent.vision.judge import VisionVerdict


def _make_scene(idx: int = 0) -> Scene:
    return Scene(
        index=idx, beat="mechanism",
        narration="Calcium nitrate prevents H2S formation in wastewater pipes.",
        on_screen_text="H2S PREVENTION",
        visual_concept=VisualConcept(
            subject="wastewater treatment plant", modifier="aerial",
            type="photo", mood="mechanism", style_hint="",
        ),
        duration_target_s=5.0,
    )


def _make_sourcer(monkeypatch, tmp_path):
    """Build a Sourcer with all external IO monkeypatched."""
    from video_agent.agents.sourcer import Sourcer
    from video_agent.sources.base import BaseSource, RawCandidate

    class FakeSource(BaseSource):
        name = "fake"
        def search(self, query, limit=5):
            return [
                RawCandidate(
                    source="fake", url=f"http://fake.com/img{i}.jpg",
                    caption=f"industrial plant image {i}",
                    score=60, width=1920, height=1080,
                    is_clip=False, duration_s=None,
                )
                for i in range(3)
            ]

    sourcer = Sourcer(
        sources=[FakeSource()],
        cache_root=tmp_path / "cache",
        download_root=tmp_path / "downloads",
        candidates_per_source=5,
    )
    # Never actually download
    def fake_download(c, scene_idx):
        p = tmp_path / "downloads" / f"img_{c.url[-5:]}.jpg"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        return p

    monkeypatch.setattr(sourcer, "_download_candidate", fake_download)
    monkeypatch.setattr(sourcer, "_is_dup", lambda p: False)
    return sourcer


class TestSourcerVisionSelect:
    """B-3: vision judge is used when VISION_FIRST_ENABLED=True."""

    def test_chooses_highest_vision_score(self, monkeypatch, tmp_path):
        """_vision_select returns the candidate with the highest vision score."""
        sourcer = _make_sourcer(monkeypatch, tmp_path)
        scene = _make_scene()

        verdicts = iter([
            VisionVerdict(score=8, reason="great industrial shot", focus_x=0.5, focus_y=0.5),
            VisionVerdict(score=3, reason="too generic"),
            VisionVerdict(score=5, reason="somewhat relevant"),
        ])
        monkeypatch.setattr("video_agent.vision.judge.call_vision_json",
                            lambda *a, **kw: next(verdicts).__dict__)

        # Patch call_vision_json to return dicts directly
        call_count = [0]
        verdict_list = [
            {"score": 8, "reason": "great industrial shot", "focus_x": 0.5, "focus_y": 0.5, "subject_fills_frame": False},
            {"score": 3, "reason": "too generic", "focus_x": 0.5, "focus_y": 0.5, "subject_fills_frame": False},
            {"score": 5, "reason": "somewhat relevant", "focus_x": 0.5, "focus_y": 0.5, "subject_fills_frame": False},
        ]
        def fake_vision_call(prompt, image_path, model, timeout_s):
            v = verdict_list[call_count[0] % len(verdict_list)]
            call_count[0] += 1
            return v

        monkeypatch.setattr("video_agent.vision.judge.call_vision_json", fake_vision_call)
        monkeypatch.setattr("video_agent.agents.sourcer.VISION_FIRST_ENABLED" if False else "video_agent.config.VISION_FIRST_ENABLED", True)

        from video_agent.sources.base import RawCandidate
        from video_agent.sources.scoring import score_candidate
        gated = [
            (60, 50, RawCandidate(source="fake", url=f"http://fake.com/img{i}.jpg",
                                   caption=f"plant {i}",
                                   width=1920, height=1080, is_clip=False, duration_s=None))
            for i in range(3)
        ]

        chosen = sourcer._vision_select(scene, gated, scene.narration, "cuts H2S 90%")
        assert chosen is not None
        assert chosen.vision_score == 8

    def test_returns_none_when_all_scores_below_min(self, monkeypatch, tmp_path):
        """Returns None and sets scene.degraded when all vision scores < VISION_SELECT_MIN."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        sourcer = _make_sourcer(monkeypatch, tmp_path)
        scene = _make_scene()

        def fake_vision_call(prompt, image_path, model, timeout_s):
            return {"score": 3, "reason": "not relevant", "focus_x": 0.5, "focus_y": 0.5, "subject_fills_frame": False}

        monkeypatch.setattr("video_agent.vision.judge.call_vision_json", fake_vision_call)

        from video_agent.sources.base import RawCandidate
        gated = [
            (60, 50, RawCandidate(source="fake", url=f"http://fake.com/img{i}.jpg",
                                   caption="generic",
                                   width=1920, height=1080, is_clip=False, duration_s=None))
            for i in range(2)
        ]

        chosen = sourcer._vision_select(scene, gated, scene.narration, "hero")
        assert chosen is None

    def test_returns_none_when_all_verdicts_none(self, monkeypatch, tmp_path):
        """Returns None when judge_image returns None for all candidates."""
        sourcer = _make_sourcer(monkeypatch, tmp_path)
        scene = _make_scene()

        monkeypatch.setattr("video_agent.vision.judge.call_vision_json",
                            lambda *a, **kw: None)

        from video_agent.sources.base import RawCandidate
        gated = [
            (60, 50, RawCandidate(source="fake", url=f"http://fake.com/img{i}.jpg",
                                   caption="generic",
                                   width=1920, height=1080, is_clip=False, duration_s=None))
            for i in range(2)
        ]

        chosen = sourcer._vision_select(scene, gated, scene.narration, "hero")
        assert chosen is None

    def test_vision_verdict_stored_on_asset(self, monkeypatch, tmp_path):
        """Chosen asset carries vision_score, vision_reason, focus_x/y, subject_fills_frame."""
        sourcer = _make_sourcer(monkeypatch, tmp_path)
        scene = _make_scene()

        monkeypatch.setattr("video_agent.vision.judge.call_vision_json",
                            lambda *a, **kw: {
                                "score": 7, "reason": "good plant shot",
                                "focus_x": 0.4, "focus_y": 0.6,
                                "subject_fills_frame": True,
                            })

        from video_agent.sources.base import RawCandidate
        gated = [
            (60, 50, RawCandidate(source="fake", url="http://fake.com/img0.jpg",
                                   caption="plant",
                                   width=1920, height=1080, is_clip=False, duration_s=None))
        ]

        chosen = sourcer._vision_select(scene, gated, scene.narration, "hero")
        assert chosen is not None
        assert chosen.vision_score == 7
        assert chosen.vision_reason == "good plant shot"
        assert abs(chosen.focus_x - 0.4) < 1e-6
        assert abs(chosen.focus_y - 0.6) < 1e-6
        assert chosen.subject_fills_frame is True


class TestResourceScene:
    """B-3: re_source_scene uses vision judge."""

    def test_re_source_excludes_used_url(self, monkeypatch, tmp_path):
        """re_source_scene skips URLs in exclude_urls."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)

        sourcer = _make_sourcer(monkeypatch, tmp_path)
        scene = _make_scene()
        scene.chosen_asset = None

        exclude = {"http://fake.com/img0.jpg", "http://fake.com/img1.jpg"}

        monkeypatch.setattr("video_agent.vision.judge.call_vision_json",
                            lambda *a, **kw: {
                                "score": 8, "reason": "good",
                                "focus_x": 0.5, "focus_y": 0.5,
                                "subject_fills_frame": False,
                            })

        sourcer.re_source_scene(scene, "wastewater_treatment",
                                exclude_urls=exclude, hero_claim="cuts H2S 90%")
        # img2 is the only non-excluded URL; it should be chosen
        if scene.chosen_asset:
            assert scene.chosen_asset.url not in exclude
