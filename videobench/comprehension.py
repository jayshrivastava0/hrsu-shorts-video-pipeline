"""Comprehension test: does the video actually convey the explanation? (HRSU-VideoBench, tier 2)

Each task has a frozen multiple-choice quiz (videobench/quizzes/<task>.json) written from the
run's verified explanation plan, one question per verified claim, each naming the beat that
explains it. Frontier viewers already know this chemistry (they score 100% with no video), so
"did accuracy go up with the video?" measures nothing. Instead the viewer must answer ONLY from
the frames, with an explicit way out:

  E) Not shown in these frames

and every question is asked twice:

  matched   frames from the beat that should explain it (muted, captions cropped away)
  control   a FIXED foreign set of frames (another task's hook) that contains none of the
            answers -> the right answer is always E; anything else is prior-knowledge leakage

  conveyed     = share of questions answered correctly from the matched frames
  leakage      = share of questions NOT answered E on the control frames
  comprehension = conveyed - leakage          (the headline number; abstaining scores 0)

Canary (planted false fact, the user's anti-cheating check): a FALSE label is drawn onto the
hook frames. A grounded viewer gives the real answer with no video, and the planted answer when
shown the doctored frames. A viewer that fails the canary must not be used as a judge.

Options are reshuffled per repetition (position bias); the viewer keeps its thinking on.

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
LETTERS = "ABCDE"
NOT_SHOWN = "Not shown in these frames"

ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "enum": list(LETTERS)}},
                 "required": ["answer"]}


def task(task_id: str) -> dict:
    for t in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]:
        if t["id"] == task_id:
            return t
    raise SystemExit(f"unknown task {task_id}")


def quiz(task_id: str) -> dict:
    return json.loads((QUIZZES / f"{task_id}.json").read_text(encoding="utf-8"))


def beat_windows(run: Path) -> dict[str, tuple[float, float]]:
    beats = json.loads((run / "beats_audio.json").read_text(encoding="utf-8"))
    return {b["beat"]: (b["start_s"], b["start_s"] + b["duration_s"]) for b in beats}


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


def control_frames(q: dict, out_dir: Path) -> list[Path]:
    """The quiz's fixed foreign control: another task's hook frames from its reference render."""
    c = q["control"]
    t = task(c["task"])
    run = ROOT / t["run"]
    return extract_frames(run / t["references"][c["role"]], *beat_windows(run)[c["beat"]],
                          out_dir, FRAMES_PER_BEAT)


def plant_label(frames: list[Path], label: str, out_dir: Path) -> list[Path]:
    """Copies of `frames` with `label` drawn as an on-brand card in the middle of the diagram."""
    from PIL import Image, ImageDraw, ImageFont

    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        w, h = im.size
        d = ImageDraw.Draw(im)
        size = max(14, w // 22)
        try:
            font = ImageFont.truetype("arialbd.ttf", size)
        except OSError:
            font = ImageFont.load_default()
        lines, line = [], ""
        for word in label.split():  # wrap to ~80% of the width
            trial = f"{line} {word}".strip()
            if d.textlength(trial, font=font) > w * 0.8 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        box_h = len(lines) * int(size * 1.35) + size
        top = int(h * 0.45) - box_h // 2
        d.rounded_rectangle([w * 0.06, top, w * 0.94, top + box_h], radius=size // 2,
                            fill=(10, 25, 47), outline=(212, 175, 55), width=max(2, size // 8))
        for i, ln in enumerate(lines):
            tw = d.textlength(ln, font=font)
            d.text(((w - tw) / 2, top + size // 2 + i * int(size * 1.35)), ln, font=font,
                   fill=(212, 175, 55))
        p = out_dir / f.name
        im.save(p, quality=90)
        out.append(p)
    return out


def options_for(item: dict, rng: random.Random, with_not_shown: bool) -> list[str]:
    opts = [item["answer"], *([item["planted"]] if "planted" in item else []), *item["distractors"]]
    rng.shuffle(opts)
    return opts + [NOT_SHOWN] if with_not_shown else opts


def build_prompt(frames: bool, item: dict, options: list[str]) -> str:
    opts = "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(options))
    if frames:
        lead = ("The images are frames, in order, from one segment of a MUTED explainer video. "
                "Answer ONLY from what these frames show. If the frames do not show the answer, "
                f"choose \"{NOT_SHOWN}\" -- even if you know the answer from elsewhere.")
    else:
        lead = "Answer this question about an industrial chemistry topic."
    return (f"{lead}\n\nQuestion: {item['q']}\n{opts}\n\n"
            f"Reply as JSON: {{\"answer\": one of {', '.join(LETTERS[:len(options)])}}}")


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


def picked(ans: str, options: list[str]) -> str:
    i = LETTERS.find(ans) if ans else -1
    return options[i] if 0 <= i < len(options) else ""


def score(answers: list[dict]) -> dict:
    """conveyed / leakage / comprehension over all question repetitions, plus the canary."""
    def rate(cond, pred):
        rows = [a for a in answers if a["cond"] == cond]
        return round(sum(pred(a) for a in rows) / len(rows), 3) if rows else None

    conveyed = rate("matched", lambda a: a["picked"] == a["answer_text"])
    leakage = rate("control", lambda a: a["picked"] != NOT_SHOWN)
    out = {"conveyed": conveyed, "leakage": leakage,
           "comprehension": round(conveyed - leakage, 3) if None not in (conveyed, leakage) else None,
           "prior": rate("none", lambda a: a["picked"] == a["answer_text"])}
    truth = rate("canary_none", lambda a: a["picked"] == a["answer_text"])
    follows = rate("canary_planted", lambda a: a["picked"] == a["planted_text"])
    if truth is not None:
        out["canary"] = {"truth_without_video": truth, "follows_planted_frames": follows,
                         "grounded": truth >= 0.5 and follows >= 0.5}
    return out


def run(task_id: str, video: Path, model: str, reps: int, think: bool, num_ctx: int,
        seed: int = 7) -> dict:
    t = task(task_id)
    run_dir = ROOT / t["run"]
    qz = quiz(task_id)
    windows = beat_windows(run_dir)
    rng = random.Random(seed)
    answers = []

    def log(item, rep, cond, options, ans, secs):
        row = {"q": item["id"], "rep": rep, "cond": cond, "answer": ans,
               "picked": picked(ans, options), "answer_text": item["answer"],
               "planted_text": item.get("planted", ""), "secs": round(secs, 1)}
        answers.append(row)
        print(f"{item['id']:7s} rep{rep} {cond:15s} {ans or '?'} -> {row['picked'][:40]!r} "
              f"{secs:.0f}s", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        frames = {b: extract_frames(video, *windows[b], tmp / b, FRAMES_PER_BEAT)
                  for b in {q["beat"] for q in qz["questions"]}}
        control = control_frames(qz, tmp / "control")
        c = qz["canary"]
        hook = extract_frames(video, *windows[c["beat"]], tmp / "canary_src", FRAMES_PER_BEAT)
        doctored = plant_label(hook, c["planted_label"], tmp / "canary")
        for q in qz["questions"]:
            for rep in range(reps):
                opts = options_for(q, rng, with_not_shown=False)
                log(q, rep, "none", opts, *ask(model, build_prompt(False, q, opts), [], think, num_ctx))
                opts = options_for(q, rng, with_not_shown=True)
                log(q, rep, "matched", opts,
                    *ask(model, build_prompt(True, q, opts), frames[q["beat"]], think, num_ctx))
                opts = options_for(q, rng, with_not_shown=True)
                log(q, rep, "control", opts,
                    *ask(model, build_prompt(True, q, opts), control, think, num_ctx))
        for rep in range(reps):
            opts = options_for(c, rng, with_not_shown=False)
            log(c, rep, "canary_none", opts, *ask(model, build_prompt(False, c, opts), [], think, num_ctx))
            opts = options_for(c, rng, with_not_shown=True)
            log(c, rep, "canary_planted", opts,
                *ask(model, build_prompt(True, c, opts), doctored, think, num_ctx))
    summary = {"task": task_id, "video": str(video), "model": model, "reps": reps,
               **score(answers)}
    return {"summary": summary, "answers": answers}


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
