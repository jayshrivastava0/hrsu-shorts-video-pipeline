"""Task 1: Encoder tests (frames to mp4 via ffmpeg)."""
from __future__ import annotations
from pathlib import Path
import pytest
from PIL import Image


class TestEncoder:
    def test_write_frames_produces_probeable_mp4(self, tmp_path):
        from shorts_engine.cards import encoder
        frames = [Image.new("RGB", (1080, 1920), (10, 25, 47)) for _ in range(15)]
        out = tmp_path / "clip.mp4"
        n = encoder.write_frames_to_mp4(iter(frames), out, fps=30)
        assert n == 15
        assert out.exists() and out.stat().st_size > 1000
        dur = encoder.probe_duration(out)
        assert abs(dur - 0.5) < 0.15

    def test_empty_frames_raises(self, tmp_path):
        from shorts_engine.cards import encoder
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            encoder.write_frames_to_mp4(iter([]), tmp_path / "e.mp4")

    def test_probe_missing_file_raises(self, tmp_path):
        from shorts_engine.cards import encoder
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            encoder.probe_duration(tmp_path / "nope.mp4")
