"""Visual director stage: prepare a HyperFrames project and have headless Claude (or Gemma) build the video."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = Path(__file__).with_name("director_prompt.md")
DIRECTOR_TIMEOUT_S = 90 * 60
CLAUDE_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,Skill,Agent,TodoWrite,WebFetch"
USAGE_RE = re.compile(r"usage limit|rate limit|limit reached|limit will reset|out of (extra )?usage|"
                      r"credit balance is too low|quota", re.IGNORECASE)


@dataclass
class CmdResult:
    returncode: int
    output: str = ""
    timed_out: bool = False


Runner = Callable[..., CmdResult]


def run_cmd(cmd: list[str], *, cwd: Path | str, timeout: float, input_text: Optional[str] = None,
            log_path: Optional[Path] = None, env: Optional[dict] = None) -> CmdResult:
    """Run a command, streaming combined output to log_path; kill the whole process tree on timeout."""
    log_path = Path(log_path) if log_path else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    sink = open(log_path, "w", encoding="utf-8", errors="replace") if log_path else subprocess.PIPE
    full_env = dict(os.environ, PYTHONUTF8="1", **(env or {}))
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE if input_text is not None else None,
                                stdout=sink, stderr=subprocess.STDOUT, env=full_env,
                                text=True, encoding="utf-8", errors="replace")
        timed_out = False
        try:
            out, _ = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                proc.kill()
            out, _ = proc.communicate()
    finally:
        if log_path:
            sink.close()
    if log_path:
        out = log_path.read_text(encoding="utf-8", errors="replace")
    return CmdResult(proc.returncode if not timed_out else -1, out or "", timed_out)


@dataclass
class DirectorResult:
    ok: bool
    director: str
    reason: Optional[str] = None
    video: Optional[str] = None
    usage_exhausted: bool = False


def _npx() -> str:
    return shutil.which("npx") or "npx"


def prepare_project(run_dir: Path | str, project_dir: Path | str, run: Runner = run_cmd) -> None:
    """npx hyperframes init + scripts/hf_prepare_project.py. Recreates the project dir from scratch."""
    run_dir, project_dir = Path(run_dir), Path(project_dir)
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    r = run([_npx(), "hyperframes", "init", str(project_dir), "--non-interactive", "--example=blank",
             "--resolution=portrait"], cwd=project_dir.parent, timeout=900)
    if r.returncode != 0 or r.timed_out:
        raise RuntimeError(f"hyperframes init failed (rc={r.returncode}): {r.output[-300:]}")
    r = run([sys.executable, str(REPO_ROOT / "scripts" / "hf_prepare_project.py"), str(run_dir), str(project_dir)],
            cwd=REPO_ROOT, timeout=1800)
    if r.returncode != 0 or r.timed_out:
        raise RuntimeError(f"hf_prepare_project failed (rc={r.returncode}): {r.output[-300:]}")


def build_prompt(project_dir: Path, run_dir: Path, template: Path = PROMPT_PATH) -> str:
    text = template.read_text(encoding="utf-8")
    return text.replace("{{PROJECT_DIR}}", str(project_dir)).replace("{{RUN_DIR}}", str(run_dir))


def read_done(project_dir: Path | str) -> Optional[dict]:
    p = Path(project_dir) / "DIRECTOR_DONE.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _result_from_done(project: Path, r: CmdResult, who: str) -> DirectorResult:
    done = read_done(project)
    if done is not None:
        if done.get("ok") is True:
            return DirectorResult(True, who, video=done.get("video") or "renders/video.mp4")
        return DirectorResult(False, who, reason=str(done.get("reason") or "director reported failure"))
    if r.timed_out:
        return DirectorResult(False, who, reason="timeout")
    if USAGE_RE.search(r.output or ""):
        return DirectorResult(False, who, reason="Claude usage exhausted / rate limited", usage_exhausted=True)
    return DirectorResult(False, who, reason=f"no DIRECTOR_DONE.json written (rc={r.returncode})")


def run_claude(project_dir: Path | str, run_dir: Path | str, *, timeout: float = DIRECTOR_TIMEOUT_S,
               run: Runner = run_cmd, log_path: Optional[Path] = None,
               model: Optional[str] = None) -> DirectorResult:
    project, run_dir = Path(project_dir), Path(run_dir)
    (project / "DIRECTOR_DONE.json").unlink(missing_ok=True)
    cmd = [shutil.which("claude") or "claude", "-p", "--permission-mode", "acceptEdits",
           "--allowedTools", CLAUDE_TOOLS, "--add-dir", str(run_dir)]
    if model:
        cmd += ["--model", model]
    r = run(cmd, cwd=project, timeout=timeout, input_text=build_prompt(project, run_dir),
            log_path=log_path or run_dir / "director_claude.log")
    return _result_from_done(project, r, "claude")


def run_gemma(project_dir: Path | str, run_dir: Path | str, *, timeout: float = 60 * 60,
              run: Runner = run_cmd, log_path: Optional[Path] = None) -> DirectorResult:
    project = Path(project_dir)
    (project / "DIRECTOR_DONE.json").unlink(missing_ok=True)
    r = run([sys.executable, str(REPO_ROOT / "scripts" / "gemma_director.py"), "--project", str(project)],
            cwd=REPO_ROOT, timeout=timeout, log_path=log_path or Path(run_dir) / "director_gemma.log")
    video = project / "renders" / "video.mp4"
    if r.timed_out:
        res = DirectorResult(False, "gemma", reason="timeout")
    elif r.returncode == 0 and video.exists():
        res = DirectorResult(True, "gemma", video="renders/video.mp4")
    else:
        res = DirectorResult(False, "gemma", reason=f"gemma_director failed (rc={r.returncode}): {r.output[-200:]}")
    (project / "DIRECTOR_DONE.json").write_text(
        json.dumps({"ok": True, "video": res.video} if res.ok else {"ok": False, "reason": res.reason}),
        encoding="utf-8")
    return res


MIN_WEEKLY_REMAINING_PCT = 8     # one video costs ~4% of the weekly allowance
MIN_SESSION_REMAINING_PCT = 10


def claude_usage_block(run: Runner = run_cmd, cwd: Path | str = REPO_ROOT) -> Optional[str]:
    """`npx hyperframes usage --json` -> reason string if Claude allowance is too low, else None.
    Any failure to read usage returns None (proceed; the run itself detects real exhaustion)."""
    try:
        r = run([_npx(), "hyperframes", "usage", "--json"], cwd=cwd, timeout=120)
        data = json.loads(r.output[r.output.index("{"):])
        if data.get("status") != "known":
            return None
        weekly = data["weekly"]["remainingPercent"]
        session = data["session"]["remainingPercent"]
    except Exception:
        return None
    if weekly < MIN_WEEKLY_REMAINING_PCT:
        return f"Claude weekly allowance low ({weekly}% left)"
    if session < MIN_SESSION_REMAINING_PCT:
        return f"Claude session allowance low ({session}% left)"
    return None


def direct(run_dir: Path | str, project_dir: Path | str, mode: str = "claude", *,
           run: Runner = run_cmd, timeout: float = DIRECTOR_TIMEOUT_S, prepare: bool = True) -> DirectorResult:
    """mode: claude | gemma | auto (claude, falling back to gemma only when Claude usage is exhausted)."""
    if mode not in ("claude", "gemma", "auto"):
        raise ValueError(f"unknown director {mode!r}")
    if prepare:
        try:
            prepare_project(run_dir, project_dir, run=run)
        except Exception as exc:
            return DirectorResult(False, mode, reason=f"prepare failed: {exc}")
    if mode == "gemma":
        return run_gemma(project_dir, run_dir, run=run)
    block = claude_usage_block(run=run)
    if block:
        logger.warning("%s", block)
        res = DirectorResult(False, "claude", reason=block, usage_exhausted=True)
    else:
        res = run_claude(project_dir, run_dir, timeout=timeout, run=run)
    if mode == "auto" and res.usage_exhausted:
        logger.warning("Claude usage exhausted; falling back to the Gemma director")
        return run_gemma(project_dir, run_dir, run=run)
    return res
