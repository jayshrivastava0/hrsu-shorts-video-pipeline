"""Frames→mp4 via ffmpeg rawvideo pipe, plus ffprobe duration helper."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Iterable

from PIL import Image

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def write_frames_to_mp4(frames: Iterable[Image.Image], out_path: Path,
                        fps: int = config.FPS) -> int:
    """Pipe RGB frames (must all be CANVAS_W×CANVAS_H) into libx264. Returns
    the frame count. Raises EngineError on zero frames or encoder failure."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{config.CANVAS_W}x{config.CANVAS_H}", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    try:
        for img in frames:
            if img.size != (config.CANVAS_W, config.CANVAS_H):
                raise EngineError(f"frame {n} is {img.size}, expected "
                                  f"{(config.CANVAS_W, config.CANVAS_H)}")
            if img.mode != "RGB":
                img = img.convert("RGB")
            proc.stdin.write(img.tobytes())
            n += 1
    finally:
        proc.stdin.close()
        proc.wait()
    if n == 0:
        out_path.unlink(missing_ok=True)
        raise EngineError("write_frames_to_mp4: no frames supplied")
    if proc.returncode != 0 or not out_path.exists():
        raise EngineError(f"ffmpeg encode failed (rc={proc.returncode}) for {out_path}")
    return n


def probe_duration(path: Path) -> float:
    """Container duration in seconds via ffprobe. EngineError if unreadable."""
    path = Path(path)
    if not path.exists():
        raise EngineError(f"probe_duration: missing file {path}")
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(res.stdout.strip())
    except ValueError as e:
        raise EngineError(f"probe_duration: unparseable ffprobe output for {path}: "
                          f"{res.stdout!r} / {res.stderr!r}") from e
