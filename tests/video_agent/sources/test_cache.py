from pathlib import Path
from video_agent.sources.cache import QueryCache
from video_agent.sources.base import RawCandidate


def test_cache_round_trip(tmp_path):
    cache = QueryCache(tmp_path)
    cands = [RawCandidate(source="unsplash", url="u1",
                          caption="c", width=1920, height=1080)]
    cache.put("acid mine drainage", "unsplash", cands)
    got = cache.get("acid mine drainage", "unsplash")
    assert len(got) == 1
    assert got[0].url == "u1"


def test_cache_miss_returns_none(tmp_path):
    cache = QueryCache(tmp_path)
    assert cache.get("never seen", "unsplash") is None


def test_cache_separates_sources(tmp_path):
    cache = QueryCache(tmp_path)
    cache.put("q", "unsplash", [RawCandidate(source="unsplash", url="a")])
    assert cache.get("q", "unsplash") is not None
    assert cache.get("q", "bing") is None
