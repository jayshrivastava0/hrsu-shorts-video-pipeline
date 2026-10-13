"""Text stages (free: Ollama + DuckDuckGo + TTS) via `python -m shorts_engine <url> --until audio`."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scheduler.director import CmdResult, Runner, run_cmd

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = REPO_ROOT / "_shorts_engine_impl"
TEXT_TIMEOUT_S = 2 * 3600
READY_STATUSES = ("audio", "visuals", "assembled", "verified", "packaged", "published")


@dataclass
class TextResult:
    status: str                 # "ready" | "held" | "failed"
    run_dir: Optional[Path] = None
    error: Optional[str] = None
    post_kind: Optional[str] = None          # "technical" | "strategic" (from the run manifest)
    trust_summary: Optional[dict] = None     # strategic only: checkable verified/dropped, analysis kept


def default_workspace_root() -> Path:
    return REPO_ROOT / "output" / "shorts"


def _find_run_dir(root: Path, url: str, output: str) -> Optional[Path]:
    m = re.search(r"Pipeline completed:\s*(run-[0-9a-f]+)", output or "")
    if m and (root / m.group(1)).exists():
        return root / m.group(1)
    best = None
    for mf in root.glob("run-*/run_manifest.json"):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("blog_url") == url and (best is None or mf.stat().st_mtime > best[0]):
            best = (mf.stat().st_mtime, mf.parent)
    return best[1] if best else None


def run_text_stages(url: str, workspace_root: Optional[Path] = None, *, until: str = "audio",
                    run: Runner = run_cmd, timeout: float = TEXT_TIMEOUT_S,
                    log_path: Optional[Path] = None) -> TextResult:
    root = Path(workspace_root) if workspace_root else default_workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    pypath = os.pathsep.join([str(ENGINE_PATH), str(REPO_ROOT), os.environ.get("PYTHONPATH", "")]).strip(os.pathsep)
    cmd = [sys.executable, "-m", "shorts_engine", url, "--until", until, "--workspace-root", str(root)]
    r: CmdResult = run(cmd, cwd=REPO_ROOT, timeout=timeout, env={"PYTHONPATH": pypath}, log_path=log_path)
    if r.timed_out:
        return TextResult("failed", None, f"text stages timed out after {timeout:.0f}s")
    run_dir = _find_run_dir(root, url, r.output)
    if run_dir is None:
        return TextResult("failed", None, f"no run workspace found (rc={r.returncode}): {r.output[-300:]}")
    try:
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return TextResult("failed", run_dir, f"unreadable run_manifest.json: {exc}")
    status = manifest.get("status")
    kind, trust = manifest.get("post_kind"), manifest.get("trust_summary") or None
    if status == "hold_for_review":
        return TextResult("held", run_dir, manifest.get("error") or "hold_for_review", kind, trust)
    if r.returncode != 0 or status == "failed":
        return TextResult("failed", run_dir, manifest.get("error") or f"rc={r.returncode}: {r.output[-300:]}")
    if status not in READY_STATUSES:
        return TextResult("failed", run_dir, f"stopped at status {status!r}")
    for name in ("script.json", "references.json", "video_description.txt", "beats_audio.json"):
        if not (run_dir / name).exists():
            return TextResult("failed", run_dir, f"{name} missing after text stages")
    return TextResult("ready", run_dir, None, kind, trust)


def wait_for_ollama(timeout_s: float = 90.0, url: str = "http://127.0.0.1:11434/api/tags",
                    probe=None, sleep=None) -> bool:
    """Poll the local Ollama server until it answers (the .bat launches the app; startup can take ~15 s)."""
    import time
    if probe is None:
        def probe():
            import urllib.request
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status == 200
    sleep = sleep or time.sleep
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if probe():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        sleep(3)
