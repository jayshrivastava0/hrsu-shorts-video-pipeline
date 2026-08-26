"""
CLI for shorts_engine.

Provides command-line interface for running the shorts_engine pipeline:
  python -m shorts_engine <blog_url> [options]

Options:
  --until {ingest,facts,script,shotlist,audio,visuals,assemble}
                              Stop after reaching this stage
  --resume                    Resume from last completed stage
  --local-only                Use local Ollama model only
  --workspace-root PATH       Custom workspace root (default: config.OUTPUT_BASE)
  --html-override PATH        Use HTML file instead of fetching from URL (testing)
  --torture                   Run full pipeline with Phase 4 torture test mode

Exit codes:
  0 = Success
  1 = Failure (exception during execution)
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from shorts_engine import config, runner
from shorts_engine.errors import EngineError
from shorts_engine.stages import (
    facts, ingest, script, shotlist, audio,
    verify, package, publish,
)

logger = logging.getLogger(__name__)


# ── Creative stages (headless-agent bridge) ───────────────────────────────
def _run_creative_stage(ctx, phase: str) -> dict[str, str]:
    """Run the `visuals` or `assemble` phase through the real harness agent.

    `author_visual_scene`/`author_assembly_composition` require a live parent agent to spawn
    their scene-authoring subagent from, so this shells out to `harness/scripts/
    run-visual-authoring.mts`, which boots `harness/cordis.yml` and drives one real agent turn.
    """
    workspace = Path(ctx.workspace)
    harness_dir = Path(config.PROJECT_ROOT) / "harness"
    bridge = harness_dir / "scripts" / "run-visual-authoring.mts"
    result = subprocess.run(
        ["node", "--import", "tsx/esm", str(bridge), phase, str(workspace.resolve())],
        cwd=harness_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise EngineError(f"{phase}: harness bridge failed: {result.stderr[-2000:]}")
    if phase == "visuals":
        return {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
    return {"video": "video_short.mp4", "captions": "captions.ass",
            "assemble_report": "assemble_report.json"}


def _visuals_stage(ctx) -> dict[str, str]:
    return _run_creative_stage(ctx, "visuals")


def _assemble_stage(ctx) -> dict[str, str]:
    return _run_creative_stage(ctx, "assemble")


# ── Build Stages ──────────────────────────────────────────────────────────
def build_stages() -> list[runner.Stage]:
    """
    Build the pipeline stages for shorts_engine.

    Returns:
        List of (name, status_after_success, callable) tuples for the runner.

    Each stage receives a StageContext and returns artifacts dict.
    """
    return [
        ("ingest", "ingested", ingest.run),
        ("facts", "facts", facts.run),
        ("script", "scripted", script.run),
        ("shotlist", "shotlisted", shotlist.run),
        ("audio", "audio", audio.run),
        ("visuals", "visuals", _visuals_stage),
        ("assemble", "assembled", _assemble_stage),
        ("verify", "verified", verify.run),
        ("package", "packaged", package.run),
        ("publish", "published", publish.run),
    ]


# ── Main CLI ───────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for shorts_engine CLI.

    Args:
        argv: Command-line arguments (default: sys.argv[1:])

    Returns:
        Exit code: 0 on success, 1 on failure

    Raises:
        SystemExit: If argument parsing fails (via argparse)
    """
    # Parse arguments
    parser = argparse.ArgumentParser(
        prog="shorts_engine",
        description="Generate short-form video scripts from blog posts",
    )

    parser.add_argument(
        "blog_url",
        help="Blog post URL to process",
    )

    parser.add_argument(
        "--until",
        choices=["ingest", "facts", "script", "shotlist", "audio", "visuals", "assemble",
                 "verify", "package", "publish"],
        default=None,
        help="Stop execution after reaching this stage",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last completed stage",
    )

    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Use local Ollama model only (no cloud)",
    )

    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=config.OUTPUT_BASE,
        help=f"Workspace root directory (default: {config.OUTPUT_BASE})",
    )

    parser.add_argument(
        "--html-override",
        type=Path,
        default=None,
        help="HTML file to use instead of fetching from URL (for testing)",
    )

    parser.add_argument(
        "--torture",
        action="store_true",
        help="Run full pipeline with all-designed cards (Phase 4 torture test)",
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help="Actually upload to YouTube (default is dry-run / hold for review)",
    )

    args = parser.parse_args(argv)

    # Convert "until" flag to correct status names
    until_map = {
        "ingest": "ingested",
        "facts": "facts",
        "script": "scripted",
        "shotlist": "shotlisted",
        "audio": "audio",
        "visuals": "visuals",
        "assemble": "assembled",
        "verify": "verified",
        "package": "packaged",
        "publish": "published",
    }
    if args.until:
        until_status = until_map[args.until]
    elif args.publish:
        until_status = "published"
    else:
        until_status = "verified"   # hold_for_review: stop at the contact sheet

    # Build flags dict for stages
    flags = {}
    if args.local_only:
        flags["local_only"] = True
    if args.html_override:
        # ingest.run() treats flags["html_override"] as literal HTML content (it hands it
        # straight to BeautifulSoup), not a path — so read the file here, exactly as
        # stage_cli.py's own _flags_from_args() already does.
        flags["html_override"] = Path(args.html_override).read_text(encoding="utf-8")
    if args.torture:
        flags["torture"] = True
    if args.publish:
        flags["publish"] = True

    # Execute pipeline
    try:
        stages = build_stages()
        manifest = runner.run(
            blog_url=args.blog_url,
            stages=stages,
            workspace_root=args.workspace_root,
            until=until_status,
            resume=args.resume,
            flags=flags,
        )

        # Print success status. ASCII markers, not check/cross glyphs: a live
        # run COMPLETED the whole pipeline and then exited 1 because Windows'
        # cp1252 console couldn't encode the success message's U+2713.
        print(f"[OK] Pipeline completed: {manifest.run_id}")
        print(f"  Status: {manifest.status}")

        # Print artifacts if any
        if manifest.artifacts:
            print("  Artifacts:")
            for key, value in manifest.artifacts.items():
                print(f"    {key}: {value}")

        if "contact_sheet" in manifest.artifacts:
            print(f"  Review: {manifest.artifacts.get('contact_sheet', '')}")

        return 0

    except Exception as exc:
        # Print error to stderr
        print(f"[FAIL] Pipeline failed: {exc}", file=sys.stderr)
        logger.exception("Pipeline execution failed")
        return 1
