import responses
from video_agent.sources.pixabay import PixabaySource

_IMG = "https://pixabay.com/api/"
_VID = "https://pixabay.com/api/videos/"

_IMG_HIT = {
    "largeImageURL": "https://cdn.pixabay.com/photo/calcium.jpg",
    "imageWidth": 1920, "imageHeight": 1080,
    "tags": "calcium nitrate, industrial, chemical",
}
_VID_HIT = {
    "tags": "factory industrial",
    "duration": 15,
    "videos": {"large": {"url": "https://cdn.pixabay.com/video/factory.mp4"}},
}


@responses.activate
def test_returns_images_and_videos():
    responses.add(responses.GET, _IMG, json={"hits": [_IMG_HIT]})
    responses.add(responses.GET, _VID, json={"hits": [_VID_HIT]})
    src = PixabaySource(api_key="test-key")
    cands = src.search("calcium nitrate industrial", limit=4)
    sources = [c.source for c in cands]
    assert all(s == "pixabay" for s in sources)
    clips = [c for c in cands if c.is_clip]
    images = [c for c in cands if not c.is_clip]
    assert len(images) >= 1
    assert len(clips) >= 1
    assert images[0].width == 1920


@responses.activate
def test_no_api_key_returns_empty():
    src = PixabaySource(api_key=None)
    assert src.search("anything") == []


@responses.activate
def test_api_error_returns_empty():
    responses.add(responses.GET, _IMG, body=Exception("timeout"))
    responses.add(responses.GET, _VID, body=Exception("timeout"))
    src = PixabaySource(api_key="test-key")
    assert src.search("calcium") == []
