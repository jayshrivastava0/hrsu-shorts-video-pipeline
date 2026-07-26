"""Test BROLL_FRAME: real assets, landscape never cropped."""
from __future__ import annotations
import numpy as np
import pytest
from PIL import Image
from shorts_engine import config


@pytest.fixture()
def landscape(tmp_path):
    p = tmp_path / "land.png"
    img = Image.new("RGB", (1600, 900), (200, 30, 30))
    img.paste(Image.new("RGB", (200, 200), (30, 200, 30)), (0, 0))       # TL green
    img.paste(Image.new("RGB", (200, 200), (30, 30, 200)), (1400, 700))  # BR blue
    img.save(p)
    return p


@pytest.fixture()
def portrait(tmp_path):
    p = tmp_path / "port.png"
    img = Image.new("RGB", (900, 1600), (120, 60, 200))
    img.paste(Image.new("RGB", (200, 300), (255, 100, 100)), (0, 0))      # TL red
    img.paste(Image.new("RGB", (200, 300), (100, 255, 100)), (700, 1300)) # BR green
    img.save(p)
    return p


class TestGeometry:
    def test_is_portrait(self):
        from shorts_engine.cards import broll_frame
        assert broll_frame.is_portrait(900, 1600)
        assert broll_frame.is_portrait(1000, 1250)
        assert not broll_frame.is_portrait(1600, 900)
        assert not broll_frame.is_portrait(1000, 1000)

    def test_placement_preserves_aspect_and_fits(self):
        from shorts_engine.cards import broll_frame
        x0, y0, x1, y1 = broll_frame.placement(1600, 900)
        w, h = x1 - x0, y1 - y0
        assert abs((w / h) - (1600 / 900)) < 0.02      # aspect preserved
        assert w <= config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        assert x0 >= config.SAFE_SIDE_PX and x1 <= config.CANVAS_W - config.SAFE_SIDE_PX

    def test_kenburns_window_stays_inside_source(self):
        from shorts_engine.cards import broll_frame
        for t in (0.0, 1.0, 2.9):
            x0, y0, x1, y1 = broll_frame.kenburns_window(900, 1600, t, 3.0)
            assert 0 <= x0 < x1 <= 900
            assert 0 <= y0 < y1 <= 1600


class TestNeverCropped:
    def test_landscape_corners_both_visible(self, landscape):
        """The ¼-crop defect killer: both corner markers of a landscape source
        must be present in the rendered frame."""
        from shorts_engine.cards import broll_frame
        img = broll_frame.frame_at({"image_path": str(landscape)}, 1.5, 3.0)
        arr = np.asarray(img).astype(int)
        green = (np.abs(arr - np.array([30, 200, 30])).sum(axis=2) < 60).sum()
        blue = (np.abs(arr - np.array([30, 30, 200])).sum(axis=2) < 60).sum()
        assert green > 100 and blue > 100

    def test_blurfill_background_is_not_flat_navy(self, landscape):
        from shorts_engine.cards import broll_frame, theme
        img = broll_frame.frame_at(
            {"image_path": str(landscape), "layout": "blurfill"}, 1.5, 3.0)
        top_strip = np.asarray(img)[:100, :, :].astype(int)
        navy = np.array(theme.NAVY)
        assert np.abs(top_strip - navy).sum(axis=2).mean() > 30

    def test_portrait_kenburns_moves(self, portrait):
        from shorts_engine.cards import broll_frame
        a = np.asarray(broll_frame.frame_at({"image_path": str(portrait)}, 0.0, 3.0))
        b = np.asarray(broll_frame.frame_at({"image_path": str(portrait)}, 2.9, 3.0))
        assert np.abs(a.astype(int) - b.astype(int)).sum() > 0

    def test_missing_file_raises(self):
        from shorts_engine.cards import broll_frame
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            broll_frame.frame_at({"image_path": "Z:/nope.png"}, 0.5, 3.0)

    def test_render_mp4(self, landscape, tmp_path):
        from shorts_engine.cards import broll_frame, encoder
        out = broll_frame.render({"image_path": str(landscape)}, 0.6,
                                 tmp_path / "b.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
