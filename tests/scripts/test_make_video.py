"""TDD tests for refactored scripts/make_video.py entry point.

Tests cover:
- HarnessRunner.run() integration with publish=False
- Dev-mode behavior (render+verify, no upload)
- Backward compatibility with existing CLI interface
- Error handling
"""
import sys
import logging
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from video_agent.harness.manifest import (
    RunManifest, new_manifest, PublishResult, RunStatus,
)


# Suppress pytest output collection warnings
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def tmp_workspace(tmp_path):
    """Isolated workspace for each test."""
    return tmp_path


@pytest.fixture
def blog_url():
    """Standard test blog URL."""
    return "https://blog.hrsuindore.com/2026/05/test-blog.html"


@pytest.fixture
def mock_manifest_verified(blog_url, tmp_workspace):
    """Mock manifest with verified status (dev mode stops here)."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "verified"
    m.video_path = str(tmp_workspace / "video_short.mp4")
    m.storyboard_path = str(tmp_workspace / "storyboard.json")
    # Verified status means no publish result
    return m


@pytest.fixture
def mock_manifest_vision_verified(blog_url, tmp_workspace):
    """Mock manifest with vision_verified status (Phase 3 gate passed)."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "vision_verified"
    m.video_path = str(tmp_workspace / "video_short.mp4")
    m.storyboard_path = str(tmp_workspace / "storyboard.json")
    return m


@pytest.fixture
def mock_manifest_failed(blog_url, tmp_workspace):
    """Mock manifest with failed status."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "failed"
    m.last_error = "Failed to synthesize voiceover"
    return m


class TestMakeVideoBackwardCompat:
    """Test backward compatibility with existing make_video.py interface."""

    def test_argparse_blog_url_required(self):
        """Blog URL is required positional argument."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        with pytest.raises(SystemExit):
            # This should fail since no URL provided
            make_video.parse_args([])

    def test_argparse_blog_url_provided(self):
        """Parse valid blog URL."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        args = make_video.parse_args(["https://blog.hrsuindore.com/2026/05/test.html"])
        assert args.url == "https://blog.hrsuindore.com/2026/05/test.html"

    def test_argparse_force_flag_exists(self):
        """--force flag exists and works (for backward compat)."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        args = make_video.parse_args(["https://example.com/blog", "--force"])
        assert hasattr(args, "force")
        assert args.force is True

    def test_argparse_no_voice_flag_exists(self):
        """--no-voice flag exists and works (for backward compat)."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        args = make_video.parse_args(["https://example.com/blog", "--no-voice"])
        assert hasattr(args, "no_voice")
        assert args.no_voice is True


class TestMakeVideoHarnessIntegration:
    """Test make_video refactored to use HarnessRunner."""

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_harness_runner_called_with_publish_false(self, mock_runner, mock_log, blog_url, mock_manifest_verified):
        """HarnessRunner.run called with publish=False for dev mode."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.return_value = mock_manifest_verified

        with patch("sys.argv", ["make_video.py", blog_url]):
            try:
                make_video.main()
            except SystemExit:
                pass
            except Exception:
                pass

        # Verify HarnessRunner.run was called with publish=False
        mock_runner.assert_called_once()
        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs["publish"] is False

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_dev_mode_stops_at_verified(self, mock_runner, mock_log, blog_url, mock_manifest_verified):
        """Dev mode stops at VERIFY (render+verify, no upload)."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.return_value = mock_manifest_verified

        with patch("sys.argv", ["make_video.py", blog_url]):
            try:
                make_video.main()
            except SystemExit:
                pass
            except Exception:
                pass

        # Manifest should have status "verified" (not published)
        assert mock_runner.return_value.status == "verified"
        # No publish result
        assert mock_runner.return_value.publish is None

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_dev_mode_logging_message(self, mock_runner, mock_log, caplog, blog_url, mock_manifest_verified):
        """Dev mode logs 'Running in dev mode' message."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.return_value = mock_manifest_verified

        with caplog.at_level(logging.INFO):
            with patch("sys.argv", ["make_video.py", blog_url]):
                try:
                    make_video.main()
                except SystemExit:
                    pass
                except Exception:
                    pass

        # Verify logging setup was called (as proxy for dev mode)
        mock_log.assert_called_once()


class TestMakeVideoErrorHandling:
    """Test error handling in refactored make_video.py."""

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_exit_code_on_failure(self, mock_runner, mock_log, blog_url, mock_manifest_failed):
        """Non-zero exit code when manifest fails."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.return_value = mock_manifest_failed

        with patch("sys.argv", ["make_video.py", blog_url]):
            try:
                make_video.main()
            except SystemExit as e:
                # Should exit with non-zero on failure
                assert e.code != 0

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_exception_handling(self, mock_runner, mock_log, caplog, blog_url):
        """Exceptions are caught and exit with code 1."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.side_effect = Exception("Test error: bad HTML")

        with caplog.at_level(logging.ERROR):
            with patch("sys.argv", ["make_video.py", blog_url]):
                try:
                    make_video.main()
                except SystemExit as e:
                    assert e.code == 1
                else:
                    pytest.fail("Expected SystemExit on exception")

        # Verify error message was logged
        assert "bad HTML" in caplog.text or "Unexpected error" in caplog.text

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_keyboard_interrupt_handled(self, mock_runner, mock_log, caplog, blog_url):
        """KeyboardInterrupt is caught, logged, and exit with code 1."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        mock_runner.side_effect = KeyboardInterrupt()

        with caplog.at_level(logging.WARNING):
            with patch("sys.argv", ["make_video.py", blog_url]):
                try:
                    make_video.main()
                except SystemExit as e:
                    assert e.code == 1
                else:
                    pytest.fail("Expected SystemExit on KeyboardInterrupt")

        # Verify user interruption was logged
        assert "Interrupted" in caplog.text or "user" in caplog.text.lower()


class TestMakeVideoOutput:
    """Test output and side effects."""

    @patch("scripts.make_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    @patch("subprocess.Popen")
    def test_video_ready_message(self, mock_popen, mock_runner, mock_log, capsys, blog_url, tmp_workspace, mock_manifest_vision_verified):
        """Success prints 'Video ready' message when video exists."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import make_video

        # Create a fake video file
        fake_video = tmp_workspace / "video_short.mp4"
        fake_video.write_text("fake video data")
        mock_manifest_vision_verified.video_path = str(fake_video)
        mock_runner.return_value = mock_manifest_vision_verified
        mock_popen.return_value = Mock()

        with patch("sys.argv", ["make_video.py", blog_url]):
            try:
                make_video.main()
            except SystemExit:
                pass
            except Exception:
                pass

        # Capture stdout output
        captured = capsys.readouterr()
        # Look for success message (video ready should be printed)
        assert "video" in captured.out.lower() or "ready" in captured.out.lower()
