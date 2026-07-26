"""Deterministic pre-judge gates: blacklist, resolution, watermark, dedupe."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace


def _cand(url="https://example.com/a.jpg", w=1600, h=900):
    return SimpleNamespace(url=url, width=w, height=h)


class TestSourcingConstants:
    def test_constants_exist(self):
        from shorts_engine import config
        assert config.MIN_LONG_EDGE_PX == 1280
        assert config.PER_TIER_CANDIDATES == 8
        assert (config.JUDGE_MIN_OWN, config.JUDGE_MIN_BLOG,
                config.JUDGE_MIN_API, config.JUDGE_MIN_SCRAPE) == (5, 6, 6, 7)
        assert config.VERIFY_MAX_REVISE_CYCLES == 2
        for d in ("shutterstock.com", "gettyimages.com", "istockphoto.com",
                  "alamy.com", "dreamstime.com", "123rf.com",
                  "depositphotos.com", "ftcdn.net"):
            assert d in config.DOMAIN_BLACKLIST, d

    def test_refusal_and_watermark_terms(self):
        from shorts_engine import config
        assert "cannot see" in config.VISION_REFUSAL_PHRASES
        assert "unable to" in config.VISION_REFUSAL_PHRASES
        assert "shutterstock" in config.WATERMARK_TERMS


class TestBlacklist:
    def test_blacklisted_domain_and_subdomain(self):
        from shorts_engine.sourcing import gates
        assert gates.blacklisted("https://www.shutterstock.com/image/x.jpg")
        assert gates.blacklisted("https://cdn.shutterstock.com/x.jpg")
        assert not gates.blacklisted("https://commons.wikimedia.org/x.jpg")

    def test_lookalike_domain_not_blacklisted(self):
        from shorts_engine.sourcing import gates
        # substring match would wrongly flag this; suffix-label match must not
        assert not gates.blacklisted("https://notshutterstock.com/x.jpg")


class TestResolutionAndDedupe:
    def test_resolution_long_edge(self):
        from shorts_engine.sourcing import gates
        assert gates.resolution_ok(1280, 720)
        assert gates.resolution_ok(720, 1280)
        assert not gates.resolution_ok(1279, 720)
        assert not gates.resolution_ok(0, 0)

    def test_seen_before_mutates_and_detects(self):
        from shorts_engine.sourcing import gates
        seen: set[str] = set()
        assert not gates.seen_before("https://a.com/x.jpg", seen)
        assert gates.seen_before("https://a.com/x.jpg", seen)
        assert not gates.seen_before("https://a.com/y.jpg", seen)


class TestRunPreGates:
    def test_order_and_reasons(self):
        from shorts_engine.sourcing import gates
        seen: set[str] = set()
        assert gates.run_pre_gates(
            _cand("https://alamy.com/x.jpg"), seen) == "blacklisted"
        c = _cand()
        assert gates.run_pre_gates(c, seen) is None
        assert gates.run_pre_gates(c, seen) == "duplicate"
        assert gates.run_pre_gates(
            _cand("https://b.com/lo.jpg", 640, 480), seen) == "low_resolution"
