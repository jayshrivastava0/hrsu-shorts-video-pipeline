"""Tests for B-5: own-footage preference wired into Sourcer._source_scene."""
from __future__ import annotations
from pathlib import Path
import pytest

from video_agent.storyboard import Scene, VisualConcept, AssetCandidate
from video_agent.vision.judge import VisionVerdict
from video_agent.vision.footage_index import FootageClip


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


def _make_sourcer(tmp_path):
    from video_agent.agents.sourcer import Sourcer
    from video_agent.sources.base import BaseSource, RawCandidate

    class FakeSource(BaseSource):
        name = "fake"
        def search(self, query, limit=5):
            return []

    return Sourcer(
        sources=[FakeSource()],
        cache_root=tmp_path / "cache",
        download_root=tmp_path / "downloads",
    )


def _make_clip(tmp_path, name: str) -> FootageClip:
    p = tmp_path / f"{name}.mp4"
    p.write_bytes(b"fake video")
    rep = tmp_path / f"{name}.jpg"
    rep.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return FootageClip(path=p, duration_s=12.0, rep_frame=rep)


class TestSourcerFootagePreference:

    def test_own_footage_chosen_when_score_meets_min(self, monkeypatch, tmp_path):
        """When own-footage vision score >= FOOTAGE_PREFER_MIN, scene is filled
        without touching the web pipeline."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "FOOTAGE_PREFER_MIN", 5)

        clips = [_make_clip(tmp_path, "factory_clip")]
        good_verdict = VisionVerdict(score=7, reason="factory floor")

        monkeypatch.setattr(
            "video_agent.agents.sourcer.best_footage_for_scene",
            lambda narration, beat, hero, subject, clips=None: (clips[0], good_verdict),
        )

        sourcer = _make_sourcer(tmp_path)
        sourcer._footage_clips = clips

        scene = _make_scene()
        sourcer._source_scene(scene, "wastewater_treatment", hero_claim="cuts H2S")

        assert scene.chosen_asset is not None
        assert scene.chosen_asset.source == "own_footage"
        assert scene.chosen_asset.vision_score == 7
        assert scene.chosen_asset.is_clip is True

    def test_own_footage_skipped_when_score_below_min(self, monkeypatch, tmp_path):
        """When footage score < FOOTAGE_PREFER_MIN, fall through to web pipeline."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "FOOTAGE_PREFER_MIN", 5)

        clips = [_make_clip(tmp_path, "generic_clip")]
        weak_verdict = VisionVerdict(score=3, reason="too generic")

        monkeypatch.setattr(
            "video_agent.agents.sourcer.best_footage_for_scene",
            lambda narration, beat, hero, subject, clips=None: (clips[0], weak_verdict),
        )
        # Web pipeline also finds nothing — scene stays degraded
        monkeypatch.setattr(
            "video_agent.agents.sourcer.Sourcer._search_all_sources",
            lambda self, q: [],
        )

        sourcer = _make_sourcer(tmp_path)
        sourcer._footage_clips = clips
        scene = _make_scene()
        sourcer._source_scene(scene, "wastewater_treatment", hero_claim="cuts H2S")

        # own_footage NOT chosen; scene degraded because web pipeline also empty
        assert scene.chosen_asset is None or scene.chosen_asset.source != "own_footage"

    def test_own_footage_skipped_when_disabled(self, monkeypatch, tmp_path):
        """When VISION_FIRST_ENABLED=False, own-footage block is bypassed entirely."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", False)

        called = [False]

        def fake_bff(*a, **kw):
            called[0] = True
            return None

        monkeypatch.setattr("video_agent.agents.sourcer.best_footage_for_scene", fake_bff)
        monkeypatch.setattr(
            "video_agent.agents.sourcer.Sourcer._search_all_sources",
            lambda self, q: [],
        )

        sourcer = _make_sourcer(tmp_path)
        sourcer._footage_clips = [_make_clip(tmp_path, "clip")]
        scene = _make_scene()
        sourcer._source_scene(scene, "wastewater_treatment")

        assert not called[0], "best_footage_for_scene must not be called when VISION_FIRST_ENABLED=False"

    def test_run_populates_footage_clips(self, monkeypatch, tmp_path):
        """Sourcer.run caches discovered clips so _source_scene can use them."""
        clips = [_make_clip(tmp_path, "clip")]
        monkeypatch.setattr(
            "video_agent.agents.sourcer.discover_clips",
            lambda: clips,
        )

        from video_agent.storyboard import Storyboard, HeroClaim  # noqa: F401
        sourcer = _make_sourcer(tmp_path)
        sb = Storyboard(version="2", blog={"category": "wastewater_treatment"},
                        hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"))

        # _build_narrative_thread uses OllamaClient — monkeypatch it
        monkeypatch.setattr(
            "video_agent.agents.sourcer.Sourcer._build_narrative_thread",
            lambda self, sb: [],
        )

        sourcer.run(sb)
        assert hasattr(sourcer, "_footage_clips")
        assert sourcer._footage_clips == clips
