"""
RunManifest: Durable, resumable state object for one video run.

This module provides the state subsystem that persists all run state as JSON,
enabling resumability across process boundaries.
"""
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional, Dict, List, Any


# Status enum as Literal type
RunStatus = Literal["init", "planned", "generated", "rendered", "verified",
                    "vision_verified", "hold_for_review",
                    "packaged", "published", "failed"]

# Manifest version for backward compatibility
MANIFEST_VERSION = "1.0"


@dataclass
class SceneGrade:
    """Vision grading for a single scene."""
    index: int
    overall: float
    scores: Dict[str, Any] = field(default_factory=dict)
    defects: List[dict] = field(default_factory=list)


@dataclass
class VisionReport:
    """Report from vision verification gate."""
    passed: bool
    hold: bool = False
    cycles_used: int = 0
    scenes: List[SceneGrade] = field(default_factory=list)


@dataclass
class VerifyReport:
    """Report from verification gate."""
    passed: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    defects: List[str] = field(default_factory=list)


@dataclass
class PublishPackage:
    """Package prepared for publishing."""
    title: str
    description: str
    tags: List[str] = field(default_factory=list)
    category_id: str = "27"  # Education category default
    thumbnail_path: Optional[str] = None
    caption_srt_path: Optional[str] = None
    privacy_status: str = "private"


@dataclass
class PublishResult:
    """Result of publishing to a platform."""
    platform: str
    video_id: str
    url: str
    visibility: str
    uploaded_at: str


@dataclass
class RunManifest:
    """Durable state object for one video run."""
    version: str  # API version for backward compatibility
    run_id: str  # Unique run identifier (12-char short UUID)
    blog_url: str
    slug: str
    status: RunStatus
    workspace: str

    # Optional fields (populated as run progresses)
    storyboard_path: Optional[str] = None
    video_path: Optional[str] = None
    srt_path: Optional[str] = None
    voice_path: Optional[str] = None
    verify: Optional[VerifyReport] = None
    vision: Optional[VisionReport] = None
    package: Optional[PublishPackage] = None
    publish: Optional[PublishResult] = None
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: str = ""  # ISO format: YYYY-MM-DDTHH:MM:SSZ
    updated_at: str = ""  # ISO format: YYYY-MM-DDTHH:MM:SSZ


def _now() -> str:
    """Return current UTC timestamp in ISO format with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_manifest(blog_url: str, slug: str, workspace: str) -> RunManifest:
    """
    Create a fresh manifest for a new run.

    Args:
        blog_url: URL of the blog post source
        slug: URL slug identifier
        workspace: Workspace directory path

    Returns:
        Fresh RunManifest with status="init" and current timestamp
    """
    now = _now()
    run_id = uuid.uuid4().hex[:12]  # 12-char short UUID

    return RunManifest(
        version=MANIFEST_VERSION,
        run_id=run_id,
        blog_url=blog_url,
        slug=slug,
        status="init",
        workspace=workspace,
        created_at=now,
        updated_at=now,
    )


def save_manifest(m: RunManifest, path: str) -> None:
    """
    Save manifest to JSON file.

    Args:
        m: RunManifest to save
        path: File path to write to
    """
    m.updated_at = _now()

    # Convert to dict, handling nested dataclasses
    data = asdict(m)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_manifest(path: str) -> RunManifest:
    """
    Load manifest from JSON file.

    Args:
        path: File path to read from

    Returns:
        Reconstructed RunManifest with all nested dataclasses

    Raises:
        ValueError: If JSON is malformed or required fields are missing
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in manifest at {path}: {e}") from e
    except IOError as e:
        raise ValueError(f"Cannot read manifest file {path}: {e}") from e

    try:
        # Reconstruct nested dataclasses conditionally
        if data.get("verify"):
            data["verify"] = VerifyReport(**data["verify"])

        if data.get("vision"):
            vis = data["vision"]
            data["vision"] = VisionReport(
                passed=vis["passed"],
                hold=vis.get("hold", False),
                cycles_used=vis.get("cycles_used", 0),
                scenes=[SceneGrade(**s) for s in vis.get("scenes", [])],
            )

        if data.get("package"):
            data["package"] = PublishPackage(**data["package"])

        if data.get("publish"):
            data["publish"] = PublishResult(**data["publish"])

        return RunManifest(**data)
    except KeyError as e:
        raise ValueError(f"Missing required field in manifest: {e}") from e
    except TypeError as e:
        raise ValueError(f"Invalid field type in manifest: {e}") from e
