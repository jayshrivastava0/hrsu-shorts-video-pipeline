"""Mixes a region-specific royalty-free track under the voiceover with
sidechain ducking (-12dB when voice is present)."""
from __future__ import annotations
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def mix_music_under_voice(voice_path: Path, output_path: Path,
                          region: str,
                          music_root: Path = Path("asset_library/music"),
                          base_gain_db: int = -20) -> Path:
    """Mix a region-specific music track under a voiceover with sidechain ducking.

    Args:
        voice_path: Path to the voiceover audio file (typically MP3 or WAV)
        output_path: Where to write the mixed audio output
        region: Region identifier (used to find region-specific music track)
        music_root: Root directory containing region-specific music tracks
        base_gain_db: Base gain for the music track in dB (default -20)

    Returns:
        Path to the mixed output. If no music track exists for the region,
        returns the voice_path unchanged (voiceover only).
    """
    voice_path = Path(voice_path)
    output_path = Path(output_path)
    track = Path(music_root) / f"{region}.mp3"
    if not track.exists():
        log.warning("No music track for region %r at %s; voiceover only",
                    region, track)
        return voice_path
    # ffmpeg sidechain compression: music ducks when voice is loud
    f = (
        f"[1:a]volume={base_gain_db}dB[m_quiet];"
        f"[m_quiet][0:a]sidechaincompress=threshold=0.05:"
        f"ratio=8:attack=20:release=400[m_ducked];"
        f"[0:a][m_ducked]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(voice_path), "-i", str(track),
        "-filter_complex", f, "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-shortest", str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path
