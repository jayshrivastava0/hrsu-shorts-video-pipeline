"""Voiceover generation via edge-tts (primary) with Kokoro-82M fallback."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from pydub import AudioSegment

import edge_tts

from video_agent.config import (
    TTS_VOICES, KOKORO_DEFAULT_VOICE,
)
from video_agent.text_normalizer import normalize_for_tts

log = logging.getLogger(__name__)

INTER_SEGMENT_GAP_MS = 350   # breath pause between scenes; 120ms read as one run-on
MAX_WORDS = 200
MIN_DURATION_S = 30
MAX_DURATION_S = 65
MIN_FILE_BYTES = 1024


class VoiceoverError(RuntimeError):
    pass


@dataclass
class VoiceSegment:
    text: str
    prosody: str = "conversational"


_PROSODY_PRESETS: dict[str, dict] = {
    "hook_emphasis":  {"rate": "-10%", "pitch": "+2st",  "emphasis": True},
    "urgent_problem": {"rate": "+5%",  "pitch": "+1st",  "emphasis": False},
    "conversational": {"rate": None,   "pitch": None,    "emphasis": False},
    "warm_cta":       {"rate": "-5%",  "pitch": "-1st",  "emphasis": False},
    "matter_of_fact": {"rate": None,   "pitch": None,    "emphasis": False},
}

# edge-tts Communicate kwargs format: rate in %, pitch in Hz (not semitones).
# Kept separate from _PROSODY_PRESETS (which uses SSML semitone notation).
# Pitch deltas are kept within ±6Hz of the base voice. Larger swings (the old
# ±25Hz) made one neural voice read as several different speakers across scenes.
_EDGE_TTS_PRESETS: dict[str, dict] = {
    "hook_emphasis":  {"rate": "-10%", "pitch": "+6Hz"},
    "urgent_problem": {"rate": "+8%",  "pitch": "+4Hz"},
    "conversational": {"rate": "+0%",  "pitch": "+0Hz"},
    "warm_cta":       {"rate": "-8%",  "pitch": "-4Hz"},
    "matter_of_fact": {"rate": "-3%",  "pitch": "+0Hz"},
}


def build_ssml_segment(text: str, prosody: str, voice: str) -> str:
    """Wrap text in SSML with prosody/emphasis from the named preset."""
    preset = _PROSODY_PRESETS.get(prosody, _PROSODY_PRESETS["conversational"])
    rate = preset["rate"]
    pitch = preset["pitch"]
    emphasis = preset.get("emphasis", False)

    inner = text
    if emphasis:
        inner = f'<emphasis level="strong">{inner}</emphasis>'
    if rate is not None or pitch is not None:
        attrs = ""
        if rate is not None:
            attrs += f' rate="{rate}"'
        if pitch is not None:
            attrs += f' pitch="{pitch}"'
        inner = f"<prosody{attrs}>{inner}</prosody>"

    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis">'
        f'<voice name="{voice}">{inner}</voice></speak>'
    )


def _edge_synthesize(text: str, voice: str, output_path: Path,
                     rate: str = "+0%", pitch: str = "+0Hz") -> None:
    """Synthesise plain text via edge-tts using its native rate/pitch kwargs."""
    async def _run():
        comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await comm.save(str(output_path))
    try:
        asyncio.run(_run())
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_run())
        else:
            raise


def _kokoro_synthesize(text: str, output_path: Path) -> None:
    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        raise VoiceoverError(
            "Kokoro fallback requires kokoro-onnx — pip install kokoro-onnx"
        ) from e
    kokoro = Kokoro.from_pretrained()
    samples, sr = kokoro.create(text, voice=KOKORO_DEFAULT_VOICE)
    seg = AudioSegment(
        samples.tobytes(), frame_rate=sr, sample_width=samples.dtype.itemsize, channels=1,
    )
    seg.export(output_path, format="mp3", bitrate="128k")


def synthesize_segments(segments: list[VoiceSegment], output_path: Path,
                        region: str, voice_override: str | None = None) -> dict:
    """Render per-segment SSML, concatenate with INTER_SEGMENT_GAP_MS silence between segments."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice = voice_override or TTS_VOICES.get(region, TTS_VOICES["default"])

    fell_back = False
    engine = "edge-tts"

    def _render_all(use_kokoro: bool) -> list:
        parts = []
        for i, seg in enumerate(segments):
            normalized = normalize_for_tts(seg.text)
            tmp = output_path.with_name(f"{output_path.stem}_seg{i:02d}.mp3")
            if use_kokoro:
                _kokoro_synthesize(normalized, tmp)
            else:
                ep = _EDGE_TTS_PRESETS.get(seg.prosody,
                                           _EDGE_TTS_PRESETS["conversational"])
                _edge_synthesize(normalized, voice, tmp,
                                 rate=ep["rate"], pitch=ep["pitch"])
                if not tmp.exists() or tmp.stat().st_size < MIN_FILE_BYTES:
                    raise RuntimeError(f"edge-tts output too small (seg {i})")
            parts.append(AudioSegment.from_mp3(str(tmp)))
        return parts

    try:
        audio_parts = _render_all(use_kokoro=False)
    except Exception as e:
        # ANY edge-tts failure => re-render the ENTIRE track in Kokoro so the
        # whole video is one consistent voice (never a per-segment splice).
        log.warning("edge-tts failed (%s) — re-rendering ALL segments in Kokoro "
                    "for voice consistency", e)
        fell_back = True
        engine = "kokoro"
        audio_parts = _render_all(use_kokoro=True)

    if not audio_parts:
        raise VoiceoverError("No audio segments produced")

    gap = AudioSegment.silent(duration=INTER_SEGMENT_GAP_MS)
    combined = audio_parts[0]
    for part in audio_parts[1:]:
        combined = combined + gap + part

    combined.export(str(output_path), format="mp3", bitrate="128k")

    for i in range(len(segments)):
        tmp = output_path.with_name(f"{output_path.stem}_seg{i:02d}.mp3")
        tmp.unlink(missing_ok=True)

    duration_s = len(combined) / 1000.0
    if not (MIN_DURATION_S <= duration_s <= MAX_DURATION_S):
        log.warning("Voiceover duration %.1fs outside target [%d, %d]",
                    duration_s, MIN_DURATION_S, MAX_DURATION_S)

    return {
        "audio_path": output_path,
        "duration_s": duration_s,
        "voice_used": voice,
        "engine_used": engine,
        "fell_back": fell_back,
    }


def synthesize(narration: str, output_path: Path, region: str,
               voice_override: str | None = None) -> dict:
    """Back-compat shim: wraps narration in a single conversational VoiceSegment."""
    words = narration.split()
    if len(words) > MAX_WORDS:
        raise VoiceoverError(
            f"Narration too long ({len(words)} words); max {MAX_WORDS} words"
        )
    return synthesize_segments(
        [VoiceSegment(narration, "conversational")],
        output_path, region, voice_override=voice_override,
    )
