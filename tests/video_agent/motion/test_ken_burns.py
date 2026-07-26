from pathlib import Path
from PIL import Image
from video_agent.motion.ken_burns import (
    plan_ken_burns, render_motion_clip, MotionPlan,
)


def test_landscape_image_pans_right_when_proof():
    img = Image.new("RGB", (3840, 2160), "white")
    plan = plan_ken_burns(img.size, mood="proof", duration_s=4.0, fps=30)
    assert plan.direction == "right"
    # Start vs end x must differ; viewport stays inside source
    assert plan.start_xy[0] != plan.end_xy[0]


def test_portrait_image_zoom_in_for_mechanism():
    img = Image.new("RGB", (1080, 1920), "white")
    plan = plan_ken_burns(img.size, mood="mechanism", duration_s=4.0, fps=30)
    # For tall source images we still zoom in regardless of mood
    assert plan.start_scale > 1.0 or plan.end_scale > 1.0


def test_render_writes_mp4(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (3000, 1700), "blue").save(src)
    out = tmp_path / "clip.mp4"
    plan = plan_ken_burns((3000, 1700), mood="problem",
                          duration_s=2.0, fps=24)
    render_motion_clip(src, plan, out, duration_s=2.0, fps=24,
                       target_size=(1080, 1920))
    assert out.exists() and out.stat().st_size > 1000
