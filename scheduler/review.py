"""Human review queue for videos held at `awaiting_review`.

    python -m scheduler.review list
    python -m scheduler.review approve ID
    python -m scheduler.review reject ID --note "what to change"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from scheduler.briefs.facts import load_facts
from scheduler.briefs.gate import run_type_gate
from scheduler.briefs.model import Brief
from scheduler.briefs.types import get_recipe
from scheduler.run_daily import DATA_DIR, Deps, default_deps, notify, publish_entry, setup_logging
from scheduler.state import State

logger = logging.getLogger("scheduler")

MAX_REJECTS = 3
INTERRUPTED_HINT = " (re-direct interrupted: run reject again or approve)"


def _safe(text: object) -> str:
    """Make text printable on a cp1252 console."""
    return str(text).encode("ascii", "replace").decode("ascii")


def _reviewable(state: State, eid: str) -> Optional[dict]:
    """Entry if it can be approved/rejected: `awaiting_review`, or `rejected` left by an interrupted re-direct."""
    e = state.get(eid)
    if e is None or e.get("status") not in ("awaiting_review", "rejected"):
        return None
    return e


def _project_dir(run_dir: Path, entry: dict) -> Path:
    return Path(entry["project_dir"]) if entry.get("project_dir") else run_dir / "videos" / "explainer"


def cmd_list(state: State) -> int:
    rows = [(eid, e) for eid, e in state.entries.items() if e.get("status") in ("awaiting_review", "rejected")]
    if not rows:
        print("nothing awaiting review")
        return 0
    for eid, e in rows:
        line = f"{eid} | {e.get('type') or 'blog'} | {e.get('title') or eid} | {e.get('video_path')}"
        if e.get("status") == "rejected":
            line += INTERRUPTED_HINT
        print(_safe(line))
    return 0


def cmd_approve(state: State, eid: str, deps: Deps, alerts: Path) -> int:
    entry = _reviewable(state, eid)
    if entry is None:
        logger.error("%s is not awaiting review", eid)
        return 1
    video = entry.get("video_path")
    if not video or not Path(video).exists():
        logger.error("video file missing for %s: %s (status unchanged)", eid, video)
        return 1
    prev_status = entry["status"]
    prev_attempts = int(entry.get("attempts") or 0)
    outcome = publish_entry(eid, state, deps, alerts)
    if outcome == "failed":
        # controller ruling: keep last_error, restore the previous status and attempts so the daily loop never retries it
        state.update(eid, status=prev_status, attempts=prev_attempts)
        logger.error("publish failed for %s; left %s", eid, prev_status)
        return 1
    logger.info("approved %s", eid)
    return 0


def cmd_reject(state: State, eid: str, note: str, deps: Deps, type_gate: Callable, alerts: Path) -> int:
    entry = _reviewable(state, eid)
    if entry is None:
        logger.error("%s is not awaiting review", eid)
        return 1
    note = (note or "").strip()
    if not note:
        logger.error("reject needs a non-empty --note")
        return 1
    rejects = int(entry.get("rejects") or 0)   # counts successful re-directs only; an interrupted one is not counted yet
    if rejects >= MAX_REJECTS:
        logger.error("%s already rejected %d times; refusing a further reject", eid, rejects)
        return 1

    run_dir = Path(entry["run_dir"])
    project = _project_dir(run_dir, entry)
    is_brief = "type" in entry
    recipe = get_recipe(entry["type"]) if is_brief else None
    # `rejects` and `review_note` are written only after the director succeeds, so a failed or
    # usage-exhausted re-direct never consumes one of the three rejects.
    state.update(eid, status="rejected")

    kwargs = {"review_note": note}
    if recipe is not None:
        kwargs["prompt_path"] = recipe.prompt_path
    dres = deps.director(run_dir, project, "claude", **kwargs)
    if not dres.ok:
        if dres.usage_exhausted:
            reason = f"usage exhausted during review: {dres.reason}"
            logger.error("Claude usage exhausted; %s returned to awaiting review", eid)
        else:
            reason = f"director: {dres.reason}"
            logger.error("director failed for %s; %s returned to awaiting review: %s", eid, eid, dres.reason)
        # the previous video is still valid, so the entry goes back to review (never `failed`)
        state.update(eid, status="awaiting_review", last_error=str(reason)[:500])
        return 1

    state.update(eid, rejects=rejects + 1, review_note=note)

    if is_brief:
        brief = Brief(id=eid, text=entry.get("brief", ""), type=entry["type"], url=entry.get("url"),
                      assets=[], orientation=None)
        allowed_path = run_dir / "allowed_text.txt"
        allowed = allowed_path.read_text(encoding="utf-8") if allowed_path.exists() else ""
        gres = type_gate(brief, recipe, load_facts(), run_dir, project, allowed_text=allowed)
    else:
        gres = deps.gate(run_dir, project)

    if not gres.ok:
        msg = "; ".join(gres.reasons)
        state.update(eid, status="failed_gate", last_error=msg[:500])
        notify(alerts, f"GATE FAILED after review (not published): {eid} -- {msg}")
        return 1

    video_path = str(gres.video_path) if gres.video_path else entry.get("video_path")
    state.update(eid, status="awaiting_review", video_path=video_path, last_error=None)
    logger.info("re-reviewed %s; back to awaiting review (video %s)", eid, video_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scheduler.review", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", type=Path, default=DATA_DIR / "video_state.json")
    p.add_argument("--runs-dir", type=Path, default=DATA_DIR / "video_runs")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show entries awaiting review")
    a = sub.add_parser("approve", help="publish an awaiting-review entry to YouTube")
    a.add_argument("id")
    r = sub.add_parser("reject", help="re-direct with a note, then re-gate")
    r.add_argument("id")
    r.add_argument("--note", required=True)
    return p


def main(argv: Optional[list] = None, deps: Optional[Deps] = None,
         type_gate: Callable = run_type_gate) -> int:
    args = build_parser().parse_args(argv)
    state = State(args.state)
    if args.command == "list":
        return cmd_list(state)

    deps = deps or default_deps()
    handler = setup_logging(args.runs_dir, deps.today())
    alerts = args.runs_dir.parent / "video_alerts.log"
    try:
        if state.get(args.id) is None:
            logger.error("unknown id %s", args.id)
            return 1
        if args.command == "approve":
            return cmd_approve(state, args.id, deps, alerts)
        return cmd_reject(state, args.id, args.note, deps, type_gate, alerts)
    finally:
        logging.getLogger("scheduler").removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    sys.exit(main())
