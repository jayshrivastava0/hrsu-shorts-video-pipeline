"""TDD tests for source_card renderer."""
import io
from pathlib import Path

import pytest
from PIL import Image

from video_agent.visual_engine.source_card import render_source_card


def _make_source_image(tmp_path: Path, w: int = 800, h: int = 600) -> Path:
    p = tmp_path / "src.jpg"
    Image.new("RGB", (w, h), color=(100, 149, 237)).save(p, format="JPEG")
    return p


def test_renders_at_correct_resolution(tmp_path):
    src = _make_source_image(tmp_path)
    source = {
        "path": src,
        "caption": "H2S reduction chart",
        "source_url": "https://example.gov/paper.pdf",
    }
    out = render_source_card(tmp_path / "card.png", source=source)
    assert out.exists()
    img = Image.open(out)
    assert img.size == (1080, 1920)


def test_output_is_png(tmp_path):
    src = _make_source_image(tmp_path)
    source = {"path": src, "caption": "test", "source_url": "https://x.com/a.pdf"}
    out = render_source_card(tmp_path / "card.png", source=source)
    assert out.suffix.lower() == ".png"
    with open(out, "rb") as f:
        magic = f.read(8)
    assert magic[:4] == b"\x89PNG"


def test_creates_parent_dirs(tmp_path):
    src = _make_source_image(tmp_path)
    source = {"path": src, "caption": "c", "source_url": "https://x.com"}
    out = render_source_card(tmp_path / "deep" / "nested" / "card.png", source=source)
    assert out.exists()


def test_caption_truncated_to_50_chars(tmp_path):
    src = _make_source_image(tmp_path)
    long_caption = "A" * 100
    source = {"path": src, "caption": long_caption, "source_url": "https://x.com"}
    # Should not raise; card renders without overflow error
    out = render_source_card(tmp_path / "card.png", source=source)
    assert out.exists()


def test_missing_source_image_raises(tmp_path):
    source = {"path": tmp_path / "nonexistent.jpg", "caption": "x",
              "source_url": "https://x.com"}
    with pytest.raises(Exception):
        render_source_card(tmp_path / "card.png", source=source)


def test_non_default_resolution(tmp_path):
    src = _make_source_image(tmp_path, w=400, h=400)
    source = {"path": src, "caption": "test", "source_url": "https://x.com"}
    out = render_source_card(tmp_path / "card.png", source=source,
                             resolution=(540, 960))
    assert Image.open(out).size == (540, 960)
