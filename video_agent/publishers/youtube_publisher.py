"""
YouTube video publisher for video harness pipeline.

Uploads videos to YouTube using Data API v3 with:
- Resumable uploads (chunked, 10MB per chunk)
- Custom thumbnails
- SRT captions
- Separate OAuth token (youtube_token.json, not Blogger token)
- Dry-run mode validation

Error handling:
- Missing optional files (thumbnail, SRT) logged as warnings, upload continues
- Invalid thumbnail dimensions (not 1280×720) raises ValueError before upload
- Missing video file raises FileNotFoundError
- Quota exceeded (403) and rate limits (429) propagate with clear messages
- Resumable upload has built-in retries (up to 3 attempts on network error)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from video_agent.config import (
    YOUTUBE_UPLOAD_SCOPES,
    YOUTUBE_CLIENT_SECRETS,
    YOUTUBE_TOKEN_PATH,
)
from video_agent.harness.manifest import PublishPackage, PublishResult

log = logging.getLogger(__name__)

# Chunk size for resumable upload: 10 MB
UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024


def _build_service():
    """
    Build YouTube API v3 client with OAuth2.

    Uses InstalledAppFlow to obtain credentials (user interaction via browser).
    Token stored in YOUTUBE_TOKEN_PATH (separate from Blogger token).
    Refreshes expired token automatically.

    Returns:
        googleapiclient.discovery.Resource: YouTube API v3 client

    Raises:
        Exception: If OAuth flow fails or credentials cannot be obtained
    """
    token_path = Path(YOUTUBE_TOKEN_PATH)
    secrets_path = Path(YOUTUBE_CLIENT_SECRETS)

    creds = None

    # Load saved token if it exists
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_UPLOAD_SCOPES)
            log.debug("Loaded YouTube credentials from %s", token_path)
        except Exception as e:
            log.warning("Failed to load YouTube token: %s, will re-authenticate", e)
            creds = None

    # Refresh or obtain new credentials
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                log.info("Refreshed expired YouTube token")
            except Exception as e:
                log.warning("Token refresh failed: %s, will re-authenticate", e)
                creds = None

    # If no valid token, run OAuth flow
    if not creds:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets_path), YOUTUBE_UPLOAD_SCOPES
            )
            creds = flow.run_local_server(port=0)
            # Save token for next run
            token_path.write_text(creds.to_json())
            log.info("Obtained new YouTube token and saved to %s", token_path)
        except Exception as e:
            raise RuntimeError(
                f"YouTube OAuth failed: {e}. Ensure {secrets_path} exists and "
                "you can interact with a browser. "
                "Check CLAUDE.md for setup instructions."
            ) from e

    # Build service
    try:
        service = build("youtube", "v3", credentials=creds)
        log.debug("Built YouTube API v3 client")
        return service
    except Exception as e:
        raise RuntimeError(f"Failed to build YouTube API client: {e}") from e


def _get_caption_language(region: Optional[str] = None) -> str:
    """
    Map blog region to YouTube caption language code.

    Args:
        region: Blog region (e.g., "australia", "usa", "germany")

    Returns:
        Language code (e.g., "en", "de"). Defaults to "en" if region unknown.
    """
    if not region:
        return "en"

    # Map regions to language codes
    region_lower = region.lower()
    language_map = {
        "australia": "en",
        "usa": "en",
        "us": "en",
        "eu": "en",
        "uk": "en",
        "germany": "de",
        "de": "de",
        "east_asia": "en",
        "gulf": "en",
        "ae": "en",
    }

    return language_map.get(region_lower, "en")


def _upload_video_resumable(service, video_path: str, metadata: dict) -> str:
    """
    Upload video to YouTube using resumable upload (chunked).

    Args:
        service: YouTube API v3 client
        video_path: Path to video file
        metadata: Video metadata dict (snippet, status, etc.)

    Returns:
        video_id: YouTube video ID

    Raises:
        HttpError: If upload fails (quota, rate limit, etc.)
        Exception: If file operations fail
    """
    video_path = Path(video_path)

    # Create resumable media upload
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=UPLOAD_CHUNK_SIZE,
        resumable=True,
    )

    # Build insert request
    request = service.videos().insert(
        part="snippet,status",
        body=metadata,
        media_body=media,
    )

    # Upload with retry logic (up to 3 attempts on transient errors)
    max_retries = 3
    attempt = 0

    while True:
        attempt += 1
        try:
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    # Log progress
                    progress_pct = int(status.progress() * 100)
                    log.info("Upload progress: %d%%", progress_pct)

            # Success
            video_id = response.get("id")
            log.info("Video uploaded successfully. ID: %s", video_id)
            return video_id

        except HttpError as e:
            # Handle specific errors
            status_code = e.resp.status

            if status_code == 403:
                # Quota exceeded
                log.error("YouTube quota exceeded (403). Please wait before retrying.")
                raise

            elif status_code == 429:
                # Rate limited
                log.error("YouTube rate limited (429). Please wait a few seconds and retry.")
                raise

            elif status_code >= 500:
                # Server error (transient, retry)
                if attempt < max_retries:
                    log.warning("Server error (5xx) on attempt %d, retrying...", attempt)
                    continue
                else:
                    log.error("Server error after %d attempts", max_retries)
                    raise

            else:
                # Other client error (don't retry)
                log.error("Unrecoverable HTTP error %d: %s", status_code, e)
                raise


def _set_thumbnail(service, video_id: str, thumbnail_path: str) -> None:
    """
    Set custom thumbnail for uploaded video.

    Assumes thumbnail is already validated to be 1280×720 JPEG.
    If file missing, logs warning and returns.

    Args:
        service: YouTube API v3 client
        video_id: YouTube video ID
        thumbnail_path: Path to thumbnail file (validated 1280×720 JPEG)

    Raises:
        HttpError: If API call fails (logged as warning, non-critical)
    """
    thumbnail_path = Path(thumbnail_path)

    if not thumbnail_path.exists():
        log.warning("Thumbnail not found at %s, skipping custom thumbnail", thumbnail_path)
        return

    # Upload thumbnail (dimensions already validated in publish_to_youtube)
    try:
        service.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(
                str(thumbnail_path),
                mimetype="image/jpeg",
                resumable=False,
            ),
        ).execute()
        log.info("Thumbnail set for video %s", video_id)
    except HttpError as e:
        log.warning("Failed to set thumbnail: %s", e)
        # Don't raise — thumbnail is non-critical


def _insert_captions(
    service,
    video_id: str,
    caption_srt_path: str,
    region: Optional[str] = None,
) -> None:
    """
    Upload SRT captions for video.

    Args:
        service: YouTube API v3 client
        video_id: YouTube video ID
        caption_srt_path: Path to SRT file
        region: Blog region (used to infer language)

    Raises:
        HttpError: If API call fails
    """
    caption_srt_path = Path(caption_srt_path)

    if not caption_srt_path.exists():
        log.warning("Caption SRT not found at %s, skipping captions", caption_srt_path)
        return

    try:
        language = _get_caption_language(region)

        # Prepare caption metadata
        caption_body = {
            "snippet": {
                "videoId": video_id,
                "language": language,
                "name": f"English (Transcribed)" if language == "en" else f"{language.upper()} (Transcribed)",
                "isDraft": False,
            }
        }

        # Upload caption
        with open(caption_srt_path, "rb") as f:
            service.captions().insert(
                part="snippet",
                body=caption_body,
                media_body=MediaFileUpload(
                    str(caption_srt_path),
                    mimetype="application/octet-stream",
                    resumable=False,
                ),
            ).execute()

        log.info("Captions uploaded for video %s (language: %s)", video_id, language)

    except HttpError as e:
        log.warning("Failed to upload captions: %s", e)
        # Don't raise — captions are non-critical
    except Exception as e:
        log.warning("Unexpected error uploading captions: %s", e)


def _save_publish_result(result: PublishResult, workspace: str) -> None:
    """
    Append PublishResult to video_history.json for audit.

    Args:
        result: PublishResult to save
        workspace: Workspace directory path
    """
    workspace = Path(workspace)
    history_path = workspace / "video_history.json"

    # Load existing history
    history = []
    if history_path.exists():
        try:
            with open(history_path, "r") as f:
                history = json.load(f)
        except Exception as e:
            log.warning("Failed to load existing history: %s, starting fresh", e)
            history = []

    # Append new result
    result_dict = {
        "platform": result.platform,
        "video_id": result.video_id,
        "url": result.url,
        "visibility": result.visibility,
        "uploaded_at": result.uploaded_at,
    }
    history.append(result_dict)

    # Save updated history
    try:
        with open(history_path, "w") as f:
            json.dump(history, f, indent=2)
        log.info("Appended result to %s", history_path)
    except Exception as e:
        log.error("Failed to save publish result to history: %s", e)


def publish_to_youtube(
    package: PublishPackage,
    video_path: str,
    workspace: str,
    dry_run: bool = False,
) -> PublishResult:
    """
    Upload video to YouTube.

    Flow:
    1. Validate metadata (title, description, tags, etc.)
    2. If dry_run: return synthetic result (video_id="DRY_RUN_<timestamp>")
    3. Else:
       a. Verify video file exists
       b. Build YouTube service (OAuth)
       c. Upload video (resumable, chunked)
       d. Set thumbnail (optional, logs warning if missing)
       e. Upload captions (optional, logs warning if missing)
       f. Create PublishResult and save to video_history.json

    Args:
        package: PublishPackage with title, description, tags, thumbnail_path, caption_srt_path
        video_path: Path to video file (MP4)
        workspace: Workspace directory for output
        dry_run: If True, validate metadata and return synthetic result (no API call)

    Returns:
        PublishResult with:
        - platform: "youtube"
        - video_id: YouTube video ID (or "DRY_RUN_<timestamp>" if dry_run)
        - url: https://youtube.com/watch?v={video_id}
        - visibility: privacy status (e.g., "unlisted")
        - uploaded_at: ISO timestamp

    Raises:
        FileNotFoundError: If video_path doesn't exist
        ValueError: If package metadata is invalid
        HttpError: If upload fails (quota, rate limit, etc.)
    """
    workspace = Path(workspace)

    # ─── Step 1: Validate metadata ───────────────────────────────────────────
    log.info("Publishing to YouTube: title=%s, privacy=%s", package.title[:50], package.privacy_status)

    if not package.title:
        raise ValueError("Package title is required")
    if not package.description:
        raise ValueError("Package description is required")
    if len(package.title) > 100:
        raise ValueError(f"Title must be ≤100 chars, got {len(package.title)}")
    if len(package.description) > 4900:
        raise ValueError(f"Description must be ≤4900 chars, got {len(package.description)}")

    # Validate thumbnail dimensions if provided
    if package.thumbnail_path:
        thumbnail_path = Path(package.thumbnail_path)
        if thumbnail_path.exists():
            try:
                from PIL import Image
                with Image.open(thumbnail_path) as img:
                    width, height = img.size
                    if (width, height) != (1280, 720):
                        raise ValueError(
                            f"Thumbnail must be 1280×720, got {width}×{height}. "
                            f"Resize before uploading."
                        )
            except ValueError:
                # Re-raise validation errors
                raise
            except Exception as e:
                # Other image errors (corrupt file, etc.) are non-fatal
                log.warning("Failed to validate thumbnail: %s, will attempt upload anyway", e)

    log.debug("Metadata validation passed")

    # ─── Step 2: Dry-run return ──────────────────────────────────────────────
    if dry_run:
        timestamp = datetime.now(timezone.utc).isoformat().replace(":", "").replace("-", "")[:14]
        video_id = f"DRY_RUN_{timestamp}"
        result = PublishResult(
            platform="youtube",
            video_id=video_id,
            url=f"https://youtube.com/watch?v={video_id}",
            visibility=package.privacy_status,
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info("DRY-RUN: Would upload video_id=%s, title=%s", video_id, package.title)
        # Don't append to history in dry-run (side-effect-free)
        return result

    # ─── Step 3: Verify video file ───────────────────────────────────────────
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    log.info("Video file verified: %s (%s bytes)", video_path, video_path.stat().st_size)

    # ─── Step 4: Build YouTube service ──────────────────────────────────────
    service = _build_service()

    # ─── Step 5: Upload video ───────────────────────────────────────────────
    metadata = {
        "snippet": {
            "title": package.title,
            "description": package.description,
            "tags": package.tags or [],
            "categoryId": package.category_id,
        },
        "status": {
            "privacyStatus": package.privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        video_id = _upload_video_resumable(service, str(video_path), metadata)
    except HttpError as e:
        log.error("Video upload failed: %s", e)
        raise

    # ─── Step 6: Set thumbnail (optional) ────────────────────────────────────
    if package.thumbnail_path:
        try:
            _set_thumbnail(service, video_id, package.thumbnail_path)
        except Exception as e:
            log.warning("Thumbnail setup failed (non-critical): %s", e)

    # ─── Step 7: Upload captions (optional) ──────────────────────────────────
    if package.caption_srt_path:
        try:
            # Note: region not available from PublishPackage, using None
            _insert_captions(service, video_id, package.caption_srt_path, region=None)
        except Exception as e:
            log.warning("Caption upload failed (non-critical): %s", e)

    # ─── Step 8: Create result and save ──────────────────────────────────────
    result = PublishResult(
        platform="youtube",
        video_id=video_id,
        url=f"https://youtube.com/watch?v={video_id}",
        visibility=package.privacy_status,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )

    _save_publish_result(result, str(workspace))

    log.info("Published to YouTube: %s", result.url)
    return result
