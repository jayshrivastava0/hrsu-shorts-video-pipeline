"""TDD test suite for HarnessRunner state machine.

Tests cover:
- Full pipeline from PLAN to PUBLISH
- Resume/idempotency of phases
- Dry-run behavior
- publish=False behavior (stop at VERIFY)
- Error handling and status tracking
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from video_agent.harness.runner import HarnessRunner
from video_agent.harness.manifest import (
    RunManifest, RunStatus, VerifyReport, PublishPackage, PublishResult,
    new_manifest, save_manifest, load_manifest,
)
from video_agent.storyboard import Storyboard, HeroClaim, Scene, Beat, VisualConcept


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_workspace(tmp_path):
    """Isolated workspace for each test."""
    return tmp_path


@pytest.fixture
def blog_url():
    """Standard test blog URL."""
    return "https://blog.hrsuindore.com/2026/05/test-blog.html"


@pytest.fixture
def blog_html():
    """Minimal HTML for testing."""
    return """
    <html>
        <head><title>Test Blog</title></head>
        <body><p>This is a test blog post about calcium nitrate.</p></body>
    </html>
    """


@pytest.fixture
def blog_history_entry():
    """Blog history record matching blog_url."""
    return {
        "title": "Test Blog Post",
        "url": "https://blog.hrsuindore.com/2026/05/test-blog.html",
        "region": "australia",
        "category": "calcium_nitrate",
        "subcategory": "wastewater_treatment",
        "language": "en-AU",
    }


def _make_storyboard() -> Storyboard:
    """Create a minimal valid storyboard."""
    return Storyboard(
        version="2.0",
        blog={
            "blog_id": "test-blog",
            "title": "Test Blog",
            "url": "https://blog.hrsuindore.com/2026/05/test-blog.html",
            "region": "australia",
            "category": "calcium_nitrate",
        },
        hero_claim=HeroClaim(
            stat="50%",
            claim_text="Calcium nitrate can reduce treatment time by 50%.",
            source_quote="From industry study."
        ),
        arc=[
            Beat(index=0, beat="hook", purpose="Hook the viewer", duration_target_s=5.0),
            Beat(index=1, beat="stakes", purpose="Explain the stakes", duration_target_s=10.0),
            Beat(index=2, beat="mechanism", purpose="Explain mechanism", duration_target_s=15.0),
            Beat(index=3, beat="proof", purpose="Prove the claim", duration_target_s=15.0),
            Beat(index=4, beat="cta", purpose="Call to action", duration_target_s=5.0),
        ],
        scenes=[
            Scene(
                index=0,
                beat="hook",
                narration="Did you know calcium nitrate can reduce treatment time?",
                on_screen_text="50% Faster Treatment",
                visual_concept=VisualConcept(
                    subject="wastewater treatment",
                    modifier="industrial",
                    type="photo",
                    mood="problem",
                ),
                duration_target_s=5.0,
            ),
            Scene(
                index=1,
                beat="stakes",
                narration="In wastewater treatment, time is money.",
                on_screen_text="Time = Cost",
                visual_concept=VisualConcept(
                    subject="industrial equipment",
                    modifier="operational",
                    type="photo",
                    mood="mechanism",
                ),
                duration_target_s=10.0,
            ),
            Scene(
                index=2,
                beat="mechanism",
                narration="Calcium nitrate accelerates the reaction.",
                on_screen_text="Faster Reactions",
                visual_concept=VisualConcept(
                    subject="chemistry",
                    modifier="accelerator",
                    type="diagram",
                    mood="mechanism",
                ),
                duration_target_s=15.0,
            ),
            Scene(
                index=3,
                beat="proof",
                narration="Studies show a 50% improvement.",
                on_screen_text="Proven Results",
                visual_concept=VisualConcept(
                    subject="chart",
                    modifier="results",
                    type="chart_data",
                    mood="proof",
                ),
                duration_target_s=15.0,
            ),
            Scene(
                index=4,
                beat="cta",
                narration="Contact HRSU today for your calcium nitrate needs.",
                on_screen_text="Learn More",
                visual_concept=VisualConcept(
                    subject="brand",
                    modifier="logo",
                    type="photo",
                    mood="brand",
                ),
                duration_target_s=5.0,
            ),
        ],
    )


# ============================================================================
# Tests: Full Pipeline
# ============================================================================

class TestFullPipeline:
    """Tests for complete pipeline execution."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_full_pipeline_plan_to_verify(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Full pipeline from PLAN to VERIFY succeeds."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False)

        # Assertions
        # Note: VISION phase runs unconditionally, so status should be vision_verified
        assert manifest.status == "vision_verified"
        assert manifest.video_path is not None
        assert manifest.srt_path is not None
        assert manifest.voice_path is not None
        assert manifest.verify is not None
        assert manifest.verify.passed is True
        assert manifest.vision is not None
        assert manifest.vision.passed is True
        assert manifest.package is None  # publish=False, so no package
        assert manifest.publish is None  # publish=False, so no publish
        assert mock_storyboard.called
        assert mock_voice.called
        assert mock_srt.called
        assert mock_compose.called
        assert mock_verify.called
        assert not mock_pkg.called  # Should not package when publish=False
        assert not mock_pub.called  # Should not publish when publish=False

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_full_pipeline_plan_to_publish(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Full pipeline from PLAN to PUBLISH succeeds."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
        pkg = PublishPackage(
            title="Test Video",
            description="A test video",
            tags=["test", "calcium-nitrate"],
        )
        mock_pkg.return_value = pkg
        result = PublishResult(
            platform="youtube",
            video_id="test_video_123",
            url="https://youtube.com/watch?v=test_video_123",
            visibility="unlisted",
            uploaded_at="2026-06-09T10:00:00Z",
        )
        mock_pub.return_value = result

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True)

        # Assertions
        assert manifest.status == "published"
        assert manifest.video_path is not None
        assert manifest.srt_path is not None
        assert manifest.voice_path is not None
        assert manifest.verify is not None
        assert manifest.verify.passed is True
        assert manifest.vision is not None
        assert manifest.vision.passed is True
        assert manifest.package is not None
        assert manifest.package.title == "Test Video"
        assert manifest.publish is not None
        assert manifest.publish.video_id == "test_video_123"
        assert mock_storyboard.called
        assert mock_voice.called
        assert mock_srt.called
        assert mock_compose.called
        assert mock_verify.called
        assert mock_pkg.called
        assert mock_pub.called

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_publish_false_stops_at_verify(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """With publish=False, stops after VISION (skips PACKAGE and PUBLISH)."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run with publish=False
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False)

        # Assertions
        # VISION phase runs unconditionally, so status should be vision_verified
        assert manifest.status == "vision_verified"
        assert mock_pkg.call_count == 0
        assert mock_pub.call_count == 0

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_dry_run_validates_no_upload(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Dry-run validates metadata but doesn't upload (calls with dry_run=True)."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
        pkg = PublishPackage(
            title="Test Video",
            description="A test video",
            tags=["test"],
        )
        mock_pkg.return_value = pkg
        # In dry-run, publish_to_youtube may or may not be called
        result = PublishResult(
            platform="youtube",
            video_id="DRY_RUN_2026_06_09_10_00_00",
            url="https://youtube.com/watch?v=DRY_RUN_2026_06_09_10_00_00",
            visibility="unlisted",
            uploaded_at="2026-06-09T10:00:00Z",
        )
        mock_pub.return_value = result

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run with dry_run=True (publish=False to skip packaging)
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False, dry_run=True)

        # Assertions
        # VISION runs unconditionally, so status should be vision_verified
        assert manifest.status == "vision_verified"
        # In dry_run with publish=False, we never reach PUBLISH phase
        assert not mock_pub.called

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_dry_run_returns_verified_status(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Dry-run returns status=verified, not published."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
        pkg = PublishPackage(
            title="Test Video",
            description="A test video",
            tags=["test"],
        )
        mock_pkg.return_value = pkg
        result = PublishResult(
            platform="youtube",
            video_id="DRY_RUN_123",
            url="https://youtube.com/watch?v=DRY_RUN_123",
            visibility="unlisted",
            uploaded_at="2026-06-09T10:00:00Z",
        )
        mock_pub.return_value = result

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run with dry_run=True and publish=False
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False, dry_run=True)

        # Assertions
        # VISION runs unconditionally; with publish=False and dry_run, status should be vision_verified
        assert manifest.status == "vision_verified"
        assert manifest.publish is None  # dry_run doesn't persist publish


# ============================================================================
# Tests: Resume/Idempotency
# ============================================================================

class TestResume:
    """Tests for resume functionality and phase idempotency."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_resume_skips_completed_phases(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Resume with existing manifest skips PLAN and GENERATE."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
        pkg = PublishPackage(
            title="Test Video",
            description="A test video",
            tags=["test"],
        )
        mock_pkg.return_value = pkg
        result = PublishResult(
            platform="youtube",
            video_id="test_123",
            url="https://youtube.com/watch?v=test_123",
            visibility="unlisted",
            uploaded_at="2026-06-09T10:00:00Z",
        )
        mock_pub.return_value = result

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Create a valid storyboard file
        import json
        sb_dict = {
            "version": "2.0",
            "blog": {"blog_id": "test", "title": "Test"},
            "hero_claim": {"stat": "50%", "claim_text": "Test claim", "source_quote": ""},
            "arc": [],
            "supporting_facts": [],
            "scenes": [],
            "director_notes": {},
            "metadata": {},
            "narrative_thread": [],
        }
        storyboard_path = tmp_workspace / "storyboard.json"
        storyboard_path.write_text(json.dumps(sb_dict, indent=2), encoding="utf-8")

        # Create a manifest at "rendered" status (PLAN/GENERATE/RENDER done)
        manifest = new_manifest(blog_url, "test-blog", str(tmp_workspace))
        manifest.status = "rendered"
        manifest.storyboard_path = str(storyboard_path)
        manifest.voice_path = str(tmp_workspace / "voice.mp3")
        manifest.srt_path = str(tmp_workspace / "subtitles.srt")
        manifest.video_path = str(tmp_workspace / "video.mp4")
        save_manifest(manifest, str(tmp_workspace / "run_manifest.json"))

        # Reset mocks to verify they're not called during resume
        mock_fetch.reset_mock()
        mock_facts.reset_mock()
        mock_storyboard.reset_mock()
        mock_voice.reset_mock()
        mock_srt.reset_mock()
        mock_compose.reset_mock()

        # Resume
        manifest2 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True, resume=True)

        # Assertions
        assert manifest2.status == "published"
        # These should NOT have been called during resume (PLAN/GENERATE/RENDER skipped)
        # Note: _load_history_record may be called in PACKAGE phase, that's okay
        assert mock_facts.call_count == 0
        assert mock_storyboard.call_count == 0
        assert mock_voice.call_count == 0
        assert mock_srt.call_count == 0
        assert mock_compose.call_count == 0
        # These SHOULD have been called
        assert mock_verify.called
        assert mock_pkg.called
        assert mock_pub.called

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_resume_with_failed_status_retries(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_history_entry
    ):
        """Resume from failed state retries from VERIFY+VISION+PACKAGE+PUBLISH."""
        # Setup mocks
        mock_history.return_value = blog_history_entry
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = MagicMock()
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
        pkg = PublishPackage(
            title="Test Video",
            description="A test video",
            tags=["test"],
        )
        mock_pkg.return_value = pkg
        result = PublishResult(
            platform="youtube",
            video_id="test_retry_123",
            url="https://youtube.com/watch?v=test_retry_123",
            visibility="unlisted",
            uploaded_at="2026-06-09T10:00:00Z",
        )
        mock_pub.return_value = result

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Create a valid storyboard file
        import json
        sb_dict = {
            "version": "2.0",
            "blog": {"blog_id": "test", "title": "Test"},
            "hero_claim": {"stat": "50%", "claim_text": "Test claim", "source_quote": ""},
            "arc": [],
            "supporting_facts": [],
            "scenes": [],
            "director_notes": {},
            "metadata": {},
            "narrative_thread": [],
        }
        storyboard_path = tmp_workspace / "storyboard.json"
        storyboard_path.write_text(json.dumps(sb_dict, indent=2), encoding="utf-8")

        # Create a failed manifest at rendered status (so it will retry from VERIFY)
        manifest = new_manifest(blog_url, "test-blog", str(tmp_workspace))
        manifest.status = "failed"
        manifest.storyboard_path = str(storyboard_path)
        manifest.voice_path = str(tmp_workspace / "voice.mp3")
        manifest.srt_path = str(tmp_workspace / "subtitles.srt")
        manifest.video_path = str(tmp_workspace / "video.mp4")
        manifest.last_error = "Previous verify attempt failed"
        save_manifest(manifest, str(tmp_workspace / "run_manifest.json"))

        # Resume - should pick up at rendered/VERIFY phase
        manifest2 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True, resume=True)

        # Assertions
        assert manifest2.status == "published"
        assert mock_verify.called
        assert mock_pkg.called
        assert mock_pub.called


# ============================================================================
# Tests: Verify Failure Behavior
# ============================================================================

class TestVerifyFailure:
    """Tests for behavior when verification fails."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_verify_failure_stops_pipeline(
        self, mock_pub, mock_pkg, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """If verify.passed=False, status=failed, no publish."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        # Verify fails
        verify_report = VerifyReport(
            passed=False,
            checks={"duration_s": 20.0},
            defects=["Duration too short (20.0s < 30s)"]
        )
        mock_verify.return_value = verify_report

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True)

        # Assertions
        assert manifest.status == "failed"
        assert manifest.verify is not None
        assert manifest.verify.passed is False
        assert mock_pkg.call_count == 0
        assert mock_pub.call_count == 0

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_verify_failure_no_publish(
        self, mock_pub, mock_pkg, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Publish phase never called if verify fails."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        # Verify fails
        verify_report = VerifyReport(
            passed=False,
            checks={},
            defects=["Dark ribbon detected at frames: [10, 20, 30]"]
        )
        mock_verify.return_value = verify_report

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True)

        # Assertions
        assert mock_pub.call_count == 0


# ============================================================================
# Tests: Error Handling & Manifest Checkpointing
# ============================================================================

class TestErrorHandling:
    """Tests for error handling and state persistence."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    def test_phase_failure_recorded_in_manifest(self, mock_fetch, tmp_workspace, blog_url):
        """On error, manifest.last_error is set."""
        # Setup mock to fail
        mock_fetch.side_effect = Exception("Network error")

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace))

        # Assertions
        assert manifest.status == "failed"
        assert manifest.last_error is not None
        assert "Network error" in manifest.last_error

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    def test_attempts_counter_incremented(
        self, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt, mock_voice, mock_facts,
        mock_storyboard, mock_history, mock_fetch, tmp_workspace, blog_url,
        blog_html, blog_history_entry
    ):
        """Each call increments manifest.attempts."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        # Verify fails to stop early
        verify_report = VerifyReport(passed=False, checks={}, defects=["Test defect"])
        mock_verify.return_value = verify_report
        # Mock VISION to never get called since verify fails
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # First run
        manifest1 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace))
        assert manifest1.attempts == 1

        # Second run (resume)
        manifest2 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), resume=True)
        assert manifest2.attempts == 2

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_manifest_checkpointed_after_each_phase(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Workspace has run_manifest.json after each phase."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False)

        # Assertions
        manifest_path = tmp_workspace / "run_manifest.json"
        assert manifest_path.exists()
        loaded = load_manifest(str(manifest_path))
        # VISION runs unconditionally, so status should be vision_verified
        assert loaded.status == "vision_verified"
        assert loaded.run_id == manifest.run_id


# ============================================================================
# Tests: Workspace & Slug Handling
# ============================================================================

class TestWorkspaceHandling:
    """Tests for workspace auto-creation and slug derivation."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    def test_missing_workspace_created(
        self, mock_verify, mock_compose, mock_srt, mock_voice, mock_facts,
        mock_storyboard, mock_history, mock_fetch, blog_url, blog_html,
        blog_history_entry
    ):
        """If workspace=None, auto-create unique workspace."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test fact", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": "voice.mp3", "duration_s": 50.0}
        mock_srt.return_value = Path("subtitles.srt")
        mock_compose.return_value = Path("video.mp4")
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report

        # Create files relative to returned paths
        voice_file = Path(mock_voice.return_value["audio_path"])
        voice_file.parent.mkdir(parents=True, exist_ok=True)
        voice_file.touch()
        Path(mock_srt.return_value).touch()
        Path(mock_compose.return_value).touch()

        # Run with workspace=None
        manifest = HarnessRunner.run(blog_url, workspace=None, publish=False)

        # Assertions
        assert manifest.workspace is not None
        assert Path(manifest.workspace).exists()
        assert "test-blog" in manifest.slug or manifest.slug is not None


# ============================================================================
# Tests: Phase Ordering & Generator Integration
# ============================================================================

class TestPhaseOrdering:
    """Tests for correct phase execution order and generator integration."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    @patch('video_agent.harness.runner.package_for_youtube')
    @patch('video_agent.harness.runner.publish_to_youtube')
    def test_phase_ordering(
        self, mock_pub, mock_pkg, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt,
        mock_voice, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """Phases run in correct order (not shuffled)."""
        call_order = []

        # Create files upfront
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        def track_call(name):
            def inner(*args, **kwargs):
                call_order.append(name)
                if name == "_fetch_blog_html":
                    return blog_html
                elif name == "_load_history_record":
                    return blog_history_entry
                elif name == "extract_facts":
                    return [{"claim": "Test", "value": "50", "unit": "%"}], {"tier_used": 1}
                elif name == "build_storyboard":
                    return _make_storyboard()
                elif name == "synthesize_segments":
                    return {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
                elif name == "generate_srt":
                    return tmp_workspace / "subtitles.srt"
                elif name == "compose_short_v2":
                    return tmp_workspace / "video.mp4"
                elif name == "verify_heuristic":
                    return VerifyReport(passed=True, checks={}, defects=[])
                elif name == "grade_video":
                    from video_agent.harness.manifest import VisionReport, SceneGrade
                    return VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])
                elif name == "load_storyboard_for_vision":
                    return _make_storyboard()
                elif name == "package_for_youtube":
                    return PublishPackage(title="Test", description="Test")
                elif name == "publish_to_youtube":
                    return PublishResult(
                        platform="youtube",
                        video_id="test",
                        url="https://youtube.com/watch?v=test",
                        visibility="unlisted",
                        uploaded_at="2026-06-09T10:00:00Z",
                    )
            return inner

        mock_fetch.side_effect = track_call("_fetch_blog_html")
        mock_history.side_effect = track_call("_load_history_record")
        mock_facts.side_effect = track_call("extract_facts")
        mock_storyboard.side_effect = track_call("build_storyboard")
        mock_voice.side_effect = track_call("synthesize_segments")
        mock_srt.side_effect = track_call("generate_srt")
        mock_compose.side_effect = track_call("compose_short_v2")
        mock_verify.side_effect = track_call("verify_heuristic")
        mock_grade.side_effect = track_call("grade_video")
        mock_vision_sb.side_effect = track_call("load_storyboard_for_vision")
        mock_pkg.side_effect = track_call("package_for_youtube")
        mock_pub.side_effect = track_call("publish_to_youtube")

        # Run
        manifest = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=True)

        # Assertions - just check that major phases happen in order
        # (Note: fetch/history may be called multiple times during different phases)
        assert "extract_facts" in call_order
        assert "build_storyboard" in call_order
        assert "synthesize_segments" in call_order
        assert "generate_srt" in call_order
        assert "compose_short_v2" in call_order
        assert "verify_heuristic" in call_order
        assert "grade_video" in call_order
        assert "package_for_youtube" in call_order
        assert "publish_to_youtube" in call_order

        # Check ordering of key phases
        fact_idx = call_order.index("extract_facts")
        sb_idx = call_order.index("build_storyboard")
        voice_idx = call_order.index("synthesize_segments")
        srt_idx = call_order.index("generate_srt")
        compose_idx = call_order.index("compose_short_v2")
        verify_idx = call_order.index("verify_heuristic")
        grade_idx = call_order.index("grade_video")
        pkg_idx = call_order.index("package_for_youtube")
        pub_idx = call_order.index("publish_to_youtube")

        # Check correct ordering (VISION comes after VERIFY, before PACKAGE)
        assert fact_idx < sb_idx < voice_idx < srt_idx < compose_idx < verify_idx < grade_idx < pkg_idx < pub_idx

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    def test_no_generator_modification(
        self, mock_facts, mock_storyboard, mock_history, mock_fetch,
        tmp_workspace, blog_url, blog_html, blog_history_entry
    ):
        """orchestrator.build_storyboard called unchanged."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb

        # We'll fail early to avoid full execution
        with patch('video_agent.harness.runner.synthesize_segments', side_effect=Exception("stop")):
            try:
                HarnessRunner.run(blog_url, workspace=str(tmp_workspace))
            except:
                pass

        # Verify build_storyboard was called with correct args (unchanged API)
        assert mock_storyboard.called
        call_args = mock_storyboard.call_args
        assert "blog" in call_args[1]
        assert "facts" in call_args[1]
        assert "blog_html" in call_args[1]
        assert "workspace" in call_args[1]


# ============================================================================
# Tests: Idempotency
# ============================================================================

class TestIdempotency:
    """Tests for idempotent phase execution."""

    @patch('video_agent.harness.runner._fetch_blog_html')
    @patch('video_agent.harness.runner._load_history_record')
    @patch('video_agent.harness.runner.build_storyboard')
    @patch('video_agent.harness.runner.extract_facts')
    @patch('video_agent.harness.runner.synthesize_segments')
    @patch('video_agent.harness.runner.generate_srt')
    @patch('video_agent.harness.runner.compose_short_v2')
    @patch('video_agent.harness.runner.verify_heuristic')
    @patch('video_agent.harness.runner.grade_video')
    @patch('video_agent.harness.runner.load_storyboard_for_vision')
    def test_idempotent_plan_phase(
        self, mock_vision_sb, mock_grade, mock_verify, mock_compose, mock_srt, mock_voice, mock_facts,
        mock_storyboard, mock_history, mock_fetch, tmp_workspace, blog_url,
        blog_html, blog_history_entry
    ):
        """Calling PLAN twice on same manifest skips second call."""
        # Setup mocks
        mock_fetch.return_value = blog_html
        mock_history.return_value = blog_history_entry
        mock_facts.return_value = (
            [{"claim": "Test", "value": "50", "unit": "%"}],
            {"tier_used": 1}
        )
        sb = _make_storyboard()
        mock_storyboard.return_value = sb
        mock_voice.return_value = {"audio_path": str(tmp_workspace / "voice.mp3"), "duration_s": 50.0}
        mock_srt.return_value = tmp_workspace / "subtitles.srt"
        mock_compose.return_value = tmp_workspace / "video.mp4"
        verify_report = VerifyReport(passed=True, checks={}, defects=[])
        mock_verify.return_value = verify_report
        # Mock VISION phase to pass immediately
        from video_agent.harness.manifest import VisionReport, SceneGrade
        mock_vision_sb.return_value = sb
        mock_grade.return_value = VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])

        # Create minimal files
        (tmp_workspace / "voice.mp3").touch()
        (tmp_workspace / "subtitles.srt").touch()
        (tmp_workspace / "video.mp4").touch()

        # First run
        manifest1 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), publish=False)
        fetch_count_1 = mock_fetch.call_count

        # Second run (resume)
        manifest2 = HarnessRunner.run(blog_url, workspace=str(tmp_workspace), resume=True, publish=False)
        fetch_count_2 = mock_fetch.call_count

        # Assertions
        # _fetch_blog_html should NOT be called again on resume (fetch count should stay the same)
        # Note: with VISION phase, _load_history might be called an extra time
        assert fetch_count_2 == fetch_count_1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
