from pathlib import Path
from PIL import Image
from video_agent.visual_engine.infographic import render_infographic


def test_bar_chart(tmp_path):
    out = tmp_path / "bar.png"
    render_infographic(out, chart_type="bar",
                       title="Test", data={"labels": ["A", "B"], "values": [10, 90]})
    assert Image.open(out).size == (1080, 1920)


def test_callout_stat(tmp_path):
    out = tmp_path / "stat.png"
    render_infographic(out, chart_type="callout_stat",
                       title="H₂S Reduction", data={"value": "90%", "label": "with calcium nitrate"})
    assert Image.open(out).size == (1080, 1920)


def test_comparison(tmp_path):
    out = tmp_path / "cmp.png"
    render_infographic(out, chart_type="comparison",
                       title="Without vs With",
                       data={"left_label": "Without", "left_value": "20%",
                             "right_label": "With", "right_value": "90%"})
    assert out.exists()


def test_flow_chart(tmp_path):
    out = tmp_path / "flow.png"
    render_infographic(out, chart_type="flow",
                       title="Process", data={"steps": ["Dose", "React", "Settle"]})
    assert out.exists()


def test_line_chart(tmp_path):
    out = tmp_path / "line.png"
    render_infographic(out, chart_type="line",
                       title="Trend", data={"x": [1, 2, 3], "y": [10, 50, 90]})
    assert out.exists()


def test_unknown_chart_falls_back_to_callout(tmp_path):
    out = tmp_path / "u.png"
    render_infographic(out, chart_type="totally_made_up",
                       title="X", data={"value": "1", "label": "x"})
    assert out.exists()


def test_deterministic_output(tmp_path):
    out1 = tmp_path / "1.png"
    out2 = tmp_path / "2.png"
    spec = dict(chart_type="bar", title="T",
                data={"labels": ["A", "B"], "values": [10, 90]}, seed=42)
    render_infographic(out1, **spec)
    render_infographic(out2, **spec)
    assert out1.read_bytes() == out2.read_bytes()
