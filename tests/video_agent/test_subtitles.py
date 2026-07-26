import re
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.subtitles import generate_srt, _chunk_words


def test_chunk_words_max_3_per_line():
    words = [
        {"word": "Calcium", "start": 0.0, "end": 0.4},
        {"word": "nitrate", "start": 0.4, "end": 0.7},
        {"word": "cuts", "start": 0.7, "end": 1.0},
        {"word": "H2S", "start": 1.0, "end": 1.3},
        {"word": "fast", "start": 1.3, "end": 1.6},
    ]
    cues = _chunk_words(words, max_words=3, max_dur=1.5)
    assert all(len(c["text"].split()) <= 3 for c in cues)
    assert cues[0]["text"] == "CALCIUM NITRATE CUTS"
    assert cues[1]["text"] == "H2S FAST"


def test_chunk_breaks_on_max_duration():
    words = [
        {"word": "long", "start": 0.0, "end": 1.6},  # single word > max_dur
    ]
    cues = _chunk_words(words, max_words=3, max_dur=1.5)
    assert len(cues) == 1
    assert cues[0]["text"] == "LONG"


def test_generate_srt_writes_valid_file(tmp_path):
    fake_segments = [
        MagicMock(words=[
            MagicMock(word="Hello", start=0.0, end=0.4),
            MagicMock(word="world", start=0.4, end=0.8),
        ]),
    ]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (fake_segments, MagicMock())
    out = tmp_path / "s.srt"
    with patch("video_agent.subtitles.WhisperModel", return_value=fake_model):
        path = generate_srt(tmp_path / "fake.mp3", out, narration_hint="Hello world")
    assert path == out
    text = out.read_text(encoding="utf-8")
    assert text.startswith("1\n")
    assert "HELLO WORLD" in text
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", text)
