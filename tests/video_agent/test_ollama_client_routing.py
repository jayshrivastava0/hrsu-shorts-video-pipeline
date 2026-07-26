"""Tests for C-1: OllamaClient routes text calls via API or CLI based on
SMART_TEXT_TRANSPORT config, and smart_client() selects the right model."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest

import video_agent.config as cfg
from video_agent.ollama_client import OllamaClient, smart_client, OllamaError


class TestSmartTextTransportAPI:
    """When SMART_TEXT_TRANSPORT='api', text calls use POST /api/generate."""

    def test_api_path_used_for_smart_model(self, monkeypatch):
        """With transport='api', generate() POSTs; CLI subprocess NOT called."""
        monkeypatch.setattr(cfg, "SMART_TEXT_TRANSPORT", "api")
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        cli_called = [False]
        original_cli = OllamaClient._generate_via_cli

        def fake_cli(self, prompt, system=None):
            cli_called[0] = True
            return "cli output"

        fake_response = MagicMock()
        fake_response.json.return_value = {"response": "api output"}
        fake_response.raise_for_status = lambda: None

        monkeypatch.setattr(OllamaClient, "_generate_via_cli", fake_cli)

        with patch("video_agent.ollama_client.requests.post",
                   return_value=fake_response) as mock_post:
            client = OllamaClient(model="gemma4:31b-cloud")
            result = client.generate("test prompt")

        assert not cli_called[0], "CLI must not be invoked when transport=api"
        mock_post.assert_called_once()
        assert result == "api output"

    def test_api_path_for_local_model_even_if_cli_transport(self, monkeypatch):
        """Local model always uses API regardless of SMART_TEXT_TRANSPORT."""
        monkeypatch.setattr(cfg, "SMART_TEXT_TRANSPORT", "cli")
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        cli_called = [False]

        def fake_cli(self, prompt, system=None):
            cli_called[0] = True
            return "cli output"

        monkeypatch.setattr(OllamaClient, "_generate_via_cli", fake_cli)

        fake_response = MagicMock()
        fake_response.json.return_value = {"response": "local output"}
        fake_response.raise_for_status = lambda: None

        with patch("video_agent.ollama_client.requests.post",
                   return_value=fake_response):
            client = OllamaClient(model="gemma3:4b")  # local model
            result = client.generate("test prompt")

        assert not cli_called[0], "Local model must not use CLI path"
        assert result == "local output"


class TestSmartTextTransportCLI:
    """When SMART_TEXT_TRANSPORT='cli', text calls for smart model use subprocess."""

    def test_cli_path_used_for_smart_model(self, monkeypatch):
        """With transport='cli', generate() uses CLI; requests.post NOT called."""
        monkeypatch.setattr(cfg, "SMART_TEXT_TRANSPORT", "cli")
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        def fake_cli(self, prompt, system=None):
            return "cli text response"

        monkeypatch.setattr(OllamaClient, "_generate_via_cli", fake_cli)

        with patch("video_agent.ollama_client.requests.post") as mock_post:
            client = OllamaClient(model="gemma4:31b-cloud")
            result = client.generate("test prompt")

        mock_post.assert_not_called()
        assert result == "cli text response"

    def test_cli_strips_ansi_codes(self, monkeypatch):
        """_generate_via_cli strips ANSI escape codes from subprocess stdout."""
        import subprocess as sp
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "\x1b[32mHello World\x1b[0m"

        with patch("video_agent.ollama_client.subprocess.run",
                   return_value=fake_result):
            client = OllamaClient(model="gemma4:31b-cloud")
            result = client._generate_via_cli("test prompt")

        assert result == "Hello World"

    def test_cli_raises_on_nonzero_exit(self, monkeypatch):
        """_generate_via_cli raises OllamaError when subprocess exits non-zero."""
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "model not found"

        with patch("video_agent.ollama_client.subprocess.run",
                   return_value=fake_result):
            client = OllamaClient(model="gemma4:31b-cloud")
            with pytest.raises(OllamaError, match="exited 1"):
                client._generate_via_cli("test prompt")

    def test_images_always_use_api_even_with_cli_transport(self, monkeypatch):
        """When images are passed, always use API (CLI transport only for text)."""
        monkeypatch.setattr(cfg, "SMART_TEXT_TRANSPORT", "cli")
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        cli_called = [False]

        def fake_cli(self, prompt, system=None):
            cli_called[0] = True
            return "cli output"

        monkeypatch.setattr(OllamaClient, "_generate_via_cli", fake_cli)

        fake_response = MagicMock()
        fake_response.json.return_value = {"response": "api output"}
        fake_response.raise_for_status = lambda: None

        with patch("video_agent.ollama_client.requests.post",
                   return_value=fake_response):
            client = OllamaClient(model="gemma4:31b-cloud")
            result = client.generate("test prompt", images=["img.jpg"])

        assert not cli_called[0], "images= forces API even with cli transport"
        assert result == "api output"


class TestSmartClient:
    """smart_client() returns the correct model based on USE_SMART_TEXT_MODEL."""

    def test_smart_client_uses_smart_model_when_enabled(self, monkeypatch):
        """Returns SMART_TEXT_MODEL when USE_SMART_TEXT_MODEL=True."""
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", True)
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        client = smart_client()
        assert client.model == "gemma4:31b-cloud"

    def test_smart_client_falls_back_to_cheap_model(self, monkeypatch):
        """Returns OLLAMA_MODEL when USE_SMART_TEXT_MODEL=False."""
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", False)
        monkeypatch.setattr(cfg, "OLLAMA_MODEL", "gemma3:4b")

        client = smart_client()
        assert client.model == "gemma3:4b"

    def test_smart_client_passes_kwargs(self, monkeypatch):
        """Extra kwargs forwarded to OllamaClient constructor."""
        monkeypatch.setattr(cfg, "USE_SMART_TEXT_MODEL", True)
        monkeypatch.setattr(cfg, "SMART_TEXT_MODEL", "gemma4:31b-cloud")

        client = smart_client(timeout=60.0)
        assert client.timeout == 60.0
