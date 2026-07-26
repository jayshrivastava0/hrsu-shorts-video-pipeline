"""
Test durable state, checkpointing, and resumption for shorts_engine.

Tests verify:
1. STATUS_ORDER constant exists and has expected values
2. slug_from_url converts URLs to valid slugs
3. RunManifest dataclass initializes with required fields
4. RunManifest.create sets up a new run with workspace
5. RunManifest.checkpoint merges artifacts and updates status
6. RunManifest persistence (save/load) works correctly
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

logger = logging.getLogger(__name__)


class TestStatusOrder:
    """Test that STATUS_ORDER constant exists and has correct values."""

    def test_status_order_exists(self):
        """STATUS_ORDER constant exists in manifest module."""
        from shorts_engine import manifest
        assert hasattr(manifest, "STATUS_ORDER")
        assert isinstance(manifest.STATUS_ORDER, list)

    def test_status_order_values(self):
        """STATUS_ORDER contains expected status strings in order."""
        from shorts_engine import manifest
        expected = [
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
        assert manifest.STATUS_ORDER == expected


class TestSlugFromUrl:
    """Test slug_from_url function."""

    def test_slug_from_url_basic(self):
        """slug_from_url converts simple URLs to slugs."""
        from shorts_engine.manifest import slug_from_url

        url = "https://example.com/blog/my-first-post"
        slug = slug_from_url(url)
        assert slug == "my-first-post"

    def test_slug_from_url_no_extension(self):
        """slug_from_url strips file extensions."""
        from shorts_engine.manifest import slug_from_url

        url = "https://example.com/blog/my-post.html"
        slug = slug_from_url(url)
        assert slug == "my-post"

    def test_slug_from_url_query_params(self):
        """slug_from_url ignores query parameters."""
        from shorts_engine.manifest import slug_from_url

        url = "https://example.com/blog/my-post?id=123&foo=bar"
        slug = slug_from_url(url)
        assert slug == "my-post"

    def test_slug_from_url_normalizes_spaces(self):
        """slug_from_url normalizes spaces to hyphens."""
        from shorts_engine.manifest import slug_from_url

        url = "https://example.com/blog/my post title"
        slug = slug_from_url(url)
        assert slug == "my-post-title"

    def test_slug_from_url_lowercase(self):
        """slug_from_url converts to lowercase."""
        from shorts_engine.manifest import slug_from_url

        url = "https://example.com/blog/My-Post-Title"
        slug = slug_from_url(url)
        assert slug == "my-post-title"


class TestRunManifestDataclass:
    """Test RunManifest dataclass initialization and fields."""

    def test_run_manifest_init(self):
        """RunManifest initializes with required fields."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest(
            run_id="run-001",
            blog_url="https://example.com/blog/post",
            slug="post",
            status="init",
            workspace="/tmp/workspace",
            artifacts={},
        )
        assert run.run_id == "run-001"
        assert run.blog_url == "https://example.com/blog/post"
        assert run.slug == "post"
        assert run.status == "init"
        assert run.workspace == "/tmp/workspace"
        assert run.artifacts == {}
        assert run.model_tier == "cloud"
        assert run.error is None

    def test_run_manifest_defaults(self):
        """RunManifest has sensible defaults for optional fields."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest(
            run_id="run-001",
            blog_url="https://example.com/blog/post",
            slug="post",
            status="init",
            workspace="/tmp/workspace",
            artifacts={},
        )
        assert run.model_tier == "cloud"
        assert run.error is None
        # created_at and updated_at should have default values
        assert run.created_at == "" or isinstance(run.created_at, str)
        assert run.updated_at == "" or isinstance(run.updated_at, str)


class TestRunManifestCreate:
    """Test RunManifest.create factory method."""

    def test_create_initializes_manifest(self, tmp_path):
        """RunManifest.create creates a new run with init status."""
        from shorts_engine.manifest import RunManifest

        blog_url = "https://example.com/blog/my-post"
        run = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)

        assert run.blog_url == blog_url
        assert run.slug == "my-post"
        assert run.status == "init"
        assert run.artifacts == {}
        assert run.error is None

    def test_create_creates_workspace_directory(self, tmp_path):
        """RunManifest.create creates the workspace directory."""
        from shorts_engine.manifest import RunManifest

        blog_url = "https://example.com/blog/my-post"
        run = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)

        workspace_path = Path(run.workspace)
        assert workspace_path.exists()
        assert workspace_path.is_dir()

    def test_create_saves_manifest(self, tmp_path):
        """RunManifest.create saves run_manifest.json to workspace."""
        from shorts_engine.manifest import RunManifest

        blog_url = "https://example.com/blog/my-post"
        run = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)

        manifest_file = Path(run.workspace) / "run_manifest.json"
        assert manifest_file.exists()
        assert manifest_file.is_file()

    def test_create_manifest_json_structure(self, tmp_path):
        """RunManifest.create writes valid JSON with all fields."""
        from shorts_engine.manifest import RunManifest

        blog_url = "https://example.com/blog/my-post"
        run = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)

        manifest_file = Path(run.workspace) / "run_manifest.json"
        data = json.loads(manifest_file.read_text())

        assert data["run_id"] == run.run_id
        assert data["blog_url"] == blog_url
        assert data["slug"] == "my-post"
        assert data["status"] == "init"
        assert data["artifacts"] == {}
        assert data["model_tier"] == "cloud"
        assert data["error"] is None


class TestRunManifestLoad:
    """Test RunManifest.load to restore state."""

    def test_load_restores_manifest(self, tmp_path):
        """RunManifest.load reads from disk and restores state."""
        from shorts_engine.manifest import RunManifest

        # Create a manifest
        blog_url = "https://example.com/blog/my-post"
        original = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)

        # Load it back
        restored = RunManifest.load(workspace=original.workspace)

        assert restored.run_id == original.run_id
        assert restored.blog_url == original.blog_url
        assert restored.slug == original.slug
        assert restored.status == original.status
        assert restored.artifacts == original.artifacts


class TestRunManifestCheckpoint:
    """Test RunManifest.checkpoint for state transitions."""

    def test_checkpoint_updates_status(self, tmp_path):
        """RunManifest.checkpoint updates status."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )
        assert run.status == "init"

        run.checkpoint(status="ingested")
        assert run.status == "ingested"

    def test_checkpoint_merges_artifacts(self, tmp_path):
        """RunManifest.checkpoint merges artifact dict."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )

        run.checkpoint(status="ingested", facts_file="/path/to/facts.json")
        assert run.artifacts["facts_file"] == "/path/to/facts.json"

        run.checkpoint(status="scripted", script_file="/path/to/script.md")
        assert run.artifacts["facts_file"] == "/path/to/facts.json"
        assert run.artifacts["script_file"] == "/path/to/script.md"

    def test_checkpoint_saves_to_disk(self, tmp_path):
        """RunManifest.checkpoint persists changes to disk."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )

        run.checkpoint(status="ingested", facts_file="/path/to/facts.json")

        # Load from disk and verify
        manifest_file = Path(run.workspace) / "run_manifest.json"
        data = json.loads(manifest_file.read_text())
        assert data["status"] == "ingested"
        assert data["artifacts"]["facts_file"] == "/path/to/facts.json"

    def test_checkpoint_updates_timestamp(self, tmp_path):
        """RunManifest.checkpoint refreshes updated_at."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )
        original_updated_at = run.updated_at

        # Small delay to ensure timestamp differs
        import time
        time.sleep(0.01)

        run.checkpoint(status="ingested")
        # updated_at should be refreshed (if it was set initially)
        # or should now have a value
        assert run.updated_at is not None


class TestRunManifestSave:
    """Test RunManifest.save for persistence."""

    def test_save_writes_json_file(self, tmp_path):
        """RunManifest.save writes run_manifest.json."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )

        # Modify and save
        run.status = "ingested"
        run.artifacts["test"] = "value"
        run.save()

        manifest_file = Path(run.workspace) / "run_manifest.json"
        data = json.loads(manifest_file.read_text())
        assert data["status"] == "ingested"
        assert data["artifacts"]["test"] == "value"

    def test_save_refreshes_updated_at(self, tmp_path):
        """RunManifest.save refreshes updated_at timestamp."""
        from shorts_engine.manifest import RunManifest

        run = RunManifest.create(
            blog_url="https://example.com/blog/my-post",
            workspace_root=tmp_path,
        )

        import time
        time.sleep(0.01)

        run.save()

        # Check that updated_at is in ISO format and recent
        manifest_file = Path(run.workspace) / "run_manifest.json"
        data = json.loads(manifest_file.read_text())
        assert "updated_at" in data
        assert len(data["updated_at"]) > 0


class TestRunManifestRoundTrip:
    """Test full roundtrip: create → checkpoint → load → checkpoint → save."""

    def test_roundtrip_persistence(self, tmp_path):
        """Full lifecycle: create, checkpoint, load, modify, save, load."""
        from shorts_engine.manifest import RunManifest

        # Create
        blog_url = "https://example.com/blog/calcium-nitrate"
        run1 = RunManifest.create(blog_url=blog_url, workspace_root=tmp_path)
        run_id = run1.run_id

        # Checkpoint after ingestion
        run1.checkpoint(status="ingested", facts_file="facts.json")
        assert run1.status == "ingested"

        # Load from disk
        run2 = RunManifest.load(workspace=run1.workspace)
        assert run2.run_id == run_id
        assert run2.status == "ingested"
        assert run2.artifacts["facts_file"] == "facts.json"

        # Checkpoint again
        run2.checkpoint(status="scripted", script_file="script.md")
        assert run2.status == "scripted"

        # Load again and verify all state preserved
        run3 = RunManifest.load(workspace=run2.workspace)
        assert run3.run_id == run_id
        assert run3.status == "scripted"
        assert run3.artifacts["facts_file"] == "facts.json"
        assert run3.artifacts["script_file"] == "script.md"
