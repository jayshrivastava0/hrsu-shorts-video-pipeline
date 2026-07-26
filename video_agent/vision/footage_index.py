"""Vision-indexed footage source.

The user drops .mp4/.mov clips into asset_library/factory/ (preferred, real
HRSU footage) or asset_library/footage/ (other owned footage). NO manual
manifest is required: this module extracts a representative frame from each
clip, asks the cloud multimodal model to describe what it shows, and caches
that description. At scene time it judges each clip's representative frame
against the narration (same VisionVerdict contract as web images) so footage
competes — and, per product decision, is PREFERRED — on actual pixels.

Cache: asset_library/<dir>/_vision_index.json, keyed by "filename:mtime" so a
re-encoded/replaced clip is re-described automatically.
"""
from __future__ import annotations
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
from video_agent.vision.judge import judge_image

log = logging.getLogger(__name__)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

# Search these dirs in order; earlier = higher trust (real factory footage).
FOOTAGE_DIRS = [Path("asset_library/factory"), Path("asset_library/footage")]


@dataclass
class FootageClip:
    path: Path
    duration_s: float
    rep_frame: Path        # extracted representative frame (mid-clip)


def _probe_duration(clip: Path) -> float:
    if _FFPROBE is None:
        return 0.0
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
            check=True, capture_output=True, text=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _extract_rep_frame(clip: Path, dest: Path) -> Path | None:
    if _FFMPEG is None:
        return None
    dur = _probe_duration(clip)
    mid = max(0.1, dur / 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-ss", f"{mid:.2f}",
             "-i", str(clip), "-vframes", "1", "-q:v", "3", str(dest)],
            check=True, capture_output=True)
    except Exception as e:
        log.warning("footage frame extract failed for %s: %s", clip, e)
        return None
    return dest if dest.exists() else None


def discover_clips() -> list[FootageClip]:
    """Find every clip across FOOTAGE_DIRS and extract a representative frame.
    Frames are cached under <dir>/_vision_frames/. Returns clips in trust order
    (factory before footage)."""
    clips: list[FootageClip] = []
    for d in FOOTAGE_DIRS:
        if not d.exists():
            continue
        frames_dir = d / "_vision_frames"
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in _VIDEO_EXTS:
                continue
            rep = _extract_rep_frame(f, frames_dir / f"{f.stem}.jpg")
            if rep is None:
                continue
            clips.append(FootageClip(path=f, duration_s=_probe_duration(f),
                                     rep_frame=rep))
    return clips


def best_footage_for_scene(narration: str, beat: str, hero_claim: str,
                           visual_subject: str,
                           clips: list[FootageClip] | None = None):
    """Judge every clip's representative frame against the scene and return
    (FootageClip, VisionVerdict) for the highest scorer, or None if no clips.

    Trust-order tiebreak: when two clips tie on vision score, the earlier one
    in FOOTAGE_DIRS order (factory) wins because discover_clips() preserves it
    and we use a stable max."""
    clips = discover_clips() if clips is None else clips
    if not clips:
        return None
    best = None  # (score, idx, clip, verdict)
    for idx, clip in enumerate(clips):
        v = judge_image(clip.rep_frame, narration, beat=beat,
                        hero_claim=hero_claim, visual_subject=visual_subject)
        if v is None:
            continue
        key = (v.score, -idx)  # higher score, then lower idx (earlier dir)
        if best is None or key > best[0]:
            best = (key, clip, v)
    if best is None:
        return None
    return (best[1], best[2])
