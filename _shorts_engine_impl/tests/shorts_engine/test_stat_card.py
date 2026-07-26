from __future__ import annotations
import numpy as np

RANGE_PAYLOAD = {"value": "1.5–3", "unit": "kg/m³", "label": "typical dosing window",
                 "citation": "Source [1] — mdpi.com"}
SCALAR_PAYLOAD = {"value": "150", "unit": "mg/L", "label": "limit"}


class TestCountUp:
    def test_format_value_keeps_decimals(self):
        from shorts_engine.cards import stat_card
        assert stat_card.format_value(1.5, "2.5") == "1.5"
        assert stat_card.format_value(120.0, "150") == "120"

    def test_scalar_counts_up(self):
        from shorts_engine.cards import stat_card
        assert stat_card.display_value(SCALAR_PAYLOAD["value"], 0.2) != "150"
        assert stat_card.display_value(SCALAR_PAYLOAD["value"], 1.2) == "150"

    def test_range_shown_verbatim_always(self):
        from shorts_engine.cards import stat_card
        assert stat_card.display_value("1.5–3", 0.1) == "1.5–3"
        assert stat_card.display_value("1.5–3", 2.0) == "1.5–3"


class TestStatFrames:
    def test_value_unit_label_chip_present_late(self):
        from shorts_engine.cards import stat_card, theme
        img = stat_card.frame_at(RANGE_PAYLOAD, 2.0, 4.0)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 500_000  # large value + label + chip drawn

    def test_underline_sweep_grows(self):
        from shorts_engine.cards import stat_card, theme
        def gold_count(t):
            img = stat_card.frame_at(SCALAR_PAYLOAD, t, 4.0)
            arr = np.asarray(img).astype(int)
            return (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum()
        assert gold_count(1.0) > gold_count(0.15)

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import stat_card, encoder
        out = stat_card.render(RANGE_PAYLOAD, 0.6, tmp_path / "s.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
