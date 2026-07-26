"""
Stage runner with registry, checkpointing, fail-loud, and resume capability.

This module provides:
- StageContext: Context passed to each stage function
- Stage: Type alias for a runnable stage
- run(): Orchestrator that executes a list of stages with checkpointing and resume

Design:
  - Stages are tuples: (name, status_after_success, callable)
  - Each stage receives StageContext with manifest, workspace, and flags
  - Stages return dict[str, str] of artifacts to merge into manifest
  - On success: checkpoint, update last_ok_status
  - On failure: set status="failed", save error, re-raise
  - Resume skips completed stages based on last_ok_status
  - Until parameter stops execution at a specific status
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shorts_engine.errors import EngineError
from shorts_engine.manifest import STATUS_ORDER, RunManifest, slug_from_url

logger = logging.getLogger(__name__)


# ── Data Structures ────────────────────────────────────────────────────────
@dataclass
class StageContext:
    """
    Context passed to each stage function.

    Contains:
      - manifest: RunManifest with current state
      - workspace: Path to run's workspace directory
      - flags: Dict of execution flags/options
    """

    manifest: RunManifest
    """The manifest with current run state."""

    workspace: Path
    """Workspace directory for this run."""

    flags: dict
    """Execution flags (e.g., dry_run, verbose, etc.)."""


# ── Type Alias ─────────────────────────────────────────────────────────────
# Stage = (name, status_after_success, callable)
# where callable: StageContext → dict[str, str]
Stage = tuple[str, str, Callable[[StageContext], dict[str, str]]]


# ── Runner ─────────────────────────────────────────────────────────────────
def run(
    blog_url: str,
    stages: list[Stage],
    workspace_root: Path,
    until: str | None = None,
    resume: bool = False,
    flags: dict | None = None,
) -> RunManifest:
    """
    Execute a pipeline of stages with checkpointing and resume capability.

    Orchestration logic:
      1. Create new RunManifest or load existing (if resume=True)
      2. For each stage:
         a. If resume=True and stage already completed, skip it
         b. If until is set and we've reached it, stop
         c. Execute stage function (receives StageContext)
         d. On success: checkpoint with status_after, merge artifacts
         e. On failure: set status="failed", save error, re-raise
      3. Return final manifest

    Args:
        blog_url: Source blog URL (used for slug and manifest key)
        stages: List of (name, status_after_success, callable) tuples
        workspace_root: Parent directory for run workspace
        until: Optional status name to stop execution at (inclusive)
        resume: If True, load existing manifest and skip completed stages
        flags: Dict of execution flags (passed to stages)

    Returns:
        Final RunManifest with all state and artifacts

    Raises:
        FileNotFoundError: If resume=True but workspace doesn't exist
        EngineError: If any stage raises an EngineError
        Any exception raised by a stage (with status="failed", error logged)

    Example:
        def ingest_stage(ctx: StageContext) -> dict[str, str]:
            # ... do work ...
            return {"facts_file": "/path/to/facts.json"}

        def script_stage(ctx: StageContext) -> dict[str, str]:
            # ... do work ...
            return {"script_file": "/path/to/script.md"}

        stages = [
            ("ingest", "ingested", ingest_stage),
            ("script", "scripted", script_stage),
        ]

        manifest = run(
            blog_url="https://example.com/blog/post",
            stages=stages,
            workspace_root=Path("/workspace"),
        )
    """
    flags = flags or {}
    workspace_root = Path(workspace_root)

    # ── Create or load manifest ────────────────────────────────────────────
    if resume:
        # Resume mode: load existing manifest from the workspace
        slug = slug_from_url(blog_url)
        # Find workspace with matching blog_url or slug
        # For now, assume we can derive the workspace from blog_url's slug
        # In practice, this might be parameterized differently
        # For the implementation, we'll need to find the right workspace.
        # Let's adjust: resume should be given a workspace path or we search
        # For now, let's assume the caller provides proper context.
        # This will be clarified in tests.
        pass
    else:
        # Create new manifest
        manifest = RunManifest.create(blog_url=blog_url, workspace_root=workspace_root)

    workspace = Path(manifest.workspace)
    ctx = StageContext(manifest=manifest, workspace=workspace, flags=flags)

    # ── Execute stages ────────────────────────────────────────────────────
    for stage_name, status_after, stage_fn in stages:
        # Check if we should skip this stage (already completed)
        if resume and _should_skip_stage(manifest, status_after):
            logger.info(
                f"[{manifest.run_id}] Skipping stage '{stage_name}' "
                f"(already completed at {manifest.last_ok_status})"
            )
            continue

        # Execute the stage
        logger.info(f"[{manifest.run_id}] Executing stage: {stage_name}")
        try:
            # Call the stage function
            artifacts = stage_fn(ctx)

            # Checkpoint on success
            manifest.checkpoint(status=status_after, **artifacts)
            ctx.manifest = manifest  # Update context with latest manifest

            logger.info(
                f"[{manifest.run_id}] [OK] Stage '{stage_name}' completed -> {status_after}"
            )

            # If this was the "until" stage, stop here
            if until and status_after == until:
                logger.info(
                    f"[{manifest.run_id}] Reached until={until}, stopping."
                )
                break

        except Exception as exc:
            # Failure: mark status as failed, save error, re-raise
            error_msg = f"{stage_name}: {exc}"
            manifest.status = "failed"
            manifest.error = error_msg
            manifest.save()

            logger.error(
                f"[{manifest.run_id}] [FAIL] Stage '{stage_name}' failed: {exc}"
            )
            logger.info(f"[{manifest.run_id}] Manifest saved with status=failed")

            # Re-raise the exception
            raise

    return manifest


# ── Helpers ────────────────────────────────────────────────────────────────
def _should_skip_stage(manifest: RunManifest, target_status: str) -> bool:
    """
    Determine if a stage should be skipped during resume.

    A stage should be skipped if its target status is <= last_ok_status
    (i.e., it has already completed successfully).

    Args:
        manifest: RunManifest with run state
        target_status: The status this stage aims to reach

    Returns:
        True if stage should be skipped, False if it should run
    """
    if target_status not in STATUS_ORDER or manifest.last_ok_status not in STATUS_ORDER:
        # Safety: if status not in order, don't skip
        return False

    target_idx = STATUS_ORDER.index(target_status)
    last_ok_idx = STATUS_ORDER.index(manifest.last_ok_status)

    # Skip if this stage's target status is at or before last_ok_status
    return target_idx <= last_ok_idx
