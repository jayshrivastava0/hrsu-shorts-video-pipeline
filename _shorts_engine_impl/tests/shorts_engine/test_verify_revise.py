from __future__ import annotations
import json
from pathlib import Path
import pytest


def _ws(tmp_path):
    ws = tmp_path
    (ws / "shots").mkdir()
    (ws / "verify").mkdir()
    (ws / "assemble_report.json").write_text(json.dumps({"shots": [
        {"id": "s00", "final_duration_s": 4.0}],
        "voice_total_s": 4.0, "video_duration_s": 5.5,
        "music_used": True}), encoding="utf-8")
    (ws / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 4.0,
         "narration_span": "n", "payload": {"wish": "w"},
         "fallback": {"type": "HEADLINE_CARD", "payload": {"text": "Fallback headline"}}}
    ]}), encoding="utf-8")
    (ws / "visuals_report.json").write_text(json.dumps({"shots": [
        {"id": "s00", "beat": "hook", "rendered_type": "BROLL",
         "payload": {"image_path": "x.png", "layout": "auto"},
         "duration_s": 4.0, "fade_in_s": 0.0, "content_pixels": 9000,
         "provenance": {"resolved": "acquired"}}]}), encoding="utf-8")
    (ws / "video_short.mp4").write_bytes(b"fake")
    # Minimal fixtures so contact_sheet.build (invoked by verify.run) doesn't
    # raise FileNotFoundError -- the _ws() helper as given in the task brief
    # only covers assemble/shotlist/visuals/video; script.json and
    # factsheet.json are also read by contact_sheet.build.
    (ws / "script.json").write_text(json.dumps({"beats": [
        {"beat": "hook", "narration": "n", "card_text": "c"}]}), encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    return ws


class TestApplyFixes:
    def test_broll_mismatch_swaps_to_fallback_and_rerenders(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        rendered = []
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: rendered.append((sid, rtype)))
        class Ctx: workspace = ws; flags = {}
        fixes = verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "broll_mismatch", "score": 3}])
        assert rendered == [("s00", "HEADLINE_CARD")]
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        assert vis["shots"][0]["rendered_type"] == "HEADLINE_CARD"
        assert vis["shots"][0]["provenance"]["reason"] == "verify_rejected"
        assert any("fallback" in f for f in fixes)

    def test_legibility_shortens_dominant_text(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        vis["shots"][0]["rendered_type"] = "HEADLINE_CARD"
        vis["shots"][0]["payload"] = {"text": "one two three four five six seven eight nine ten"}
        (ws / "visuals_report.json").write_text(json.dumps(vis), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: captured.update(payload))
        class Ctx: workspace = ws; flags = {}
        verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "legibility", "issues": []}])
        assert len(captured["text"].split()) == 7  # 10 * 0.7

    def test_legibility_shortens_logo_cta_fields(self, tmp_path, monkeypatch):
        """Regression: _TEXT_FIELDS originally covered only HEADLINE/STAT/
        QUOTE/PAPER's flat text fields -- a legibility failure on a LOGO_CTA
        shot matched none of them, so the "fix" re-rendered an IDENTICAL
        payload while the fix log lied about having shortened something."""
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        vis["shots"][0]["rendered_type"] = "LOGO_CTA"
        vis["shots"][0]["payload"] = {
            "differentiator": "one two three four five six seven eight",
            "cta_line": "c", "domain": "hrsuindore.com"}
        (ws / "visuals_report.json").write_text(json.dumps(vis), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: captured.update(payload))
        class Ctx: workspace = ws; flags = {}
        fixes = verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "legibility", "issues": []}])
        assert len(captured["differentiator"].split()) == 5  # 8 * 0.7 (int-truncated)
        assert "shortened differentiator" in fixes[0]

    def test_legibility_shortens_diagram_labels(self, tmp_path, monkeypatch):
        """Regression: DIAGRAM/flow's on-screen text lives in a `labels`
        list, not a flat _TEXT_FIELDS string -- previously unfixable."""
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        vis["shots"][0]["rendered_type"] = "DIAGRAM"
        vis["shots"][0]["payload"] = {"template": "flow",
                                     "labels": ["Effluent inflow point", "Dosing station here"]}
        (ws / "visuals_report.json").write_text(json.dumps(vis), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: captured.update(payload))
        class Ctx: workspace = ws; flags = {}
        fixes = verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "legibility", "issues": []}])
        assert all(len(l.split()) <= 2 for l in captured["labels"])
        assert "shortened labels" in fixes[0]

    def test_heuristic_dark_ribbon_sets_fix_flag(self, tmp_path, monkeypatch):
        """The dark-ribbon fix is a real ASSEMBLE-level flag (accent band),
        not a caption-margin bump -- confirm apply_fixes sets it and only
        it (doesn't also touch caption_margin_bump)."""
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        class Ctx: workspace = ws; flags = {}
        ctx = Ctx()
        fixes = verify.apply_fixes(ctx, [{"id": "_global", "kind": "heuristic_dark_ribbon"}])
        assert ctx.flags["dark_ribbon_fix"] is True
        assert "caption_margin_bump" not in ctx.flags
        assert "dark-ribbon accent band" in fixes[0]

    def test_legibility_with_no_fixable_field_logs_honestly(self, tmp_path, monkeypatch):
        """A shot payload with no _TEXT_FIELDS string and no `labels` list
        (e.g. a bare image-only BROLL payload) has no deterministic
        legibility fix -- the fix log must say so rather than claiming a
        shortening that never happened, and _render_shot must not be
        called with an unchanged payload disguised as a fix."""
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        vis["shots"][0]["rendered_type"] = "BROLL"
        vis["shots"][0]["payload"] = {"image_path": "x.png", "layout": "auto"}
        (ws / "visuals_report.json").write_text(json.dumps(vis), encoding="utf-8")
        rendered = []
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: rendered.append(1))
        class Ctx: workspace = ws; flags = {}
        fixes = verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "legibility", "issues": []}])
        assert rendered == []
        assert "no deterministic legibility fix" in fixes[0]


class TestRunLoop:
    def test_clean_gates_write_report_and_sheet(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        monkeypatch.setattr(verify, "run_gates", lambda ctx: {
            "heuristic": {"passed": True}, "shots": [
                {"id": "s00", "frame": str(ws / "verify" / "f.png"),
                 "match_score": 9, "legible": True, "issues": []}],
            "failures": []})
        class Ctx: workspace = ws; flags = {}
        arts = verify.run(Ctx())
        rep = json.loads((ws / arts["verify_report"]).read_text(encoding="utf-8"))
        assert rep["cycles"] == 1 and rep["fixes_applied"] == []
        assert (ws / arts["contact_sheet"]).exists()
        html = (ws / arts["contact_sheet"]).read_text(encoding="utf-8")
        assert "video_short.mp4" in html and "s00" in html

    def test_failures_fixed_then_pass_within_cycle_budget(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        gates = [
            {"heuristic": {"passed": True}, "shots": [],
             "failures": [{"id": "s00", "kind": "broll_mismatch", "score": 3}]},
            {"heuristic": {"passed": True}, "shots": [], "failures": []},
        ]
        monkeypatch.setattr(verify, "run_gates", lambda ctx: gates.pop(0))
        fixed = []
        monkeypatch.setattr(verify, "apply_fixes",
                            lambda ctx, failures: fixed.append(1) or ["swap"])
        monkeypatch.setattr(verify, "_reassemble", lambda ctx: None)
        class Ctx: workspace = ws; flags = {}
        arts = verify.run(Ctx())
        rep = json.loads((ws / arts["verify_report"]).read_text(encoding="utf-8"))
        assert rep["cycles"] == 2 and fixed == [1]

    def test_residual_failures_after_budget_raise(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        from shorts_engine.errors import EngineError
        ws = _ws(tmp_path)
        bad = {"heuristic": {"passed": True}, "shots": [],
               "failures": [{"id": "s00", "kind": "legibility", "issues": []}]}
        monkeypatch.setattr(verify, "run_gates", lambda ctx: bad)
        monkeypatch.setattr(verify, "apply_fixes", lambda ctx, f: ["shrink"])
        monkeypatch.setattr(verify, "_reassemble", lambda ctx: None)
        class Ctx: workspace = ws; flags = {}
        with pytest.raises(EngineError, match="revise"):
            verify.run(Ctx())


class TestBuildAssMargin:
    def test_margin_v_default_unchanged(self, tmp_path):
        from shorts_engine.stages import assemble
        out = assemble.build_ass([], tmp_path / "c.ass")
        text = out.read_text(encoding="utf-8")
        assert ",72,72,440,1\n" in text  # default MarginV still 440

    def test_margin_v_parameterized(self, tmp_path):
        from shorts_engine.stages import assemble
        out = assemble.build_ass([], tmp_path / "c.ass", margin_v=480)
        text = out.read_text(encoding="utf-8")
        assert ",72,72,480,1\n" in text
