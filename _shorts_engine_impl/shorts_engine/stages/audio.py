"""Stage 5 — AUDIO: per-beat TTS (existing voiceover engine), stitch, word
timings. Guards: no tiny/zero segment files (F10); duration within ±15%."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

# Late-binding test seams (resolved at call time so monkeypatching works).
_synthesize = None
_transcribe = None


def _resolve():
    global _synthesize, _transcribe
    synth, trans = _synthesize, _transcribe
    if synth is None:
        from video_agent.voiceover import synthesize_segments
        synth = synthesize_segments
    if trans is None:
        from video_agent.subtitles import transcribe_words
        trans = transcribe_words
    return synth, trans


def run(ctx) -> dict[str, str]:
    from video_agent.voiceover import VoiceSegment
    from pydub import AudioSegment

    ws = Path(ctx.workspace)
    beats = json.loads((ws / "script.json").read_text(encoding="utf-8"))["beats"]
    region = json.loads((ws / "post.json").read_text(encoding="utf-8")).get(
        "region") or "default"
    synth, trans = _resolve()

    beat_files: list[Path] = []
    beat_durs: list[float] = []
    for i, beat in enumerate(beats):
        prosody = config.PROSODY_BY_BEAT.get(beat["beat"], "conversational")
        out = ws / f"voice_beat_{i:02d}.mp3"
        res = synth([VoiceSegment(beat["narration"], prosody)], out, region)
        if not out.exists() or out.stat().st_size < config.MIN_SEGMENT_BYTES:
            size = out.stat().st_size if out.exists() else -1
            raise EngineError(
                f"AUDIO: beat '{beat['beat']}' voice file invalid "
                f"({size} bytes < {config.MIN_SEGMENT_BYTES}) — {out}")
        beat_files.append(out)
        beat_durs.append(float(res["duration_s"]))

    est = sum(len(b["narration"].split()) for b in beats) / config.WORDS_PER_SECOND
    actual = sum(beat_durs) + (len(beats) - 1) * config.AUDIO_BEAT_GAP_MS / 1000
    if est > 0 and abs(actual - est) / est > config.AUDIO_DURATION_TOLERANCE:
        raise EngineError(
            f"AUDIO: total voice duration {actual:.1f}s deviates from script "
            f"estimate {est:.1f}s by more than "
            f"{config.AUDIO_DURATION_TOLERANCE:.0%}")

    gap = AudioSegment.silent(duration=config.AUDIO_BEAT_GAP_MS)
    combined = AudioSegment.from_mp3(str(beat_files[0]))
    starts = [0.0]
    for f, d in zip(beat_files[1:], beat_durs[:-1]):
        starts.append(starts[-1] + d + config.AUDIO_BEAT_GAP_MS / 1000)
        combined = combined + gap + AudioSegment.from_mp3(str(f))
    voice_path = ws / "voiceover.mp3"
    combined.export(str(voice_path), format="mp3", bitrate="128k")
    if voice_path.stat().st_size < config.MIN_SEGMENT_BYTES:
        raise EngineError("AUDIO: stitched voiceover.mp3 is undersized")

    hint = " ".join(b["narration"] for b in beats)
    words = trans(voice_path, narration_hint=hint)
    (ws / "word_timings.json").write_text(json.dumps(words, indent=2),
                                          encoding="utf-8")
    beats_audio = [{"beat": b["beat"], "start_s": round(s, 3),
                    "duration_s": round(d, 3)}
                   for b, s, d in zip(beats, starts, beat_durs)]
    (ws / "beats_audio.json").write_text(json.dumps(beats_audio, indent=2),
                                         encoding="utf-8")
    logger.info("audio: %.1fs voice across %d beats (est %.1fs)",
                actual, len(beats), est)
    return {"voice": "voiceover.mp3", "word_timings": "word_timings.json",
            "beats_audio": "beats_audio.json"}
