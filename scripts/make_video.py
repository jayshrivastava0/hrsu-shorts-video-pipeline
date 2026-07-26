"""Run the video pipeline in dev mode (render+verify+vision, no publish).

This script generates videos for local testing without uploading to YouTube.
It uses the HarnessRunner pipeline with publish=False to stop after VISION phase.

Usage:
    python scripts/make_video.py <blog-url>
    python scripts/make_video.py <blog-url> --resume   # Resume from existing workspace
    python scripts/make_video.py <blog-url> --force    # (deprecated: ignored)
    python scripts/make_video.py <blog-url> --no-voice # (deprecated: ignored)

The script:
1. Fetches blog HTML and metadata
2. Generates storyboard from content
3. Renders video with voiceover and subtitles
4. Verifies video quality (heuristic gate)
5. Grades all scene frames via Vision LLM
6. Writes MP4 to workspace and opens in player (Windows)

Running in dev mode (PLAN -> GENERATE -> RENDER -> VERIFY -> VISION, stops before PACKAGE)

Example:
    python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization.html
    python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization.html --resume
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Ensure the project is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from video_agent.harness.runner import HarnessRunner
from video_agent.voiceover import VoiceSegment
from video_agent.storyboard import Storyboard

# ============================================================================
# Utility functions
# ============================================================================

def build_voice_segments(storyboard: Storyboard) -> list[VoiceSegment]:
    """Build VoiceSegment list from storyboard scenes.

    Extracts narration and prosody from each scene. If a scene has cinematography
    with voice_prosody set, uses that; otherwise falls back to 'conversational'.

    Args:
        storyboard: Storyboard with populated scenes

    Returns:
        List of VoiceSegment objects ready for voiceover synthesis
    """
    segments = []
    for scene in storyboard.scenes:
        # Get prosody from cinematography if available
        prosody = "conversational"
        if scene.cinematography and scene.cinematography.voice_prosody:
            prosody = scene.cinematography.voice_prosody
        segments.append(VoiceSegment(text=scene.narration, prosody=prosody))
    return segments


# ============================================================================
# Logging setup
# ============================================================================

log = logging.getLogger("make_video")


def setup_logging():
    """Configure logging to stdout and file."""
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "make_video.log"

    # Setup handlers
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Remove any existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    log.info("Logging initialized: console + %s", log_file)


# ============================================================================
# Argument parsing
# ============================================================================

def parse_args(args=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate a video from an HRSU blog post (dev mode, no publish).",
        epilog="Example:\n"
               "  %(prog)s https://blog.hrsuindore.com/2026/05/blog.html",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "url",
        help="Full URL of the blog post",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="(Deprecated: ignored) Force regeneration",
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="(Deprecated: ignored) Skip TTS",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing manifest (skip completed phases)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Render and verify but skip any upload (same as default for make_video, "
             "but explicitly sets dry_run=True in the runner)",
    )
    return parser.parse_args(args)


# ============================================================================
# Main orchestration
# ============================================================================

def main():
    """Main entry point."""
    try:
        # Parse arguments
        args = parse_args()

        # Setup logging
        setup_logging()
        log.info("Starting make_video pipeline (dev mode)")
        log.info("Blog URL: %s", args.url)

        # Note: --force and --no-voice are deprecated and ignored
        if args.force:
            log.warning("--force flag is deprecated and ignored")
        if args.no_voice:
            log.warning("--no-voice flag is deprecated and ignored")

        # Log dev mode
        log.info("Running in dev mode (render+verify+vision, no upload)")

        # Use default workspace (output/videos/<slug>)
        # HarnessRunner will auto-create if workspace is None
        workspace = None

        # Run the harness with publish=False (stops at VISION)
        log.info("Running HarnessRunner...")
        manifest = HarnessRunner.run(
            blog_url=args.url,
            workspace=workspace,
            publish=False,  # Dev mode: no publish
            resume=args.resume,
            dry_run=args.dry_run,
        )

        # Log results
        log.info("Pipeline complete")
        log.info("  Status: %s", manifest.status)
        log.info("  Run ID: %s", manifest.run_id)
        if manifest.video_path:
            log.info("  Video: %s", manifest.video_path)
        if manifest.last_error:
            log.error("  Error: %s", manifest.last_error)

        # Print summary
        if manifest.status == "vision_verified":
            video_path = Path(manifest.video_path) if manifest.video_path else None
            if video_path and video_path.exists():
                size_mb = video_path.stat().st_size / 1_048_576
                print(f"\n  Video ready: {video_path.resolve()}  ({size_mb:.1f} MB)\n")

                # Try to open in default player on Windows
                try:
                    subprocess.Popen(["start", "", str(video_path.resolve())], shell=True)
                except Exception:
                    pass
        elif manifest.status == "hold_for_review":
            print(f"\n  Video held for review: {manifest.video_path}\n"
                  "  Check review_queue.json for details.\n")
        elif manifest.status == "failed":
            print(f"\nError: {manifest.last_error}\n", file=sys.stderr)

        # Exit with appropriate code
        if manifest.status in ("vision_verified", "hold_for_review"):
            log.info("SUCCESS: Video rendered (status=%s)", manifest.status)
            sys.exit(0)
        else:
            log.error("FAILURE: Pipeline did not reach vision_verified status")
            sys.exit(1)

    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        log.error("Unexpected error: %s", e, exc_info=True)
        print(f"\nError: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
