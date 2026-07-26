import responses
from video_agent.sources.bing import BingSource


@responses.activate
def test_bing_returns_candidates():
    responses.add(
        responses.GET, "https://api.bing.microsoft.com/v7.0/images/search",
        json={"value": [
            {"contentUrl": "https://x/1.jpg", "name": "wastewater plant",
             "width": 1920, "height": 1080, "encodingFormat": "jpeg"},
            {"contentUrl": "https://x/2.jpg", "name": "industrial",
             "width": 2400, "height": 1600, "encodingFormat": "jpeg"},
        ]},
    )
    src = BingSource(api_key="fake")
    cands = src.search("wastewater", limit=2)
    assert len(cands) == 2
    assert cands[0].url == "https://x/1.jpg"
    assert cands[0].width == 1920


def test_bing_no_key_empty():
    assert BingSource(api_key=None).search("x") == []
