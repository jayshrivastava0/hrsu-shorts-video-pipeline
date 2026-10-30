"""YouTube publishing for the scheduler: metadata, privacy switch, non-interactive auth, read-back."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_PRIVACY = ("public", "unlisted", "private")
PRIVACY_ENV = "HRSU_YOUTUBE_PRIVACY"
YOUTUBE_DIR_ENV = "HRSU_YOUTUBE_DIR"
AUTH_HINT = "run `python -m scheduler.youtube_auth` interactively once"
MAIN_SITE = "https://hrsuindore.com"
GENERIC_CTA = "Sourcing calcium nitrate? Visit hrsuindore.com"


class YouTubeAuthMissing(RuntimeError):
    pass


def get_privacy() -> str:
    """Scheduler privacy: env HRSU_YOUTUBE_PRIVACY, default public (other callers keep video_agent's unlisted)."""
    value = (os.environ.get(PRIVACY_ENV) or "public").strip().lower()
    if value not in VALID_PRIVACY:
        raise ValueError(f"{PRIVACY_ENV}={value!r} invalid; use one of {VALID_PRIVACY}")
    return value


def youtube_dir() -> Path:
    """HRSU_YOUTUBE_DIR, else the first of (this repo, the main checkout when running from a
    `.worktrees/<name>` worktree) that already holds client_secrets.json / youtube_token.json."""
    if os.environ.get(YOUTUBE_DIR_ENV):
        return Path(os.environ[YOUTUBE_DIR_ENV])
    candidates = [REPO_ROOT]
    if REPO_ROOT.parent.name == ".worktrees":
        candidates.append(REPO_ROOT.parent.parent)
    for c in candidates:
        if (c / "client_secrets.json").exists() or (c / "youtube_token.json").exists():
            return c
    return REPO_ROOT


def auth_paths(base: Optional[Path] = None) -> tuple[Path, Path]:
    base = Path(base) if base else youtube_dir()
    return base / "client_secrets.json", base / "youtube_token.json"


def preflight_auth(base: Optional[Path] = None) -> None:
    """Fail fast (never open a browser) when YouTube credentials are not in place."""
    secrets, token = auth_paths(base)
    if not secrets.exists():
        raise YouTubeAuthMissing(f"{secrets} not found -- put the Google OAuth client_secrets.json there, then {AUTH_HINT}")
    if not token.exists():
        raise YouTubeAuthMissing(f"{token} not found -- {AUTH_HINT}")


def build_service_noninteractive(base: Optional[Path] = None):
    """YouTube client from the saved token only; refreshes if needed, never starts an OAuth flow."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from video_agent.config import YOUTUBE_UPLOAD_SCOPES

    _, token = auth_paths(base)
    creds = Credentials.from_authorized_user_file(str(token), YOUTUBE_UPLOAD_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise YouTubeAuthMissing(f"YouTube token unusable -- {AUTH_HINT}")
    return build("youtube", "v3", credentials=creds)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _video_type(run: Path) -> Optional[str]:
    """Brief-driven video type from references.json post_kind, or None when absent or not a known type."""
    from scheduler.briefs.types import get_recipe

    try:
        post_kind = _read(run / "references.json").get("post_kind")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(post_kind, str):
        return None
    try:
        get_recipe(post_kind)
    except KeyError:
        return None
    return post_kind


def build_package(run_dir: Path | str, blog_url: Optional[str], privacy: str,
                  thumbnail_path: Optional[Path | str] = None, cta_line: Optional[str] = None):
    from video_agent.config import YOUTUBE_CATEGORY_ID, YOUTUBE_DESC_MAX, YOUTUBE_TITLE_MAX
    from video_agent.harness.manifest import PublishPackage

    run = Path(run_dir)
    plan = _read(run / "explanation_plan.json") if (run / "explanation_plan.json").exists() else {}
    post = _read(run / "post.json") if (run / "post.json").exists() else {}
    title = (plan.get("question") or post.get("title") or "Calcium nitrate explained").strip()
    if len(title) > YOUTUBE_TITLE_MAX:
        title = title[: YOUTUBE_TITLE_MAX - 3].rstrip() + "..."

    body = (run / "video_description.txt").read_text(encoding="utf-8").strip()
    video_type = None
    if blog_url:
        footer = f"\n\nFull article: {blog_url}\nHRSU Indore: {MAIN_SITE}/\n"
    else:
        from scheduler.briefs.types import get_recipe

        video_type = _video_type(run)
        if cta_line is None:
            cta_line = get_recipe(video_type).cta_line if video_type else GENERIC_CTA
        footer = f"\n\n{cta_line}\nHRSU Indore: {MAIN_SITE}/\n"
    description = body[: YOUTUBE_DESC_MAX - len(footer)] + footer

    tags = ["calcium nitrate", "HRSU", "chemical procurement", "explainer"]
    for key in ("category", "subcategory", "region"):
        if post.get(key):
            tags.append(str(post[key]).replace("_", " "))
    if video_type:
        tags.append(video_type)
    return PublishPackage(title=title, description=description, tags=tags,
                          category_id=YOUTUBE_CATEGORY_ID, privacy_status=privacy,
                          thumbnail_path=str(thumbnail_path) if thumbnail_path else None)


def read_privacy(service, video_id: str) -> Optional[str]:
    resp = service.videos().list(part="status", id=video_id).execute()
    items = resp.get("items") or []
    return items[0]["status"]["privacyStatus"] if items else None


def publish_video(run_dir: Path | str, video_path: Path | str, blog_url: Optional[str], *,
                  cta_line: Optional[str] = None, dry_run: bool = False, base: Optional[Path] = None,
                  publisher: Optional[Callable] = None,
                  service_factory: Optional[Callable] = None) -> dict[str, Any]:
    """Upload and verify. Returns {video_id, url, privacy_requested, privacy_actual}."""
    privacy = get_privacy()
    run = Path(run_dir)
    thumb = None
    if not dry_run:
        from scheduler.thumbnail import make_thumbnail
        thumb = make_thumbnail(video_path, run / "thumbnail.jpg")
        if thumb is None:
            logger.warning("thumbnail generation failed; uploading with YouTube's auto thumbnail")
    package = build_package(run_dir, blog_url, privacy, thumb, cta_line=cta_line)
    if dry_run:
        return {"video_id": None, "url": None, "privacy_requested": privacy,
                "privacy_actual": None, "title": package.title}

    preflight_auth(base)
    service_factory = service_factory or (lambda: build_service_noninteractive(base))
    if publisher is None:
        from video_agent.publishers import youtube_publisher as yp
        secrets, token = auth_paths(base)
        yp.YOUTUBE_CLIENT_SECRETS, yp.YOUTUBE_TOKEN_PATH = str(secrets), str(token)
        yp._build_service = service_factory      # never fall through to a browser OAuth flow
        publisher = yp.publish_to_youtube
    result = publisher(package, str(video_path), str(run), dry_run=False)

    actual = None
    try:
        actual = read_privacy(service_factory(), result.video_id)
    except Exception as exc:
        logger.warning("could not read back privacyStatus for %s: %s", result.video_id, exc)
    if actual != privacy:
        logger.warning(
            "!!! PRIVACY MISMATCH: requested %r but YouTube reports %r for %s. Unaudited YouTube API "
            "projects have uploads FORCED to private -- complete the API compliance audit or publish "
            "manually from YouTube Studio.", privacy, actual, result.video_id)
    return {"video_id": result.video_id, "url": result.url, "privacy_requested": privacy,
            "privacy_actual": actual, "title": package.title,
            "published_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
