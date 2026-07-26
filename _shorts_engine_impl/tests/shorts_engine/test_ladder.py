from __future__ import annotations
from pathlib import Path
from PIL import Image
import pytest


def _img(tmp_path, name="a.png", size=(1600, 900)):
    p = tmp_path / name
    Image.new("RGB", size, (40, 40, 40)).save(p)
    return p


class TestTortureMode:
    def test_torture_short_circuits_everything(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        called = []
        monkeypatch.setattr(ladder, "_query_library",
                            lambda *a: called.append("lib") or [])
        out = ladder.acquire("tanks", "narration", tmp_path, [], torture=True)
        assert out["image_path"] is None
        assert out["provenance"]["reason"] == "torture_mode"
        assert called == []  # nothing searched at all


class TestOwnLibraryTier:
    def test_own_footage_accepted_at_lower_threshold(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        own = _img(tmp_path, "own.png")
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [
            {"path": str(own), "description": "clarifier tanks", "score_hint": 3}])
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: {
            "accepted_score": 5, "description": "d", "focal_hint": "left",
            "reject_reason": None})  # 5 passes OWN (>=5) but would fail API (>=6)
        out = ladder.acquire("clarifier tanks", "narr", tmp_path, [])
        assert out["image_path"] == str(own)
        assert out["focal_hint"] == "left"
        assert out["provenance"]["tiers"][0]["tier"] == "own"

    def test_judge_reject_falls_through_to_next_tier(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        own = _img(tmp_path, "own.png")
        blog_img = _img(tmp_path, "blog.png")
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [
            {"path": str(own), "description": "d", "score_hint": 2}])
        monkeypatch.setattr(ladder, "_download",
                            lambda cand, d: blog_img)
        scores = {str(own): 3, str(blog_img): 8}
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: {
            "accepted_score": scores[str(p)], "description": "d",
            "focal_hint": "center", "reject_reason": None})
        out = ladder.acquire("tanks", "narr", tmp_path,
                             [{"url": "https://blog/img1.png", "width": 1600, "height": 900}])
        assert out["image_path"] == str(blog_img)
        tiers = {t["tier"]: t for t in out["provenance"]["tiers"]}
        assert tiers["own"]["accepted"] is None
        assert tiers["blog"]["accepted"] is not None

    def test_blog_tier_accepts_real_ingest_image_shape(self, tmp_path, monkeypatch):
        """Regression: shorts_engine.stages.ingest._extract_images writes
        {"src": ..., "alt": ...} with no "url" and no dimensions -- the blog
        tier's candidate list previously filtered on i.get("url"), which is
        always absent, silently dropping every real blog image (the whole
        tier was dead in production even though every ladder test used a
        fixture shape ingest never actually produces). This test uses the
        REAL shape ingest writes."""
        from shorts_engine.sourcing import ladder
        blog_img = _img(tmp_path, "blog.png", size=(1600, 900))
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        monkeypatch.setattr(ladder, "_download", lambda cand, d: blog_img)
        monkeypatch.setattr(ladder, "_watermarked", lambda p: False)
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: {
            "accepted_score": 8, "description": "d", "focal_hint": "center",
            "reject_reason": None})
        out = ladder.acquire("tanks", "narr", tmp_path,
                             [{"src": "https://blog.example/img1.png", "alt": ""}])
        assert out["image_path"] == str(blog_img)
        blog_tier = next(t for t in out["provenance"]["tiers"] if t["tier"] == "blog")
        assert blog_tier["candidates_seen"] == 1
        assert blog_tier["accepted"]["url"] == "https://blog.example/img1.png"


class TestGatesAndBudget:
    def test_pre_gate_reject_recorded_and_final(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        # Isolate from live network: this environment has real Unsplash/
        # Openverse/Wikimedia keys configured, so an unmocked _search_tier
        # would return real candidates for the api/scrape tiers, reach the
        # judge stub below, and crash on its None return -- the test's own
        # intent is narrower (assert the ONE blacklisted blog candidate is
        # gate-rejected without ever being judged), so later tiers must find
        # nothing, matching the isolation already used by the sibling test
        # test_no_acceptance_returns_none_reason in this same class.
        monkeypatch.setattr(ladder, "_search_tier", lambda tier, q: [])
        judged = []
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: judged.append(p))
        out = ladder.acquire("tanks", "narr", tmp_path, [
            {"url": "https://www.shutterstock.com/x.jpg", "width": 1600, "height": 900}])
        blog_tier = next(t for t in out["provenance"]["tiers"] if t["tier"] == "blog")
        assert blog_tier["rejections"][0]["reason"] == "blacklisted"
        assert judged == []  # a hard gate reject is FINAL — never judged

    def test_no_acceptance_returns_none_reason(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        monkeypatch.setattr(ladder, "_search_tier", lambda tier, q: [])
        out = ladder.acquire("tanks", "narr", tmp_path, [])
        assert out["image_path"] is None
        assert out["provenance"]["reason"] == "no_acceptance"

    def test_judge_budget_capped_per_tier(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        from shorts_engine import config
        from video_agent.sources.base import RawCandidate
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        img = _img(tmp_path, "c.png")
        cands = [RawCandidate(source="s", url=f"https://ok{i}.example/i.jpg",
                              width=1600, height=900) for i in range(20)]
        monkeypatch.setattr(ladder, "_search_tier",
                            lambda tier, q: cands if tier == "api" else [])
        monkeypatch.setattr(ladder, "_download", lambda c, d: img)
        monkeypatch.setattr(ladder, "_watermarked", lambda p: False)
        judged = []
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: (judged.append(1) or {
            "accepted_score": 0, "description": "", "focal_hint": "center",
            "reject_reason": "low"}))
        ladder.acquire("tanks", "narr", tmp_path, [])
        # api tier judges at most PER_TIER_CANDIDATES (scrape tier found nothing)
        assert len(judged) == config.PER_TIER_CANDIDATES
