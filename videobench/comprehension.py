"""Comprehension test: does the video actually convey the explanation? (HRSU-VideoBench, tier 2)

Each task has a frozen multiple-choice quiz (videobench/quizzes/<task>.json) written from the
run's verified explanation plan; every question names the beat that explains it. A viewer model
answers each question under these conditions:

  none      no video at all          -> what the model already knows (prior)
  visual    frames from that beat, MUTED and cropped above the caption band, so burned-in
            captions cannot leak the narration -> what the VISUALS teach
  both      the same frames + the beat's narration -> what the finished video conveys

  visual_lift = acc(visual) - acc(none)   the headline number: knowledge the visuals add

Each question is asked `reps` times with the options in a different order (position bias),
and the viewer keeps its reasoning ("thinking") on.

  python -m videobench.comprehension <task-id> <video.mp4> [--model bonsai-2:9b] [--reps 2]
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUIZZES = Path(__file__).resolve().parent / "quizzes"
TASKS = Path(__file__).resolve().parent / "tasks.json"

CROP_TOP, CROP_BOTTOM = 0.05, 0.80   # keep the content area; drop wordmark strip and captions
FRAMES_PER_BEAT = 6
LETTERS = "ABCD"
CONDITIONS = ("none", "visual", "both")

ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "enum": list(LETTERS)}},
                 "required": ["answer"]}


def task(task_id: str) -> dict:
    for t in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]:
        if t["id"] == task_id:
            return t
    raise SystemExit(f"unknown task {task_id}")


def beat_windows(run: Path) -> dict[str, tuple[float, float]]:
    beats = json.loads((run / "beats_audio.json").read_text(encoding="utf-8"))
    return {b["beat"]: (b["start_s"], b["start_s"] + b["duration_s"]) for b in beats}


def beat_narration(run: Path) -> dict[str, str]:
    script = json.loads((run / "script.json").read_text(encoding="utf-8"))
    return {b["beat"]: b["narration"] for b in script["beats"]}


def extract_frames(video: Path, start: float, end: float, out_dir: Path, n: int) -> list[Path]:
    """n frames spread over [start, end], cropped to the content area, as JPEG files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pad = min(0.5, (end - start) / 10)
    times = [start + pad + (end - start - 2 * pad) * (k + 0.5) / n for k in range(n)]
    crop = f"crop=iw:ih*{CROP_BOTTOM - CROP_TOP}:0:ih*{CROP_TOP},scale=480:-2"
    paths = []
    for k, t in enumerate(times):
        p = out_dir / f"f{k}_{t:.1f}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", crop, "-q:v", "3", str(p)], check=True)
        paths.append(p)
    return paths


def build_prompt(cond: str, q: dict, options: list[str], narration: str) -> str:
    opts = "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(options))
    if cond == "none":
        lead = "Answer this question about an industrial chemistry topic."
    elif cond == "visual":
        lead = ("The images are frames, in order, from one segment of a MUTED explainer video "
                "(no sound, no captions). Answer using what the visuals show; if they do not "
                "show it, give your best guess.")
    else:
        lead = ("The images are frames, in order, from one segment of an explainer video, and "
                f"this is what the narrator says during it:\n\"{narration}\"\n"
                "Answer using the video.")
    return f"{lead}\n\nQuestion: {q['q']}\n{opts}\n\nReply as JSON: {{\"answer\": \"A\"|\"B\"|\"C\"|\"D\"}}"


def ask(model: str, prompt: str, images: list[Path], think: bool, num_ctx: int) -> tuple[str, float]:
    import ollama

    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [str(p) for p in images]
    t = time.time()
    for attempt in range(3):
        try:
            r = ollama.Client(timeout=1800).chat(
                model=model, messages=[msg], format=ANSWER_SCHEMA, think=think,
                options={"temperature": 0.2, "num_ctx": num_ctx, "num_predict": 6000})
            ans = json.loads(r.message.content or "{}").get("answer", "")
            if ans in LETTERS:
                return ans, time.time() - t
        except Exception:  # noqa: BLE001 -- retry transport/parse errors
            time.sleep(3 * (attempt + 1))
    return "", time.time() - t


def run(task_id: str, video: Path, model: str, reps: int, think: bool, num_ctx: int,
        conditions=CONDITIONS, seed: int = 7) -> dict:
    t = task(task_id)
    run_dir = ROOT / t["run"]
    quiz = json.loads((QUIZZES / f"{task_id}.json").read_text(encoding="utf-8"))
    windows, narr = beat_windows(run_dir), beat_narration(run_dir)
    rng = random.Random(seed)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        frames = {}
        for b in {q["beat"] for q in quiz["questions"]}:
            frames[b] = extract_frames(video, *windows[b], Path(tmp) / b, FRAMES_PER_BEAT)
        for q in quiz["questions"]:
            for rep in range(reps):
                options = [q["answer"], *q["distractors"]]
                rng.shuffle(options)
                correct = LETTERS[options.index(q["answer"])]
                for cond in conditions:
                    imgs = [] if cond == "none" else frames[q["beat"]]
                    ans, secs = ask(model, build_prompt(cond, q, options, narr[q["beat"]]),
                                    imgs, think, num_ctx)
                    results.append({"q": q["id"], "rep": rep, "cond": cond, "answer": ans,
                                    "correct": ans == correct, "secs": round(secs, 1)})
                    print(f"{q['id']} rep{rep} {cond:6s} {ans or '?'} "
                          f"{'ok' if ans == correct else 'x '} {secs:.0f}s", flush=True)
    acc = {c: round(sum(r["correct"] for r in results if r["cond"] == c)
                    / max(1, sum(r["cond"] == c for r in results)), 3) for c in conditions}
    summary = {"task": task_id, "video": str(video), "model": model, "reps": reps,
               "accuracy": acc}
    if "visual" in acc and "none" in acc:
        summary["visual_lift"] = round(acc["visual"] - acc["none"], 3)
    return {"summary": summary, "answers": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task")
    ap.add_argument("video", type=Path)
    ap.add_argument("--model", default="bonsai-2:9b")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    res = run(a.task, a.video, a.model, a.reps, not a.no_think, a.num_ctx)
    print(json.dumps(res["summary"]))
    if a.out:
        a.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
