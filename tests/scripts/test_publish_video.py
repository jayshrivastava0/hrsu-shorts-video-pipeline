"""TDD tests for scripts/publish_video.py entry point.

Tests cover:
- Argparse validation (positional blog_url, --dry-run, --resume, --workspace)
- HarnessRunner.run() integration
- Exit codes (0 for published, 1 for failure)
- Error handling and messages
"""
import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call

import pytest

from video_agent.harness.manifest import (
    RunManifest, new_manifest, PublishResult, RunStatus,
)


@pytest.fixture
def tmp_workspace(tmp_path):
    """Isolated workspace for each test."""
    return tmp_path


@pytest.fixture
def blog_url():
    """Standard test blog URL."""
    return "https://blog.hrsuindore.com/2026/05/test-blog.html"


@pytest.fixture
def mock_manifest_published(blog_url, tmp_workspace):
    """Mock manifest with published status."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "published"
    m.video_path = str(tmp_workspace / "video_short.mp4")
    m.publish = PublishResult(
        platform="youtube",
        video_id="test_video_123",
        url="https://youtube.com/watch?v=test_video_123",
        visibility="private",
        uploaded_at="2026-06-11T12:00:00Z",
    )
    return m


@pytest.fixture
def mock_manifest_failed(blog_url, tmp_workspace):
    """Mock manifest with failed status."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "failed"
    m.last_error = "Failed to synthesize voiceover"
    return m


@pytest.fixture
def mock_manifest_verified(blog_url, tmp_workspace):
    """Mock manifest with verified status (dry-run mode)."""
    m = new_manifest(blog_url, "test-blog", str(tmp_workspace))
    m.status = "verified"
    m.video_path = str(tmp_workspace / "video_short.mp4")
    m.storyboard_path = str(tmp_workspace / "storyboard.json")
    # Verified status means no publish result
    return m


class TestPublishVideoArgparse:
    """Test argparse validation for publish_video.py."""

    def test_argparse_required_blog_url(self):
        """Blog URL is required positional argument."""
        # Import here to avoid import-time side effects
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        # Test missing blog_url
        with pytest.raises(SystemExit):
            args = publish_video.parse_args([])

    def test_argparse_blog_url_provided(self):
        """Parse valid blog URL."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        args = publish_video.parse_args(["https://blog.hrsuindore.com/2026/05/test.html"])
        assert args.blog_url == "https://blog.hrsuindore.com/2026/05/test.html"
        assert not args.dry_run
        assert not args.resume
        assert args.workspace is None

    def test_argparse_dry_run_flag(self):
        """Parse --dry-run flag."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        args = publish_video.parse_args(["https://example.com/blog", "--dry-run"])
        assert args.dry_run is True

    def test_argparse_resume_flag(self):
        """Parse --resume flag."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        args = publish_video.parse_args(["https://example.com/blog", "--resume"])
        assert args.resume is True

    def test_argparse_workspace_option(self):
        """Parse --workspace <path>."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        args = publish_video.parse_args(
            ["https://example.com/blog", "--workspace", "/tmp/custom"]
        )
        assert args.workspace == "/tmp/custom"

    def test_argparse_all_flags(self):
        """Parse all flags together."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        args = publish_video.parse_args(
            [
                "https://example.com/blog",
                "--dry-run",
                "--resume",
                "--workspace", "/tmp/ws",
            ]
        )
        assert args.blog_url == "https://example.com/blog"
        assert args.dry_run is True
        assert args.resume is True
        assert args.workspace == "/tmp/ws"


class TestPublishVideoIntegration:
    """Test publish_video main integration with HarnessRunner."""

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_exit_code_zero_on_published(self, mock_runner, mock_log, capsys, tmp_workspace, blog_url, mock_manifest_published):
        """Exit code 0 when manifest status is 'published'."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url]):
            try:
                publish_video.main()
            except SystemExit as e:
                assert e.code == 0
            else:
                pytest.fail("Expected SystemExit with code 0")

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_exit_code_one_on_failed(self, mock_runner, mock_log, tmp_workspace, blog_url, mock_manifest_failed):
        """Exit code 1 when manifest status is 'failed'."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_failed

        with patch("sys.argv", ["publish_video.py", blog_url]):
            try:
                publish_video.main()
            except SystemExit as e:
                assert e.code == 1
            else:
                pytest.fail("Expected SystemExit with code 1")

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_harness_runner_called_with_publish_true(self, mock_runner, mock_log, blog_url, mock_manifest_published):
        """HarnessRunner.run called with publish=True."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url]):
            try:
                publish_video.main()
            except SystemExit:
                pass

        # Verify HarnessRunner.run was called with correct args
        mock_runner.assert_called_once()
        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs["publish"] is True
        assert call_kwargs["dry_run"] is False
        assert call_kwargs["resume"] is False

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_harness_runner_dry_run_flag(self, mock_runner, mock_log, blog_url, mock_manifest_published):
        """HarnessRunner.run called with dry_run=True when --dry-run passed."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url, "--dry-run"]):
            try:
                publish_video.main()
            except SystemExit:
                pass

        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs["dry_run"] is True

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_harness_runner_resume_flag(self, mock_runner, mock_log, blog_url, mock_manifest_published):
        """HarnessRunner.run called with resume=True when --resume passed."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url, "--resume"]):
            try:
                publish_video.main()
            except SystemExit:
                pass

        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs["resume"] is True

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_harness_runner_workspace_option(self, mock_runner, mock_log, blog_url, tmp_workspace, mock_manifest_published):
        """HarnessRunner.run called with custom workspace."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url, "--workspace", str(tmp_workspace)]):
            try:
                publish_video.main()
            except SystemExit:
                pass

        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs["workspace"] == str(tmp_workspace)

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_dry_run_status_verified(self, mock_runner, mock_log, capsys, blog_url, mock_manifest_verified):
        """Exit code 1 when manifest status is 'verified' (dry-run mode)."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_verified

        with patch("sys.argv", ["publish_video.py", blog_url, "--dry-run"]):
            try:
                publish_video.main()
            except SystemExit as e:
                assert e.code == 1
            else:
                pytest.fail("Expected SystemExit with code 1")

        # Verify output mentions verification/ready state
        captured = capsys.readouterr()
        output = (captured.out + captured.err).lower()
        assert "verified" in output or "ready" in output

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_manifest_printed(self, mock_runner, mock_log, capsys, blog_url, mock_manifest_published):
        """Manifest status and results printed to stdout."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with patch("sys.argv", ["publish_video.py", blog_url]):
            try:
                publish_video.main()
            except SystemExit:
                pass

        captured = capsys.readouterr()
        output = captured.out + captured.err
        # Check that manifest fields are printed
        assert "published" in output.lower()
        assert "status" in output.lower() or "Pipeline Status" in output
        assert "run id" in output.lower() or "Run ID" in output
        assert "video url" in output.lower() or "Video URL" in output or "youtube" in output.lower()


class TestPublishVideoLogging:
    """Test logging setup."""

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_logging_configured(self, mock_runner, mock_log, tmp_workspace, blog_url, mock_manifest_published, caplog):
        """Logging is configured (setup_logging is called)."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.return_value = mock_manifest_published

        with caplog.at_level(logging.INFO):
            with patch("sys.argv", ["publish_video.py", blog_url]):
                try:
                    publish_video.main()
                except SystemExit:
                    pass

        # Verify setup_logging was called
        mock_log.assert_called_once()


class TestPublishVideoErrorHandling:
    """Test error handling."""

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_exception_caught_and_logged(self, mock_runner, mock_log, caplog, blog_url):
        """Unexpected exceptions are caught, logged, and exit with code 1."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.side_effect = Exception("Test error: network failure")

        with caplog.at_level(logging.ERROR):
            with patch("sys.argv", ["publish_video.py", blog_url]):
                try:
                    publish_video.main()
                except SystemExit as e:
                    assert e.code == 1
                else:
                    pytest.fail("Expected SystemExit on exception")

        # Verify error message was logged
        assert "network failure" in caplog.text or "Unexpected error" in caplog.text

    @patch("scripts.publish_video.setup_logging")
    @patch("video_agent.harness.runner.HarnessRunner.run")
    def test_keyboard_interrupt_handled(self, mock_runner, mock_log, caplog, blog_url):
        """KeyboardInterrupt is caught, logged, and exit with code 1."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scripts import publish_video

        mock_runner.side_effect = KeyboardInterrupt()

        with caplog.at_level(logging.WARNING):
            with patch("sys.argv", ["publish_video.py", blog_url]):
                try:
                    publish_video.main()
                except SystemExit as e:
                    assert e.code == 1
                else:
                    pytest.fail("Expected SystemExit on KeyboardInterrupt")

        # Verify user interruption was logged
        assert "Interrupted" in caplog.text or "user" in caplog.text.lower()
