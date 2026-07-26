"""
HarnessRunner: Deterministic state machine for video harness pipeline.

Orchestrates PLAN → GENERATE → RENDER → VERIFY → PACKAGE → PUBLISH phases
with resumable checkpoints and idempotent phase execution.
"""
import logging
import re
from pathlib import Path
from typing import Optional

import requests

from video_agent.harness.manifest import (
    RunManifest, VerifyReport, PublishPackage, PublishResult,
    new_manifest, save_manifest, load_manifest,
)
from video_agent.harness.verify_heuristic import verify_heuristic
from video_agent.config import VISION_MAX_REVISE_CYCLES, REVIEW_QUEUE_PATH
from video_agent.harness.verify_vision import grade_video
from video_agent.harness.revise_router import route_defects, apply_actions
from video_agent.harness.rubric import write_rubric
from video_agent.orchestrator import build_storyboard
from video_agent.script_builder import extract_facts
from video_agent.voiceover import synthesize_segments, VoiceSegment
from video_agent.composer import compose_short_v2
from video_agent.publishers.youtube_packager import package_for_youtube
from video_agent.publishers.youtube_publisher import publish_to_youtube

log = logging.getLogger(__name__)


def generate_srt(*args, **kwargs):
    """Lazy wrapper — defers the real import to avoid loading faster_whisper/CUDA at startup.
    Module-level so tests can patch 'video_agent.harness.runner.generate_srt'."""
    from video_agent.subtitles import generate_srt as _fn
    return _fn(*args, **kwargs)


# Status ordering for phase progression checks
_STATUS_ORDER = [
    "init",
    "planned",
    "generated",
    "rendered",
    "verified",
    "vision_verified",
    "packaged",
    "published",
]


def _status_index(status: str) -> int:
    """Return ordinal index of status, or -1 if 'failed' or unknown."""
    try:
        return _STATUS_ORDER.index(status)
    except ValueError:
        return -1  # 'failed' or unknown


def _fetch_blog_html(url: str) -> str:
    """Fetch blog HTML from URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
    }
    log.info("Fetching blog HTML from %s", url)
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    return r.text


def _load_history_record(url: str) -> dict:
    """Load blog history record for given URL from blog_history.json."""
    history_path = Path("blog_history.json")
    if not history_path.exists():
        log.warning("blog_history.json not found")
        return {}
    try:
        import json
        data = json.loads(history_path.read_text(encoding="utf-8"))
        for post in data.get("posts", []):
            if post.get("url", "").rstrip("/") == url.rstrip("/"):
                log.info("Found history record: %s (region=%s, category=%s)",
                         post.get("title"), post.get("region"), post.get("category"))
                return post
    except Exception as e:
        log.warning("Failed to load history record: %s", e)
    return {}


def _slug(url: str) -> str:
    """Derive slug from URL."""
    path = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return slug[:60] or "video"


def _build_voice_segments(storyboard):
    """Build VoiceSegment list from storyboard scenes."""
    segments = []
    for scene in storyboard.scenes:
        # Get prosody from cinematography if available
        prosody = "conversational"
        if scene.cinematography and scene.cinematography.voice_prosody:
            prosody = scene.cinematography.voice_prosody
        segments.append(VoiceSegment(text=scene.narration, prosody=prosody))
    return segments


class HarnessRunner:
    """Deterministic video harness state machine."""

    @staticmethod
    def run(
        blog_url: str,
        workspace: str | None = None,
        publish: bool = True,
        resume: bool = False,
        dry_run: bool = False
    ) -> RunManifest:
        """
        Execute the full video harness pipeline.

        Args:
            blog_url: URL of blog post to convert to video
            workspace: Output directory (auto-created if None)
            publish: If False, stop after VERIFY (render+verify only)
            resume: If True, load existing manifest and skip completed phases
            dry_run: If True, validate but don't upload

        Returns:
            RunManifest with status="published" (or "verified" if publish=False)
        """
        # ────────────────────────────────────────────────────────────────────
        # Initialize manifest and workspace
        # ────────────────────────────────────────────────────────────────────
        if workspace is None:
            # Per the Phase 1 plan: runs live under output/videos/<slug>, not a
            # hidden temp dir, so artifacts are where the operator expects them.
            workspace = str(Path("output/videos") / _slug(blog_url))
            log.info("Using default workspace: %s", workspace)

        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        manifest_path = workspace / "run_manifest.json"

        # Load existing manifest or create new one
        if resume and manifest_path.exists():
            try:
                manifest = load_manifest(str(manifest_path))
                log.info("Loaded existing manifest (status=%s, run_id=%s)",
                         manifest.status, manifest.run_id)
            except Exception as e:
                log.error("Failed to load manifest: %s", e)
                manifest = None
        else:
            manifest = None

        if manifest is None:
            slug = _slug(blog_url)
            manifest = new_manifest(blog_url, slug, str(workspace))
            log.info("Created new manifest (run_id=%s)", manifest.run_id)

        # Increment attempts on each run() call
        manifest.attempts += 1

        # If manifest is in "failed" state and we're resuming, infer the last
        # successful phase from workspace artifacts so we don't restart from scratch.
        if manifest.status == "failed" and resume:
            manifest.last_error = None
            if manifest.video_path and Path(manifest.video_path).exists():
                log.info("Resuming failed run: video exists -> retrying from VERIFY")
                manifest.status = "rendered"
            elif (workspace / "storyboard.json").exists():
                log.info("Resuming failed run: storyboard exists -> retrying from RENDER")
                manifest.status = "generated"
            else:
                log.info("Resuming failed run: no artifacts -> retrying from GENERATE")
                manifest.status = "planned"

        # If manifest is in "hold_for_review" state and we're resuming, treat it as
        # "verified" so only the VISION phase re-runs (video already rendered & verified).
        if manifest.status == "hold_for_review" and resume and manifest.video_path:
            log.info("Resuming from hold_for_review; restarting from VISION phase")
            manifest.status = "verified"
            manifest.vision = None

        try:
            # ────────────────────────────────────────────────────────────────
            # PHASE 1: PLAN
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("init"):
                log.info("[PLAN] Starting...")
                try:
                    _phase_plan(manifest, blog_url, str(workspace))
                    manifest.status = "planned"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[PLAN] Complete -> status=planned")
                except Exception as e:
                    log.error("[PLAN] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 2: GENERATE
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("planned"):
                log.info("[GENERATE] Starting...")
                try:
                    _phase_generate(manifest, str(workspace))
                    manifest.status = "generated"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[GENERATE] Complete -> status=generated")
                except Exception as e:
                    log.error("[GENERATE] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 3: RENDER
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("generated"):
                log.info("[RENDER] Starting...")
                try:
                    # Need the blog_record for region info during render
                    history = _load_history_record(blog_url)
                    _phase_render(manifest, str(workspace), history)
                    manifest.status = "rendered"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[RENDER] Complete -> status=rendered")
                except Exception as e:
                    log.error("[RENDER] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 4: VERIFY
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("rendered"):
                log.info("[VERIFY] Starting...")
                try:
                    verify_report = verify_heuristic(manifest.video_path, str(workspace))
                    manifest.verify = verify_report

                    if not verify_report.passed:
                        log.warning("[VERIFY] Failed: %s", verify_report.defects)
                        manifest.status = "failed"
                        manifest.last_error = f"Verification failed: {verify_report.defects}"
                        save_manifest(manifest, str(manifest_path))
                        return manifest

                    manifest.status = "verified"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[VERIFY] Complete -> status=verified")
                except Exception as e:
                    log.error("[VERIFY] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 4.5: VISION (Phase 3 gate; runs only after heuristic pass)
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("verified"):
                log.info("[VISION] Starting...")
                try:
                    history = _load_history_record(blog_url)
                    _phase_vision(manifest, str(workspace), history)
                    save_manifest(manifest, str(manifest_path))
                    if manifest.status != "vision_verified":
                        log.warning("[VISION] stopped: status=%s",
                                    manifest.status)
                        return manifest
                    log.info("[VISION] Complete -> status=vision_verified")
                except Exception as e:
                    log.error("[VISION] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 5: PACKAGE (only if publish=True)
            # ────────────────────────────────────────────────────────────────
            if publish and _status_index(manifest.status) <= _status_index("vision_verified"):
                log.info("[PACKAGE] Starting...")
                try:
                    history = _load_history_record(blog_url)
                    blog_record = _build_blog_record(blog_url, history, str(workspace))
                    _phase_package(manifest, blog_record, str(workspace))
                    manifest.status = "packaged"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[PACKAGE] Complete -> status=packaged")
                except Exception as e:
                    log.error("[PACKAGE] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # ────────────────────────────────────────────────────────────────
            # PHASE 6: PUBLISH (only if publish=True and not dry_run)
            # ────────────────────────────────────────────────────────────────
            if publish and not dry_run and _status_index(manifest.status) <= _status_index("packaged"):
                log.info("[PUBLISH] Starting...")
                try:
                    _phase_publish(manifest, str(workspace), dry_run=False)
                    manifest.status = "published"
                    save_manifest(manifest, str(manifest_path))
                    log.info("[PUBLISH] Complete -> status=published")
                except Exception as e:
                    log.error("[PUBLISH] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest

            # If dry_run=True and we got to packaged, stop at verified
            if dry_run and _status_index(manifest.status) == _status_index("packaged"):
                log.info("[PUBLISH] Dry-run mode: stopping at verified (not persisting publish)")
                manifest.status = "verified"
                manifest.publish = None
                save_manifest(manifest, str(manifest_path))

            return manifest

        except Exception as e:
            # Catch-all for unexpected errors
            log.error("Unexpected error: %s", e, exc_info=True)
            manifest.status = "failed"
            manifest.last_error = str(e)
            save_manifest(manifest, str(manifest_path))
            return manifest


# ============================================================================
# Phase implementations
# ============================================================================

def _phase_plan(manifest: RunManifest, blog_url: str, workspace: str) -> None:
    """
    PLAN phase: Fetch blog HTML, load metadata, initialize manifest.
    """
    # Fetch HTML
    html = _fetch_blog_html(blog_url)

    # Load history
    history = _load_history_record(blog_url)

    # Phase 3: the written grading contract is emitted up front.
    write_rubric(Path(workspace), hero_claim="")

    # For now, we just verify these are available for next phases
    # (We'll use them again in GENERATE)
    log.info("Blog fetched and history loaded")


def _phase_generate(manifest: RunManifest, workspace: str) -> None:
    """
    GENERATE phase: Extract facts and build storyboard.
    """
    from video_agent.storyboard import save_storyboard

    workspace = Path(workspace)
    blog_url = manifest.blog_url

    # Fetch again (could cache but following the pattern from make_video.py)
    html = _fetch_blog_html(blog_url)
    history = _load_history_record(blog_url)
    blog_record = _build_blog_record(blog_url, history, str(workspace))

    # Extract facts
    log.info("Extracting facts from blog...")
    facts, _ = extract_facts(blog_record)
    log.info("Extracted %d facts", len(facts))

    # Build storyboard
    log.info("Building storyboard...")
    storyboard = build_storyboard(
        blog=blog_record,
        facts=facts,
        blog_html=html,
        workspace=workspace,
    )
    log.info("Storyboard built with %d scenes", len(storyboard.scenes))

    # Save storyboard
    storyboard_path = workspace / "storyboard.json"
    save_storyboard(storyboard, storyboard_path)
    manifest.storyboard_path = str(storyboard_path)


def _phase_render(manifest: RunManifest, workspace: str, history: dict) -> None:
    """
    RENDER phase: Synthesize voiceover, generate subtitles, compose video.
    """
    from video_agent.storyboard import load_storyboard

    workspace = Path(workspace)
    storyboard_path = Path(manifest.storyboard_path)

    # Load storyboard
    log.info("Loading storyboard from %s", storyboard_path)
    storyboard = load_storyboard(storyboard_path)

    # Get region from history
    region = history.get("region", "default")

    # Build voice segments from storyboard
    voice_segments = _build_voice_segments(storyboard)
    log.info("Built %d voice segments from scenes", len(voice_segments))

    # Synthesize voiceover (reuse existing file on resume to avoid network calls)
    voice_path = workspace / "voiceover.mp3"
    if voice_path.exists() and voice_path.stat().st_size > 1024:
        log.info("Reusing existing voiceover: %s", voice_path)
        from pydub.utils import mediainfo
        try:
            dur = float(mediainfo(str(voice_path)).get("duration", 0))
        except Exception:
            dur = 0.0
        voice_info = {"audio_path": str(voice_path), "duration_s": dur}
    else:
        log.info("Synthesizing voiceover -> %s", voice_path)
        voice_info = synthesize_segments(voice_segments, voice_path, region=region)
    log.info("Voice synthesized: %s (%.1fs)", voice_info.get("audio_path"), voice_info.get("duration_s", 0))
    manifest.voice_path = str(voice_path)

    srt_path = workspace / "subtitles.srt"
    if srt_path.exists() and srt_path.stat().st_size > 0:
        log.info("Reusing existing subtitles: %s", srt_path)
        srt_result = srt_path
    else:
        log.info("Generating subtitles -> %s", srt_path)
        narration = " ".join(s.narration for s in storyboard.scenes)
        srt_result = generate_srt(voice_path, srt_path, narration_hint=narration)
        log.info("Subtitles generated: %s", srt_result)
    manifest.srt_path = str(srt_result)

    # Compose video
    video_path = workspace / "video_short.mp4"
    log.info("Composing video -> %s", video_path)
    result = compose_short_v2(
        storyboard,
        voice_path=voice_path,
        subtitle_path=srt_path,
        output_path=video_path,
        workspace=workspace,
    )
    log.info("Video composed: %s", result)
    manifest.video_path = str(result)


def _phase_package(manifest: RunManifest, blog_record: dict, workspace: str) -> None:
    """
    PACKAGE phase: Build PublishPackage for YouTube.
    """
    from video_agent.storyboard import load_storyboard

    workspace = Path(workspace)
    storyboard_path = Path(manifest.storyboard_path)

    # Load storyboard
    log.info("Loading storyboard for packaging")
    storyboard = load_storyboard(storyboard_path)

    # Package for YouTube
    log.info("Packaging for YouTube...")
    package = package_for_youtube(storyboard, blog_record, str(workspace))
    log.info("Package created: %s (tags=%s)", package.title, package.tags)
    manifest.package = package


def _phase_publish(manifest: RunManifest, workspace: str, dry_run: bool) -> None:
    """
    PUBLISH phase: Upload to YouTube.
    """
    workspace = Path(workspace)

    # Publish to YouTube
    log.info("Publishing to YouTube (dry_run=%s)...", dry_run)
    result = publish_to_youtube(
        manifest.package,
        manifest.video_path,
        str(workspace),
        dry_run=dry_run,
    )
    log.info("Published: %s -> %s", result.video_id, result.url)

    if not dry_run:
        manifest.publish = result


def _build_blog_record(blog_url: str, history: dict, workspace: str) -> dict:
    """Build blog_record dict from URL, history, and HTML."""
    slug = _slug(blog_url)
    html = _fetch_blog_html(blog_url)

    return {
        "blog_id": slug,
        "title": history.get("title", "HRSU Blog Video"),
        "url": blog_url,
        "content_html": html,
        "region": history.get("region", "default"),
        "persona": "procurement",
        "category": history.get("category", "industry"),
        "subcategory": history.get("subcategory", ""),
        "language": history.get("language", "en-US"),
    }


def load_storyboard_for_vision(manifest: RunManifest):
    from video_agent.storyboard import load_storyboard
    return load_storyboard(Path(manifest.storyboard_path))


def _revise_sourcer(workspace: str):
    from video_agent.orchestrator import _build_sourcer
    return _build_sourcer(Path(workspace))


def _append_review_queue(manifest: RunManifest) -> None:
    """Append a hold entry to the operator review queue (best-effort)."""
    import json
    qp = Path(REVIEW_QUEUE_PATH)
    try:
        queue = json.loads(qp.read_text(encoding="utf-8")) if qp.exists() else []
    except Exception:
        queue = []
    queue.append({
        "run_id": manifest.run_id,
        "blog_url": manifest.blog_url,
        "video_path": manifest.video_path,
        "workspace": manifest.workspace,
        "scene_overalls": ([
            {"index": s.index, "overall": s.overall}
            for s in manifest.vision.scenes] if manifest.vision else []),
        "held_at": manifest.updated_at,
    })
    qp.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                  encoding="utf-8")


def _phase_vision(manifest: RunManifest, workspace: str, history: dict) -> None:
    """VISION phase: grade -> (route -> revise -> re-render -> re-grade)*N.
    Ends in status vision_verified, hold_for_review, or failed."""
    storyboard = load_storyboard_for_vision(manifest)
    cycles = 0
    while True:
        report = grade_video(storyboard, Path(workspace))
        report.cycles_used = cycles
        manifest.vision = report

        if report.passed:
            manifest.status = "vision_verified"
            log.info("[VISION] passed (cycles=%d)", cycles)
            return

        if report.hold:
            manifest.status = "hold_for_review"
            _append_review_queue(manifest)
            log.warning("[VISION] hold for review (uncertain grades)")
            return

        actions = route_defects(report)
        if not actions or cycles >= VISION_MAX_REVISE_CYCLES:
            manifest.status = "hold_for_review"
            _append_review_queue(manifest)
            log.warning("[VISION] hold for review (cycles=%d, actions=%s)",
                        cycles, actions)
            return

        log.info("[VISION] revise cycle %d: %s", cycles + 1, actions)
        sourcer = _revise_sourcer(workspace)
        apply_actions(actions, storyboard, sourcer, Path(workspace))
        _phase_render(manifest, workspace, history)
        heur = verify_heuristic(manifest.video_path, workspace)
        if not heur.passed:
            manifest.status = "failed"
            manifest.last_error = (
                f"re-render failed heuristic gate: {heur.defects}")
            return
        cycles += 1
