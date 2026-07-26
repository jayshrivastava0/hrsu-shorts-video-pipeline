"""Integration tests: source_extractor + source_card wired into dispatcher."""
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from video_agent.visual_engine.dispatcher import generate_visual


def _make_source_entry(tmp_path: Path) -> dict:
    img = tmp_path / "ref.jpg"
    Image.new("RGB", (800, 600), (100, 149, 237)).save(img, format="JPEG")
    return {
        "id": "b1_img00",
        "path": img,
        "source_type": "pdf_page",
        "source_url": "https://example.gov/paper.pdf",
        "caption": "H2S reduction study",
        "tokens": {"h2s", "reduction", "wastewater"},
        "is_authority": True,
    }


def test_source_outranks_infographic_for_non_hook_cta(tmp_path):
    """A scene with a matching _source should render via source_card, not infographic."""
    source = _make_source_entry(tmp_path)
    scene = {
        "index": 2,
        "visual_type": "infographic",
        "visual_spec": {"chart_type": "callout_stat", "data": {"value": "90%"}},
        "on_screen_text": "90% REDUCTION",
        "narration": "H2S reduction wastewater",
        "_source": {
            "path": str(source["path"]),
            "caption": source["caption"],
            "host": "example.gov",
            "is_authority": True,
        },
    }
    out = generate_visual(scene, tmp_path / "scene.png")
    assert out["generator_used"] == "source_card"
    assert Image.open(out["asset_path"]).size == (1080, 1920)


def test_source_is_static(tmp_path):
    """Source cards must be static (no Ken Burns)."""
    source = _make_source_entry(tmp_path)
    scene = {
        "index": 2,
        "visual_type": "text_card",
        "visual_spec": {"layout": "hook"},
        "on_screen_text": "HOOK",
        "narration": "wastewater",
        "_source": {
            "path": str(source["path"]),
            "caption": "caption",
            "host": "x.gov",
            "is_authority": True,
        },
    }
    out = generate_visual(scene, tmp_path / "scene.png")
    assert out.get("is_static") is True


def test_hook_layout_skips_source(tmp_path):
    """hook layout text cards should NOT be replaced by a source image."""
    source = _make_source_entry(tmp_path)
    scene = {
        "index": 0,
        "visual_type": "text_card",
        "visual_spec": {"layout": "hook"},
        "on_screen_text": "HOOK",
        "narration": "wastewater",
        "_source": {
            "path": str(source["path"]),
            "caption": "caption",
            "host": "x.gov",
            "is_authority": False,
        },
    }
    out = generate_visual(scene, tmp_path / "scene.png")
    assert out["generator_used"] == "text_card"


def test_cta_layout_skips_source(tmp_path):
    """cta layout text cards should NOT be replaced by a source image."""
    source = _make_source_entry(tmp_path)
    scene = {
        "index": 8,
        "visual_type": "text_card",
        "visual_spec": {"layout": "cta"},
        "on_screen_text": "VISIT US",
        "narration": "contact us today",
        "_source": {
            "path": str(source["path"]),
            "caption": "caption",
            "host": "x.gov",
            "is_authority": False,
        },
    }
    out = generate_visual(scene, tmp_path / "scene.png")
    assert out["generator_used"] == "text_card"


def test_no_source_falls_through_normally(tmp_path):
    """Scenes without _source still route through normal visual_type logic."""
    scene = {
        "index": 1,
        "visual_type": "text_card",
        "visual_spec": {"layout": "hook"},
        "on_screen_text": "NORMAL",
        "narration": "test",
    }
    out = generate_visual(scene, tmp_path / "scene.png")
    assert out["generator_used"] == "text_card"
