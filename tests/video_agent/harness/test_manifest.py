"""
Tests for RunManifest state subsystem.
"""
import json
import pytest
from pathlib import Path
from datetime import datetime

from video_agent.harness.manifest import (
    RunStatus,
    VerifyReport,
    PublishPackage,
    PublishResult,
    RunManifest,
    new_manifest,
    save_manifest,
    load_manifest,
)


class TestNewManifest:
    """Test fresh manifest creation."""

    def test_new_manifest_defaults(self):
        """Verify new manifest has status='init', empty optional fields."""
        blog_url = "https://example.com/blog/test-post"
        slug = "test-post"
        workspace = "/tmp/workspace"

        m = new_manifest(blog_url, slug, workspace)

        assert m.version == "1.0"
        assert m.status == "init"
        assert len(m.run_id) == 12  # short UUID hex
        assert m.blog_url == blog_url
        assert m.slug == slug
        assert m.workspace == workspace
        assert m.storyboard_path is None
        assert m.video_path is None
        assert m.srt_path is None
        assert m.voice_path is None
        assert m.verify is None
        assert m.package is None
        assert m.publish is None
        assert m.attempts == 0
        assert m.last_error is None
        assert m.created_at is not None  # ISO format timestamp
        assert m.updated_at is not None
        # Verify timestamp format: YYYY-MM-DDTHH:MM:SSZ
        datetime.fromisoformat(m.created_at.replace("Z", "+00:00"))


class TestRoundtripWithNested:
    """Test manifest with all nested fields populated."""

    def test_roundtrip_with_nested(self, tmp_path):
        """Create manifest with all nested fields, save, load, verify all data survives."""
        blog_url = "https://example.com/blog/complex-post"
        slug = "complex-post"
        workspace = str(tmp_path / "workspace")

        # Create manifest and populate all fields
        m = new_manifest(blog_url, slug, workspace)
        m.storyboard_path = str(tmp_path / "storyboard.json")
        m.video_path = str(tmp_path / "video.mp4")
        m.srt_path = str(tmp_path / "captions.srt")
        m.voice_path = str(tmp_path / "voice.mp3")
        m.status = "rendered"
        m.attempts = 2

        # Add verify report
        m.verify = VerifyReport(
            passed=True,
            checks={
                "duration": {"passed": True, "value": 120},
                "quality": {"passed": True, "score": 0.95},
            },
            defects=[],
        )

        # Add package info
        m.package = PublishPackage(
            title="Test Video",
            description="A test video description",
            tags=["test", "demo"],
            category_id="27",
            thumbnail_path=str(tmp_path / "thumb.jpg"),
            caption_srt_path=str(tmp_path / "captions.srt"),
            privacy_status="public",
        )

        # Add publish result
        m.publish = PublishResult(
            platform="youtube",
            video_id="dQw4w9WgXcQ",
            url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            visibility="public",
            uploaded_at="2026-06-09T12:00:00Z",
        )

        # Save
        manifest_path = tmp_path / "manifest.json"
        save_manifest(m, str(manifest_path))

        # Verify file exists
        assert manifest_path.exists()

        # Load
        loaded = load_manifest(str(manifest_path))

        # Verify all fields
        assert loaded.version == m.version
        assert loaded.run_id == m.run_id
        assert loaded.blog_url == m.blog_url
        assert loaded.slug == m.slug
        assert loaded.workspace == m.workspace
        assert loaded.status == "rendered"
        assert loaded.storyboard_path == m.storyboard_path
        assert loaded.video_path == m.video_path
        assert loaded.srt_path == m.srt_path
        assert loaded.voice_path == m.voice_path
        assert loaded.attempts == 2

        # Verify nested VerifyReport
        assert loaded.verify is not None
        assert loaded.verify.passed is True
        assert loaded.verify.checks["duration"]["value"] == 120
        assert loaded.verify.defects == []

        # Verify nested PublishPackage
        assert loaded.package is not None
        assert loaded.package.title == "Test Video"
        assert loaded.package.description == "A test video description"
        assert loaded.package.tags == ["test", "demo"]
        assert loaded.package.category_id == "27"

        # Verify nested PublishResult
        assert loaded.publish is not None
        assert loaded.publish.platform == "youtube"
        assert loaded.publish.video_id == "dQw4w9WgXcQ"
        assert loaded.publish.visibility == "public"


class TestRoundtripAllNone:
    """Test manifest with all optional fields None."""

    def test_roundtrip_all_none(self, tmp_path):
        """Create manifest with all optional fields None, save, load, verify."""
        blog_url = "https://example.com/blog/simple"
        slug = "simple"
        workspace = str(tmp_path / "workspace")

        # Create manifest (all optional fields default to None)
        m = new_manifest(blog_url, slug, workspace)

        # Ensure all optional fields are None
        m.storyboard_path = None
        m.video_path = None
        m.srt_path = None
        m.voice_path = None
        m.verify = None
        m.package = None
        m.publish = None
        m.last_error = None

        # Save
        manifest_path = tmp_path / "manifest.json"
        save_manifest(m, str(manifest_path))

        # Load
        loaded = load_manifest(str(manifest_path))

        # Verify all optional fields are still None
        assert loaded.storyboard_path is None
        assert loaded.video_path is None
        assert loaded.srt_path is None
        assert loaded.voice_path is None
        assert loaded.verify is None
        assert loaded.package is None
        assert loaded.publish is None
        assert loaded.last_error is None

        # Core fields still intact
        assert loaded.blog_url == blog_url
        assert loaded.slug == slug
        assert loaded.status == "init"


class TestVisionReport:
    """Test vision grading results and status tracking."""

    def test_vision_report_roundtrip(self, tmp_path):
        """Create manifest with VisionReport, save, load, verify all vision data survives."""
        from video_agent.harness.manifest import SceneGrade, VisionReport

        m = new_manifest(blog_url="https://example.com/blog/vision-test", slug="vision-test", workspace=str(tmp_path))
        m.status = "vision_verified"
        m.vision = VisionReport(
            passed=True,
            hold=False,
            cycles_used=1,
            scenes=[
                SceneGrade(
                    index=0,
                    overall=8.5,
                    scores={"visual_match": 9, "readability": 8},
                    defects=[],
                ),
                SceneGrade(
                    index=1,
                    overall=7.5,
                    scores={"visual_match": 7, "readability": 8},
                    defects=["minor_text_cutoff"],
                ),
            ],
        )

        p = tmp_path / "m.json"
        save_manifest(m, str(p))
        loaded = load_manifest(str(p))

        assert loaded.status == "vision_verified"
        assert loaded.vision is not None
        assert loaded.vision.passed is True
        assert loaded.vision.hold is False
        assert loaded.vision.cycles_used == 1
        assert len(loaded.vision.scenes) == 2
        assert loaded.vision.scenes[0].index == 0
        assert loaded.vision.scenes[0].overall == 8.5
        assert loaded.vision.scenes[0].scores["visual_match"] == 9
        assert loaded.vision.scenes[0].scores["readability"] == 8
        assert loaded.vision.scenes[0].defects == []
        assert loaded.vision.scenes[1].index == 1
        assert loaded.vision.scenes[1].overall == 7.5
        assert loaded.vision.scenes[1].defects == ["minor_text_cutoff"]

    def test_vision_none_roundtrip(self, tmp_path):
        """Create manifest with vision=None, save, load, verify it remains None."""
        m = new_manifest(blog_url="https://example.com/blog/no-vision", slug="no-vision", workspace=str(tmp_path))
        p = tmp_path / "m.json"
        save_manifest(m, str(p))
        loaded = load_manifest(str(p))
        assert loaded.vision is None
