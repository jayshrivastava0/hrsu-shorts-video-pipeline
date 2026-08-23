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

from shorts_engine.cli import build_stages
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
