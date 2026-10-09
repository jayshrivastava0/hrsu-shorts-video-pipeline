from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


def _ws(tmp_path):
    ws = tmp_path
    (ws / "post.json").write_text(json.dumps({
        "title": "Optimizing Nitrate Removal", "region": "eu",
        "category": "wastewater_treatment"}), encoding="utf-8")
    (ws / "script.json").write_text(json.dumps({"beats": [
        {"beat": "hook", "narration": "n", "card_text": "Nitrate limits tightening",
         "fact_ids": [], "broll_wish": ""}]}), encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({"facts": [
        {"id": "f1", "claim_summary": "dosing window 1.5-3 kg/m3",
         "procurement_significance": 5, "verbatim_quote": "q", "value": "1.5",
         "unit": "kg"},
        {"id": "f2", "claim_summary": "92 percent removal",
         "procurement_significance": 4, "verbatim_quote": "q", "value": "92",
         "unit": "%"},
    ]}), encoding="utf-8")
    (ws / "word_timings.json").write_text(json.dumps([
        {"word": "nitrate", "start": 0.0, "end": 0.4},
        {"word": "limits", "start": 0.4, "end": 0.8}]), encoding="utf-8")
    (ws / "video_short.mp4").write_bytes(b"fake")
    return ws


class Ctx:
    def __init__(self, ws, flags=None):
        self.workspace = ws
        self.flags = flags or {}
        self.manifest = MagicMock(blog_url="https://blog.hrsuindore.com/x.html",
                                  slug="x")


class TestPackage:
    def test_package_writes_all_artifacts(self, tmp_path, monkeypatch):
        from shorts_engine.stages import package
        ws = _ws(tmp_path)
        fake_pkg = MagicMock(title="T", description="D", tags=["a"],
                             category_id="28", privacy_status="unlisted",
                             thumbnail_path=str(ws / "th.jpg"),
                             caption_srt_path=str(ws / "subtitles.srt"))
        monkeypatch.setattr(package, "_package_for_youtube",
                            lambda sb, br, w: fake_pkg)
        monkeypatch.setattr(package, "probe_duration", lambda p: 45.0)
        arts = package.run(Ctx(ws))
        pkg = json.loads((ws / arts["publish_package"]).read_text(encoding="utf-8"))
        assert pkg["title"] == "T" and pkg["privacy_status"] == "unlisted"
        assert pkg["format"] == "short" and pkg["duration_s"] == 45.0
        cap = (ws / arts["linkedin_caption"]).read_text(encoding="utf-8")
        assert "Nitrate limits tightening" in cap
        assert "dosing window" in cap and "hrsuindore.com/x.html" in cap
        srt = (ws / arts["captions_srt"]).read_text(encoding="utf-8")
        assert "-->" in srt and "NITRATE" in srt

    def test_hero_claim_is_a_real_heroclaim_object(self, tmp_path, monkeypatch):
        """package_for_youtube (video_agent) does hero_claim.stat and
        hero_claim.claim_text -- a plain str blows up with AttributeError.
        Regression test for the PACKAGE stage crash hit on the Task 15 live
        run: 'str' object has no attribute 'stat'."""
        from shorts_engine.stages import package
        from video_agent.storyboard import HeroClaim
        ws = _ws(tmp_path)
        seen = {}
        monkeypatch.setattr(package, "_package_for_youtube",
                            lambda sb, br, w: (seen.update(h=sb.hero_claim, br=br)
                                               or MagicMock(title="T", description="", tags=[],
                                                            category_id="28", privacy_status="unlisted",
                                                            thumbnail_path=None, caption_srt_path=None)))
        package.run(Ctx(ws))
        assert isinstance(seen["h"], HeroClaim)
        assert seen["h"].claim_text == "Nitrate limits tightening"
        assert seen["h"].stat == "dosing window 1.5-3 kg/m3"
        assert seen["br"]["region"] == "eu"


def _plan_claim(cid, text, kind="blog_stated", verdict="supported"):
    return {"id": cid, "text": text, "kind": kind, "verdict": verdict,
            "support": {"type": None, "quote": "", "url": None}, "needs_number": False}


def _write_plan(ws, status="verified"):
    plan = {"question": "q", "status": status, "payoff": {"takeaway": "t",
                                                          "differentiator_id": "b_purity"},
            "steps": [
                {"step_id": "s1", "claims": [
                    _plan_claim("c1", "Plants dose 1.5 to 3 kg per cubic meter."),
                    _plan_claim("c0", "Like a sponge soaking water.", kind="illustrative"),
                    _plan_claim("cx", "An unsupported leap.", verdict="unsupported")]},
                {"step_id": "s2", "claims": [
                    _plan_claim("c2", "Bacteria convert nitrate to nitrogen gas.",
                                kind="external_fact")]},
                {"step_id": "s3", "claims": [
                    _plan_claim("c3", "So the dose is tuned per site.", kind="reasoning"),
                    _plan_claim("c4", "A fourth verified claim.", kind="reasoning")]},
            ]}
    (ws / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")


class TestPackageFromVerifiedPlan:
    """Published metadata comes from the verified plan's supported claims, never from
    the factsheet's unverified claim_summary paraphrases (review I2)."""

    def _run(self, ws, monkeypatch):
        from shorts_engine.stages import package
        seen = {}
        monkeypatch.setattr(package, "_package_for_youtube",
                            lambda sb, br, w: (seen.update(h=sb.hero_claim)
                                               or MagicMock(title="T", description="", tags=[],
                                                            category_id="28",
                                                            privacy_status="unlisted",
                                                            thumbnail_path=None,
                                                            caption_srt_path=None)))
        monkeypatch.setattr(package, "probe_duration", lambda p: 45.0)
        package.run(Ctx(ws))
        return seen["h"], (ws / "linkedin_caption.txt").read_text(encoding="utf-8")

    def test_caption_and_stat_use_supported_claims_in_chain_order(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path)
        _write_plan(ws)
        hero, cap = self._run(ws, monkeypatch)
        assert hero.stat == "Plants dose 1.5 to 3 kg per cubic meter."
        assert "- Plants dose 1.5 to 3 kg per cubic meter." in cap
        assert "- Bacteria convert nitrate to nitrogen gas." in cap
        assert "- So the dose is tuned per site." in cap
        assert "A fourth verified claim." not in cap          # first 3 only
        assert "sponge" not in cap and "unsupported leap" not in cap
        assert "dosing window" not in cap and "92 percent" not in cap

    def test_unverified_plan_never_falls_back_to_factsheet(self, tmp_path, monkeypatch):
        ws = _ws(tmp_path)
        _write_plan(ws, status="held")
        hero, cap = self._run(ws, monkeypatch)
        assert hero.stat == ""
        assert "dosing window" not in cap and "92 percent" not in cap


class TestPublish:
    def _pkg(self, ws):
        (ws / "publish_package.json").write_text(json.dumps({
            "title": "T", "description": "D", "tags": ["a"], "category_id": "28",
            "privacy_status": "unlisted", "thumbnail_path": None,
            "caption_srt_path": None}), encoding="utf-8")

    def test_default_is_dry_run(self, tmp_path, monkeypatch):
        from shorts_engine.stages import publish
        ws = _ws(tmp_path); self._pkg(ws)
        seen = {}
        def fake_pub(package, video_path, workspace, dry_run=False):
            seen["dry_run"] = dry_run
            return MagicMock(video_id="DRY_RUN_1", url="", platform="youtube")
        monkeypatch.setattr(publish, "_publish_to_youtube", fake_pub)
        publish.run(Ctx(ws))
        assert seen["dry_run"] is True

    def test_publish_flag_uploads_for_real(self, tmp_path, monkeypatch):
        from shorts_engine.stages import publish
        ws = _ws(tmp_path); self._pkg(ws)
        seen = {}
        def fake_pub(package, video_path, workspace, dry_run=False):
            seen["dry_run"] = dry_run
            return MagicMock(video_id="abc123", url="https://youtu.be/abc123",
                             platform="youtube")
        monkeypatch.setattr(publish, "_publish_to_youtube", fake_pub)
        arts = publish.run(Ctx(ws, flags={"publish": True}))
        assert seen["dry_run"] is False
        res = json.loads((ws / arts["publish_result"]).read_text(encoding="utf-8"))
        assert res["video_id"] == "abc123"


def test_format_label_boundaries():
    from shorts_engine import config
    from shorts_engine.stages.package import format_label
    assert format_label(45.0) == "short"
    assert format_label(config.SHORT_FORM_MAX_S) == "short"
    assert format_label(config.SHORT_FORM_MAX_S + 0.1) == "long"
    assert format_label(None) == "unknown"


def _run_package(ws, monkeypatch):
    from shorts_engine.stages import package
    fake_pkg = MagicMock(title="T", description="D", tags=["a"],
                         category_id="28", privacy_status="unlisted",
                         thumbnail_path=None, caption_srt_path=None)
    monkeypatch.setattr(package, "_package_for_youtube", lambda sb, br, w: fake_pkg)
    arts = package.run(Ctx(ws))
    return json.loads((ws / arts["publish_package"]).read_text(encoding="utf-8"))


def test_package_labels_long_video(tmp_path, monkeypatch):
    from shorts_engine.stages import package
    ws = _ws(tmp_path)
    monkeypatch.setattr(package, "probe_duration", lambda p: 240.0)
    pkg = _run_package(ws, monkeypatch)
    assert pkg["format"] == "long" and pkg["duration_s"] == 240.0


def test_package_format_unknown_without_video(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / "video_short.mp4").unlink()
    pkg = _run_package(ws, monkeypatch)
    assert pkg["format"] == "unknown" and pkg["duration_s"] is None


def test_package_survives_probe_failure(tmp_path, monkeypatch):
    from shorts_engine.stages import package
    ws = _ws(tmp_path)
    def boom(p):
        from shorts_engine.errors import EngineError
        raise EngineError("ffprobe failed")
    monkeypatch.setattr(package, "probe_duration", boom)
    pkg = _run_package(ws, monkeypatch)
    assert pkg["format"] == "unknown" and pkg["duration_s"] is None


def test_package_output_round_trips_through_publish(tmp_path, monkeypatch):
    from shorts_engine.stages import package, publish
    ws = _ws(tmp_path)
    monkeypatch.setattr(package, "probe_duration", lambda p: 45.0)
    _run_package(ws, monkeypatch)
    seen = {}
    def fake_pub(pkg, video_path, workspace, dry_run=False):
        seen["pkg"] = pkg
        seen["dry_run"] = dry_run
        return MagicMock(video_id="DRY_RUN_1", url="", platform="youtube")
    monkeypatch.setattr(publish, "_publish_to_youtube", fake_pub)
    arts = publish.run(Ctx(ws))
    assert seen["dry_run"] is True and seen["pkg"].title == "T"
    res = json.loads((ws / arts["publish_result"]).read_text(encoding="utf-8"))
    assert res["video_id"] == "DRY_RUN_1"


def test_publish_ignores_unknown_package_keys(tmp_path, monkeypatch):
    from shorts_engine.stages import publish
    ws = _ws(tmp_path)
    (ws / "publish_package.json").write_text(json.dumps({
        "title": "T", "description": "D", "tags": ["a"], "category_id": "28",
        "privacy_status": "unlisted", "thumbnail_path": None,
        "caption_srt_path": None, "something_new": 1}), encoding="utf-8")
    monkeypatch.setattr(publish, "_publish_to_youtube",
                        lambda *a, **k: MagicMock(video_id="v", url="", platform="youtube"))
    publish.run(Ctx(ws))
    assert (ws / "publish_result.json").exists()
