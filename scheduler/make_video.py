"""Brief-driven video entry point: one brief -> text stage -> director -> gate -> awaiting review.

    python -m scheduler.make_video --brief "..." [--type auto|explainer|proof|esg|market|marketing]
        [--url URL] [--assets DIR] [--orientation landscape|portrait|square] [--format short|standard|long]
        [--ratio 16:9|9:16|1:1] [--duration SECONDS] [--angle REASON ...] [--cta LINE] [--yes | --resume]

Exit codes: 0 awaiting review; 1 pipeline failure or busy lock; 2 needs --type or refused brief;
3 duplicate brief id without --yes or --resume. --resume continues an unfinished brief from its saved text stage.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from scheduler.briefs.facts import load_facts
from scheduler.briefs.gate import run_type_gate
from scheduler.briefs.intake import BriefRefused, NeedsType, build_brief
from scheduler.briefs.model import VIDEO_TYPES, Brief
from scheduler.briefs.textstage import run_brief_text
from scheduler.briefs import formats
from scheduler.briefs.types import recipe_for
from scheduler.lock import LockBusy, RunLock
from scheduler.run_daily import DATA_DIR, Deps, default_deps, notify, setup_logging
from scheduler.state import State, now_iso
from scheduler.textstage import TextResult

logger = logging.getLogger("scheduler")

TITLE_MAX = 80
FINAL_STATUSES = ("awaiting_review", "rejected", "published")   # --yes never resets these
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(VIDEO_TYPES)},
        "confidence": {"type": "number"},
    },
    "required": ["type", "confidence"],
}


def _safe(text: object) -> str:
    """Make owner text printable on a cp1252 console."""
    return str(text).encode("cp1252", errors="replace").decode("cp1252")


def _default_classifier(prompt: str) -> dict:
    from shorts_engine.llm.text_llm import generate_schema_json

    return generate_schema_json(prompt, "You classify video briefs for HRSU.", _CLASSIFY_SCHEMA,
                                role="planner")


def _title(text: str) -> str:
    return (text.strip().splitlines() or [""])[0].strip()[:TITLE_MAX]


def _write_entry(state: State, brief: Brief, existing: Optional[dict], **extra) -> dict:
    """Replace the whole entry for this brief (no run-scoped field from an earlier run survives).

    `created_at` is kept from an existing entry; everything else starts fresh.
    """
    ts = now_iso()
    state.entries[brief.id] = {
        "status": "queued",
        "run_dir": None,
        "video_id": None,
        "privacy_actual": None,
        "attempts": 0,
        "last_error": None,
        "created_at": (existing or {}).get("created_at") or ts,
        "updated_at": ts,
        "type": brief.type,
        "brief": brief.text,
        "title": _title(brief.text),
        "assets": [str(a) for a in brief.assets],
        "url": brief.url,
        "rejects": 0,
        **extra,
    }
    state.save()
    return state.entries[brief.id]


def _allowed_text(run_dir: Path, recipe) -> str:
    """Every string the video may state. Proof (facts_source "documents") states only supplied documents,
    so its narration is left out; other types also allow their non-opinion narration. Source snippets
    and asset text are always included."""
    parts: list[str] = []
    if recipe.facts_source != "documents":
        beats = json.loads((run_dir / "script.json").read_text(encoding="utf-8")).get("beats", [])
        narration = [b.get("narration", "") for b in beats if b.get("kind") != "opinion" and b.get("narration")]
        if narration:
            parts.append("\n".join(narration))
    assets_txt = run_dir / "assets_text.txt"
    if assets_txt.exists():
        parts.append(assets_txt.read_text(encoding="utf-8"))
    refs = json.loads((run_dir / "references.json").read_text(encoding="utf-8"))
    snippets = [s["snippet"] for s in refs.get("sources", []) if s.get("snippet")]
    if snippets:
        parts.append("\n".join(snippets))
    return "\n".join(parts)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scheduler.make_video", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--brief", required=True, help="the brief text")
    p.add_argument("--type", default="auto", choices=["auto", *VIDEO_TYPES])
    p.add_argument("--url", default=None, help="blog URL to link from the YouTube package")
    p.add_argument("--assets", type=Path, default=None, help="folder of PDF/PNG/JPEG supporting assets")
    p.add_argument("--orientation", choices=["landscape", "portrait", "square"], default=None)
    p.add_argument("--format", dest="fmt", choices=formats.format_names(), default=None,
                   help="short | standard | long: sets the target length and default ratio (see briefs/formats.yaml)")
    p.add_argument("--ratio", choices=formats.ratio_names(), default=None, help="canvas ratio; overrides --format")
    p.add_argument("--duration", type=int, default=None, metavar="SECONDS",
                   help="target narration length in seconds; overrides --format")
    p.add_argument("--angle", action="append", default=[], metavar="REASON",
                   help="why-choose-HRSU angle to lead with: a buyer_reasons id from brand_facts.yaml or free text "
                        "(repeatable). Default: the writer picks what fits the brief")
    p.add_argument("--cta", default=None, help="call-to-action line; default: the video type's own")
    p.add_argument("--yes", action="store_true", help="run again even if this brief id already exists")
    p.add_argument("--resume", action="store_true",
                   help="continue an unfinished brief from its saved text stage (skips research, script and "
                        "voice; runs the director and the gate again)")
    p.add_argument("--state", type=Path, default=DATA_DIR / "video_state.json")
    p.add_argument("--runs-dir", type=Path, default=DATA_DIR / "video_runs")
    p.add_argument("--lock", type=Path, default=DATA_DIR / "video_scheduler.lock")
    p.add_argument("--workspace-root", type=Path, default=None, help="default: output/shorts")
    return p


_TEXT_ARTEFACTS = ("script.json", "references.json", "video_description.txt", "beats_audio.json")
_RESUMABLE_STATUSES = ("text_done", "directed", "failed", "failed_gate")


def _resumable_run_dir(existing: Optional[dict]) -> Optional[Path]:
    """The saved run folder of an unfinished brief whose text stage completed, else None."""
    if not existing or existing.get("status") not in _RESUMABLE_STATUSES or not existing.get("run_dir"):
        return None
    run_dir = Path(existing["run_dir"])
    return run_dir if all((run_dir / name).is_file() for name in _TEXT_ARTEFACTS) else None


def _after_text(state: State, brief: Brief, run_dir: Path, deps: Deps, alerts: Path,
                type_gate: Callable) -> int:
    """Director and gate, once the text stage is ready."""
    if brief.orientation:
        (run_dir / "orientation.txt").write_text(brief.orientation, encoding="utf-8")
    recipe = recipe_for(brief)
    (run_dir / "cta.txt").write_text(recipe.cta_line, encoding="utf-8")
    (run_dir / "allowed_text.txt").write_text(_allowed_text(run_dir, recipe), encoding="utf-8")
    project = run_dir / "videos" / "explainer"
    dres = deps.director(run_dir, project, "claude", prompt_path=recipe.prompt_path)
    if dres.usage_exhausted:
        state.update(brief.id, status="text_done", last_error=str(dres.reason)[:500])
        notify(alerts, f"Claude usage exhausted; brief {brief.id} text is done, rerun with --yes later")
        print(f"Claude usage exhausted; rerun later with --yes: {_safe(dres.reason)}")
        return 1
    if not dres.ok:
        state.update(brief.id, status="failed", last_error=f"director[{dres.director}]: {dres.reason}"[:500])
        notify(alerts, f"DIRECTOR FAILED brief {brief.id}: {dres.reason}")
        print(f"Director failed for {brief.id}: {_safe(dres.reason)}")
        return 1
    state.update(brief.id, status="directed", director=dres.director, project_dir=str(project), last_error=None)

    allowed = (run_dir / "allowed_text.txt").read_text(encoding="utf-8")
    gres = type_gate(brief, recipe, load_facts(), run_dir, project, allowed_text=allowed)
    if not gres.ok:
        msg = "; ".join(gres.reasons)
        state.update(brief.id, status="failed_gate", last_error=msg[:500])
        notify(alerts, f"GATE FAILED (not published): brief {brief.id} -- {msg}")
        print(f"Gate failed for {brief.id}: {_safe(msg)}")
        return 1

    state.update(brief.id, status="awaiting_review", video_path=str(gres.video_path),
                 project_dir=str(project), last_error=None)
    print(f"Awaiting review: {_safe(gres.video_path)}")
    print(f"Approve:  python -m scheduler.review approve {brief.id}")
    print(f'Reject:   python -m scheduler.review reject {brief.id} --note "what to change"')
    return 0


def _run(args, deps: Deps, alerts: Path, type_gate: Callable, brief_text: Callable,
         llm: Optional[Callable]) -> int:
    state = State(args.state)
    if args.type == "auto" and llm is None:
        llm = _default_classifier
    try:
        brief = build_brief(args.brief, args.type, url=args.url, assets_dir=args.assets,
                            orientation=args.orientation, llm=llm,
                            angles=args.angle, cta=args.cta, fmt=args.fmt, ratio=args.ratio,
                            duration_s=args.duration)
    except NeedsType as exc:
        print(f"Could not classify this brief confidently: guess {_safe(exc.guess)} "
              f"(confidence {exc.confidence:.2f}).")
        print(f"Rerun with --type X, where X is one of: {', '.join(VIDEO_TYPES)}.")
        return 2
    except BriefRefused as exc:
        print(_safe(exc.message))
        return 2
    except ValueError as exc:   # e.g. an unknown --type or a classifier that cannot run: refused, nothing spent
        print(f"Brief refused: {_safe(exc)}")
        return 2
    except Exception as exc:
        logger.exception("unexpected error while building the brief")
        notify(alerts, f"UNEXPECTED brief intake error: {exc}")
        print(f"Unexpected error while reading the brief: {_safe(exc)}")
        return 1

    existing = state.get(brief.id)
    if existing is not None and existing.get("status") in FINAL_STATUSES:
        # a finished (or waiting) video must not be silently replaced by --yes
        print(f"Brief {brief.id} is already {_safe(existing.get('status'))}; review it with "
              "python -m scheduler.review, or use a different brief text")
        return 3
    resume_dir: Optional[Path] = None
    if args.resume:
        resume_dir = _resumable_run_dir(existing)
        if resume_dir is None:
            print(f"Nothing to resume for brief {brief.id}: it needs an earlier run that finished the text stage "
                  "(status text_done, directed, failed or failed_gate, with its run folder still on disk). "
                  "Use --yes to start it again from scratch.")
            return 1
    elif existing is not None and not args.yes:
        print(f"Brief {brief.id} already exists (status {existing.get('status')}). "
              "Rerun with --yes to run it again, or --resume to continue from its saved text stage.")
        return 3

    # fail fast on YouTube credentials before any text-stage or Claude spend.
    # A failed preflight touches an existing entry only to record the error, so an awaiting_review
    # video keeps its pointers; the fresh entry is written only once preflight has succeeded.
    try:
        deps.preflight()
    except Exception as exc:
        if existing is not None:
            state.update(brief.id, last_error=f"youtube auth: {exc}"[:500])
        notify(alerts, f"YouTube auth problem, brief {brief.id} not started: {exc}")
        print(f"YouTube auth problem, nothing was run: {_safe(exc)}")
        return 1
    if resume_dir is None:
        _write_entry(state, brief, existing)

    try:
        res = (TextResult("ready", resume_dir) if resume_dir is not None
               else brief_text(brief, args.workspace_root,
                               log_path=args.runs_dir / f"{deps.today()}_{brief.id}_text.log"))
    except Exception as exc:  # an unexpected text-stage crash is a failed entry, never a silent text_done
        logger.exception("unexpected error in the text stage for %s", brief.id)
        msg = f"unexpected: {exc}"[:500]
        state.update(brief.id, status="failed", last_error=msg)
        notify(alerts, f"UNEXPECTED FAILURE brief {brief.id}: {exc}")
        print(f"Text stage crashed for {brief.id}: {_safe(exc)}")
        return 1
    if res.status != "ready":
        status = "held" if res.status == "held" else "failed"
        fields = {"run_dir": str(res.run_dir)} if res.run_dir else {}
        state.update(brief.id, status=status, last_error=(res.error or "")[:500], **fields)
        notify(alerts, f"{status.upper()} brief {brief.id}: {res.error}")
        print(f"Text stage {status} for {brief.id}: {_safe(res.error)}")
        return 1

    run_dir = Path(res.run_dir)
    state.update(brief.id, status="text_done", run_dir=str(run_dir), last_error=None)
    try:
        return _after_text(state, brief, run_dir, deps, alerts, type_gate)
    except Exception as exc:  # unexpected failure after the text stage: record it, never leave text_done silently
        logger.exception("unexpected error after text stage for %s", brief.id)
        state.update(brief.id, status="failed", last_error=f"unexpected: {exc}"[:500])
        notify(alerts, f"UNEXPECTED FAILURE brief {brief.id}: {exc}")
        print(f"Unexpected failure for {brief.id}: {_safe(exc)}")
        return 1


def main(argv: Optional[list] = None, deps: Optional[Deps] = None, *, type_gate: Callable = run_type_gate,
         brief_text: Callable = run_brief_text, llm: Optional[Callable] = None) -> int:
    args = _build_parser().parse_args(argv)
    deps = deps or default_deps()
    handler = setup_logging(args.runs_dir, deps.today())
    alerts = args.runs_dir.parent / "video_alerts.log"
    try:
        with RunLock(args.lock):
            return _run(args, deps, alerts, type_gate, brief_text, llm)
    except LockBusy as exc:
        print(f"Another run is active, nothing was done: {_safe(exc)}")
        return 1
    finally:
        logging.getLogger("scheduler").removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    sys.exit(main())
