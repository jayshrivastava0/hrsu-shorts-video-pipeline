"""Tests for B-7: LocalCritic adds visual_mismatch when vision_score is low."""
from __future__ import annotations
import pytest

from video_agent.storyboard import Scene, VisualConcept, AssetCandidate, CriticNotes


def _make_scene(vision_score: int = 8) -> Scene:
    s = Scene(
        index=0, beat="mechanism",
        narration="Calcium nitrate treats H2S.",
        on_screen_text="H2S CONTROL",
        visual_concept=VisualConcept(
            subject="wastewater plant", modifier="aerial",
            type="photo", mood="mechanism", style_hint="",
        ),
        duration_target_s=5.0,
    )
    s.chosen_asset = AssetCandidate(
        source="fake", url="http://fake.com/img.jpg", score=80,
        local_path="/tmp/img.jpg", caption="wastewater plant",
        width=1920, height=1080, is_clip=False, duration_s=None,
        vision_score=vision_score, vision_reason="test",
    )
    return s


def _make_critic(monkeypatch, llm_score: int = 8, llm_flags: list | None = None):
    from video_agent.agents.critic_local import LocalCritic
    from video_agent.ollama_client import OllamaClient

    def fake_generate_json(self, prompt, system=None, retries=3, images=None):
        return {
            "alignment_score": llm_score,
            "flags": llm_flags or [],
            "revision": None,
        }

    monkeypatch.setattr(OllamaClient, "generate_json", fake_generate_json)
    return LocalCritic()


class TestCriticVisionMismatch:

    def test_low_vision_score_adds_visual_mismatch_flag(self, monkeypatch):
        """When vision_score < VISION_SELECT_MIN, visual_mismatch is added to flags."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        critic = _make_critic(monkeypatch, llm_score=8, llm_flags=[])
        scene = _make_scene(vision_score=3)
        critic._critique(scene, None, "(none)")

        assert "visual_mismatch" in scene.critic_notes.flags

    def test_low_vision_score_caps_alignment_score(self, monkeypatch):
        """Low vision_score caps alignment_score to 4 so reviser triggers re-source."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        critic = _make_critic(monkeypatch, llm_score=9, llm_flags=[])
        scene = _make_scene(vision_score=2)
        critic._critique(scene, None, "(none)")

        assert scene.critic_notes.alignment_score <= 4

    def test_good_vision_score_does_not_add_visual_mismatch(self, monkeypatch):
        """When vision_score >= VISION_SELECT_MIN, no mismatch flag is forced."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        critic = _make_critic(monkeypatch, llm_score=8, llm_flags=[])
        scene = _make_scene(vision_score=7)
        critic._critique(scene, None, "(none)")

        assert "visual_mismatch" not in scene.critic_notes.flags
        assert scene.critic_notes.alignment_score == 8

    def test_vision_flag_not_added_when_vision_first_disabled(self, monkeypatch):
        """When VISION_FIRST_ENABLED=False, low vision_score does not add flag."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", False)
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        critic = _make_critic(monkeypatch, llm_score=8, llm_flags=[])
        scene = _make_scene(vision_score=1)
        critic._critique(scene, None, "(none)")

        assert "visual_mismatch" not in scene.critic_notes.flags

    def test_no_asset_does_not_crash(self, monkeypatch):
        """No chosen_asset → no crash (degraded scene with no asset)."""
        import video_agent.config as cfg
        monkeypatch.setattr(cfg, "VISION_FIRST_ENABLED", True)
        monkeypatch.setattr(cfg, "VISION_SELECT_MIN", 6)

        critic = _make_critic(monkeypatch, llm_score=5, llm_flags=["degraded_visual"])
        scene = _make_scene(vision_score=0)
        scene.chosen_asset = None
        critic._critique(scene, None, "(none)")

        # No crash; flags from LLM still applied
        assert "visual_mismatch" not in scene.critic_notes.flags
        assert "degraded_visual" in scene.critic_notes.flags
