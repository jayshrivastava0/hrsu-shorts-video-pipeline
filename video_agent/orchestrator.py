"""Wires together Strategist → Storyboarder → Sourcer → Critics → Reviser.
Returns a populated Storyboard ready for rendering."""
from __future__ import annotations
import logging
from pathlib import Path

from video_agent.agents.strategist import Strategist
from video_agent.agents.storyboarder import Storyboarder
from video_agent.agents.cinematographer import Cinematographer
from video_agent.agents.sourcer import Sourcer
from video_agent.agents.critic_local import LocalCritic
from video_agent.agents.critic_global import GlobalDirector
from video_agent.agents.reviser import Reviser
from video_agent.agents.narration_polisher import NarrationPolisher
from video_agent.sources.unsplash import UnsplashSource
from video_agent.sources.bing import BingSource
from video_agent.sources.wikimedia import WikimediaSource
from video_agent.sources.archive_org import ArchiveOrgSource
from video_agent.sources.pixabay import PixabaySource
from video_agent.sources.duckduckgo import DuckDuckGoSource
from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
from video_agent.sources.pexels import PexelsSource
from video_agent.sources.youtube import YouTubeSource
from video_agent.sources.blog_references import (
    parse_blog_references, BlogReferencesSource,
)
from video_agent.storyboard import Storyboard, save_storyboard

log = logging.getLogger(__name__)


def _build_sourcer(workspace: Path, blog_html: str = "") -> Sourcer:
    """Build the Sourcer.

    Blog references (if any) are pre-resolved into og:image candidates and
    fed in as the highest-authority source. Internal HRSU links are stripped
    so we never loop back to ourselves.
    """
    sources = [
        UnsplashSource(),
        PexelsSource(),
        PixabaySource(),
        WikimediaSource(),
        ArchiveOrgSource(),
        BingSource(),
        GoogleImagesBrowserSource(),
        DuckDuckGoSource(),
        YouTubeSource(),
    ]
    if blog_html:
        try:
            refs = parse_blog_references(blog_html)
            if refs:
                # Build the references source — it scrapes og:images during init.
                # Prepend so authority-weight tiebreaks favor it.
                sources.insert(0, BlogReferencesSource(refs))
        except Exception as e:
            log.warning("Failed to build BlogReferencesSource (%s); continuing", e)
    return Sourcer(
        sources=sources,
        cache_root=Path("output/_image_cache"),
        download_root=workspace / "_assets",
    )


def build_storyboard(blog: dict, facts: list, blog_html: str,
                     workspace: Path) -> Storyboard:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    sb = Storyboard(version="2.0", blog=blog)

    log.info("[1/6] Strategist: starting...")
    try:
        Strategist().run(sb, facts, blog_html)
        log.info("[1/6] Strategist: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[1/6] Strategist FAILED: %s", e, exc_info=True)
        raise

    log.info("[2/6] Storyboarder: starting...")
    try:
        Storyboarder().run(sb)
        log.info("[2/6] Storyboarder: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[2/6] Storyboarder FAILED: %s", e, exc_info=True)
        raise

    log.info("[2.5/6] Cinematographer: starting...")
    try:
        Cinematographer().run(sb)
        log.info("[2.5/6] Cinematographer: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[2.5/6] Cinematographer FAILED: %s", e, exc_info=True)
        # Non-fatal: fallback Cinematography defaults already set by agent
        log.warning("Cinematographer failed; continuing with mood-based fallbacks")

    log.info("[3/6] NarrationPolisher: starting...")
    try:
        NarrationPolisher().run(sb)
        log.info("[3/6] NarrationPolisher: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[3/6] NarrationPolisher FAILED: %s", e, exc_info=True)
        raise

    # Build sourcer once and share it between the Sourcer and Reviser passes.
    # Two separate _build_sourcer() calls would create two Playwright browser
    # instances (both register atexit handlers), causing an EPIPE race on exit.
    sourcer = _build_sourcer(workspace, blog_html)

    log.info("[4/6] Sourcer: starting...")
    try:
        sourcer.run(sb)
        log.info("[4/6] Sourcer: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[4/6] Sourcer FAILED: %s", e, exc_info=True)
        raise

    log.info("Source attribution:")
    for s in sb.scenes:
        if s.chosen_asset:
            log.info("  Scene %d: source=%s score=%d res=%dx%d%s",
                     s.index, s.chosen_asset.source, s.chosen_asset.score,
                     s.chosen_asset.width, s.chosen_asset.height,
                     " [DEGRADED]" if s.degraded else "")
        else:
            log.warning("  Scene %d: NO ASSET (degraded fallback to brand card)",
                        s.index)

    log.info("[5/6] Critics (parallel): starting...")
    try:
        log.info("[5/6] LocalCritic: starting...")
        LocalCritic().run(sb)
        log.info("[5/6] LocalCritic: complete")
        log.info("[5/6] GlobalDirector: starting...")
        GlobalDirector().run(sb)
        log.info("[5/6] GlobalDirector: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[5/6] Critics FAILED: %s", e, exc_info=True)
        raise

    log.info("[6/6] Reviser: starting...")
    try:
        Reviser(sourcer=sourcer).run(sb)
        log.info("[6/6] Reviser: complete")
        save_storyboard(sb, workspace / "storyboard.json")
    except Exception as e:
        log.error("[6/6] Reviser FAILED: %s", e, exc_info=True)
        raise

    log.info("Storyboard built -- %d scenes, %d real, %d degraded",
             len(sb.scenes),
             sum(1 for s in sb.scenes if not s.degraded),
             sum(1 for s in sb.scenes if s.degraded))
    return sb
