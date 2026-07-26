from pathlib import Path
from video_agent.visual_engine.dispatcher import generate_visual, generate_all_visuals


def _scene(idx, vt, spec=None, text=""):
    return {"index": idx, "narration": "n", "duration_s": 3.0,
            "visual_type": vt, "visual_spec": spec or {},
            "on_screen_text": text, "transition_in": "fade"}


def test_dispatches_text_card(tmp_path):
    s = _scene(0, "text_card", {"layout": "hook"}, "HOOK")
    out = generate_visual(s, tmp_path / "0.png")
    assert out["asset_path"].exists()
    assert out["generator_used"] == "text_card"
    assert not out["is_video_clip"]


def test_dispatches_infographic(tmp_path):
    s = _scene(1, "infographic",
               {"chart_type": "callout_stat", "data": {"value": "90%", "label": "x"}})
    out = generate_visual(s, tmp_path / "1.png")
    assert out["asset_path"].exists()
    assert out["generator_used"] == "infographic"


def test_unknown_visual_falls_back_to_text_card(tmp_path):
    s = _scene(2, "unknown_type", {}, "FALLBACK")
    out = generate_visual(s, tmp_path / "2.png")
    assert out["generator_used"] == "text_card"


def test_generate_all_preserves_order(tmp_path):
    scenes = [_scene(i, "text_card", {"layout": "hook"}, f"S{i}") for i in range(4)]
    results = generate_all_visuals(scenes, tmp_path)
    assert len(results) == 4
    for i, r in enumerate(results):
        assert r["asset_path"].name.startswith(f"scene_{i:02d}")


def test_per_scene_failure_falls_back(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("matplotlib died")
    monkeypatch.setattr("video_agent.visual_engine.dispatcher.render_infographic", boom)
    s = _scene(0, "infographic", {"chart_type": "bar", "data": {"labels": [], "values": []}}, "FB")
    out = generate_visual(s, tmp_path / "0.png")
    assert out["generator_used"] == "text_card"
