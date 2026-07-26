from unittest.mock import patch, MagicMock
from video_agent.sources.youtube import YouTubeSource


def test_youtube_filters_short_or_low_view_videos():
    fake_results = {"entries": [
        {"id": "id1", "title": "industrial water plant", "duration": 120,
         "view_count": 50_000, "thumbnail": "https://i.ytimg.com/x.jpg"},
        {"id": "id2", "title": "low quality", "duration": 5,
         "view_count": 2_000, "thumbnail": ""},  # too short / too few views
    ]}
    fake_ydl = MagicMock()
    fake_ydl.extract_info.return_value = fake_results
    with patch("video_agent.sources.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__.return_value = fake_ydl
        cands = YouTubeSource().search("industrial water", limit=5)
    assert len(cands) == 1
    assert cands[0].is_clip is True
    assert cands[0].duration_s == 120
    assert "industrial" in cands[0].caption.lower()
