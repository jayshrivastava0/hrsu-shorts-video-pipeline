from __future__ import annotations
import json
from pathlib import Path
import pytest


class TestShotTimeline:
    def test_cumulative_midpoints(self):
        from shorts_engine.stages import verify
        rep = {"shots": [
            {"id": "s00", "final_duration_s": 4.0},
            {"id": "s01", "final_duration_s": 3.0},
            {"id": "s02", "final_duration_s": 5.0},
        ]}
        tl = verify.shot_timeline(rep)
        assert [t["start_s"] for t in tl] == [0.0, 4.0, 7.0]
        assert tl[1]["mid_s"] == 5.5


class TestJudgeShotFrame:
    GOOD = {"description": "a navy slide with a large gold number 92 percent and "
                           "the label nitrate removal in a white serif typeface, "
                           "sharp and clearly legible on screen",
            "visible_text": "92% nitrate removal", "quality_notes": "sharp"}

    def test_happy_path_scores(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        monkeypatch.setattr(verify, "_describe", lambda p: self.GOOD)
        monkeypatch.setattr(verify, "_verdict_call", lambda *a, **k: {
            "match_score": 8, "legible": True, "issues": []})
        out = verify.judge_shot_frame(f, "ninety two percent removal",
                                      "STAT_CARD", {"value": "92", "unit": "%"})
        assert out["match_score"] == 8 and out["legible"] is True

    def test_describe_failure_is_ungradeable_not_a_pass(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        monkeypatch.setattr(verify, "_describe", lambda p: None)
        out = verify.judge_shot_frame(f, "span", "STAT_CARD", {})
        assert out.get("ungradeable") is True


class TestRunGates:
    def _ws(self, tmp_path):
        ws = tmp_path
        (ws / "assemble_report.json").write_text(json.dumps({"shots": [
            {"id": "s00", "final_duration_s": 4.0}]}), encoding="utf-8")
        (ws / "shotlist.json").write_text(json.dumps({"shots": [
            {"id": "s00", "narration_span": "nitrate is rising",
             "type": "HEADLINE_CARD", "payload": {"text": "t"}}]}), encoding="utf-8")
        (ws / "visuals_report.json").write_text(json.dumps({"shots": [
            {"id": "s00", "rendered_type": "HEADLINE_CARD",
             "payload": {"text": "t"},
             "provenance": {"resolved": "designed"}}]}), encoding="utf-8")
        (ws / "video_short.mp4").write_bytes(b"fake")
        return ws

    def test_all_pass_no_failures(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 9, "legible": True, "issues": []})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        assert out["failures"] == []

    def test_heuristic_audio_defect_routes_to_heuristic_audio(self, tmp_path, monkeypatch):
        """Regression: video_agent.harness.verify_heuristic stores MEASUREMENTS
        in `checks` (audio_rms, resolution tuples, ...), never booleans -- a
        classification that scanned `checks` for `v is False` could never
        match, so a real audio failure always fell through to
        heuristic_safezone (bumping caption margins, which cannot fix broken
        audio). The real pass/fail signal lives in `defects`, a list of
        human-readable strings ("Audio RMS too low (...)")."""
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR:
            passed = False
            checks = {"audio_rms": 12.0, "duration_s": 40.0}
            defects = ["Audio RMS too low (12.0 < 250.0): effectively silent"]
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 9, "legible": True, "issues": []})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        global_failures = [f for f in out["failures"] if f["id"] == "_global"]
        assert global_failures[0]["kind"] == "heuristic_audio"

    def test_heuristic_non_audio_defect_routes_to_safezone(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR:
            passed = False
            checks = {"safezone": [3, 7], "duration_s": 40.0}
            defects = ["Captions exceed safe zone at frames: [3, 7]"]
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 9, "legible": True, "issues": []})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        global_failures = [f for f in out["failures"] if f["id"] == "_global"]
        assert global_failures[0]["kind"] == "heuristic_safezone"

    def test_heuristic_dark_ribbon_defect_gets_its_own_kind(self, tmp_path, monkeypatch):
        """Regression: confirmed live that shorts_engine's navy-branded
        cards trip the reused dark-ribbon heuristic (bottom-strip luma
        ~19-20 vs. a 24 floor). A caption-margin bump cannot fix background
        luma, so this defect needs its OWN kind, distinct from the vague
        heuristic_safezone catch-all, so apply_fixes can route it to a real
        fix (the accent-band flag)."""
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR:
            passed = False
            checks = {"dark_ribbon": [0, 1, 2, 3, 4], "duration_s": 40.0}
            defects = ["Dark ribbon persistent across all 5 sampled frames"]
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 9, "legible": True, "issues": []})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        global_failures = [f for f in out["failures"] if f["id"] == "_global"]
        assert global_failures[0]["kind"] == "heuristic_dark_ribbon"

    def test_illegible_designed_shot_flagged(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 7, "legible": False, "issues": ["caption too small"]})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        assert out["failures"][0]["kind"] == "legibility"

    def test_ungradeable_raises_engine_error(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        from shorts_engine.errors import EngineError
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame",
                            lambda *a, **k: {"ungradeable": True})
        class Ctx: workspace = ws; flags = {}
        with pytest.raises(EngineError, match="ungradeable"):
            verify.run_gates(Ctx())
