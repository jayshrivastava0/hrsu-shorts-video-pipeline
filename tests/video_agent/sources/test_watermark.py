import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image

from video_agent.sources.watermark import is_watermarked, _ensure_tesseract


@pytest.fixture
def fake_image(tmp_path):
    """A simple solid-grey 800x600 image saved to a temp path."""
    p = tmp_path / "test.jpg"
    Image.new("RGB", (800, 600), (128, 128, 128)).save(p)
    return p


@pytest.fixture(autouse=True)
def reset_tesseract_flag():
    """Reset the module-level _TESSERACT_OK flag between tests."""
    import video_agent.sources.watermark as m
    m._TESSERACT_OK = None
    yield
    m._TESSERACT_OK = None


def test_blocklist_match_rejects(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright Getty Images 2024"):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is True
    assert "blocklist_match" in reason


def test_text_density_threshold_rejects(fake_image, tmp_path):
    long_text = "x" * 50    # 50 chars, no blocklist match
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value=long_text):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is True
    assert "text_density" in reason


def test_clean_image_passes(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="  "):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is False


def test_cache_hit_skips_ocr(fake_image, tmp_path):
    cache_root = tmp_path / "cache"
    # First call populates cache
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright") as mock_ocr:
        is_watermarked(fake_image, cache_root)
        assert mock_ocr.call_count == 1
    # Second call should hit cache, NOT re-run OCR
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright") as mock_ocr:
        watermarked, _ = is_watermarked(fake_image, cache_root)
        assert mock_ocr.call_count == 0    # cache hit
        assert watermarked is True


def test_missing_tesseract_graceful_skip(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=False):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is False
    assert reason == "tesseract_unavailable"
