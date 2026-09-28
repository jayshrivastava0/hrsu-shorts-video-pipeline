"""Tests for shorts_engine.harness_bridge -- the shared subprocess call into the Node harness's
real headless-agent turn, factored out of cli.py so verify.py's reassembly can reuse it without
a circular import (cli.py imports shorts_engine.stages.verify)."""
from __future__ import annotations

from unittest import mock

import pytest

from shorts_engine import harness_bridge
from shorts_engine.errors import EngineError


def test_run_creative_stage_visuals_reports_shots_and_report():
    with mock.patch("shorts_engine.harness_bridge.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        reported = harness_bridge.run_creative_stage(mock.Mock(workspace="/ws"), "visuals")
    assert reported == {"shots_dir": "shots", "visuals_report": "visuals_report.json"}


def test_run_creative_stage_assemble_reports_video_and_report():
    with mock.patch("shorts_engine.harness_bridge.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        reported = harness_bridge.run_creative_stage(mock.Mock(workspace="/ws"), "assemble")
    assert reported == {"video": "video_short.mp4", "assemble_report": "assemble_report.json"}


def test_run_creative_stage_raises_engine_error_with_stderr_on_failure():
    with mock.patch("shorts_engine.harness_bridge.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=1, stderr="agent turn trace: (no tool calls made)")
        with pytest.raises(EngineError, match="agent turn trace"):
            harness_bridge.run_creative_stage(mock.Mock(workspace="/ws"), "assemble")


def test_run_creative_stage_invokes_node_bridge_script_with_phase_and_workspace():
    ctx = mock.Mock(workspace="/ws/run-123")
    with mock.patch("shorts_engine.harness_bridge.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        harness_bridge.run_creative_stage(ctx, "assemble")
    args = mock_run.call_args[0][0]
    assert args[0] == "node"
    assert "run-visual-authoring.mts" in args[3]
    assert args[4] == "assemble"
