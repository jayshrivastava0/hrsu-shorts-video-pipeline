"""Score rendered videos with the tier-1 pixel metrics.

  python -m videobench.score <video.mp4> [...]      score files
  python -m videobench.score --references           score every task's reference renders and
                                                    check each ceiling beats its floor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import frames, metrics

ROOT = Path(__file__).resolve().parent.parent
TASKS = Path(__file__).resolve().parent / "tasks.json"


def score_video(path: Path) -> dict:
    fr, ts = frames.sample_video(path)
    return metrics.summarize(fr, ts)


def load_tasks() -> list[dict]:
    return json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]


def check_references() -> tuple[list[dict], bool]:
    """Score every reference render; ok only if each ceiling outscores its floor."""
    rows, ok = [], True
    for task in load_tasks():
        scored = {}
        for role, rel in task["references"].items():
            path = ROOT / task["run"] / rel
            if not path.exists():
                rows.append({"task": task["id"], "role": role, "missing": str(path)})
                continue
            scored[role] = score_video(path)
            rows.append({"task": task["id"], "role": role, **scored[role]})
        if "ceiling" in scored and "floor" in scored:
            ok &= scored["ceiling"]["composite"] > scored["floor"]["composite"]
    return rows, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("videos", nargs="*", type=Path)
    ap.add_argument("--references", action="store_true")
    a = ap.parse_args(argv)
    if a.references:
        rows, ok = check_references()
        for r in rows:
            print(json.dumps(r))
        print("ceiling > floor on every pair:", ok)
        return 0 if ok else 1
    for v in a.videos:
        print(json.dumps({"video": str(v), **score_video(v)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
