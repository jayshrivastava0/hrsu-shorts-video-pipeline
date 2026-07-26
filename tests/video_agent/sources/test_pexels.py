import responses
from video_agent.sources.pexels import PexelsSource


def test_pexels_no_key_returns_empty():
    src = PexelsSource(api_key="")
    assert src.search("anything") == []


@responses.activate
def test_pexels_search_extracts_large2x_url():
    responses.add(
        responses.GET, "https://api.pexels.com/v1/search",
        json={"photos": [
            {"src": {"large2x": "https://images.pexels.com/p1_large2x",
                     "original": "https://images.pexels.com/p1_original"},
             "alt": "industrial plant", "photographer": "Jane Doe",
             "width": 1920, "height": 1080},
            {"src": {"large2x": "", "original": "https://images.pexels.com/p2_original"},
             "alt": "", "photographer": "John",
             "width": 2400, "height": 1600},
        ]},
        status=200,
    )
    src = PexelsSource(api_key="fake")
    cands = src.search("industrial", limit=2)
    assert len(cands) == 2
    assert cands[0].source == "pexels"
    assert cands[0].url == "https://images.pexels.com/p1_large2x"
    assert cands[1].url == "https://images.pexels.com/p2_original"   # falls back


@responses.activate
def test_pexels_handles_api_failure_gracefully():
    responses.add(responses.GET, "https://api.pexels.com/v1/search", status=500)
    src = PexelsSource(api_key="fake")
    assert src.search("anything") == []
