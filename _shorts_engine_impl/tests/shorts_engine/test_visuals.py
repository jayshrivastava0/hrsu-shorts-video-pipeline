"""Tests for the VISUALS stage."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

SHOTS = {"shots": [
    {"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 2.0,
     "narration_span": "x", "payload": {"text": "Nitrate limits tighten"},
     "fallback": None},
    {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 2.0,
     "narration_span": "x", "payload": {"marker": 2, "url": "https://mdpi.com/x"},
     "fallback": {"type": "QUOTE_CARD",
                  "payload": {"quote": "dosage range of 1.5 to 3 kg",
                              "source": "Source [2] — mdpi.com"}}},
    {"id": "s02", "beat": "cta", "type": "LOGO_CTA", "duration_s": 2.0,
     "narration_span": "x", "payload": {"differentiator": "high-purity",
                                        "cta_line": "guide", "domain": "hrsuindore.com"},
     "fallback": None},
], "total_s": 6.0}


class TestResolveShot:
    def test_designed_resolves_to_itself(self):
        from shorts_engine.stages import visuals
        t, p, prov = visuals.resolve_shot(SHOTS["shots"][0])
        assert t == "HEADLINE_CARD" and prov["resolved"] == "designed"

    def test_paper_card_resolves_to_fallback(self):
        from shorts_engine.stages import visuals
        t, p, prov = visuals.resolve_shot(SHOTS["shots"][1])
        assert t == "QUOTE_CARD"
        assert prov["resolved"] == "fallback"
        assert prov["planned_type"] == "PAPER_CARD"

    def test_paper_card_without_fallback_raises(self):
        from shorts_engine.stages import visuals
        from shorts_engine.errors import EngineError
        bad = dict(SHOTS["shots"][1], fallback=None)
        with pytest.raises(EngineError):
            visuals.resolve_shot(bad)


class TestRun:
    def _ctx(self, tmp_path):
        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = Path(m.workspace)
        (ws / "shotlist.json").write_text(json.dumps(SHOTS), encoding="utf-8")
        (ws / "post.json").write_text(json.dumps({"images": []}), encoding="utf-8")
        return StageContext(manifest=m, workspace=ws, flags={})

    def test_all_shots_render_with_content(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from shorts_engine.cards import encoder
        # PAPER_CARD acquisition must not make a real network call in a unit
        # test — force a deterministic miss so it falls back, as asserted below.
        monkeypatch.setattr(visuals, "_fetch_front_page", lambda url, torture: None)
        ctx = self._ctx(tmp_path)
        arts = visuals.run(ctx)
        ws = Path(ctx.workspace)
        report = json.loads((ws / arts["visuals_report"]).read_text(encoding="utf-8"))
        assert len(report["shots"]) == 3
        for entry in report["shots"]:
            clip = ws / "shots" / f"shot_{entry['id']}.mp4"
            assert clip.exists()
            assert entry["content_pixels"] >= 500
        assert report["shots"][1]["provenance"]["resolved"] == "fallback"
        # beat boundary fade: s01 (proof, not first beat) got fade_in
        assert report["shots"][1]["fade_in_s"] == 0.25
        assert report["shots"][0]["fade_in_s"] == 0.0

    def test_blank_render_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from shorts_engine.errors import EngineError
        monkeypatch.setitem(visuals.RENDERERS, "HEADLINE_CARD",
                            _blank_renderer())
        with pytest.raises(EngineError, match="content"):
            visuals.run(self._ctx(tmp_path))


def _blank_renderer():
    from shorts_engine.cards import theme
    def render(payload, duration, out_path, fade_in_s=0.0):
        return theme.render_card(lambda p, t, d: theme.background(t), payload,
                                 duration, out_path, fade_in_s)
    return render


class TestAcquisitionResolution:
    def _broll_shot(self):
        return {"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 2.0,
                "narration_span": "nitrate is rising",
                "payload": {"wish": "aeration basin", "layout": "auto"},
                "fallback": {"type": "HEADLINE_CARD",
                             "payload": {"text": "Nitrate limits tighten"}}}

    def test_broll_acquired_resolves_with_image(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from PIL import Image
        img = tmp_path / "b.png"
        Image.new("RGB", (1600, 900), (60, 60, 60)).save(img)
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: {
            "image_path": str(img), "focal_hint": "left",
            "provenance": {"tiers": [{"tier": "own"}], "reason": None}})
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert rtype == "BROLL"
        assert payload["image_path"] == str(img)
        assert payload["layout"] == "inset"      # off-center focal hint
        assert prov["resolved"] == "acquired"

    def test_broll_miss_resolves_to_fallback(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: {
            "image_path": None, "focal_hint": "center",
            "provenance": {"tiers": [], "reason": "no_acceptance"}})
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert rtype == "HEADLINE_CARD"
        assert prov["resolved"] == "fallback"
        assert prov["reason"] == "no_acceptance"

    def test_torture_flag_reaches_ladder(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        seen = {}
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: (seen.update(kw) or {
            "image_path": None, "focal_hint": "center",
            "provenance": {"tiers": [], "reason": "torture_mode"}}))
        class Ctx: workspace = tmp_path; flags = {"torture": True}
        visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert seen["torture"] is True

    def test_paper_card_acquired_renders_paper(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from PIL import Image
        page = tmp_path / "page.png"
        Image.new("RGB", (1600, 2100), (250, 250, 250)).save(page)
        monkeypatch.setattr(visuals, "_fetch_front_page", lambda url, torture: page)
        shot = {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                "narration_span": "x",
                "payload": {"marker": 12, "url": "https://arxiv.org/pdf/2602.21290",
                            "highlight": "92 percent removal"},
                "fallback": {"type": "QUOTE_CARD",
                             "payload": {"quote": "q", "source": "s"}}}
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(shot, Ctx(), {"images": []})
        assert rtype == "PAPER_CARD"
        assert payload["image_path"] == str(page)
        assert "arxiv.org" in payload["citation"]
        assert prov["resolved"] == "acquired"

    def test_paper_fetch_failure_keeps_quote_fallback(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        monkeypatch.setattr(visuals, "_fetch_front_page", lambda url, torture: None)
        shot = {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                "narration_span": "x",
                "payload": {"marker": 12, "url": "https://arxiv.org/pdf/x",
                            "highlight": "h"},
                "fallback": {"type": "QUOTE_CARD",
                             "payload": {"quote": "q", "source": "s"}}}
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(shot, Ctx(), {"images": []})
        assert rtype == "QUOTE_CARD" and prov["resolved"] == "fallback"

    def test_legacy_one_arg_call_still_resolves_fallback(self):
        # Compatibility: resolve_shot(shot) with no ctx behaves like torture
        # (used by assemble.py's fallback path when visuals_report.json is absent)
        from shorts_engine.stages import visuals
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot())
        assert rtype == "HEADLINE_CARD" and prov["resolved"] == "fallback"
