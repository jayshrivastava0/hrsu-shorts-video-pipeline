import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.voiceover import synthesize, VoiceoverError, VoiceSegment, build_ssml_segment, synthesize_segments, INTER_SEGMENT_GAP_MS


def _fake_pydub_segment(duration_s: float):
    seg = MagicMock()
    seg.__len__ = lambda self: int(duration_s * 1000)  # ms
    return seg


def test_voice_picked_by_region(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda text, voice, path, rate="+0%", pitch="+0Hz": path.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        result = synthesize("hello world " * 30, out, region="australia")
    assert result["voice_used"] == "en-AU-WilliamNeural"
    assert result["engine_used"] == "edge-tts"
    assert result["fell_back"] is False


def test_voice_override_wins(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda text, voice, path, rate="+0%", pitch="+0Hz": path.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        result = synthesize("hello " * 30, out, region="usa",
                           voice_override="en-GB-RyanNeural")
    assert result["voice_used"] == "en-GB-RyanNeural"


def test_falls_back_to_kokoro_when_edge_fails(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize",
               side_effect=ConnectionError("net")), \
         patch("video_agent.voiceover._kokoro_synthesize") as mock_k, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_k.side_effect = lambda txt, path: path.write_bytes(b"y" * 80_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        result = synthesize("hi " * 30, out, region="usa")
    assert result["fell_back"] is True
    assert result["engine_used"] == "kokoro"


def test_text_normalized_before_tts(tmp_path):
    captured = {}
    def capture(text, voice, path, rate="+0%", pitch="+0Hz"):
        captured["text"] = text
        path.write_bytes(b"x" * 60_000)
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize", side_effect=capture), \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        synthesize("H2S at 50 mg/L cut by 90%. " * 5, out, region="usa")
    assert "H 2 S" in captured["text"]
    assert "milligrams per liter" in captured["text"]
    assert "percent" in captured["text"]


def test_rejects_oversized_narration(tmp_path):
    long_text = "word " * 250
    with pytest.raises(VoiceoverError, match="200 words"):
        synthesize(long_text, tmp_path / "v.mp3", region="usa")


def test_warns_on_duration_outside_range(tmp_path, caplog):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda t, v, p, rate="+0%", pitch="+0Hz": p.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(15.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        synthesize("hi " * 30, out, region="usa")
    assert any("duration" in r.message.lower() for r in caplog.records)


# ── New tests for Task 12 ──────────────────────────────────────────────────

def test_build_ssml_hook_emphasis_has_prosody_and_emphasis():
    ssml = build_ssml_segment("Scaling clogs your pipes.", "hook_emphasis", "en-AU-WilliamNeural")
    assert "<speak" in ssml
    assert 'rate="-10%"' in ssml
    assert 'pitch="+2st"' in ssml
    assert 'level="strong"' in ssml


def test_build_ssml_conversational_has_no_prosody_changes():
    ssml = build_ssml_segment("Just a regular line.", "conversational", "en-US-GuyNeural")
    assert "<speak" in ssml
    assert "<prosody" not in ssml
    assert "<emphasis" not in ssml


def test_build_ssml_warm_cta_lowers_pitch_and_rate():
    ssml = build_ssml_segment("Visit us today.", "warm_cta", "en-AU-WilliamNeural")
    assert 'rate="-5%"' in ssml
    assert 'pitch="-1st"' in ssml


def test_synthesize_segments_concatenates_with_gap(tmp_path):
    segs = [
        VoiceSegment("line one", "conversational"),
        VoiceSegment("line two", "hook_emphasis"),
        VoiceSegment("line three", "warm_cta"),
    ]
    out = tmp_path / "vo.mp3"

    def fake_edge(text, voice, path, rate="+0%", pitch="+0Hz"):
        path.write_bytes(b"x" * 60_000)

    with patch("video_agent.voiceover._edge_synthesize", side_effect=fake_edge), \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_audio.from_mp3.return_value = _fake_pydub_segment(0.5)
        mock_audio.silent.return_value = _fake_pydub_segment(INTER_SEGMENT_GAP_MS / 1000)
        synthesize_segments(segs, out, region="australia")

    mock_audio.silent.assert_called_once_with(duration=INTER_SEGMENT_GAP_MS)
    assert mock_audio.from_mp3.call_count == 3


def test_synthesize_backcompat_single_string(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda t, v, p, rate="+0%", pitch="+0Hz": p.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        mock_audio.silent.return_value = _fake_pydub_segment(0.12)
        result = synthesize("hello world " * 30, out, region="australia")
    assert result["voice_used"] == "en-AU-WilliamNeural"
    assert result["engine_used"] == "edge-tts"
    assert result["fell_back"] is False
