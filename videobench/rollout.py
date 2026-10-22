"""Rollout runner: one gemma_director run on a frozen benchmark task (HRSU-VideoBench).

The same code runs locally and inside the Kaggle rollout kernel.

  prepare   build the task's frozen project skeleton once (hyperframes init + hf_prepare_project:
            per-beat voice, word timings, brand tokens) -> output/videobench/<task>/_skeleton/
            plus a copy of the run's explanation_plan.json. Needs faster-whisper, so run locally.
  run       copy the skeleton to output/videobench/<task>/<label>/, run the director with the
            given model, render, and write rollout.json (status, timings, director summary,
            periodic `ollama ps` samples so GPU placement is on record).

  python -m videobench.rollout prepare <task>
  python -m videobench.rollout run <task> <label> --model gemma4:31b [--think 1] [--num-ctx 32768]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = Path(__file__).resolve().parent / "tasks.json"
OUT = Path(os.environ.get("VIDEOBENCH_OUT") or ROOT / "output" / "videobench")


def task(task_id: str) -> dict:
    for t in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]:
        if t["id"] == task_id:
            return t
    raise SystemExit(f"unknown task {task_id}")


def skeleton(task_id: str) -> Path:
    return OUT / task_id / "_skeleton"


def prepare(task_id: str) -> Path:
    sys.path.insert(0, str(ROOT))
    from scheduler.director import prepare_project

    run_dir = ROOT / task(task_id)["run"]
    sk = skeleton(task_id)
    prepare_project(run_dir, sk / "project")
    shutil.copy(run_dir / "explanation_plan.json", sk / "explanation_plan.json")
    print(sk)
    return sk


def gpu_sampler(samples: list, stop: threading.Event, every: float = 300) -> None:
    while not stop.is_set():
        try:
            ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=30).stdout
            samples.append({"t": round(time.time()), "ollama_ps": ps.strip()})
        except Exception as e:  # noqa: BLE001
            samples.append({"t": round(time.time()), "error": str(e)[:200]})
        stop.wait(every)


def run(task_id: str, label: str, model: str, think: str, think_budget: int, num_ctx: int,
        timeout: int, concurrency: int, render: bool) -> dict:
    sk = skeleton(task_id)
    if not (sk / "project").exists():
        raise SystemExit(f"no skeleton at {sk}; run: python -m videobench.rollout prepare {task_id}")
    dest = OUT / task_id / label
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(sk / "project", dest)
    env = {**os.environ, "GEMMA_DIRECTOR_MODEL": model, "GEMMA_DIRECTOR_NUM_CTX": str(num_ctx),
           "GEMMA_DIRECTOR_TIMEOUT": str(timeout), "GEMMA_DIRECTOR_THINK_BUDGET": str(think_budget),
           "PYTHONUTF8": "1"}
    if think in ("0", "1"):
        env["GEMMA_DIRECTOR_THINK"] = think
    cmd = [sys.executable, str(ROOT / "scripts" / "gemma_director.py"), "--project", str(dest),
           "--run", str(sk), "--concurrency", str(concurrency)] + ([] if render else ["--no-render"])
    samples: list = []
    stop = threading.Event()
    threading.Thread(target=gpu_sampler, args=(samples, stop), daemon=True).start()
    t = time.time()
    p = subprocess.run(cmd, env=env, cwd=str(ROOT))
    stop.set()
    video = dest / "renders" / "video.mp4"
    summary = dest / "gemma_work" / "summary.json"
    rec = {"task": task_id, "label": label, "model": model, "think": think, "num_ctx": num_ctx,
           "returncode": p.returncode, "secs": round(time.time() - t),
           "video": str(video) if video.exists() else None,
           "director_summary": json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None,
           "gpu_samples": samples}
    (dest / "rollout.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("task", "label", "returncode", "secs", "video")}))
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("task")
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("label")
    r.add_argument("--model", required=True)
    r.add_argument("--think", default="", help="1 on, 0 off, empty = model default")
    r.add_argument("--think-budget", type=int, default=8000)
    r.add_argument("--num-ctx", type=int, default=32768)
    r.add_argument("--timeout", type=int, default=3600)
    r.add_argument("--concurrency", type=int, default=1)
    r.add_argument("--no-render", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        prepare(a.task)
        return 0
    rec = run(a.task, a.label, a.model, a.think, a.think_budget, a.num_ctx, a.timeout,
              a.concurrency, not a.no_render)
    return 0 if rec["video"] or (a.no_render and rec["returncode"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
