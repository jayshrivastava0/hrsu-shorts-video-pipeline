"""
Tests for heuristic verification gate.

Deterministic checks on rendered MP4s before publishing.
"""
import json
import pytest
import subprocess
import tempfile
from pathlib import Path
from pydub import AudioSegment
from pydub.generators import Sine
from video_agent.harness.verify_heuristic import verify_heuristic
from video_agent.harness.manifest import VerifyReport


# Session-scoped fixture fixtures directory
@pytest.fixture(scope="session")
def fixture_dir():
    """Create and populate test fixtures directory."""
    fixtures = Path(__file__).parent / "fixtures" / "verify"
    fixtures.mkdir(parents=True, exist_ok=True)
    return fixtures


def _generate_fixture_if_missing(path, generator_func):
    """Generate fixture if it doesn't exist."""
    if not path.exists():
        generator_func(path)


def _generate_good_video(path: Path):
    """Generate a valid MP4: 45s, 1080×1920, audio RMS ~2000, no dark ribbon."""
    # Use ffmpeg to create a 45s video with testsrc2 (light colorful pattern)
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1920:duration=45:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=45",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_short_video(path: Path):
    """Generate a short MP4: 15s (< 30s min)."""
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1920:duration=15:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=15",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_long_video(path: Path):
    """Generate a long MP4: 150s (> 65s max)."""
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1920:duration=150:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=150",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_silent_video(path: Path):
    """Generate video with silent audio (RMS < 250)."""
    # Use anullsrc to create silent audio (DC offset = 0)
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1920:duration=45:rate=30",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", "45",  # specify duration for anullsrc
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_clipped_video(path: Path):
    """Generate video with clipped audio (peak > 32500)."""
    # Generate clipped audio using numpy to ensure peak > 32500
    import numpy as np

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_wav = Path(tmpdir) / "loud.wav"
        temp_mp4 = Path(tmpdir) / "base.mp4"

        # Create base video without audio
        subprocess.run([
            "ffmpeg", "-f", "lavfi",
            "-i", "testsrc2=size=1080x1920:duration=45:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(temp_mp4), "-y"
        ], capture_output=True, check=True)

        # Generate clipped audio directly using numpy
        sample_rate = 44100
        duration_s = 45
        freq = 1000
        t = np.linspace(0, duration_s, sample_rate * duration_s)
        # Amplitude 1.1 * 32767 will clip to 32768
        samples = np.sin(2 * np.pi * freq * t) * (1.1 * 32767)
        samples = np.int16(np.clip(samples, -32768, 32767))

        audio = AudioSegment(
            data=samples.tobytes(),
            sample_width=2,
            frame_rate=sample_rate,
            channels=1
        )
        audio.export(str(temp_wav), format="wav")

        # Mux video and clipped audio
        subprocess.run([
            "ffmpeg", "-i", str(temp_mp4),
            "-i", str(temp_wav),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            str(path), "-y"
        ], capture_output=True, check=True)


def _generate_dark_ribbon_video(path: Path):
    """Generate video with dark ribbon at bottom (luma < 24)."""
    # Create a shorter video and pad it with black
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1800:duration=45:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=45",
        "-vf", "pad=1080:1920:0:0:black",  # pad to 1920 height with black
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_wrong_res_video(path: Path):
    """Generate video with wrong resolution: 720×1280."""
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=720x1280:duration=45:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=45",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_no_audio_video(path: Path):
    """Generate video with no audio stream."""
    cmd = [
        "ffmpeg", "-f", "lavfi",
        "-i", "testsrc2=size=1080x1920:duration=45:rate=30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(path), "-y"
    ]
    subprocess.run(cmd, capture_output=True, check=True)


class TestGoodVideo:
    """Test that a valid video passes all checks."""

    def test_good_video(self, fixture_dir, tmp_path):
        """Valid MP4 (45s, 1080×1920, audio RMS ~2000, no defects) passes."""
        fixture = fixture_dir / "good_45s.mp4"
        _generate_fixture_if_missing(fixture, _generate_good_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is True
        assert len(result.defects) == 0, f"Unexpected defects: {result.defects}"
        assert "duration_s" in result.checks
        assert "audio_rms" in result.checks or "safezone" in result.checks


class TestDurationChecks:
    """Test duration validation."""

    def test_duration_too_short(self, fixture_dir, tmp_path):
        """MP4 < 30s fails duration check."""
        fixture = fixture_dir / "short_15s.mp4"
        _generate_fixture_if_missing(fixture, _generate_short_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("Duration" in d or "duration" in d.lower() for d in result.defects), \
            f"Expected duration defect, got: {result.defects}"

    def test_duration_too_long(self, fixture_dir, tmp_path):
        """MP4 > 65s fails duration check."""
        fixture = fixture_dir / "long_150s.mp4"
        _generate_fixture_if_missing(fixture, _generate_long_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("Duration" in d or "duration" in d.lower() for d in result.defects), \
            f"Expected duration defect, got: {result.defects}"


class TestAudioChecks:
    """Test audio level validation."""

    def test_silent_audio(self, fixture_dir, tmp_path):
        """RMS < 250 (silence floor) fails."""
        fixture = fixture_dir / "silent.mp4"
        _generate_fixture_if_missing(fixture, _generate_silent_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("RMS" in d or "audio" in d.lower() or "silent" in d.lower() for d in result.defects), \
            f"Expected audio defect, got: {result.defects}"

    def test_clipped_audio(self, fixture_dir, tmp_path):
        """Peak > 32500 (clipping ceiling) fails."""
        fixture = fixture_dir / "clipped.mp4"
        _generate_fixture_if_missing(fixture, _generate_clipped_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("peak" in d.lower() or "clip" in d.lower() or "audio" in d.lower() for d in result.defects), \
            f"Expected clipping defect, got: {result.defects}"


class TestResolutionChecks:
    """Test video resolution validation."""

    def test_wrong_resolution(self, fixture_dir, tmp_path):
        """Resolution != (1080, 1920) fails."""
        fixture = fixture_dir / "wrong_res.mp4"
        _generate_fixture_if_missing(fixture, _generate_wrong_res_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("resolution" in d.lower() or "1080" in d for d in result.defects), \
            f"Expected resolution defect, got: {result.defects}"


class TestStreamChecks:
    """Test stream availability validation."""

    def test_no_audio_stream(self, fixture_dir, tmp_path):
        """Video-only MP4 (no audio) fails."""
        fixture = fixture_dir / "no_audio.mp4"
        _generate_fixture_if_missing(fixture, _generate_no_audio_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("audio" in d.lower() or "stream" in d.lower() for d in result.defects), \
            f"Expected audio stream defect, got: {result.defects}"


class TestDarkRibbon:
    """Test dark ribbon (visual defect) detection."""

    def test_dark_ribbon(self, fixture_dir, tmp_path):
        """Bottom strip luma < 24 (dark band) is detected."""
        fixture = fixture_dir / "dark_ribbon.mp4"
        _generate_fixture_if_missing(fixture, _generate_dark_ribbon_video)

        result = verify_heuristic(str(fixture), workspace=str(tmp_path))

        assert result.passed is False
        assert any("ribbon" in d.lower() or "dark" in d.lower() or "luma" in d.lower() for d in result.defects), \
            f"Expected dark ribbon defect, got: {result.defects}"
