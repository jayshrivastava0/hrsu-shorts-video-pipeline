"""Generate SRT subtitles via faster-whisper, mobile-optimized 3-word chunks."""
import logging
from datetime import timedelta
from pathlib import Path
from faster_whisper import WhisperModel

from video_agent.config import (
    WHISPER_MODEL, WHISPER_MODEL_MULTILINGUAL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
    SUBTITLE_MAX_WORDS_PER_LINE, SUBTITLE_MAX_LINE_DURATION_S,
)

log = logging.getLogger(__name__)


def _format_ts(seconds: float) -> str:
    td = timedelta(seconds=max(0.0, seconds))
    total_ms = int(td.total_seconds() * 1000)
    hh, rem = divmod(total_ms, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _chunk_words(words: list[dict], max_words: int, max_dur: float) -> list[dict]:
    cues = []
    buf = []
    for w in words:
        if not buf:
            buf.append(w)
            continue
        new_words = buf + [w]
        new_dur = w["end"] - buf[0]["start"]
        if len(new_words) > max_words or new_dur > max_dur:
            cues.append(_flush(buf))
            buf = [w]
        else:
            buf.append(w)
    if buf:
        cues.append(_flush(buf))
    return cues


def _flush(buf: list[dict]) -> dict:
    return {
        "start": buf[0]["start"],
        "end": buf[-1]["end"],
        "text": " ".join(w["word"].strip() for w in buf).upper(),
    }


def transcribe_words(audio_path: Path, narration_hint: str | None = None,
                     multilingual: bool = False) -> list[dict]:
    """Whisper word timings as a flat list of {word, start, end} dicts."""
    model_name = WHISPER_MODEL_MULTILINGUAL if multilingual else WHISPER_MODEL
    model = WhisperModel(model_name, device=WHISPER_DEVICE,
                         compute_type=WHISPER_COMPUTE_TYPE)
    segments, _info = model.transcribe(
        str(audio_path), word_timestamps=True, initial_prompt=narration_hint,
    )
    flat_words = []
    for seg in segments:
        for w in (seg.words or []):
            flat_words.append({"word": w.word.strip(),
                               "start": float(w.start), "end": float(w.end)})
    return flat_words


def generate_srt(audio_path: Path, output_srt_path: Path,
                 narration_hint: str | None = None,
                 multilingual: bool = False) -> Path:
    flat_words = transcribe_words(audio_path, narration_hint, multilingual)
    cues = _chunk_words(flat_words, SUBTITLE_MAX_WORDS_PER_LINE,
                        SUBTITLE_MAX_LINE_DURATION_S)
    output_srt_path.parent.mkdir(parents=True, exist_ok=True)
    with output_srt_path.open("w", encoding="utf-8") as f:
        for i, cue in enumerate(cues, start=1):
            f.write(f"{i}\n")
            f.write(f"{_format_ts(cue['start'])} --> {_format_ts(cue['end'])}\n")
            f.write(f"{cue['text']}\n\n")
    return output_srt_path
