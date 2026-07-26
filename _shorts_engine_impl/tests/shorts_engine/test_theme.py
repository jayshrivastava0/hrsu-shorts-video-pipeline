from __future__ import annotations
import numpy as np
from PIL import Image, ImageDraw


class TestPalette:
    def test_hex_to_rgb(self):
        from shorts_engine.cards import theme
        assert theme.hex_to_rgb("#d4af37") == (212, 175, 55)
        assert theme.GOLD == (212, 175, 55)
        assert theme.NAVY == (10, 25, 47)


class TestFonts:
    def test_resolve_font_never_raises_and_caches(self):
        from shorts_engine.cards import theme
        f1 = theme.resolve_font("heading", 80)
        f2 = theme.resolve_font("heading", 80)
        assert f1 is f2  # cached
        assert theme.resolve_font("body", 40) is not None

    def test_unknown_kind_raises(self):
        import pytest
        from shorts_engine.cards import theme
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            theme.resolve_font("comic", 40)


class TestBackground:
    def test_size_and_navyish(self):
        from shorts_engine.cards import theme
        img = theme.background(0.0)
        assert img.size == (1080, 1920)
        arr = np.asarray(img)
        assert arr.mean() < 40  # dark navy overall

    def test_gradient_drifts_over_time(self):
        from shorts_engine.cards import theme
        a = np.asarray(theme.background(0.0)).astype(int)
        b = np.asarray(theme.background(2.0)).astype(int)
        assert np.abs(a - b).sum() > 0


class TestMotion:
    def test_fade_rise_stagger(self):
        from shorts_engine.cards import theme
        a0, dy0 = theme.fade_rise(0.0, 0)
        assert a0 == 0.0 and dy0 > 0
        a_done, dy_done = theme.fade_rise(1.0, 0)
        assert a_done == 1.0 and dy_done == 0
        # element 1 starts 80ms later: at t=0.30 element 0 is done, element 1 is not
        assert theme.fade_rise(0.30, 0)[0] == 1.0
        assert theme.fade_rise(0.30, 1)[0] < 1.0

    def test_ease_monotonic(self):
        from shorts_engine.cards import theme
        vals = [theme.ease_out_cubic(p / 10) for p in range(11)]
        assert vals == sorted(vals) and vals[0] == 0.0 and vals[-1] == 1.0


class TestChipAndText:
    def test_citation_chip_inside_safe_zone(self):
        from shorts_engine.cards import theme
        from shorts_engine import config
        img = theme.background(0.0)
        before = np.asarray(img).copy()
        theme.draw_citation_chip(img, "Source [1] — mdpi.com")
        arr = np.asarray(img)
        diff = np.argwhere((arr.astype(int) - before.astype(int)).sum(axis=2) != 0)
        assert len(diff) > 50  # something drew
        ys, xs = diff[:, 0], diff[:, 1]
        assert xs.min() >= config.SAFE_SIDE_PX
        assert ys.max() <= config.CANVAS_H - config.SAFE_BOTTOM_PX
        assert ys.min() >= config.CANVAS_H - config.SAFE_BOTTOM_PX - 200

    def test_fit_text_shrinks_and_wraps(self):
        from shorts_engine.cards import theme
        img = Image.new("RGB", (1080, 1920))
        d = ImageDraw.Draw(img)
        long = "calcium nitrate dosing keeps European effluent inside directive limits"
        font, lines, size = theme.fit_text(d, long, "heading", max_w=936, max_size=110)
        assert 1 <= len(lines) <= 4
        assert all(d.textlength(l, font=font) <= 936 for l in lines)
        short_font, short_lines, short_size = theme.fit_text(d, "Hi", "heading", 936, 110)
        assert short_size >= size


class TestRenderCard:
    def test_render_card_encodes_and_fades_in(self, tmp_path):
        from shorts_engine.cards import theme, encoder
        def frame_fn(payload, t, duration):
            img = theme.background(t)
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle([300, 800, 780, 1100], fill=theme.TEXT)
            return img
        out = tmp_path / "c.mp4"
        theme.render_card(frame_fn, {}, 0.6, out, fade_in_s=0.25)
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
        # fade-in: first frame darker than a late frame
        first = theme.render_frame_with_fade(frame_fn, {}, 0.0, 0.6, 0.25)
        late = theme.render_frame_with_fade(frame_fn, {}, 0.5, 0.6, 0.25)
        assert np.asarray(first).mean() < np.asarray(late).mean() * 0.5
