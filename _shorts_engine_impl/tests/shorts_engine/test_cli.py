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

    def test_build_stages_has_twelve_stages(self):
        """build_stages returns exactly twelve stages (+ explain/verify_claims, verify/package/publish)."""
        from shorts_engine.cli import build_stages

        stages = build_stages()
        assert len(stages) == 12

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
        name, status, fn = stages[4]
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

            # Verify runner.run was called with html_override in flags. ingest.run() hands
            # flags["html_override"] straight to BeautifulSoup, so it must be the file's
            # CONTENTS, not its path (the same contract stage_cli.py already implements).
            call_kwargs = mock_run.call_args[1]
            flags = call_kwargs.get("flags", {})
            assert flags.get("html_override") == "<html></html>"


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
            assert stages[2][0] == "explain"
            assert stages[3][0] == "verify_claims"
            assert stages[4][0] == "script"


class TestPrintPlan:
    """--print-plan prints the explanation plan; hold manifests print [HOLD]."""

    def _manifest(self, tmp_path, status="claims_verified"):
        (tmp_path / "explanation_plan.json").write_text(
            json.dumps({"question": "Why model sand?", "status": "verified",
                        "steps": [], "dropped_claims": [], "dropped_steps": []}),
            encoding="utf-8")
        m = mock.Mock()
        m.artifacts = {}
        m.run_id = "r1"
        m.status = status
        m.workspace = str(tmp_path)
        return m

    def test_print_plan_flag_prints_question(self, tmp_path, capsys):
        from shorts_engine.cli import main

        with mock.patch("shorts_engine.runner.run", return_value=self._manifest(tmp_path)):
            assert main(["https://example.com/b", "--print-plan"]) == 0
        assert "Why model sand?" in capsys.readouterr().out

    def test_no_flag_does_not_print_plan(self, tmp_path, capsys):
        from shorts_engine.cli import main

        with mock.patch("shorts_engine.runner.run", return_value=self._manifest(tmp_path)):
            assert main(["https://example.com/b"]) == 0
        assert "Why model sand?" not in capsys.readouterr().out

    def test_hold_manifest_prints_hold_and_plan(self, tmp_path, capsys):
        from shorts_engine.cli import main

        m = self._manifest(tmp_path, status="hold_for_review")
        with mock.patch("shorts_engine.runner.run", return_value=m):
            assert main(["https://example.com/b", "--print-plan"]) == 0
        out = capsys.readouterr().out
        assert "[HOLD]" in out and "[OK]" not in out and "Why model sand?" in out

    def test_print_plan_failure_does_not_change_exit_code(self, tmp_path, capsys):
        from shorts_engine.cli import main

        m = self._manifest(tmp_path)
        (tmp_path / "explanation_plan.json").write_text("not json", encoding="utf-8")
        with mock.patch("shorts_engine.runner.run", return_value=m):
            assert main(["https://example.com/b", "--print-plan"]) == 0


    def test_non_ascii_plan_survives_cp1252_console(self, tmp_path, monkeypatch):
        import io
        from shorts_engine.cli import main

        m = self._manifest(tmp_path)
        (tmp_path / "explanation_plan.json").write_text(
            json.dumps({"question": "Why model sand?", "status": "verified",
                        "steps": [{"step_id": "s1", "claim_text": "g", "claims": [
                            {"text": "H\u2082S \u2265 5 \u2192", "kind": "external_fact",
                             "verdict": "supported"}]}]}),
            encoding="utf-8")
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
        monkeypatch.setattr(sys, "stdout", out)
        with mock.patch("shorts_engine.runner.run", return_value=m):
            assert main(["https://example.com/b", "--print-plan"]) == 0
        text = raw.getvalue().decode("cp1252")
        assert "Why model sand?" in text and "H" in text and "[supported]" in text

    def test_hold_prints_reason_from_manifest_error(self, tmp_path, capsys):
        from shorts_engine.cli import main

        m = self._manifest(tmp_path, status="hold_for_review")
        m.error = "only 2 verified steps"
        with mock.patch("shorts_engine.runner.run", return_value=m):
            assert main(["https://example.com/b"]) == 0
        assert "Hold reason: only 2 verified steps" in capsys.readouterr().out


class TestCreativeStageBridge:
    """The visuals/assemble stages shell out to the harness agent bridge."""

    @pytest.mark.parametrize("phase, finalize_cmd", [
        ("visuals", "cmd_visuals_finalize"),
        ("assemble", "cmd_assemble_finalize"),
    ])
    def test_reported_artifacts_match_what_stage_cli_actually_checkpoints(
            self, phase, finalize_cmd):
        """The manifest must not claim artifacts the bridge never produces.

        `run_creative_stage` reports artifacts on the runner's behalf, but the files are
        actually written by `stage_cli.py`'s `visuals-finalize`/`assemble-finalize`, which
        checkpoint their own artifact dicts. The two must agree exactly. A stale entry --
        e.g. `captions.ass`, an ffmpeg-ASS artifact of the retired assemble.py that the
        HyperFrames path never writes -- makes run_manifest.json lie about what exists.
        """
        import inspect
        import re
        from shorts_engine import harness_bridge, stage_cli

        # Read the expected set straight from the finalize command's own source, so this
        # test tracks stage_cli.py rather than duplicating its dict.
        src = inspect.getsource(getattr(stage_cli, finalize_cmd))
        match = re.search(r"artifacts = \{(.*?)\}", src, re.S)
        assert match, f"no `artifacts = {{...}}` literal found in {finalize_cmd}"
        expected = set(re.findall(r'"(\w+)":', match.group(1)))
        assert expected, f"parsed an empty artifacts dict from {finalize_cmd}"

        with mock.patch("shorts_engine.harness_bridge.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            reported = harness_bridge.run_creative_stage(mock.Mock(workspace="/ws"), phase)

        assert set(reported) == expected


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
        assert names[:9] == ["ingest", "facts", "explain", "verify_claims", "script",
                             "shotlist", "audio", "visuals", "assemble"]
        statuses = [s[1] for s in build_stages()]
        assert statuses[:9] == ["ingested", "facts", "explained", "claims_verified",
                                "scripted", "shotlisted", "audio", "visuals", "assembled"]

    def test_build_stages_explain_and_verify_claims(self):
        from shorts_engine.cli import build_stages
        from shorts_engine.stages import explain, verify_claims
        stages = build_stages()
        assert stages[2] == ("explain", "explained", explain.run)
        assert stages[3] == ("verify_claims", "claims_verified", verify_claims.run)

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
    def test_build_stages_has_twelve_in_order(self):
        from shorts_engine.cli import build_stages
        names = [s[0] for s in build_stages()]
        assert names == ["ingest", "facts", "explain", "verify_claims", "script",
                         "shotlist", "audio", "visuals", "assemble", "verify", "package", "publish"]
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
