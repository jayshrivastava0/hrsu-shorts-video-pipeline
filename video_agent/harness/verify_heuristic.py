"""
Heuristic verification gate for rendered MP4 artifacts.

Deterministic (no-LLM) checks on video files before publishing:
- Duration within bounds
- Audio presence and level validation (RMS/peak)
- Resolution verification
- Dark ribbon (visual defect) detection
- Caption safe-zone validation (optional, tesseract-dependent)
"""
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import cv2
from pydub import AudioSegment

from video_agent.config import (
    SHORT_FORMAT,
    VERIFY_AUDIO_RMS_FLOOR,
    VERIFY_AUDIO_PEAK_CEIL,
    VERIFY_FRAME_SAMPLES,
    VERIFY_DARK_RIBBON_STRIP_PX,
    VERIFY_DARK_RIBBON_LUMA_MAX,
    VERIFY_SAFEZONE_MARGIN_FRAC,
)
from video_agent.harness.manifest import VerifyReport


logger = logging.getLogger(__name__)


def verify_heuristic(video_path: str, workspace: str) -> VerifyReport:
    """
    Run deterministic checks on rendered MP4.

    Verifies:
    1. Duration within SHORT_FORMAT bounds
    2. Both video and audio streams present
    3. Resolution exactly (1080, 1920)
    4. Filesize within limits
    5. Audio RMS level (floor and peak ceiling)
    6. Dark ribbon detection (bottom strip luma check)
    7. Caption safe-zone OCR (optional, skipped if tesseract unavailable)

    Args:
        video_path: Path to rendered MP4 file
        workspace: Workspace directory for temp files

    Returns:
        VerifyReport with passed (bool), checks (dict), defects (list[str])

    Raises:
        FileNotFoundError: If video_path does not exist
        RuntimeError: If ffprobe fails or video is unreadable
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    checks = {}
    defects = []

    # 1. ffprobe: Duration, streams, resolution, filesize
    try:
        probe_data = _ffprobe(str(video_path))
    except RuntimeError as e:
        return VerifyReport(passed=False, checks=checks, defects=[str(e)])

    # Check duration
    duration_s = float(probe_data.get("format", {}).get("duration", 0))
    checks["duration_s"] = duration_s
    min_dur = SHORT_FORMAT["min_duration_s"]
    max_dur = SHORT_FORMAT["max_duration_s"]

    if duration_s < min_dur:
        defects.append(f"Duration too short ({duration_s:.1f}s < {min_dur}s)")
    elif duration_s > max_dur:
        defects.append(f"Duration too long ({duration_s:.1f}s > {max_dur}s)")

    # Check filesize
    filesize_mb = video_path.stat().st_size / (1024 * 1024)
    checks["filesize_mb"] = filesize_mb
    max_size = SHORT_FORMAT["max_filesize_mb"]
    if filesize_mb > max_size:
        defects.append(f"Filesize exceeds limit ({filesize_mb:.1f}MB > {max_size}MB)")

    # Check streams: video and audio must both be present
    streams = probe_data.get("streams", [])
    video_stream = None
    audio_stream = None

    for stream in streams:
        if stream.get("codec_type") == "video":
            video_stream = stream
        elif stream.get("codec_type") == "audio":
            audio_stream = stream

    if not video_stream:
        defects.append("No video stream found")
        return VerifyReport(passed=False, checks=checks, defects=defects)

    if not audio_stream:
        defects.append("No audio stream found")
        # Don't return yet; continue with video checks

    # Check resolution
    width = video_stream.get("width")
    height = video_stream.get("height")
    expected_res = SHORT_FORMAT["resolution"]  # (1080, 1920)
    checks["resolution"] = (width, height)

    if (width, height) != expected_res:
        defects.append(f"Resolution mismatch: got ({width}, {height}), expected {expected_res}")

    # 2. Audio checks (only if audio stream exists)
    if audio_stream:
        try:
            audio_rms, audio_peak = _extract_audio_levels(str(video_path))
            checks["audio_rms"] = audio_rms
            checks["audio_peak"] = audio_peak

            if audio_rms < VERIFY_AUDIO_RMS_FLOOR:
                defects.append(f"Audio RMS too low ({audio_rms:.1f} < {VERIFY_AUDIO_RMS_FLOOR}): effectively silent")

            if audio_peak > VERIFY_AUDIO_PEAK_CEIL:
                defects.append(f"Audio peak too high ({audio_peak:.0f} > {VERIFY_AUDIO_PEAK_CEIL}): risk of clipping")

        except Exception as e:
            logger.warning(f"Failed to extract audio levels: {e}")
            defects.append(f"Audio analysis failed: {e}")

    # 3. Dark ribbon detection — per the Phase 1 design, a ribbon is only a
    # defect when it is a *persistent* band (every sampled frame dark).
    # Individual dark frames are legitimate (brand cards, letterboxed scenes).
    try:
        dark_frames, n_sampled = _detect_dark_ribbon(str(video_path))
        checks["dark_ribbon"] = dark_frames
        if dark_frames and n_sampled and len(dark_frames) == n_sampled:
            defects.append(
                f"Dark ribbon persistent across all {n_sampled} sampled frames")
    except Exception as e:
        logger.warning(f"Dark ribbon detection failed: {e}")

    # 4. Caption safe-zone OCR (optional, skipped if tesseract unavailable)
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        # Tesseract available; run the check
        try:
            unsafe_frames = _check_safezone(str(video_path))
            checks["safezone"] = "skipped" if unsafe_frames is None else unsafe_frames
            if unsafe_frames:
                defects.append(f"Captions exceed safe zone at frames: {unsafe_frames}")
        except Exception as e:
            logger.warning(f"Safe-zone check failed: {e}")
            checks["safezone"] = "error"
    except Exception:
        # Tesseract not available; skip safe-zone check
        checks["safezone"] = "skipped"

    passed = len(defects) == 0
    return VerifyReport(passed=passed, checks=checks, defects=defects)


def _ffprobe(video_path: str) -> dict[str, Any]:
    """
    Run ffprobe and return parsed JSON output.

    Args:
        video_path: Path to video file

    Returns:
        Dictionary from ffprobe JSON output

    Raises:
        RuntimeError: If ffprobe fails or output is invalid
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        video_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe output parsing failed: {e}") from e


def _sample_frame_indices(frame_count: int) -> list[int]:
    """
    Return evenly-spaced frame indices to sample.

    Args:
        frame_count: Total number of frames in video

    Returns:
        List of frame indices evenly distributed across the video
    """
    if frame_count <= VERIFY_FRAME_SAMPLES:
        return list(range(frame_count))
    step = frame_count / VERIFY_FRAME_SAMPLES
    return [int(i * step) for i in range(VERIFY_FRAME_SAMPLES)]


def _extract_audio_levels(video_path: str) -> tuple[float, float]:
    """
    Extract audio RMS and peak levels using pydub.

    Args:
        video_path: Path to video file

    Returns:
        Tuple of (rms, peak) where both are linear 16-bit PCM amplitude values

    Raises:
        RuntimeError: If audio extraction fails
    """
    # Extract audio stream to temporary file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-q:a", "9",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            tmp_path,
            "-y"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        # Load with pydub and measure
        audio = AudioSegment.from_file(tmp_path, format="wav")
        rms = audio.rms
        peak = audio.max

        return rms, peak

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Audio extraction failed: {e.stderr}") from e
    except Exception as e:
        raise RuntimeError(f"Audio measurement failed: {e}") from e
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


def _detect_dark_ribbon(video_path: str) -> tuple[list[int], int]:
    """
    Detect dark ribbon at bottom of frames.

    Samples VERIFY_FRAME_SAMPLES frames evenly across the video.
    For each frame, checks if the bottom VERIFY_DARK_RIBBON_STRIP_PX pixels
    have mean luma < VERIFY_DARK_RIBBON_LUMA_MAX.

    Args:
        video_path: Path to video file

    Returns:
        Tuple of (frame indices with a dark strip, number of frames sampled).
        The caller decides whether the ribbon is persistent (all sampled
        frames dark) — only that counts as a defect.

    Raises:
        RuntimeError: If frame extraction fails
    """
    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise RuntimeError("Cannot open video with cv2.VideoCapture")

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Sample frame indices evenly across video
        sample_indices = _sample_frame_indices(frame_count)

        dark_frames = []

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue

            # Extract bottom strip and compute mean luma
            height = frame.shape[0]
            strip_start = max(0, height - VERIFY_DARK_RIBBON_STRIP_PX)
            strip = frame[strip_start:, :]

            # Convert to grayscale (luma)
            if len(frame.shape) == 3 and frame.shape[2] == 3:
                # BGR to grayscale
                gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
            else:
                gray = strip if len(strip.shape) == 2 else cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)

            mean_luma = gray.mean()

            if mean_luma < VERIFY_DARK_RIBBON_LUMA_MAX:
                dark_frames.append(frame_idx)

        return dark_frames, len(sample_indices)

    except Exception as e:
        raise RuntimeError(f"Dark ribbon detection failed: {e}") from e
    finally:
        cap.release()


def _check_safezone(video_path: str) -> Optional[list[int]]:
    """
    Check caption safe zone using OCR.

    Samples VERIFY_FRAME_SAMPLES frames and runs OCR on each.
    Verifies detected text bounding boxes fit within VERIFY_SAFEZONE_MARGIN_FRAC
    margin from edges.

    Args:
        video_path: Path to video file

    Returns:
        List of frame indices where captions exceed safe zone, or None if unavailable

    Raises:
        RuntimeError: If OCR fails or is unavailable
    """
    try:
        import pytesseract
    except ImportError:
        return None

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return None

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise RuntimeError("Cannot open video with cv2.VideoCapture")

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Sample frame indices evenly
        sample_indices = _sample_frame_indices(frame_count)

        unsafe_frames = []
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

        # Safe zone margins
        margin_h = int(height * VERIFY_SAFEZONE_MARGIN_FRAC)
        margin_w = int(width * VERIFY_SAFEZONE_MARGIN_FRAC)

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue

            # Run OCR
            data = pytesseract.image_to_data(frame, output_type=pytesseract.Output.DICT)

            # Check bounding boxes
            for i, conf in enumerate(data["conf"]):
                if int(conf) < 50:  # Skip low-confidence detections
                    continue

                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                x_end = x + w
                y_end = y + h

                # Check if bbox exceeds safe zone
                if x < margin_w or y < margin_h or x_end > (width - margin_w) or y_end > (height - margin_h):
                    unsafe_frames.append(frame_idx)
                    break  # One unsafe text per frame is enough

        return unsafe_frames if unsafe_frames else []

    except Exception as e:
        raise RuntimeError(f"Safe-zone check failed: {e}") from e
    finally:
        cap.release()
