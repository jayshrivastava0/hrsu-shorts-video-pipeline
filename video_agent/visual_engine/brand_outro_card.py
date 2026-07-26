"""Brand-template renderer for the final CTA scene and the silent 2s
logo stinger that closes the video.

These replace the deprecated pre-rendered OUTRO_VIDEO_PATH + bolted-on
templated CTA voiceover path."""
from __future__ import annotations
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_agent.config import BRAND_NAVY_2
from video_agent.safezone import FRAME_W, FRAME_H

STINGER_DURATION_S = 2.0


def _compose_brand_still(logo_path: Path, url_text: str,
                          out_png: Path) -> Path:
    """Compose a single 1080x1920 PNG with brand-navy background, logo
    in the top-third, URL wordmark in the bottom-third."""
    bg = Image.new("RGB", (FRAME_W, FRAME_H), BRAND_NAVY_2)

    logo = Image.open(logo_path).convert("RGBA")
    target_w = int(FRAME_W * 0.50)
    aspect = logo.height / max(1, logo.width)
    logo_resized = logo.resize((target_w, int(target_w * aspect)),
                               Image.LANCZOS)
    lx = (FRAME_W - logo_resized.width) // 2
    ly = int(FRAME_H * 0.28)
    bg.paste(logo_resized, (lx, ly), logo_resized)

    draw = ImageDraw.Draw(bg)
    font = None
    for _fname in ("Poppins-Bold.ttf", "Poppins Bold.ttf",
                   "arialbd.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(_fname, 78)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=78)
        except TypeError:
            font = ImageFont.load_default()
    tw = draw.textlength(url_text, font=font)
    tx = (FRAME_W - tw) // 2
    ty = int(FRAME_H * 0.72)
    draw.text((tx, ty), url_text, fill=(255, 255, 255), font=font)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_png, "PNG")
    return out_png


def render_cta_scene(logo_path: Path, url_text: str,
                      duration_s: float, output_path: Path,
                      fps: int = 30) -> Path:
    """Render the final CTA scene: brand background + logo settles in
    (scale 1.05 → 1.0, opacity 0 → 1 over first 0.6s) + URL wordmark.

    Output is video-only (no audio). The voiceover is added by the
    composer in the main mux pass."""
    logo_path = Path(logo_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    still = output_path.with_suffix(".still.png")
    _compose_brand_still(logo_path, url_text, still)

    total_frames = max(1, int(duration_s * fps))
    settle_frames = max(1, int(0.6 * fps))
    zoom_expr = f"'if(lte(on,{settle_frames}),1.05-0.05*on/{settle_frames},1.0)'"
    vf = (
        f"zoompan=z={zoom_expr}:d={total_frames}:s={FRAME_W}x{FRAME_H}:fps={fps},"
        f"fade=t=in:st=0:d=0.6,setsar=1"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(still),
        "-vf", vf, "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(output_path),
    ], check=True)
    still.unlink(missing_ok=True)
    return output_path


def render_logo_stinger(logo_path: Path, output_path: Path,
                         fps: int = 30) -> Path:
    """Render a silent 2-second logo stinger: held brand still that
    crossfades in over the first 0.4s. Closes the video."""
    logo_path = Path(logo_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    still = output_path.with_suffix(".stinger_still.png")
    _compose_brand_still(logo_path, "hrsuindore.com", still)
    vf = (
        f"fade=t=in:st=0:d=0.4,setsar=1,scale={FRAME_W}:{FRAME_H},"
        f"trim=duration={STINGER_DURATION_S}"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(still),
        "-vf", vf, "-t", str(STINGER_DURATION_S), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(output_path),
    ], check=True)
    still.unlink(missing_ok=True)
    return output_path
