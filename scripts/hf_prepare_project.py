"""Prepare a HyperFrames faceless-explainer project from a finished shorts_engine run.

Plumbing only (no creative decisions): reuses the run's existing per-beat voice files
instead of regenerating narration, re-transcribes each beat file with faster-whisper so word
timings are RELATIVE to that beat's own audio, and writes the files the plugin's workflow
expects:

  <project>/assets/voice/frame-NN.mp3     one voice file per beat (frame N = beat N)
  <project>/audio_meta.json               {"bgm":null,"bgm_pending":false,"voices":[...],"sfx":[]}
  <project>/user_script.txt               the verified narration, verbatim, one block per beat
  <project>/capture/extracted/visible-text.txt, tokens.json   (HRSU brand colours)

Run AFTER `npx hyperframes init <project> --non-interactive --example=blank --resolution=portrait`
(init refuses a non-empty directory).

Usage: python scripts/hf_prepare_project.py <run_dir> <project_dir>
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

BRAND_COLORS = ["#0a192f", "#0a1428", "#d4af37", "#ccd6f6", "#8892b0"]


def _duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())


def main(run_dir: str, project_dir: str) -> None:
    from faster_whisper import WhisperModel

    run, proj = Path(run_dir), Path(project_dir)
    script = json.loads((run / "script.json").read_text(encoding="utf-8"))
    plan = json.loads((run / "explanation_plan.json").read_text(encoding="utf-8"))
    beats_audio = json.loads((run / "beats_audio.json").read_text(encoding="utf-8"))
    (proj / "assets" / "voice").mkdir(parents=True, exist_ok=True)
    (proj / "capture" / "extracted").mkdir(parents=True, exist_ok=True)

    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    voices, blocks = [], []
    for i, beat in enumerate(script["beats"]):
        src = run / f"voice_beat_{i:02d}.mp3"
        rel = f"assets/voice/frame-{i + 1:02d}.mp3"
        shutil.copy(src, proj / rel)
        segs, _ = model.transcribe(str(src), word_timestamps=True, language="en")
        words = []
        for seg in segs:
            for w in seg.words:
                words.append({"id": f"f{i + 1}w{len(words) + 1}", "text": w.word.strip(),
                              "start": round(w.start, 3), "end": round(w.end, 3)})
        voices.append({"frame": i + 1, "path": rel, "duration_s": round(_duration(src), 3),
                       "words": words})
        blocks.append(f"[{beat['beat']}] (frame {i + 1}, on-screen card hint: {beat.get('card_text', '')})\n"
                      f"{beat['narration']}\n")
        print(f"frame {i + 1} {beat['beat']}: {voices[-1]['duration_s']}s, {len(words)} words")

    (proj / "audio_meta.json").write_text(
        json.dumps({"bgm": None, "bgm_pending": False, "voices": voices, "sfx": []}, indent=2),
        encoding="utf-8")
    narration = "\n".join(blocks)
    (proj / "user_script.txt").write_text(narration, encoding="utf-8")
    (proj / "capture" / "extracted" / "visible-text.txt").write_text(
        "VERIFIED EXPLAINER SCRIPT (every claim fact-checked against the HRSU blog)\n"
        f"Question: {plan['question']}\n\n{narration}", encoding="utf-8")
    (proj / "capture" / "extracted" / "tokens.json").write_text(json.dumps({
        "title": plan["question"], "description": plan["question"],
        "colors": BRAND_COLORS, "fonts": []}, indent=2), encoding="utf-8")
    total = sum(b["duration_s"] for b in beats_audio)
    print(f"ok: {len(voices)} frames, total voice {total:.1f}s -> {proj}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
