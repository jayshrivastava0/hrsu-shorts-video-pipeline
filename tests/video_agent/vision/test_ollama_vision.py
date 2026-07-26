"""Tests for video_agent.vision.ollama_vision — cloud multimodal helper."""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess
import pytest

from video_agent.vision.ollama_vision import _parse_json_from_cli, call_vision_json


class TestParseJsonFromCli:
    """Test _parse_json_from_cli parsing logic."""

    def test_clean_json_string(self):
        """Parse simple JSON object from clean string."""
        raw = '{"score": 8}'
        result = _parse_json_from_cli(raw)
        assert result == {"score": 8}

    def test_json_after_thinking_block(self):
        """Extract JSON after Gemma-4 thinking marker."""
        raw = "Thinking...\n...done thinking.\n\n{\"score\": 7}"
        result = _parse_json_from_cli(raw)
        assert result == {"score": 7}

    def test_json_with_ansi_codes(self):
        """Strip ANSI escape codes before parsing."""
        raw = "\x1b[32m{\"score\": 5}\x1b[0m"
        result = _parse_json_from_cli(raw)
        assert result == {"score": 5}

    def test_no_json_in_string(self):
        """Return None when no valid JSON found."""
        raw = "hello world"
        result = _parse_json_from_cli(raw)
        assert result is None

    def test_complex_json_with_nested_structure(self):
        """Parse complex nested JSON."""
        raw = '{"outer": {"inner": [1, 2, 3]}, "key": "value"}'
        result = _parse_json_from_cli(raw)
        assert result == {"outer": {"inner": [1, 2, 3]}, "key": "value"}

    def test_last_balanced_json_wins(self):
        """When multiple JSON objects present, extract the last balanced one."""
        raw = '{"first": 1} and then {"second": 2}'
        result = _parse_json_from_cli(raw)
        # Should get the last balanced JSON object
        assert result == {"second": 2}

    def test_terminal_wrap_sequence_stripped_without_duplication(self):
        """Wrap escapes are stripped conservatively; no line re-assembly.

        The old wrap heuristic deleted the '\\x1b[K\\n' pair, stitching the
        word back together ({"score": 9}) — but on real captured output it
        DUPLICATED characters ('brabranded') because the CLI re-prints the
        wrapped word. The fixed parser only removes the escape sequence
        itself; the surviving newline becomes a space inside the string.
        """
        raw = '{"scor\x1b[K\ne": 9}'
        result = _parse_json_from_cli(raw)
        # Still parses as JSON; wrap point surfaces as a space, never as
        # duplicated characters.
        assert result == {"scor e": 9}

    def test_ansi_and_thinking_combined(self):
        """Handle both ANSI codes and thinking blocks."""
        raw = "\x1b[32mThinking...\x1b[0m\n...done thinking.\n\n\x1b[1m{\"score\": 6}\x1b[0m"
        result = _parse_json_from_cli(raw)
        assert result == {"score": 6}


class TestCallVisionJson:
    """Test call_vision_json subprocess orchestration."""

    def test_returns_none_when_ollama_not_found(self, monkeypatch):
        """Return None when ollama binary is not on PATH."""
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", None)
        result = call_vision_json(
            prompt="test",
            image_path=Path("/fake/image.jpg"),
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )
        assert result is None

    def test_subprocess_success_with_valid_json(self, monkeypatch, tmp_path):
        """Parse and return JSON from successful subprocess call."""
        # Create a fake image file for the test
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        # Mock subprocess.run to return success with JSON output
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = b'{"score": 9}'
        mock_process.stderr = b''

        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_process)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        result = call_vision_json(
            prompt="Grade this image",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )
        assert result == {"score": 9}

    def test_returns_none_on_non_zero_exit(self, monkeypatch, tmp_path):
        """Return None when subprocess returns non-zero exit code."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        # Mock subprocess.run to return failure
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.stdout = b''
        mock_process.stderr = b'Model not found'

        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_process)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        result = call_vision_json(
            prompt="Grade this image",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )
        assert result is None

    def test_returns_none_on_timeout(self, monkeypatch, tmp_path):
        """Return None when subprocess raises TimeoutExpired."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        def mock_run_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], timeout=kwargs.get("timeout"))

        monkeypatch.setattr("subprocess.run", mock_run_timeout)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        result = call_vision_json(
            prompt="Grade this image",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=1.0,
        )
        assert result is None

    def test_returns_none_on_generic_exception(self, monkeypatch, tmp_path):
        """Return None on any other subprocess exception."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        def mock_run_error(*args, **kwargs):
            raise OSError("Failed to execute ollama")

        monkeypatch.setattr("subprocess.run", mock_run_error)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        result = call_vision_json(
            prompt="Grade this image",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )
        assert result is None

    def test_sets_term_dumb_environment(self, monkeypatch, tmp_path):
        """Verify TERM=dumb is set in subprocess environment."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = b'{"score": 8}'
        mock_process.stderr = b''

        call_args = {}

        def capture_run(*args, **kwargs):
            call_args["env"] = kwargs.get("env", {})
            return mock_process

        monkeypatch.setattr("subprocess.run", capture_run)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        call_vision_json(
            prompt="Test",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )

        # Verify TERM=dumb was set
        assert call_args.get("env", {}).get("TERM") == "dumb"

    def test_stdin_devnull_passed(self, monkeypatch, tmp_path):
        """Verify stdin=DEVNULL is passed to subprocess."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = b'{"score": 7}'
        mock_process.stderr = b''

        call_args = {}

        def capture_run(*args, **kwargs):
            call_args["stdin"] = kwargs.get("stdin")
            return mock_process

        monkeypatch.setattr("subprocess.run", capture_run)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        call_vision_json(
            prompt="Test",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )

        # Verify stdin=DEVNULL
        assert call_args.get("stdin") == subprocess.DEVNULL

    def test_parses_json_with_ansi_from_subprocess(self, monkeypatch, tmp_path):
        """Parse JSON output with ANSI codes from subprocess."""
        fake_image = tmp_path / "test.jpg"
        fake_image.write_bytes(b"fake image data")

        mock_process = MagicMock()
        mock_process.returncode = 0
        # Simulate output with ANSI color codes
        mock_process.stdout = b'\x1b[32m{"score": 10}\x1b[0m'
        mock_process.stderr = b''

        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_process)
        monkeypatch.setattr("video_agent.vision.ollama_vision._OLLAMA", "/usr/bin/ollama")

        result = call_vision_json(
            prompt="Test",
            image_path=fake_image,
            model="gemma4:31b-cloud",
            timeout_s=30.0,
        )
        assert result == {"score": 10}
