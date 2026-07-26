"""Stage 7 — ASSEMBLE. Pure half: re-flow shot durations onto real audio and
build burned-caption ASS. run() (Task 12) concats, muxes, and asserts the
duration law: video = voice + END_CARD_HOLD_S, never `-shortest`."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def beat_spans(beats_audio: list[dict], voice_total_s: float) -> list[dict]:
    spans = []
    for i, b in enumerate(beats_audio):
        if i + 1 < len(beats_audio):
            span = beats_audio[i + 1]["start_s"] - b["start_s"]
        else:
            span = voice_total_s - b["start_s"] + config.END_CARD_HOLD_S
        spans.append({"beat": b["beat"], "start_s": b["start_s"],
                      "span_s": span})
    return spans


def _cap(shot: dict) -> float:
    return config.LOGO_CTA_MAX_S if shot["type"] == "LOGO_CTA" else config.SHOT_MAX_S


def reflow(shots: list[dict], beats_audio: list[dict],
           voice_total_s: float) -> list[dict]:
    spans = {s["beat"]: s["span_s"] for s in beat_spans(beats_audio, voice_total_s)}
    out: list[dict] = []
    for beat, span in spans.items():
        group = [dict(s) for s in shots if s["beat"] == beat]
        if not group:
            raise EngineError(f"reflow: no shots for beat '{beat}'")
        planned = sum(s["duration_s"] for s in group) or 1.0
        scale = span / planned
        for s in group[:-1]:
            new = min(max(s["duration_s"] * scale, config.SHOT_MIN_S), _cap(s))
            s["reflow_delta_s"] = round(new - s["duration_s"], 3)
            s["duration_s"] = new
        last = group[-1]
        allotted = sum(s["duration_s"] for s in group[:-1])
        new_last = span - allotted
        if new_last < config.SHOT_MIN_S / 2:
            raise EngineError(f"reflow: beat '{beat}' leaves last shot "
                              f"{new_last:.2f}s — audio/shot mismatch")
        last["reflow_delta_s"] = round(new_last - last["duration_s"], 3)
        last["duration_s"] = new_last
        out.extend(group)
    return out


def group_words_into_cues(words: list[dict], max_words: int = 3,
                          max_dur_s: float = 1.5) -> list[dict]:
    cues, buf = [], []

    def flush():
        if buf:
            cues.append({"start": buf[0]["start"], "end": buf[-1]["end"],
                         "text": " ".join(w["word"].strip().upper()
                                          for w in buf)})
            buf.clear()

    for w in words:
        if buf and (len(buf) >= max_words
                    or w["end"] - buf[0]["start"] > max_dur_s):
            flush()
        buf.append(w)
    flush()
    return cues


def ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def _ass_header(margin_v: int = 440) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins,60,&H00F6D6CC,&H000000FF,&H00101826,&H90000000,-1,0,0,0,100,100,0,0,3,6,0,2,72,72,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[dict], out_path: Path, margin_v: int = 440) -> Path:
    out_path = Path(out_path)
    lines = [_ass_header(margin_v)]
    for cue in group_words_into_cues(words):
        lines.append(f"Dialogue: 0,{ass_time(cue['start'])},"
                     f"{ass_time(cue['end'])},Cap,,0,0,0,,{cue['text']}\n")
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def _ffmpeg(args: list[str], what: str) -> None:
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise EngineError(f"ASSEMBLE {what} failed: {res.stderr[:800]}")


def _ass_filter_path(p: Path) -> str:
    # ffmpeg filter-string escaping for Windows paths
    return str(p).replace("\\", "/").replace(":", "\\:")


def _final_mux(silent: Path, audio: Path, ass_path: Path, out: Path,
               video_len_s: float, dark_ribbon_fix: bool = False) -> None:
    logo = config.BRAND_LOGO_FILE
    use_logo = logo.exists()
    inputs = ["-i", str(silent), "-i", str(audio)]
    if use_logo:
        inputs += ["-i", str(logo)]
    fc = [f"[0:v]ass='{_ass_filter_path(ass_path)}'[v1]"]
    last = "v1"
    if use_logo:
        fc.append("[2:v]scale=96:-1,format=rgba,colorchannelmixer=aa=0.85[lg]")
        fc.append(f"[{last}][lg]overlay=W-w-40:40[v2]")
        last = "v2"
    if dark_ribbon_fix:
        # Persistent brand-gold accent band under the moving progress bar --
        # see config.DARK_RIBBON_FIX_BAR_PX for why this exists and the luma
        # math behind its height. Drawn BEFORE the moving bar overlay below
        # so that bar still animates visibly on top of this static band.
        band_y = config.CANVAS_H - config.DARK_RIBBON_FIX_BAR_PX
        fc.append(f"color=c=0xd4af37:s={config.CANVAS_W}x"
                  f"{config.DARK_RIBBON_FIX_BAR_PX}:d={video_len_s:.3f}[dband]")
        fc.append(f"[{last}][dband]overlay=x=0:y={band_y}:eof_action=pass[vdr]")
        last = "vdr"
    fc.append(f"color=c=0xd4af37:s=1080x6:d={video_len_s:.3f}[bar]")
    fc.append(f"[{last}][bar]overlay=x='-1080+1080*t/{video_len_s:.3f}'"
              f":y=1914:eof_action=pass[vout]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(fc),
             "-map", "[vout]", "-map", "1:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
             str(out)], "final mux")


def run(ctx) -> dict[str, str]:
    from shorts_engine.cards import encoder
    from shorts_engine.stages import visuals

    ws = Path(ctx.workspace)
    shots = json.loads((ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]
    beats_audio = json.loads((ws / "beats_audio.json").read_text(encoding="utf-8"))
    words = json.loads((ws / "word_timings.json").read_text(encoding="utf-8"))
    region = json.loads((ws / "post.json").read_text(encoding="utf-8")).get(
        "region") or "default"
    voice = ws / "voiceover.mp3"
    voice_total = encoder.probe_duration(voice)

    final_shots = reflow(shots, beats_audio, voice_total)
    final_dir = ws / "shots_final"
    final_dir.mkdir(exist_ok=True)
    first_beat = final_shots[0]["beat"] if final_shots else None
    prev_beat = None
    report_shots = []
    concat_lines = []

    visuals_report_path = ws / "visuals_report.json"
    resolved_by_id = {}
    if visuals_report_path.exists():
        vrep = json.loads(visuals_report_path.read_text(encoding="utf-8"))
        resolved_by_id = {s["id"]: (s["rendered_type"], s["payload"])
                         for s in vrep["shots"]}

    for shot in final_shots:
        fade = config.TRANSITION_FADE_S if (
            shot["beat"] != prev_beat and shot["beat"] != first_beat) else 0.0
        prev_beat = shot["beat"]
        src = ws / "shots" / f"shot_{shot['id']}.mp4"
        dst = final_dir / f"shot_{shot['id']}.mp4"
        rerender = abs(shot.get("reflow_delta_s", 0.0)) > config.CARD_RERENDER_EPSILON_S
        if rerender or not src.exists():
            if shot["id"] in resolved_by_id:
                rtype, payload = resolved_by_id[shot["id"]]
            else:
                rtype, payload, _prov = visuals.resolve_shot(shot)
            visuals.RENDERERS[rtype](payload, shot["duration_s"], dst,
                                     fade_in_s=fade)
            rerender = True
        else:
            dst.write_bytes(src.read_bytes())
        concat_lines.append(f"file '{dst.resolve().as_posix()}'\n")
        report_shots.append({"id": shot["id"], "beat": shot["beat"],
                             "final_duration_s": round(shot["duration_s"], 3),
                             "reflow_delta_s": shot.get("reflow_delta_s", 0.0),
                             "rerendered": rerender})

    concat_file = ws / "concat.txt"
    concat_file.write_text("".join(concat_lines), encoding="utf-8")
    silent = ws / "silent.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c", "copy", str(silent)], "concat")

    margin_v = 440 + int(ctx.flags.get("caption_margin_bump", 0))
    ass_path = build_ass(words, ws / "captions.ass", margin_v=margin_v)

    from video_agent.music import mix_music_under_voice
    mixed = mix_music_under_voice(voice, ws / "music_mix.mp3", region)
    music_used = Path(mixed) != voice

    video_len = sum(s["duration_s"] for s in final_shots)
    out = ws / "video_short.mp4"
    dark_ribbon_fix = bool(ctx.flags.get("dark_ribbon_fix", False))
    _final_mux(silent, Path(mixed), ass_path, out, video_len, dark_ribbon_fix)

    vd = encoder.probe_duration(out)
    if vd < voice_total + config.AUDIO_COMPLETENESS_MARGIN_S:
        raise EngineError(f"ASSEMBLE: video {vd:.2f}s < voice {voice_total:.2f}s "
                          f"+ {config.AUDIO_COMPLETENESS_MARGIN_S}s — CTA would clip")
    if abs(vd - (voice_total + config.END_CARD_HOLD_S)) > 0.35:
        raise EngineError(f"ASSEMBLE: video {vd:.2f}s violates duration law "
                          f"(voice {voice_total:.2f}s + hold "
                          f"{config.END_CARD_HOLD_S}s)")

    report = {"voice_total_s": round(voice_total, 3),
              "video_duration_s": round(vd, 3), "music_used": music_used,
              "shots": report_shots}
    (ws / "assemble_report.json").write_text(json.dumps(report, indent=2),
                                             encoding="utf-8")
    logger.info("assemble: %.1fs video over %.1fs voice (music=%s)",
                vd, voice_total, music_used)
    return {"video": "video_short.mp4", "captions": "captions.ass",
            "assemble_report": "assemble_report.json"}
