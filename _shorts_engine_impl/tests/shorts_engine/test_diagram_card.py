from __future__ import annotations
import numpy as np
import pytest

FLOW = {"template": "flow",
        "labels": ["Effluent in", "Calcium nitrate dosing", "Denitrifying filter",
                   "Clear discharge"],
        "reveal_stage": 3, "reveal_total": 3}


def _diff(img, bg):
    return np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()


class TestFlow:
    def test_all_nodes_visible_at_final_stage(self):
        from shorts_engine.cards import diagram_card, theme
        img = diagram_card.frame_at(FLOW, 2.5, 3.0)
        assert _diff(img, theme.background(2.5)) > 400_000

    def test_reveal_stage_gates_node_count(self):
        from shorts_engine.cards import diagram_card
        assert diagram_card.visible_nodes(4, stage=1, total=3) == 2  # ceil(4/3)
        assert diagram_card.visible_nodes(4, stage=2, total=3) == 3
        assert diagram_card.visible_nodes(4, stage=3, total=3) == 4

    def test_stage1_draws_less_than_stage3(self):
        from shorts_engine.cards import diagram_card, theme
        s1 = dict(FLOW, reveal_stage=1)
        a = _diff(diagram_card.frame_at(s1, 2.5, 3.0), theme.background(2.5))
        b = _diff(diagram_card.frame_at(FLOW, 2.5, 3.0), theme.background(2.5))
        assert b > a

    def test_flow_needs_2_to_4_labels(self):
        from shorts_engine.cards import diagram_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            diagram_card.frame_at({"template": "flow", "labels": ["only"]}, 0.5, 3.0)


class TestOtherTemplates:
    def test_before_after_renders(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "before_after", "before": ["High nitrate load"],
             "after": ["Compliant discharge"]}
        assert _diff(diagram_card.frame_at(p, 2.0, 3.0), theme.background(2.0)) > 200_000

    def test_comparison_renders(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "comparison",
             "left": {"title": "Granular", "items": ["slow dissolve"]},
             "right": {"title": "Powder", "items": ["fast dissolve"]}}
        assert _diff(diagram_card.frame_at(p, 2.0, 3.0), theme.background(2.0)) > 200_000

    def test_dosing_scale_band_is_gold(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "dosing_scale", "lo": "1.5", "hi": "3", "min": "0",
             "max": "5", "unit": "kg/m³", "label": "dosing window"}
        img = diagram_card.frame_at(p, 2.0, 3.0)
        arr = np.asarray(img).astype(int)
        assert (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum() > 500

    def test_unknown_template_raises(self):
        from shorts_engine.cards import diagram_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            diagram_card.frame_at({"template": "pie"}, 0.5, 3.0)

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import diagram_card, encoder
        out = diagram_card.render(FLOW, 0.6, tmp_path / "d.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
