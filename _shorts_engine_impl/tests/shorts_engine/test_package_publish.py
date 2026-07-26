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
        arts = package.run(Ctx(ws))
        pkg = json.loads((ws / arts["publish_package"]).read_text(encoding="utf-8"))
        assert pkg["title"] == "T" and pkg["privacy_status"] == "unlisted"
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
