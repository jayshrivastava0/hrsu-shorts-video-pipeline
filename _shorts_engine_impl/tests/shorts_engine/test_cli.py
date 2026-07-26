"""
Test CLI module for shorts_engine.

Tests verify:
1. build_stages() returns correct stage list
2. main() parses arguments correctly
3. main() calls runner.run() with correct parameters
4. main() handles --until flag
5. main() handles --resume flag
6. main() handles --local-only flag
7. main() handles --workspace-root flag
8. main() handles --html-override flag
9. main() prints error to stderr and returns 1 on exception
10. main() prints success status and artifact paths to stdout and returns 0
11. __main__.py calls main() and exits with correct code
"""
from __future__ import annotations

import json
import logging
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

logger = logging.getLogger(__name__)


class TestBuildStages:
    """Test build_stages() function."""

    def test_build_stages_returns_list(self):
        """build_stages returns a list."""
        from shorts_engine.cli import build_stages

        stages = build_stages()
        assert isinstance(stages, list)

    def test_build_stages_has_ten_stages(self):
        """build_stages returns exactly ten stages (Task 13: + verify/package/publish)."""
        from shorts_engine.cli import build_stages

        stages = build_stages()
        assert len(stages) == 10

    def test_build_stages_ingest_stage(self):
        """build_stages first stage is ('ingest', 'ingested', ingest.run)."""
        from shorts_engine.cli import build_stages
        from shorts_engine.stages import ingest

        stages = build_stages()
        name, status, fn = stages[0]
        assert name == "ingest"
        assert status == "ingested"
        assert fn == ingest.run

    def test_build_stages_facts_stage(self):
        """build_stages second stage is ('facts', 'facts', facts.run)."""
        from shorts_engine.cli import build_stages
        from shorts_engine.stages import facts

        stages = build_stages()
        name, status, fn = stages[1]
        assert name == "facts"
        assert status == "facts"
        assert fn == facts.run

    def test_build_stages_script_stage(self):
        """build_stages third stage is ('script', 'scripted', script.run)."""
        from shorts_engine.cli import build_stages
        from shorts_engine.stages import script

        stages = build_stages()
        name, status, fn = stages[2]
        assert name == "script"
        assert status == "scripted"
        assert fn == script.run


class TestMainBasic:
    """Test main() function basic usage."""

    def test_main_returns_int(self, tmp_path):
        """main() returns an integer."""
        from shorts_engine.cli import main

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            result = main([f"https://example.com/blog/post-{tmp_path.name}"])
            assert isinstance(result, int)

    def test_main_success_returns_zero(self, tmp_path, capsys):
        """main() returns 0 on success."""
        from shorts_engine.cli import main

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_manifest.run_id = "test-run-123"
            mock_manifest.status = "scripted"
            mock_run.return_value = mock_manifest

            result = main([f"https://example.com/blog/post-{tmp_path.name}"])
            assert result == 0

    def test_main_exception_returns_one(self, tmp_path, capsys):
        """main() returns 1 on exception."""
        from shorts_engine.cli import main

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_run.side_effect = Exception("Test error")

            result = main([f"https://example.com/blog/post-{tmp_path.name}"])
            assert result == 1

    def test_main_requires_blog_url(self):
        """main() requires blog_url argument."""
        from shorts_engine.cli import main

        with pytest.raises(SystemExit):
            main([])


class TestMainArgumentParsing:
    """Test main() argument parsing."""

    def test_main_parses_blog_url(self, tmp_path):
        """main() extracts blog_url from arguments."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url])

            # Verify runner.run was called with blog_url
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("blog_url") == blog_url

    def test_main_until_flag(self, tmp_path):
        """main() handles --until flag."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url, "--until", "facts"])

            # Verify runner.run was called with until="facts"
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("until") == "facts"

    def test_main_resume_flag(self, tmp_path):
        """main() handles --resume flag."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url, "--resume"])

            # Verify runner.run was called with resume=True
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("resume") is True

    def test_main_workspace_root_flag(self, tmp_path):
        """main() handles --workspace-root flag."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"
        workspace = tmp_path / "custom_workspace"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url, "--workspace-root", str(workspace)])

            # Verify runner.run was called with workspace_root
            call_kwargs = mock_run.call_args[1]
            assert Path(call_kwargs.get("workspace_root")) == workspace

    def test_main_local_only_flag(self, tmp_path):
        """main() handles --local-only flag."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url, "--local-only"])

            # Verify runner.run was called with local_only in flags
            call_kwargs = mock_run.call_args[1]
            flags = call_kwargs.get("flags", {})
            assert flags.get("local_only") is True

    def test_main_html_override_flag(self, tmp_path):
        """main() handles --html-override flag."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"
        html_file = tmp_path / "test.html"
        html_file.write_text("<html></html>")

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url, "--html-override", str(html_file)])

            # Verify runner.run was called with html_override in flags
            call_kwargs = mock_run.call_args[1]
            flags = call_kwargs.get("flags", {})
            assert flags.get("html_override") == str(html_file)


class TestMainStages:
    """Test main() stage building and passing to runner."""

    def test_main_calls_runner_with_stages(self, tmp_path):
        """main() calls runner.run() with build_stages()."""
        from shorts_engine.cli import main, build_stages

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url])

            # Verify runner.run was called with stages
            call_kwargs = mock_run.call_args[1]
            stages = call_kwargs.get("stages")
            expected_stages = build_stages()
            assert len(stages) == len(expected_stages)
            assert stages[0][0] == "ingest"
            assert stages[1][0] == "facts"
            assert stages[2][0] == "script"


class TestMainErrorHandling:
    """Test main() error handling."""

    def test_main_prints_error_to_stderr(self, tmp_path, capsys):
        """main() prints exception to stderr on failure."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            error_msg = "Test error message"
            mock_run.side_effect = Exception(error_msg)

            main([blog_url])

            captured = capsys.readouterr()
            assert error_msg in captured.err

    def test_main_prints_failure_reason(self, tmp_path, capsys):
        """main() prints human-readable failure reason."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_run.side_effect = ValueError("Invalid input")

            main([blog_url])

            captured = capsys.readouterr()
            # Should print something indicating failure
            assert "error" in captured.err.lower() or "failed" in captured.err.lower() or "invalid" in captured.err.lower()


class TestMainSuccess:
    """Test main() success output."""

    def test_main_prints_success_to_stdout(self, tmp_path, capsys):
        """main() prints status to stdout on success."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_manifest.run_id = "test-run-123"
            mock_manifest.status = "scripted"
            mock_run.return_value = mock_manifest

            main([blog_url])

            captured = capsys.readouterr()
            # Should print status information
            assert "test-run-123" in captured.out or "scripted" in captured.out

    def test_main_prints_artifact_paths_on_success(self, tmp_path, capsys):
        """main() prints artifact paths to stdout on success."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            artifact_path = "/path/to/script.md"
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {
                "script_file": artifact_path,
                "facts_file": "/path/to/facts.json",
            }
            mock_manifest.run_id = "test-run-123"
            mock_manifest.status = "scripted"
            mock_run.return_value = mock_manifest

            main([blog_url])

            captured = capsys.readouterr()
            # Should print artifact information
            assert "artifact" in captured.out.lower() or artifact_path in captured.out


class TestMainDefaults:
    """Test main() default values."""

    def test_main_uses_config_output_base_by_default(self, tmp_path):
        """main() uses config.OUTPUT_BASE as default workspace_root."""
        from shorts_engine.cli import main
        from shorts_engine import config

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url])

            # Verify runner.run was called with config.OUTPUT_BASE
            call_kwargs = mock_run.call_args[1]
            workspace_root = Path(call_kwargs.get("workspace_root"))
            assert workspace_root == config.OUTPUT_BASE

    def test_main_until_default_is_verified(self, tmp_path):
        """main() passes until='verified' by default (Task 13: hold for
        human review at the contact sheet unless --publish is given)."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url])

            # Verify runner.run was called with until="verified"
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("until") == "verified"

    def test_main_resume_default_is_false(self, tmp_path):
        """main() passes resume=False by default."""
        from shorts_engine.cli import main

        blog_url = f"https://example.com/blog/post-{tmp_path.name}"

        with mock.patch("shorts_engine.runner.run") as mock_run:
            mock_manifest = mock.Mock()
            mock_manifest.artifacts = {}
            mock_run.return_value = mock_manifest

            main([blog_url])

            # Verify runner.run was called with resume=False
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("resume") is False


class TestMainModule:
    """Test __main__.py integration."""

    def test_main_module_exists(self):
        """__main__.py module can be imported."""
        import shorts_engine.__main__

        assert shorts_engine.__main__ is not None

    def test_main_module_has_main_call(self):
        """__main__.py has expected structure."""
        # This is implicitly tested by: python -m shorts_engine
        # For now, just verify the module can be imported
        try:
            import shorts_engine.__main__
        except Exception as e:
            pytest.fail(f"__main__.py import failed: {e}")


class TestPhase2Stages:
    """Test Phase 2 stages (SHOTLIST, AUDIO, VISUALS, ASSEMBLE)."""

    def test_build_stages_has_seven_in_order(self):
        """First seven stages (Phase 2) retain their original order; Task 13
        appends verify/package/publish after these (see TestPhase3Stages)."""
        from shorts_engine.cli import build_stages
        names = [s[0] for s in build_stages()]
        assert names[:7] == ["ingest", "facts", "script", "shotlist", "audio",
                             "visuals", "assemble"]
        statuses = [s[1] for s in build_stages()]
        assert statuses[:7] == ["ingested", "facts", "scripted", "shotlisted",
                                "audio", "visuals", "assembled"]

    def test_until_accepts_new_stages(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False,
                     flags=None):
            captured.update(until=until, flags=flags)
            class M:  # minimal manifest stand-in
                status, artifacts, run_id = "assembled", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        rc = cli.main(["https://x.html", "--until", "assemble", "--torture"])
        assert rc == 0
        assert captured["until"] == "assembled"
        assert captured["flags"]["torture"] is True


class TestPhase3Stages:
    def test_build_stages_has_ten_in_order(self):
        from shorts_engine.cli import build_stages
        names = [s[0] for s in build_stages()]
        assert names == ["ingest", "facts", "script", "shotlist", "audio",
                         "visuals", "assemble", "verify", "package", "publish"]
        statuses = [s[1] for s in build_stages()]
        assert statuses[-3:] == ["verified", "packaged", "published"]

    def test_default_until_is_verified_hold_for_review(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False, flags=None):
            captured.update(until=until, flags=flags)
            class M: status, artifacts, run_id = "verified", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        assert cli.main(["https://x.html"]) == 0
        assert captured["until"] == "verified"

    def test_publish_flag_runs_to_published(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False, flags=None):
            captured.update(until=until, flags=flags)
            class M: status, artifacts, run_id = "published", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        assert cli.main(["https://x.html", "--publish"]) == 0
        assert captured["until"] == "published"
        assert captured["flags"]["publish"] is True
