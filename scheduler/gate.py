"""Automatic quality gate run before anything is published. Offline-testable (ffprobe / lint injectable)."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

MIN_DURATION_S = 30.0
DURATION_TOLERANCE_S = 3.0


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    video_path: Optional[Path] = None
    duration_s: Optional[float] = None
    voice_s: Optional[float] = None


def _npx() -> str:
    return shutil.which("npx") or "npx"


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        timeout=60)
    return float(out.decode().strip())


def count_lint_errors(data) -> int:
    """Tolerant error counter for `hyperframes lint --json` output."""
    if isinstance(data, list):
        return sum(1 for d in data if isinstance(d, dict) and str(d.get("severity", "")).lower() == "error")
    if not isinstance(data, dict):
        return 0
    for key in ("errorCount", "errors_count", "error_count"):
        if isinstance(data.get(key), int):
            return data[key]
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("errors"), int):
        return summary["errors"]
    errs = data.get("errors")
    if isinstance(errs, int):
        return errs
    if isinstance(errs, list):
        return len(errs)
    for key in ("findings", "issues", "diagnostics"):
        if isinstance(data.get(key), list):
            return count_lint_errors(data[key])
    return 0


def run_lint(project_dir: Path) -> tuple[int, str]:
    """Returns (error_count, detail). A lint run that cannot execute counts as 1 error."""
    try:
        proc = subprocess.run([_npx(), "hyperframes", "lint", "--json"], cwd=str(project_dir),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=300)
    except Exception as exc:
        return 1, f"lint could not run: {exc}"
    text = proc.stdout.strip()
    try:
        start = text.index("{") if "{" in text else text.index("[")
        data = json.loads(text[start:])
    except Exception:
        return (0, "") if proc.returncode == 0 else (1, f"lint output unparseable (rc={proc.returncode})")
    n = count_lint_errors(data)
    if n == 0 and proc.returncode != 0 and isinstance(data, (dict, list)) and not data:
        n = 1
    return n, text[:500]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_gate(run_dir: Path | str, project_dir: Path | str, *,
             ffprobe: Callable[[Path], float] = probe_duration,
             lint: Callable[[Path], tuple[int, str]] = run_lint) -> GateResult:
    run, proj = Path(run_dir), Path(project_dir)
    reasons: list[str] = []
    res = GateResult(ok=False)

    # manifest / text artefacts
    manifest = run / "run_manifest.json"
    try:
        if _read_json(manifest).get("status") == "hold_for_review":
            reasons.append("run is hold_for_review")
    except Exception:
        reasons.append("run_manifest.json missing or unreadable")
    for name in ("script.json", "references.json"):
        if not (run / name).exists():
            reasons.append(f"{name} missing")

    # director marker
    video_rel = "renders/video.mp4"
    done_file = proj / "DIRECTOR_DONE.json"
    try:
        done = _read_json(done_file)
        if done.get("ok") is not True:
            reasons.append(f"director reported failure: {done.get('reason')}")
        video_rel = done.get("video") or video_rel
    except Exception:
        reasons.append("DIRECTOR_DONE.json missing or unreadable")

    # video + duration
    video = proj / video_rel
    if not video.exists() or video.stat().st_size == 0:
        reasons.append(f"video missing: {video}")
    else:
        res.video_path = video
        try:
            res.duration_s = ffprobe(video)
        except Exception as exc:
            reasons.append(f"ffprobe failed: {exc}")
        if res.duration_s is not None:
            if res.duration_s < MIN_DURATION_S:
                reasons.append(f"video too short: {res.duration_s:.1f}s < {MIN_DURATION_S:.0f}s")
            try:
                meta = _read_json(proj / "audio_meta.json")
                res.voice_s = sum(float(v["duration_s"]) for v in meta["voices"])
            except Exception:
                reasons.append("audio_meta.json missing/unreadable (cannot compare voice duration)")
            if res.voice_s is not None and abs(res.duration_s - res.voice_s) > DURATION_TOLERANCE_S:
                reasons.append(f"duration {res.duration_s:.1f}s differs from voice {res.voice_s:.1f}s "
                               f"by more than {DURATION_TOLERANCE_S:.0f}s")

    # hyperframes lint
    if proj.exists():
        n, detail = lint(proj)
        if n:
            reasons.append(f"hyperframes lint: {n} error(s) {detail[:200]}")

    res.reasons = reasons
    res.ok = not reasons
    return res
