import responses
from video_agent.sources.wikimedia import WikimediaSource


@responses.activate
def test_wikimedia_returns_candidates_with_image_info():
    # First call: search
    responses.add(
        responses.GET, "https://commons.wikimedia.org/w/api.php",
        json={"query": {"search": [
            {"title": "File:Sulfide oxidation.png", "snippet": "diagram"},
        ]}},
    )
    # Second call: imageinfo
    responses.add(
        responses.GET, "https://commons.wikimedia.org/w/api.php",
        json={"query": {"pages": {"1": {
            "imageinfo": [{
                "url": "https://upload.../Sulfide_oxidation.png",
                "width": 1600, "height": 1200,
                "extmetadata": {"ImageDescription": {"value": "oxidation diagram"}},
            }],
        }}}},
    )
    src = WikimediaSource()
    cands = src.search("sulfide oxidation", limit=1)
    assert len(cands) == 1
    assert cands[0].source == "wikimedia"
    assert cands[0].width == 1600
    assert "oxidation" in cands[0].caption.lower()
