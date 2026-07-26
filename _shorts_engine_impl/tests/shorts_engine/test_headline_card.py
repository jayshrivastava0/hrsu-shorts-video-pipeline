from __future__ import annotations
import numpy as np
from shorts_engine import config

PAYLOAD = {"text": "EU nitrate limits are tightening", "accent": "tightening"}


def _content_pixels(img, bg):
    a = np.asarray(img).astype(int)
    b = np.asarray(bg).astype(int)
    return np.argwhere(np.abs(a - b).sum(axis=2) > 30)


class TestHeadlineFrames:
    def test_text_drawn_inside_safe_zone(self):
        from shorts_engine.cards import headline_card, theme
        img = headline_card.frame_at(PAYLOAD, 2.0, 3.0)
        diff = _content_pixels(img, theme.background(2.0))
        assert len(diff) > 200
        ys, xs = diff[:, 0], diff[:, 1]
        assert xs.min() >= config.SAFE_SIDE_PX - 2
        assert xs.max() <= config.CANVAS_W - config.SAFE_SIDE_PX + 2
        assert ys.min() >= config.SAFE_TOP_PX - 2
        assert ys.max() <= config.CANVAS_H - config.SAFE_BOTTOM_PX + 2

    def test_accent_word_is_gold(self):
        from shorts_engine.cards import headline_card, theme
        img = headline_card.frame_at(PAYLOAD, 2.0, 3.0)
        arr = np.asarray(img).astype(int)
        gold = np.array(theme.GOLD)
        near_gold = (np.abs(arr - gold).sum(axis=2) < 90).sum()
        assert near_gold > 50

    def test_default_accent_prefers_numeric(self):
        from shorts_engine.cards import headline_card
        assert headline_card.pick_accent("dosing at 1.5 kg per cubic meter") == "1.5"
        assert headline_card.pick_accent("nitrate compliance window") == "compliance"

    def test_animation_reveals_over_time(self):
        from shorts_engine.cards import headline_card, theme
        early = _content_pixels(headline_card.frame_at(PAYLOAD, 0.02, 3.0),
                                theme.background(0.02))
        late = _content_pixels(headline_card.frame_at(PAYLOAD, 1.0, 3.0),
                               theme.background(1.0))
        assert len(late) > len(early)


class TestHeadlineRender:
    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import headline_card, encoder
        out = headline_card.render(PAYLOAD, 0.6, tmp_path / "h.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
