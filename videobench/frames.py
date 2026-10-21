"""Sample grayscale frames from a video or load still snapshots, for the tier-1 metrics."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

SAMPLE_W, SAMPLE_H = 270, 480  # portrait working resolution (aspect-preserving for 1080x1920)


def probe_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = (int(x) for x in out.split(",")[:2])
    return w, h


def sample_video(video: Path, fps: float = 2.0) -> tuple[list[np.ndarray], list[float]]:
    """Frames at `fps`, scaled so the long side is 480 px, as float32 gray arrays."""
    w, h = probe_size(video)
    sw, sh = (SAMPLE_W, SAMPLE_H) if h >= w else (SAMPLE_H, SAMPLE_W)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf",
         f"fps={fps},scale={sw}:{sh}:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    n = len(raw) // (sw * sh)
    arr = np.frombuffer(raw[: n * sw * sh], dtype=np.uint8).reshape(n, sh, sw)
    frames = [a.astype(np.float32) for a in arr]
    return frames, [i / fps for i in range(n)]


def load_still(path: Path) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    size = (SAMPLE_W, SAMPLE_H) if h >= w else (SAMPLE_H, SAMPLE_W)
    return np.asarray(img.resize(size, Image.BOX), dtype=np.float32)
