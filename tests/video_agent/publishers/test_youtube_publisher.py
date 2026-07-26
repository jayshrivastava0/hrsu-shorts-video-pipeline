"""
TDD tests for YouTube video publisher.

Tests verify:
1. OAuth setup and token handling (separate youtube_token.json)
2. Dry-run validation (metadata check, no API call, synthetic video_id)
3. Video upload (resumable, chunked)
4. Thumbnail upload (1280×720 validation, optional)
5. Caption upload (SRT, optional)
6. Error handling (quota, rate limit, missing files)
7. Result tracking (video_history.json)
8. Metadata validation (title length, description length, etc.)
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime, timezone

from video_agent.publishers.youtube_publisher import (
    publish_to_youtube,
    _build_service,
    _get_caption_language,
    _upload_video_resumable,
    _set_thumbnail,
    _insert_captions,
    _save_publish_result,
    UPLOAD_CHUNK_SIZE,
)
from video_agent.harness.manifest import PublishPackage, PublishResult
from googleapiclient.errors import HttpError


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def dummy_video(tmp_path):
    """Create a dummy video file (1MB)."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"dummy_video_content" * 50000)  # ~1MB
    return video_path


@pytest.fixture
def dummy_thumbnail_1280x720(tmp_path):
    """Create a real 1280×720 JPEG thumbnail using PIL."""
    try:
        from PIL import Image
        thumb_path = tmp_path / "thumbnail.jpg"
        img = Image.new("RGB", (1280, 720), color=(10, 25, 47))
        img.save(thumb_path, "JPEG")
        return thumb_path
    except ImportError:
        pytest.skip("Pillow not available")


@pytest.fixture
def dummy_thumbnail_wrong_size(tmp_path):
    """Create a JPEG thumbnail with wrong dimensions (800×600)."""
    try:
        from PIL import Image
        thumb_path = tmp_path / "thumbnail_wrong.jpg"
        img = Image.new("RGB", (800, 600), color=(10, 25, 47))
        img.save(thumb_path, "JPEG")
        return thumb_path
    except ImportError:
        pytest.skip("Pillow not available")


@pytest.fixture
def dummy_srt(tmp_path):
    """Create a dummy SRT subtitle file."""
    srt_path = tmp_path / "subtitles.srt"
    srt_content = """1
00:00:00,000 --> 00:00:05,000
This is the first subtitle.

2
00:00:05,000 --> 00:00:10,000
This is the second subtitle.
"""
    srt_path.write_text(srt_content)
    return srt_path


@pytest.fixture
def publish_package():
    """Create a standard PublishPackage for testing."""
    return PublishPackage(
        title="Test Video Title: How to Optimize Calcium Nitrate Dosage",
        description="Learn the best practices for using calcium nitrate in wastewater treatment systems.\n\nLearn more: https://hrsuindore.com/\n\n#Shorts #Chemistry",
        tags=["calcium nitrate", "wastewater", "chemistry"],
        category_id="28",
        thumbnail_path=None,
        caption_srt_path=None,
        privacy_status="unlisted",
    )


# ─── Tests: Dry-run ────────────────────────────────────────────────────────

def test_dry_run_no_api_call(publish_package, tmp_workspace, dummy_video):
    """Dry-run validates metadata and returns synthetic result without API call."""
    with patch('video_agent.publishers.youtube_publisher.build') as mock_build:
        result = publish_to_youtube(
            package=publish_package,
            video_path=str(dummy_video),
            workspace=str(tmp_workspace),
            dry_run=True,
        )

        # Verify no API call was made
        mock_build.assert_not_called()

        # Verify synthetic result
        assert result.platform == "youtube"
        assert result.video_id.startswith("DRY_RUN_")
        assert result.url.startswith("https://youtube.com/watch?v=DRY_RUN_")
        assert result.visibility == "unlisted"


def test_dry_run_with_nonexistent_video(publish_package, tmp_workspace):
    """Dry-run with nonexistent video returns result without checking file."""
    with patch('video_agent.publishers.youtube_publisher.build') as mock_build:
        result = publish_to_youtube(
            package=publish_package,
            video_path="/nonexistent/video.mp4",
            workspace=str(tmp_workspace),
            dry_run=True,
        )

        # Should not raise FileNotFoundError in dry-run
        assert result.video_id.startswith("DRY_RUN_")
        mock_build.assert_not_called()


def test_dry_run_result_structure(publish_package, tmp_workspace, dummy_video):
    """Dry-run result has all required PublishResult fields."""
    with patch('video_agent.publishers.youtube_publisher.build'):
        result = publish_to_youtube(
            package=publish_package,
            video_path=str(dummy_video),
            workspace=str(tmp_workspace),
            dry_run=True,
        )

        assert result.platform == "youtube"
        assert result.video_id is not None
        assert result.url is not None
        assert result.visibility is not None
        assert result.uploaded_at is not None


def test_dry_run_result_timestamp_iso(publish_package, tmp_workspace, dummy_video):
    """Dry-run result uploaded_at is ISO 8601 format."""
    with patch('video_agent.publishers.youtube_publisher.build'):
        result = publish_to_youtube(
            package=publish_package,
            video_path=str(dummy_video),
            workspace=str(tmp_workspace),
            dry_run=True,
        )

        # Verify ISO format (should parse without error)
        try:
            datetime.fromisoformat(result.uploaded_at.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Invalid ISO timestamp: {result.uploaded_at}")


def test_dry_run_no_history_write(publish_package, tmp_workspace, dummy_video):
    """Dry-run does not write to video_history.json."""
    with patch('video_agent.publishers.youtube_publisher.build'):
        result = publish_to_youtube(
            package=publish_package,
            video_path=str(dummy_video),
            workspace=str(tmp_workspace),
            dry_run=True,
        )

        history_path = tmp_workspace / "video_history.json"
        assert not history_path.exists(), "Dry-run should not write to video_history.json"


# ─── Tests: Metadata Validation ────────────────────────────────────────────

def test_missing_title_raises(tmp_workspace, dummy_video):
    """Missing title raises ValueError."""
    pkg = PublishPackage(
        title="",  # Empty
        description="Test description",
        tags=["test"],
        category_id="28",
        privacy_status="unlisted",
    )

    with pytest.raises(ValueError, match="title is required"):
        publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)


def test_missing_description_raises(tmp_workspace, dummy_video):
    """Missing description raises ValueError."""
    pkg = PublishPackage(
        title="Test Title",
        description="",  # Empty
        tags=["test"],
        category_id="28",
        privacy_status="unlisted",
    )

    with pytest.raises(ValueError, match="description is required"):
        publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)


def test_title_too_long_raises(tmp_workspace, dummy_video):
    """Title exceeding 100 chars raises ValueError."""
    pkg = PublishPackage(
        title="A" * 101,  # 101 chars
        description="Test description",
        tags=["test"],
        category_id="28",
        privacy_status="unlisted",
    )

    with pytest.raises(ValueError, match="Title must be ≤100 chars"):
        publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)


def test_description_too_long_raises(tmp_workspace, dummy_video):
    """Description exceeding 4900 chars raises ValueError."""
    pkg = PublishPackage(
        title="Test Title",
        description="B" * 4901,  # 4901 chars
        tags=["test"],
        category_id="28",
        privacy_status="unlisted",
    )

    with pytest.raises(ValueError, match="Description must be ≤4900 chars"):
        publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)


# ─── Tests: File Existence ─────────────────────────────────────────────────

def test_video_file_not_found_raises(publish_package, tmp_workspace):
    """Missing video file raises FileNotFoundError."""
    with patch('video_agent.publishers.youtube_publisher._build_service'):
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            publish_to_youtube(
                package=publish_package,
                video_path="/nonexistent/video.mp4",
                workspace=str(tmp_workspace),
                dry_run=False,
            )


# ─── Tests: OAuth and Service Building ─────────────────────────────────────

@patch('video_agent.publishers.youtube_publisher.InstalledAppFlow')
@patch('video_agent.publishers.youtube_publisher.Credentials')
@patch('video_agent.publishers.youtube_publisher.build')
def test_build_service_uses_installed_app_flow(mock_build, mock_creds_class, mock_flow_class, tmp_path):
    """_build_service uses InstalledAppFlow for OAuth."""
    # Mock the flow and credentials
    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.expired = False
    mock_creds.refresh_token = None

    mock_flow_class.from_client_secrets_file.return_value = mock_flow
    mock_flow.run_local_server.return_value = mock_creds
    mock_creds.to_json.return_value = '{"test": "json"}'

    # Patch token path to tmp
    with patch('video_agent.publishers.youtube_publisher.YOUTUBE_TOKEN_PATH', str(tmp_path / "token.json")):
        service = _build_service()

        mock_build.assert_called_once()
        assert service is not None


@patch('video_agent.publishers.youtube_publisher.Credentials')
@patch('video_agent.publishers.youtube_publisher.build')
def test_build_service_loads_existing_token(mock_build, mock_creds_class, tmp_path):
    """_build_service loads and reuses saved token."""
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"token": "test_token"}')

    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds_class.from_authorized_user_file.return_value = mock_creds

    with patch('video_agent.publishers.youtube_publisher.YOUTUBE_TOKEN_PATH', str(token_path)):
        service = _build_service()

        mock_creds_class.from_authorized_user_file.assert_called_once()
        mock_build.assert_called_once()


@patch('video_agent.publishers.youtube_publisher.build')
def test_build_service_token_saved_separately(mock_build, tmp_path):
    """Token saved to youtube_token.json, not blogger_token.json."""
    # Arrange
    token_path = tmp_path / "youtube_token.json"
    secrets_path = tmp_path / "client_secrets.json"

    # Create dummy secrets file
    secrets_path.write_text('{"installed": {"client_id": "test"}}')

    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.to_json.return_value = '{"token": "test"}'

    with patch('video_agent.publishers.youtube_publisher.InstalledAppFlow') as mock_flow_class:
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_class.from_client_secrets_file.return_value = mock_flow

        with patch('video_agent.publishers.youtube_publisher.YOUTUBE_TOKEN_PATH', str(token_path)):
            with patch('video_agent.publishers.youtube_publisher.YOUTUBE_CLIENT_SECRETS', str(secrets_path)):
                service = _build_service()

                # Verify token saved to youtube_token.json
                assert token_path.exists(), "youtube_token.json should exist"
                assert token_path.read_text() == '{"token": "test"}'


# ─── Tests: Thumbnail Handling ─────────────────────────────────────────────

def test_missing_thumbnail_skipped(publish_package, tmp_workspace, dummy_video):
    """Missing thumbnail logged as warning, upload continues."""
    pkg = PublishPackage(
        title=publish_package.title,
        description=publish_package.description,
        tags=publish_package.tags,
        category_id="28",
        thumbnail_path="/nonexistent/thumbnail.jpg",  # Doesn't exist
        caption_srt_path=None,
        privacy_status="unlisted",
    )

    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            with patch('video_agent.publishers.youtube_publisher._set_thumbnail') as mock_set_thumb:
                mock_service = MagicMock()
                mock_service_build.return_value = mock_service
                mock_upload.return_value = "test_video_id"

                result = publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)

                # _set_thumbnail should be called but handle the missing file gracefully
                assert result.video_id == "test_video_id"
                # Upload should succeed despite missing thumbnail
                mock_upload.assert_called_once()


def test_invalid_thumbnail_dimensions_raises(publish_package, tmp_workspace, dummy_video, dummy_thumbnail_wrong_size):
    """Thumbnail with wrong dimensions (not 1280×720) raises ValueError before upload."""
    pkg = PublishPackage(
        title=publish_package.title,
        description=publish_package.description,
        tags=publish_package.tags,
        category_id="28",
        thumbnail_path=str(dummy_thumbnail_wrong_size),
        caption_srt_path=None,
        privacy_status="unlisted",
    )

    with pytest.raises(ValueError, match="Thumbnail must be 1280×720"):
        publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)


# ─── Tests: Caption Handling ────────────────────────────────────────────

def test_missing_srt_skipped(publish_package, tmp_workspace, dummy_video):
    """Missing SRT logged as warning, upload continues."""
    pkg = PublishPackage(
        title=publish_package.title,
        description=publish_package.description,
        tags=publish_package.tags,
        category_id="28",
        thumbnail_path=None,
        caption_srt_path="/nonexistent/subtitles.srt",  # Doesn't exist
        privacy_status="unlisted",
    )

    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            with patch('video_agent.publishers.youtube_publisher._insert_captions') as mock_captions:
                mock_service = MagicMock()
                mock_service_build.return_value = mock_service
                mock_upload.return_value = "test_video_id"

                result = publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)

                # Upload should succeed despite missing SRT
                assert result.video_id == "test_video_id"


def test_caption_language_from_region():
    """Caption language inferred from blog region."""
    assert _get_caption_language("australia") == "en"
    assert _get_caption_language("usa") == "en"
    assert _get_caption_language("us") == "en"
    assert _get_caption_language("germany") == "de"
    assert _get_caption_language("de") == "de"
    assert _get_caption_language("east_asia") == "en"
    assert _get_caption_language(None) == "en"
    assert _get_caption_language("unknown_region") == "en"  # Default


# ─── Tests: HTTP Error Handling ────────────────────────────────────────────

def test_quota_exceeded_403_raises(dummy_video):
    """Quota exceeded (403) raises HttpError."""
    mock_service = MagicMock()
    metadata = {
        "snippet": {"title": "Test", "description": "Test desc"},
        "status": {"privacyStatus": "unlisted"},
    }

    # Mock request that raises 403
    mock_request = MagicMock()
    mock_service.videos().insert.return_value = mock_request

    resp = Mock()
    resp.status = 403
    error = HttpError(resp, b"Quota exceeded")
    mock_request.next_chunk.side_effect = error

    with patch('video_agent.publishers.youtube_publisher.MediaFileUpload'):
        with pytest.raises(HttpError):
            _upload_video_resumable(mock_service, str(dummy_video), metadata)


def test_rate_limit_429_raises(dummy_video):
    """Rate limit (429) raises HttpError."""
    mock_service = MagicMock()
    metadata = {
        "snippet": {"title": "Test", "description": "Test desc"},
        "status": {"privacyStatus": "unlisted"},
    }

    # Mock request that raises 429
    mock_request = MagicMock()
    mock_service.videos().insert.return_value = mock_request

    resp = Mock()
    resp.status = 429
    error = HttpError(resp, b"Rate limited")
    mock_request.next_chunk.side_effect = error

    with patch('video_agent.publishers.youtube_publisher.MediaFileUpload'):
        with pytest.raises(HttpError):
            _upload_video_resumable(mock_service, str(dummy_video), metadata)


# ─── Tests: Resumable Upload ────────────────────────────────────────────

def test_resumable_upload_chunk_size():
    """Resumable upload uses 10MB chunk size."""
    assert UPLOAD_CHUNK_SIZE == 10 * 1024 * 1024


def test_resumable_upload_progress_logged(dummy_video):
    """Upload progress logged at each chunk."""
    mock_service = MagicMock()
    metadata = {
        "snippet": {"title": "Test", "description": "Test desc"},
        "status": {"privacyStatus": "unlisted"},
    }

    # Mock request and chunks
    mock_request = MagicMock()
    mock_service.videos().insert.return_value = mock_request

    # Simulate 3 chunks: 0%, 50%, 100%
    mock_status_1 = MagicMock()
    mock_status_1.progress.return_value = 0.0
    mock_status_2 = MagicMock()
    mock_status_2.progress.return_value = 0.5
    mock_response = {"id": "test_video_id"}

    mock_request.next_chunk.side_effect = [
        (mock_status_1, None),
        (mock_status_2, None),
        (None, mock_response),
    ]

    with patch('video_agent.publishers.youtube_publisher.MediaFileUpload'):
        video_id = _upload_video_resumable(mock_service, str(dummy_video), metadata)

        assert video_id == "test_video_id"
        # Verify next_chunk was called for each chunk
        assert mock_request.next_chunk.call_count == 3


# ─── Tests: Result Tracking ────────────────────────────────────────────────

def test_publish_result_appended_to_history(publish_package, tmp_workspace, dummy_video):
    """PublishResult appended to video_history.json."""
    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            mock_service = MagicMock()
            mock_service_build.return_value = mock_service
            mock_upload.return_value = "test_video_id_123"

            result = publish_to_youtube(
                publish_package,
                str(dummy_video),
                str(tmp_workspace),
                dry_run=False,
            )

            # Verify history file created
            history_path = tmp_workspace / "video_history.json"
            assert history_path.exists()

            # Verify entry in history
            history = json.load(open(history_path))
            assert len(history) == 1
            assert history[0]["platform"] == "youtube"
            assert history[0]["video_id"] == "test_video_id_123"
            assert history[0]["url"] == "https://youtube.com/watch?v=test_video_id_123"
            assert history[0]["visibility"] == "unlisted"


def test_publish_result_url_format(publish_package, tmp_workspace, dummy_video):
    """PublishResult URL is https://youtube.com/watch?v=VIDEO_ID."""
    with patch('video_agent.publishers.youtube_publisher._build_service'):
        result = publish_to_youtube(
            publish_package,
            str(dummy_video),
            str(tmp_workspace),
            dry_run=True,
        )

        assert result.url.startswith("https://youtube.com/watch?v=")
        video_id = result.url.replace("https://youtube.com/watch?v=", "")
        assert video_id == result.video_id


def test_publish_result_timestamp_iso_format(publish_package, tmp_workspace, dummy_video):
    """PublishResult uploaded_at is ISO 8601."""
    with patch('video_agent.publishers.youtube_publisher._build_service'):
        result = publish_to_youtube(
            publish_package,
            str(dummy_video),
            str(tmp_workspace),
            dry_run=True,
        )

        # Should parse as ISO datetime
        try:
            datetime.fromisoformat(result.uploaded_at.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Invalid ISO timestamp: {result.uploaded_at}")


def test_multiple_publish_results_in_history(publish_package, tmp_workspace, dummy_video):
    """Multiple publish results accumulate in video_history.json."""
    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            mock_service = MagicMock()
            mock_service_build.return_value = mock_service

            # First publish
            mock_upload.return_value = "video_id_1"
            publish_to_youtube(publish_package, str(dummy_video), str(tmp_workspace), dry_run=False)

            # Second publish
            mock_upload.return_value = "video_id_2"
            publish_to_youtube(publish_package, str(dummy_video), str(tmp_workspace), dry_run=False)

            # Verify both in history
            history_path = tmp_workspace / "video_history.json"
            history = json.load(open(history_path))
            assert len(history) == 2
            assert history[0]["video_id"] == "video_id_1"
            assert history[1]["video_id"] == "video_id_2"


# ─── Tests: Metadata Passed to API ────────────────────────────────────────

def test_metadata_sent_to_api(publish_package, tmp_workspace, dummy_video):
    """Package metadata sent exactly to YouTube API."""
    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        mock_service = MagicMock()
        mock_service_build.return_value = mock_service

        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            mock_upload.return_value = "test_video_id"

            publish_to_youtube(publish_package, str(dummy_video), str(tmp_workspace), dry_run=False)

            # Verify metadata passed to _upload_video_resumable
            assert mock_upload.called
            call_args = mock_upload.call_args
            metadata = call_args[0][2]

            assert metadata["snippet"]["title"] == publish_package.title
            assert metadata["snippet"]["description"] == publish_package.description
            assert metadata["snippet"]["tags"] == publish_package.tags
            assert metadata["snippet"]["categoryId"] == "28"
            assert metadata["status"]["privacyStatus"] == "unlisted"


def test_privacy_status_respected(tmp_workspace, dummy_video):
    """Privacy status from package is used in upload."""
    pkg = PublishPackage(
        title="Test",
        description="Test desc",
        tags=[],
        category_id="28",
        privacy_status="private",  # Different from default
    )

    with patch('video_agent.publishers.youtube_publisher._build_service') as mock_service_build:
        mock_service = MagicMock()
        mock_service_build.return_value = mock_service

        with patch('video_agent.publishers.youtube_publisher._upload_video_resumable') as mock_upload:
            mock_upload.return_value = "test_video_id"

            result = publish_to_youtube(pkg, str(dummy_video), str(tmp_workspace), dry_run=False)

            # Verify result has correct privacy status
            assert result.visibility == "private"

            # Verify API called with correct privacy status
            call_args = mock_upload.call_args
            metadata = call_args[0][2]
            assert metadata["status"]["privacyStatus"] == "private"


# ─── Tests: Helper Functions ────────────────────────────────────────────

def test_save_publish_result_creates_file(tmp_path):
    """_save_publish_result creates video_history.json."""
    result = PublishResult(
        platform="youtube",
        video_id="test_id_123",
        url="https://youtube.com/watch?v=test_id_123",
        visibility="unlisted",
        uploaded_at="2026-06-09T12:00:00Z",
    )

    _save_publish_result(result, str(tmp_path))

    history_path = tmp_path / "video_history.json"
    assert history_path.exists()
    history = json.load(open(history_path))
    assert history[0]["video_id"] == "test_id_123"


def test_save_publish_result_appends_to_existing(tmp_path):
    """_save_publish_result appends to existing history."""
    # Create initial history
    history_path = tmp_path / "video_history.json"
    initial = [{"platform": "youtube", "video_id": "existing_id"}]
    history_path.write_text(json.dumps(initial))

    # Append new result
    result = PublishResult(
        platform="youtube",
        video_id="new_id",
        url="https://youtube.com/watch?v=new_id",
        visibility="unlisted",
        uploaded_at="2026-06-09T12:00:00Z",
    )
    _save_publish_result(result, str(tmp_path))

    # Verify both in history
    history = json.load(open(history_path))
    assert len(history) == 2
    assert history[0]["video_id"] == "existing_id"
    assert history[1]["video_id"] == "new_id"
