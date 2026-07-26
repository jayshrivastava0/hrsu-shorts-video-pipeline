"""Ken Burns motion: pans/zooms the (1080x1920) viewport across a still image.

Direction is mood-aware:
  problem    → slow downward drift
  mechanism  → zoom in (focusing)
  proof      → pan left to right (revealing)
  brand/cta  → zoom out (concluding)
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.safezone import FRAME_W, FRAME_H


@dataclass
class MotionPlan:
    direction: str            # "left", "right", "up", "down", "in", "out"
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    start_scale: float
    end_scale: float


def _viewport_at(src_w: int, src_h: int, scale: float) -> tuple[int, int]:
    """Size of the visible portrait viewport in source pixels at `scale`."""
    target_aspect = FRAME_W / FRAME_H
    # Fit a 9:16 viewport entirely within the source at `scale`
    vp_h = int(src_h / scale)
    vp_w = int(vp_h * target_aspect)
    if vp_w > src_w:
        vp_w = int(src_w / scale)
        vp_h = int(vp_w / target_aspect)
    return vp_w, vp_h


def plan_ken_burns(src_size: tuple[int, int], mood: str,
                   duration_s: float, fps: int = 30,
                   focus_x: float = 0.5, focus_y: float = 0.5) -> MotionPlan:
    """Plan Ken Burns motion. focus_x/focus_y (0..1) bias the viewport toward the
    most important subject — keeps faces, gauges, and focal elements on-screen
    instead of always centering."""
    src_w, src_h = src_size
    aspect = src_w / src_h
    # Clamp focus to valid range
    fx = max(0.0, min(1.0, focus_x))
    fy = max(0.0, min(1.0, focus_y))

    def _focus_center(vp_w: int, vp_h: int) -> tuple[float, float]:
        """Viewport top-left so the focus point is inside and the viewport
        is clamped within the source."""
        cx = src_w * fx - vp_w / 2
        cy = src_h * fy - vp_h / 2
        return (max(0, min(cx, src_w - vp_w)),
                max(0, min(cy, src_h - vp_h)))

    if mood == "mechanism":
        s0, s1 = 1.0, 1.05   # gentler zoom so the whole diagram stays in frame
        vp0 = _viewport_at(src_w, src_h, s0)
        vp1 = _viewport_at(src_w, src_h, s1)
        x0, y0 = _focus_center(vp0[0], vp0[1])
        x1, y1 = _focus_center(vp1[0], vp1[1])
        return MotionPlan("in", (x0, y0), (x1, y1), s0, s1)
    if mood in ("brand", "cta"):
        s0, s1 = 1.18, 1.0
        vp0 = _viewport_at(src_w, src_h, s0)
        vp1 = _viewport_at(src_w, src_h, s1)
        x0, y0 = _focus_center(vp0[0], vp0[1])
        x1, y1 = _focus_center(vp1[0], vp1[1])
        return MotionPlan("out", (x0, y0), (x1, y1), s0, s1)
    if mood == "proof" and aspect > 1.05:
        # Pan left-to-right; start near the focus point
        s = 1.0
        vp_w, vp_h = _viewport_at(src_w, src_h, s)
        y = max(0, min(src_h * fy - vp_h / 2, src_h - vp_h))
        return MotionPlan("right",
                          (0, y),
                          (max(0, src_w - vp_w), y),
                          s, s)
    # Default (problem mood): drift along whichever axis has travel room.
    # A landscape source has no vertical room — pan horizontally instead.
    s = 1.0
    vp_w, vp_h = _viewport_at(src_w, src_h, s)
    h_room = max(0, src_w - vp_w)
    v_room = max(0, src_h - vp_h)
    if max(h_room, v_room) < 24:
        # Source is already ~9:16 — no pan room; gentle zoom-in toward focus point.
        s0, s1 = 1.0, 1.08
        vp0 = _viewport_at(src_w, src_h, s0)
        vp1 = _viewport_at(src_w, src_h, s1)
        x0, y0 = _focus_center(vp0[0], vp0[1])
        x1, y1 = _focus_center(vp1[0], vp1[1])
        return MotionPlan("in", (x0, y0), (x1, y1), s0, s1)
    if h_room >= v_room:
        y = max(0, min(src_h * fy - vp_h / 2, v_room))
        return MotionPlan("right", (0, y), (h_room, y), s, s)
    x = max(0, min(src_w * fx - vp_w / 2, h_room))
    return MotionPlan("down", (x, 0), (x, v_room), s, s)


def render_motion_clip(src: Path, plan: MotionPlan, dest: Path,
                       duration_s: float, fps: int,
                       target_size: tuple[int, int] = (FRAME_W, FRAME_H)) -> Path:
    """Render the still + motion plan to an MP4.

    zoompan can only crop regions with the SOURCE aspect ratio — asking it for
    a 9:16 output from a landscape source squashes the whole image. So:
    - pans (constant scale): animated `crop` of a true 9:16 viewport, then scale.
    - zooms: pre-crop a centered max 9:16 region, then zoompan on it (the
      region aspect now matches the output, so no distortion).
    """
    from PIL import Image

    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(1, int(duration_s * fps))
    tw, th = target_size
    target_aspect = tw / th
    with Image.open(src) as im:
        src_w, src_h = im.size
    denom = total_frames - 1 or 1

    if abs(plan.start_scale - plan.end_scale) < 1e-6:
        # Pan: fixed-size 9:16 viewport sliding from start_xy to end_xy.
        vp_w, vp_h = _viewport_at(src_w, src_h, plan.start_scale)
        # crop x/y are evaluated per frame; clamp inside the source.
        x_expr = (f"min(max({plan.start_xy[0]}+"
                  f"({plan.end_xy[0]-plan.start_xy[0]})*n/{denom},0),iw-{vp_w})")
        y_expr = (f"min(max({plan.start_xy[1]}+"
                  f"({plan.end_xy[1]-plan.start_xy[1]})*n/{denom},0),ih-{vp_h})")
        vf = (f"crop={vp_w}:{vp_h}:'{x_expr}':'{y_expr}',"
              f"scale={tw}:{th},setsar=1")
    else:
        # Zoom: centered max 9:16 base region, zoompan interpolates the scale.
        base_h = src_h
        base_w = int(base_h * target_aspect)
        if base_w > src_w:
            base_w = src_w
            base_h = int(base_w / target_aspect)
        base_w -= base_w % 2
        base_h -= base_h % 2
        cx = (src_w - base_w) // 2
        cy = (src_h - base_h) // 2
        z_expr = (f"'{plan.start_scale}+({plan.end_scale-plan.start_scale})"
                  f"*on/{denom}'")
        vf = (f"crop={base_w}:{base_h}:{cx}:{cy},"
              f"zoompan=z={z_expr}:x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
              f"d=1:s={tw}x{th}:fps={fps},setsar=1")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", str(fps), "-i", str(src),
        "-vf", vf,
        "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest
