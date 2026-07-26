from unittest.mock import patch
import responses
from video_agent.sources.unsplash import UnsplashSource


@responses.activate
def test_unsplash_search_returns_candidates():
    responses.add(
        responses.GET, "https://api.unsplash.com/search/photos",
        json={"results": [
            {"urls": {"raw": "https://u/img1"},
             "alt_description": "industrial water plant",
             "width": 1920, "height": 1080,
             "user": {"name": "Photographer"}},
            {"urls": {"raw": "https://u/img2"},
             "alt_description": "factory aerial",
             "width": 2400, "height": 1600,
             "user": {"name": "Photographer"}},
        ]},
        status=200,
    )
    src = UnsplashSource(api_key="fake")
    cands = src.search("industrial water", limit=2)
    assert len(cands) == 2
    assert cands[0].source == "unsplash"
    assert cands[0].url == "https://u/img1"
    assert cands[0].width == 1920
    assert "industrial" in cands[0].caption


def test_unsplash_no_key_returns_empty():
    src = UnsplashSource(api_key=None)
    assert src.search("anything") == []
