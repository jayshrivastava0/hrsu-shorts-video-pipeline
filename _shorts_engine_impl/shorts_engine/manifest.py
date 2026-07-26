"""
Durable state and checkpointing for shorts generation runs.

This module provides:
- STATUS_ORDER: Canonical status progression
- slug_from_url: Convert URLs to valid slugs
- RunManifest: Persistent state for a single run with checkpoint/resume capability

Design:
  - Each run gets a unique workspace directory with run_manifest.json
  - Status transitions are validated against STATUS_ORDER
  - Artifacts are merged on checkpoint (no overwrites of earlier stages)
  - All I/O is JSON-based for durability and human readability
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Status progression ─────────────────────────────────────────────────────
STATUS_ORDER = [
    "init",
    "ingested",
    "facts",
    "scripted",
    "shotlisted",
    "audio",
    "visuals",
    "assembled",
    "verified",
    "packaged",
    "published",
]


# ── Utilities ──────────────────────────────────────────────────────────────
def slug_from_url(url: str) -> str:
    """
    Extract and normalize a slug from a URL.

    Examples:
      - "https://example.com/blog/my-post" → "my-post"
      - "https://example.com/blog/my-post.html" → "my-post"
      - "https://example.com/blog/my post?id=123" → "my-post"
      - "https://example.com/blog/My-Post-Title" → "my-post-title"
    """
    # Parse URL to extract path
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    # Get the last part of the path (filename-like component)
    parts = path.split("/") if path else []
    slug = parts[-1] if parts else ""

    # Remove query string if present
    if "?" in slug:
        slug = slug.split("?")[0]

    # Remove file extension
    if "." in slug:
        slug = slug.rsplit(".", 1)[0]

    # Normalize: spaces to hyphens, lowercase
    slug = slug.replace(" ", "-").lower()

    return slug


# ── RunManifest dataclass ──────────────────────────────────────────────────
@dataclass
class RunManifest:
    """
    Durable state for a single shorts generation run.

    Tracks:
      - Unique run_id and blog URL being processed
      - Current status in the pipeline
      - Artifacts produced at each stage
      - Timestamps for audit/debugging

    Workflow:
      1. create(blog_url, workspace_root) → initializes and saves
      2. checkpoint(status, **artifacts) → advances status, merges artifacts, saves
      3. load(workspace) → restore from disk to resume
    """

    run_id: str
    """Unique identifier for this run (e.g., 'run-a1b2c3d4')."""

    blog_url: str
    """Source blog post URL."""

    slug: str
    """Normalized slug extracted from blog_url."""

    status: str
    """Current status (must be in STATUS_ORDER)."""

    workspace: str
    """Absolute path to run's workspace directory."""

    artifacts: dict[str, str] = field(default_factory=dict)
    """Paths to artifacts produced by each stage (cumulative)."""

    model_tier: str = "cloud"
    """Which model tier was used ('cloud' or 'local')."""

    error: str | None = None
    """If set, describes why the run failed."""

    last_ok_status: str = "init"
    """Last status that completed successfully (used for resume logic)."""

    created_at: str = ""
    """ISO timestamp of run creation."""

    updated_at: str = ""
    """ISO timestamp of last checkpoint/save."""

    @classmethod
    def create(cls, blog_url: str, workspace_root: Path) -> RunManifest:
        """
        Create and initialize a new run.

        Steps:
          1. Generate unique run_id
          2. Extract slug from blog_url
          3. Create workspace directory
          4. Initialize with status='init'
          5. Save to disk

        Args:
            blog_url: Source blog post URL
            workspace_root: Parent directory for run's workspace

        Returns:
            New RunManifest, initialized and persisted to disk
        """
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        slug = slug_from_url(blog_url)
        workspace = Path(workspace_root) / run_id

        # Create workspace directory
        workspace.mkdir(parents=True, exist_ok=True)

        # Initialize with timestamps
        now = datetime.utcnow().isoformat()
        manifest = cls(
            run_id=run_id,
            blog_url=blog_url,
            slug=slug,
            status="init",
            workspace=str(workspace),
            artifacts={},
            model_tier="cloud",
            error=None,
            created_at=now,
            updated_at=now,
        )

        # Persist to disk
        manifest.save()

        logger.info(
            f"Created run {run_id} for {blog_url} (slug={slug}) "
            f"in {workspace}"
        )
        return manifest

    @classmethod
    def load(cls, workspace: str | Path) -> RunManifest:
        """
        Load a persisted manifest from disk.

        Args:
            workspace: Path to run's workspace directory

        Returns:
            RunManifest with all state restored

        Raises:
            FileNotFoundError: If run_manifest.json does not exist
            json.JSONDecodeError: If JSON is malformed
        """
        workspace = Path(workspace)
        manifest_file = workspace / "run_manifest.json"

        if not manifest_file.exists():
            raise FileNotFoundError(
                f"Manifest not found at {manifest_file}. "
                f"Is this a valid workspace?"
            )

        data = json.loads(manifest_file.read_text())
        logger.debug(f"Loaded manifest from {manifest_file}")
        return cls(**data)

    def checkpoint(self, status: str, **artifacts: str) -> None:
        """
        Advance to a new status and merge artifact paths.

        Updates:
          - status field
          - last_ok_status field (to track successful checkpoints)
          - artifacts dict (merged, no overwrites of earlier stages)
          - updated_at timestamp
          - persists to disk

        Args:
            status: New status (should be in STATUS_ORDER)
            **artifacts: Artifact name → path pairs to merge into self.artifacts

        Raises:
            (Currently no validation; could raise in future if status not in
             STATUS_ORDER)
        """
        self.status = status
        self.last_ok_status = status
        self.artifacts.update(artifacts)
        self.save()

        logger.info(
            f"Checkpoint: {self.run_id} → {status} "
            f"(artifacts: {list(artifacts.keys())})"
        )

    def save(self) -> None:
        """
        Persist manifest to disk, updating updated_at timestamp.

        Writes to: <workspace>/run_manifest.json
        """
        # Update timestamp
        self.updated_at = datetime.utcnow().isoformat()

        # Ensure workspace exists
        workspace = Path(self.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        # Serialize and write
        manifest_file = workspace / "run_manifest.json"
        data = asdict(self)
        manifest_file.write_text(json.dumps(data, indent=2))

        logger.debug(f"Saved manifest to {manifest_file}")
