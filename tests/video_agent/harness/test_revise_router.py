"""Defect -> action routing. Re-source actions are per-scene; re-render is
global and deduplicated (one re-render covers every text defect)."""
from unittest.mock import MagicMock, patch
from video_agent.harness.manifest import SceneGrade, VisionReport
from video_agent.harness.revise_router import route_defects, apply_actions


def _report(*scene_defects):
    scenes = []
    for i, defects in enumerate(scene_defects):
        scenes.append(SceneGrade(
            index=i, overall=3.0,
            defects=[{"code": c, "detail": ""} for c in defects]))
    return VisionReport(passed=False, hold=False, scenes=scenes)


def test_visual_mismatch_routes_to_re_source():
    actions = route_defects(_report(["visual_mismatch"]))
    assert ("re_source", 0) in actions


def test_text_defects_route_to_single_re_render():
    actions = route_defects(_report(["text_clipped"], ["text_unreadable"]))
    assert actions.count(("re_render", None)) == 1


def test_re_source_implies_re_render_not_duplicated():
    actions = route_defects(_report(["visual_mismatch", "text_clipped"]))
    assert ("re_source", 0) in actions
    assert actions.count(("re_render", None)) == 1


def test_low_quality_and_off_brand_re_source():
    actions = route_defects(_report(["low_quality"], ["off_brand"]))
    assert ("re_source", 0) in actions and ("re_source", 1) in actions


def test_unknown_codes_yield_no_actions():
    assert route_defects(_report(["ungradeable"])) == []


def test_apply_actions_re_sources_then_saves(tmp_path):
    sb = MagicMock()
    scene = MagicMock(); scene.index = 0
    sb.scenes = [scene]
    sb.blog = {"category": "mining"}
    sb.narrative_thread = []
    sb.hero_claim = None
    sourcer = MagicMock()
    with patch("video_agent.harness.revise_router.save_storyboard") as save:
        apply_actions([("re_source", 0)], sb, sourcer, tmp_path)
    sourcer.re_source_scene.assert_called_once()
    save.assert_called_once()
