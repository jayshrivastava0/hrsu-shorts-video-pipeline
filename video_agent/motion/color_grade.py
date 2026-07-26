"""Per-palette color grading: ffmpeg filter chains keyed by the
Cinematographer's color_grade choice.

Legacy mood-keyed lookup remains as a fallback for scenes without
cinematography decisions."""
from __future__ import annotations
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_PALETTE_FILTERS: dict[str, str | None] = {
    # 12% red shift + soft vignette — tension / problem open
    "red_tension": (
        "colorchannelmixer=rr=1.12:rg=0.0:rb=0.0:"
        "gr=0.0:gg=0.96:gb=0.0:br=0.0:bg=0.0:bb=0.96,"
        "vignette=PI/5"
    ),
    # 8% blue shift + 5% desaturation — sterile, mechanical feel
    "cold_blue": (
        "colorchannelmixer=rr=0.95:gg=0.97:bb=1.08,"
        "hue=s=0.95"
    ),
    # 6% warm tint + gentle vignette — conclusive / brand
    "warm_brand": (
        "colorchannelmixer=rr=1.06:gg=1.02:bb=0.94,"
        "vignette=PI/6:eval=init"
    ),
    # 10% amber + slight contrast bump — urgency without alarm
    "urgent_amber": (
        "colorchannelmixer=rr=1.10:gg=1.04:bb=0.90,"
        "eq=contrast=1.05"
    ),
    # Slight desat + +5% brightness — lab / clinical
    "clinical_white": (
        "hue=s=0.92,eq=brightness=0.05"
    ),
    # neutral_doc: explicitly no grade
    "neutral_doc": None,
}


def grade_filter_for_palette(palette: str) -> str | None:
    """Returns an ffmpeg filter chain for the palette, or None for no grading."""
    return _PALETTE_FILTERS.get(palette)


# Legacy mood-based map — used as fallback when scene has no Cinematography.
_MOOD_TO_PALETTE = {
    "problem": "red_tension",
    "brand": "warm_brand",
    "cta": "warm_brand",
}


def grade_filter_for_mood(mood: str) -> str | None:
    palette = _MOOD_TO_PALETTE.get(mood)
    if palette is None:
        return None
    return grade_filter_for_palette(palette)


def apply_grade(clip: Path, palette_or_mood: str) -> Path:
    """Renders an in-place graded copy; returns the same path.

    Accepts either a palette name (preferred) or a legacy mood ("problem",
    "brand"). Falls through to no-op when neither matches.
    On filter failure, leaves the clip untouched and logs a warning."""
    flt = grade_filter_for_palette(palette_or_mood)
    if flt is None:
        flt = grade_filter_for_mood(palette_or_mood)
    if not flt:
        return clip
    out = clip.with_name(clip.stem + "_graded.mp4")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip),
            "-vf", flt, "-c:v", "libx264", "-preset", "fast",
            "-crf", "20", "-pix_fmt", "yuv420p", str(out),
        ], check=True)
    except subprocess.CalledProcessError as e:
        log.warning("apply_grade failed for %s (%s); leaving clip ungraded",
                    palette_or_mood, e)
        return clip
    clip.unlink()
    out.rename(clip)
    return clip
