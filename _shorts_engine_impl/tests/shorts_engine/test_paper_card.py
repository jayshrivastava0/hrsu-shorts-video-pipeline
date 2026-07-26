from __future__ import annotations
import numpy as np
import pytest
from PIL import Image
from shorts_engine import config


@pytest.fixture()
def front_page(tmp_path):
    p = tmp_path / "page1.png"
    img = Image.new("RGB", (1600, 2100), (250, 250, 248))  # white paper
    img.save(p)
    return p


def _payload(front_page):
    return {"image_path": str(front_page),
            "highlight": "92 percent nitrate removal",
            "citation": "Source [12] — arxiv.org"}


class TestPaperFrames:
    def test_paper_inset_visible_and_inside_safe_zone(self, front_page):
        from shorts_engine.cards import paper_card, theme
        img = paper_card.frame_at(_payload(front_page), 1.5, 4.0)
        arr = np.asarray(img).astype(int)
        # the white paper dominates the mid region
        white = (arr.min(axis=2) > 200).sum()
        assert white > 200_000
        ys, xs = np.where(arr.min(axis=2) > 200)
        assert xs.min() >= config.SAFE_SIDE_PX - 40   # tilt tolerance
        assert xs.max() <= config.CANVAS_W - config.SAFE_SIDE_PX + 40

    def test_gold_sweep_grows_over_first_second(self, front_page):
        from shorts_engine.cards import paper_card, theme
        def gold(t):
            arr = np.asarray(paper_card.frame_at(_payload(front_page), t, 4.0)).astype(int)
            return (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum()
        assert gold(0.9) > gold(0.1)

    def test_push_in_changes_frame_over_time(self, front_page):
        from shorts_engine.cards import paper_card
        a = np.asarray(paper_card.frame_at(_payload(front_page), 0.0, 4.0))
        b = np.asarray(paper_card.frame_at(_payload(front_page), 3.9, 4.0))
        assert np.abs(a.astype(int) - b.astype(int)).sum() > 0

    def test_missing_image_raises(self):
        from shorts_engine.cards import paper_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            paper_card.frame_at({"image_path": "Z:/nope.png",
                                 "highlight": "h", "citation": "c"}, 0.5, 4.0)

    def test_render_mp4(self, front_page, tmp_path):
        from shorts_engine.cards import paper_card, encoder
        out = paper_card.render(_payload(front_page), 0.6, tmp_path / "p.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
