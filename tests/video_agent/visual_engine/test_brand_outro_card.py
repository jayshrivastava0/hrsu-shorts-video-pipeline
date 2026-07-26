from pathlib import Path
import subprocess
from PIL import Image


def _make_logo(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (400, 400), (255, 200, 0, 255)).save(path)


def test_render_cta_scene_produces_mp4(tmp_path):
    from video_agent.visual_engine.brand_outro_card import render_cta_scene
    logo = tmp_path / "logo.png"
    _make_logo(logo)
    out = tmp_path / "cta.mp4"
    render_cta_scene(
        logo_path=logo, url_text="hrsuindore.com",
        duration_s=4.0, output_path=out,
    )
    assert out.exists()
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    dur = float(res.stdout.strip())
    assert 3.8 <= dur <= 4.2


def test_render_logo_stinger_is_silent_and_2_seconds(tmp_path):
    from video_agent.visual_engine.brand_outro_card import render_logo_stinger
    logo = tmp_path / "logo.png"
    _make_logo(logo)
    out = tmp_path / "stinger.mp4"
    render_logo_stinger(logo_path=logo, output_path=out)
    assert out.exists()
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams",
         "-of", "default=noprint_wrappers=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "codec_type=audio" not in res.stdout
    res2 = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    dur = float(res2.stdout.strip())
    assert 1.8 <= dur <= 2.2
