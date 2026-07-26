from unittest.mock import patch
from video_agent.sources.duckduckgo import DuckDuckGoSource


def test_ddg_maps_results_to_candidates():
    fake = [
        {"image": "https://x/a.jpg", "title": "industrial",
         "width": 1920, "height": 1080},
        {"image": "https://x/b.jpg", "title": "factory",
         "width": 1280, "height": 720},
    ]
    with patch("video_agent.sources.duckduckgo.DDGS") as mock_ddgs:
        mock_ddgs.return_value.__enter__.return_value.images.return_value = fake
        cands = DuckDuckGoSource().search("industrial", limit=2)
    assert len(cands) == 2
    assert cands[0].url == "https://x/a.jpg"
    assert cands[0].width == 1920
