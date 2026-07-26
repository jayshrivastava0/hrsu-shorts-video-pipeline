from pathlib import Path
from unittest.mock import patch
from video_agent.tools.render_brand_assets import render_intro, render_outro


def test_render_intro_calls_ffmpeg(tmp_path):
    out = tmp_path / "intro.mp4"
    with patch("video_agent.tools.render_brand_assets._ffmpeg") as m:
        m.side_effect = lambda cmd: out.write_bytes(b"fake mp4")
        render_intro(out, duration_s=3.0)
    m.assert_called_once()


def test_render_outro_creates_output(tmp_path):
    out = tmp_path / "outro.mp4"
    with patch("video_agent.motion.ken_burns.render_motion_clip") as m:
        m.side_effect = lambda *a, **kw: out.write_bytes(b"fake mp4")
        render_outro(out, duration_s=5.0)
    m.assert_called_once()
