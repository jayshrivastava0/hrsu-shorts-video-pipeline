"""
Per-stage CLI bridge for shorts_engine, driven by the DeepSeek Harness instead of
runner.run()'s own loop.

This factors ONE iteration of runner.run()'s stage-execution body (see runner.py:139-183) out
into a standalone, subprocess-invokable operation, with zero change to RunManifest's on-disk
schema or checkpoint/resume semantics — the harness's agent loop takes over sequencing what
runner.run()'s `for` loop used to do, calling one stage per invocation of this module instead.

Usage:
  python -m shorts_engine.stage_cli init <blog_url> --workspace-root PATH [--local-only]
      [--html-override PATH]
  python -m shorts_engine.stage_cli run-stage <stage_name> --workspace PATH [--local-only]
      [--html-override PATH] [--torture] [--publish]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shorts_engine import config
from shorts_engine.cli import build_stages
from shorts_engine.errors import EngineError
from shorts_engine.manifest import STATUS_ORDER, RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages.visuals import content_pixels, resolve_shot, sample_frame

STAGE_FUNCTIONS = {name: fn for name, _status_after, fn in build_stages()}
STAGE_STATUS_AFTER = {name: status_after for name, status_after, _fn in build_stages()}


def _flags_from_args(args: argparse.Namespace) -> dict:
    flags: dict = {}
    if getattr(args, "local_only", False):
        flags["local_only"] = True
    if getattr(args, "html_override", None):
        # ingest.py's `run(ctx)` treats flags["html_override"] as literal HTML content
        # (it hands it straight to BeautifulSoup), not a path — so read the file here.
        flags["html_override"] = Path(args.html_override).read_text(encoding="utf-8")
    if getattr(args, "torture", False):
        flags["torture"] = True
    if getattr(args, "publish", False):
        flags["publish"] = True
    return flags


def cmd_init(args: argparse.Namespace) -> int:
    manifest = RunManifest.create(blog_url=args.blog_url, workspace_root=args.workspace_root)
    print(json.dumps({"workspace": manifest.workspace, "run_id": manifest.run_id}))
    return 0


def _stage_order_error(last_ok_status: str, target_status: str, stage_name: str) -> str | None:
    """
    Mirror of runner.py's `_should_skip_stage` ordering check, adapted for a
    single out-of-process stage invocation: `run-stage` has no `for` loop to skip
    ahead in, so instead of silently skipping a completed stage we must reject a
    stage that isn't the correct next one for the manifest's current progress.

    Returns an error message if `stage_name` (whose success target is
    `target_status`) is not the immediate next stage after `last_ok_status` in
    STATUS_ORDER, or None if it is safe to run.
    """
    if target_status not in STATUS_ORDER or last_ok_status not in STATUS_ORDER:
        # Safety: if either status isn't recognized, don't block (matches
        # _should_skip_stage's own "don't skip" fallback for unknown statuses).
        return None

    target_idx = STATUS_ORDER.index(target_status)
    last_ok_idx = STATUS_ORDER.index(last_ok_status)

    if target_idx <= last_ok_idx:
        return (
            f"Stage '{stage_name}' (-> '{target_status}') has already completed; "
            f"manifest is already at '{last_ok_status}'."
        )
    if target_idx > last_ok_idx + 1:
        expected_stage = STATUS_ORDER[last_ok_idx + 1]
        return (
            f"Stage '{stage_name}' (-> '{target_status}') is out of order; "
            f"manifest is at '{last_ok_status}', expected next status is '{expected_stage}'."
        )
    return None


def cmd_run_stage(args: argparse.Namespace) -> int:
    stage_fn = STAGE_FUNCTIONS.get(args.stage_name)
    if stage_fn is None:
        print(json.dumps({
            "status": "error",
            "message": f"Unknown stage '{args.stage_name}'. Known stages: {sorted(STAGE_FUNCTIONS)}",
        }), file=sys.stderr)
        return 1

    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    ctx = StageContext(manifest=manifest, workspace=workspace, flags=_flags_from_args(args))
    status_after = STAGE_STATUS_AFTER[args.stage_name]

    order_error = _stage_order_error(manifest.last_ok_status, status_after, args.stage_name)
    if order_error is not None:
        print(json.dumps({"status": "error", "message": order_error}), file=sys.stderr)
        return 1

    try:
        artifacts = stage_fn(ctx)
    except Exception as exc:  # mirrors runner.py's own except branch, for one stage
        manifest.status = "failed"
        manifest.error = f"{args.stage_name}: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    manifest.checkpoint(status=status_after, **artifacts)
    print(json.dumps({"status": "ok", "status_after": status_after, "artifacts": artifacts}))
    return 0


def cmd_visuals_prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    order_error = _stage_order_error(manifest.last_ok_status, "visuals", "visuals-prepare")
    if order_error is not None:
        print(json.dumps({"status": "error", "message": order_error}), file=sys.stderr)
        return 1

    ctx = StageContext(manifest=manifest, workspace=workspace, flags=_flags_from_args(args))
    try:
        shots = json.loads((workspace / "shotlist.json").read_text(encoding="utf-8"))["shots"]
        post = json.loads((workspace / "post.json").read_text(encoding="utf-8"))
        briefs = []
        for shot in shots:
            rtype, payload, prov = resolve_shot(shot, ctx, post)
            briefs.append({
                "shot_id": shot["id"], "beat": shot["beat"], "type": rtype,
                "payload": payload, "duration_s": shot["duration_s"],
                "fade_in_s": config.TRANSITION_FADE_S if shot["beat"] != shots[0]["beat"] else 0.0,
                "provenance": prov,
            })
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"visuals-prepare: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    (workspace / "shot_briefs.json").write_text(json.dumps(briefs, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "shot_briefs": "shot_briefs.json"}))
    return 0


def cmd_visuals_finalize(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    try:
        briefs = json.loads((workspace / "shot_briefs.json").read_text(encoding="utf-8"))
        shots_dir = workspace / "shots"
        report = {"shots": []}
        for brief in briefs:
            shot_id = brief["shot_id"]
            mp4 = shots_dir / f"shot_{shot_id}.mp4"
            if not mp4.exists():
                raise EngineError(f"visuals-finalize: shot {shot_id} has no rendered mp4 at {mp4}")
            png = shots_dir / f"shot_{shot_id}_mid.png"
            sample_frame(mp4, brief["duration_s"] / 2, png)
            pixels = content_pixels(png)
            if pixels < config.MIN_CONTENT_PIXELS:
                raise EngineError(
                    f"visuals-finalize: shot {shot_id} ({brief['type']}) rendered without "
                    f"visible content ({pixels} bright px < {config.MIN_CONTENT_PIXELS}) — "
                    f"never-blank violated")
            report["shots"].append({**brief, "content_pixels": pixels})
        (workspace / "visuals_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"visuals-finalize: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    artifacts = {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
    manifest.checkpoint(status="visuals", **artifacts)
    print(json.dumps({"status": "ok", "status_after": "visuals", "artifacts": artifacts}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shorts_engine.stage_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("blog_url")
    init_parser.add_argument("--workspace-root", type=Path, required=True)
    init_parser.add_argument("--local-only", action="store_true")
    init_parser.add_argument("--html-override", type=Path, default=None)
    init_parser.set_defaults(func=cmd_init)

    run_stage_parser = subparsers.add_parser("run-stage")
    run_stage_parser.add_argument("stage_name")
    run_stage_parser.add_argument("--workspace", required=True)
    run_stage_parser.add_argument("--local-only", action="store_true")
    run_stage_parser.add_argument("--html-override", type=Path, default=None)
    run_stage_parser.add_argument("--torture", action="store_true")
    run_stage_parser.add_argument("--publish", action="store_true")
    run_stage_parser.set_defaults(func=cmd_run_stage)

    visuals_prepare_parser = subparsers.add_parser("visuals-prepare")
    visuals_prepare_parser.add_argument("--workspace", required=True)
    visuals_prepare_parser.add_argument("--local-only", action="store_true")
    visuals_prepare_parser.add_argument("--torture", action="store_true")
    visuals_prepare_parser.set_defaults(func=cmd_visuals_prepare)

    visuals_finalize_parser = subparsers.add_parser("visuals-finalize")
    visuals_finalize_parser.add_argument("--workspace", required=True)
    visuals_finalize_parser.set_defaults(func=cmd_visuals_finalize)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
