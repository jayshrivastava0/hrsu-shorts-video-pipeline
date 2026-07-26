"""Tests for C-2: Strategist, Storyboarder, NarrationPolisher default to smart model."""
from __future__ import annotations
import pytest
import video_agent.config as cfg


class TestStrategistSmartDefault:
    def test_default_client_uses_smart_model(self, monkeypatch):
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", True)
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        from video_agent.agents.strategist import Strategist
        s = Strategist()
        assert s.client.model == "gemma4:31b-cloud"

    def test_injected_client_is_respected(self, monkeypatch):
        from video_agent.ollama_client import OllamaClient
        from video_agent.agents.strategist import Strategist
        fake = OllamaClient(model="custom-model")
        s = Strategist(client=fake)
        assert s.client.model == "custom-model"


class TestStoryboarderSmartDefault:
    def test_default_client_uses_smart_model(self, monkeypatch):
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", True)
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        from video_agent.agents.storyboarder import Storyboarder
        sb = Storyboarder()
        assert sb.client.model == "gemma4:31b-cloud"

    def test_injected_client_is_respected(self, monkeypatch):
        from video_agent.ollama_client import OllamaClient
        from video_agent.agents.storyboarder import Storyboarder
        fake = OllamaClient(model="custom-model")
        s = Storyboarder(client=fake)
        assert s.client.model == "custom-model"


class TestNarrationPolisherSmartDefault:
    def test_default_client_uses_smart_model(self, monkeypatch):
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", True)
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        from video_agent.agents.narration_polisher import NarrationPolisher
        np = NarrationPolisher()
        assert np.ollama.model == "gemma4:31b-cloud"

    def test_injected_client_is_respected(self, monkeypatch):
        from video_agent.ollama_client import OllamaClient
        from video_agent.agents.narration_polisher import NarrationPolisher
        fake = OllamaClient(model="custom-model")
        np = NarrationPolisher(ollama=fake)
        assert np.ollama.model == "custom-model"
