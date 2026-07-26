"""Tests for voice consistency (A-1) and prosody pitch bounds (A-2)."""
from __future__ import annotations
import re
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import pytest

from video_agent.voiceover import (
    VoiceSegment, synthesize_segments, _EDGE_TTS_PRESETS,
)


def _make_segments(n: int = 3) -> list[VoiceSegment]:
    return [VoiceSegment(f"Segment text {i}.", "conversational") for i in range(n)]


class TestEdgeTtsPresetPitchBounds:
    """A-2: all pitch values must stay within ±6Hz."""

    def test_all_presets_within_pitch_bounds(self):
        hz_re = re.compile(r"([+-]?\d+)Hz")
        for name, preset in _EDGE_TTS_PRESETS.items():
            pitch_str = preset.get("pitch", "+0Hz")
            m = hz_re.search(pitch_str)
            assert m is not None, f"Preset '{name}' pitch '{pitch_str}' not parseable"
            hz = int(m.group(1))
            assert -6 <= hz <= 6, (
                f"Preset '{name}' pitch {hz}Hz exceeds ±6Hz bound"
            )


class TestVoiceConsistency:
    """A-1: all-or-nothing engine selection."""

    def _make_fake_mp3(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfb" + b"\x00" * 2048)

    def test_happy_path_uses_edge_tts_only(self, tmp_path, monkeypatch):
        """When edge-tts succeeds for all segments, Kokoro is never called."""
        kokoro_calls = []
        edge_calls = []

        def fake_edge(text, voice, path, rate="+0%", pitch="+0Hz"):
            edge_calls.append(text)
            self._make_fake_mp3(path)

        def fake_kokoro(text, path):
            kokoro_calls.append(text)

        from pydub import AudioSegment as AS
        monkeypatch.setattr("video_agent.voiceover._edge_synthesize", fake_edge)
        monkeypatch.setattr("video_agent.voiceover._kokoro_synthesize", fake_kokoro)
        monkeypatch.setattr(
            "video_agent.voiceover.AudioSegment.from_mp3",
            lambda p: AS.silent(duration=500),
        )

        segs = _make_segments(3)
        out = tmp_path / "voice.mp3"
        result = synthesize_segments(segs, out, "usa")

        assert result["engine_used"] == "edge-tts"
        assert result["fell_back"] is False
        assert len(edge_calls) == 3
        assert len(kokoro_calls) == 0

    def test_any_edge_failure_rerenders_all_in_kokoro(self, tmp_path, monkeypatch):
        """When edge-tts fails on segment 1, Kokoro is called for ALL segments."""
        kokoro_calls = []
        edge_call_count = [0]

        def fake_edge(text, voice, path, rate="+0%", pitch="+0Hz"):
            edge_call_count[0] += 1
            if edge_call_count[0] == 2:
                raise RuntimeError("edge-tts network error")
            self._make_fake_mp3(path)

        def fake_kokoro(text, path):
            kokoro_calls.append(text)
            self._make_fake_mp3(path)

        from pydub import AudioSegment as AS
        monkeypatch.setattr("video_agent.voiceover._edge_synthesize", fake_edge)
        monkeypatch.setattr("video_agent.voiceover._kokoro_synthesize", fake_kokoro)
        monkeypatch.setattr(
            "video_agent.voiceover.AudioSegment.from_mp3",
            lambda p: AS.silent(duration=500),
        )

        segs = _make_segments(3)
        out = tmp_path / "voice.mp3"
        result = synthesize_segments(segs, out, "usa")

        assert result["engine_used"] == "kokoro"
        assert result["fell_back"] is True
        # Kokoro must be called for ALL 3 segments, not just the failing one.
        assert len(kokoro_calls) == 3
