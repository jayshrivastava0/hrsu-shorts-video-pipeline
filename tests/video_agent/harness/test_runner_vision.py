"""VISION phase loop: pass-through, revise-then-pass, hold after budget,
hold queue file written."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from video_agent.harness import runner as runner_mod
from video_agent.harness.manifest import (
    new_manifest, save_manifest, VisionReport, SceneGrade,
)


def _manifest(tmp_path, status="verified"):
    m = new_manifest("https://blog.hrsuindore.com/x.html", "x", str(tmp_path))
    m.status = status
    m.video_path = str(tmp_path / "video_short.mp4")
    m.storyboard_path = str(tmp_path / "storyboard.json")
    return m


def _passing():
    return VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])


def _actionable():
    return VisionReport(passed=False, hold=False, scenes=[
        SceneGrade(0, 3.0, defects=[{"code": "visual_mismatch",
                                     "detail": ""}])])


def _hold():
    return VisionReport(passed=False, hold=True,
                        scenes=[SceneGrade(0, 6.0)])


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Patch every collaborator of _phase_vision."""
    mocks = {}
    monkeypatch.setattr(runner_mod, "load_storyboard_for_vision",
                        MagicMock(return_value=MagicMock()), raising=False)
    for name in ("grade_video", "route_defects", "apply_actions",
                 "_revise_sourcer", "_phase_render", "verify_heuristic"):
        mocks[name] = MagicMock()
        monkeypatch.setattr(runner_mod, name, mocks[name], raising=False)
    mocks["verify_heuristic"].return_value = MagicMock(passed=True,
                                                       defects=[])
    return mocks


def test_vision_pass_sets_status(tmp_path, patched):
    patched["grade_video"].return_value = _passing()
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "vision_verified"
    assert m.vision.passed


def test_actionable_revises_then_passes(tmp_path, patched):
    patched["grade_video"].side_effect = [_actionable(), _passing()]
    patched["route_defects"].return_value = [("re_source", 0),
                                             ("re_render", None)]
    patched["apply_actions"].return_value = True
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "vision_verified"
    assert m.vision.cycles_used == 1
    patched["_phase_render"].assert_called_once()


def test_budget_exhausted_holds(tmp_path, patched):
    patched["grade_video"].return_value = _actionable()
    patched["route_defects"].return_value = [("re_render", None)]
    patched["apply_actions"].return_value = True
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "hold_for_review"
    # 1 initial + VISION_MAX_REVISE_CYCLES re-grades
    assert patched["grade_video"].call_count == 3


def test_hold_band_holds_immediately_and_writes_queue(tmp_path, patched,
                                                      monkeypatch):
    monkeypatch.setattr(runner_mod, "REVIEW_QUEUE_PATH",
                        str(tmp_path / "review_queue.json"))
    patched["grade_video"].return_value = _hold()
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "hold_for_review"
    patched["apply_actions"].assert_not_called()
    queue = json.loads((tmp_path / "review_queue.json").read_text())
    assert queue[0]["run_id"] == m.run_id
