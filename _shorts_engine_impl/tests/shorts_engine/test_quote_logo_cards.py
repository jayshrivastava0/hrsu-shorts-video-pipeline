from __future__ import annotations
import numpy as np


class TestQuoteCard:
    def test_trim_quote_at_word_boundary(self):
        from shorts_engine.cards import quote_card
        q = "x" * 50 + " " + "y" * 100
        out = quote_card.trim_quote(q, limit=120)
        assert len(out) <= 121 and out.endswith("…")
        assert quote_card.trim_quote("short quote") == "short quote"

    def test_frame_has_quote_and_chip(self):
        from shorts_engine.cards import quote_card, theme
        p = {"quote": "the optimal dosage range of 1.5 to 3 kg per cubic meter",
             "source": "Source [2] — springer.com"}
        img = quote_card.frame_at(p, 2.0, 3.5)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 300_000

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import quote_card, encoder
        out = quote_card.render({"quote": "q"}, 0.6, tmp_path / "q.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15


class TestLogoCta:
    PAYLOAD = {"differentiator": "Consistent high-purity calcium nitrate powder",
               "cta_line": "Full technical guide on the HRSU blog",
               "domain": "hrsuindore.com"}

    def test_frame_has_content_and_gold_domain(self):
        from shorts_engine.cards import logo_cta_card, theme
        img = logo_cta_card.frame_at(self.PAYLOAD, 2.0, 7.0)
        arr = np.asarray(img).astype(int)
        assert (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum() > 300

    def test_wordmark_fallback_when_logo_missing(self, monkeypatch, tmp_path):
        from shorts_engine.cards import logo_cta_card, theme
        monkeypatch.setattr(logo_cta_card, "_logo_path", tmp_path / "missing.png")
        logo_cta_card._load_logo.cache_clear()
        img = logo_cta_card.frame_at(self.PAYLOAD, 2.0, 7.0)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 200_000
        logo_cta_card._load_logo.cache_clear()

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import logo_cta_card, encoder
        out = logo_cta_card.render(self.PAYLOAD, 0.6, tmp_path / "l.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
