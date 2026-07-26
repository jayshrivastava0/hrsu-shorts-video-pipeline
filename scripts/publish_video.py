"""Full video harness pipeline orchestration: PLAN → PUBLISH.

This script runs the complete video generation and publishing pipeline,
converting a blog post to a YouTube video with automatic upload.

Usage:
    python scripts/publish_video.py <blog-url>
    python scripts/publish_video.py <blog-url> --dry-run  # Don't upload to YouTube
    python scripts/publish_video.py <blog-url> --resume   # Resume from last checkpoint
    python scripts/publish_video.py <blog-url> --workspace /path/to/ws  # Custom workspace

Example:
    python scripts/publish_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization.html
    python scripts/publish_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization.html --dry-run --resume

The script:
1. Fetches blog HTML and metadata from blog_history.json
2. Generates storyboard from blog content (PLAN → GENERATE)
3. Renders video with voiceover and subtitles (RENDER)
4. Verifies video quality (VERIFY)
5. Packages for YouTube (PACKAGE)
6. Uploads to YouTube (PUBLISH) — unless --dry-run

Exit codes:
    0: Success (status="published")
    1: Failure (status="failed" or other non-published)
"""
import argparse
import logging
import sys
from pathlib import Path

# Ensure the project is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from video_agent.harness.runner import HarnessRunner

# ============================================================================
# Logging setup
# ============================================================================

log = logging.getLogger("publish_video")


def setup_logging():
    """Configure logging to stdout and file."""
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "publish_video.log"

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
        description="Generate and publish a video from an HRSU blog post.",
        epilog="Examples:\n"
               "  %(prog)s https://blog.hrsuindore.com/2026/05/blog.html\n"
               "  %(prog)s https://blog.hrsuindore.com/2026/05/blog.html --dry-run\n"
               "  %(prog)s https://blog.hrsuindore.com/2026/05/blog.html --resume",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "blog_url",
        help="Full URL of the blog post",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and prepare but don't upload to YouTube",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint (requires --workspace)",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Custom workspace directory (auto-created if not provided)",
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
        log.info("Starting publish_video pipeline")
        log.info("Blog URL: %s", args.blog_url)
        log.info("Flags: dry_run=%s, resume=%s, workspace=%s",
                 args.dry_run, args.resume, args.workspace)

        # Run the harness
        log.info("Running HarnessRunner...")
        manifest = HarnessRunner.run(
            blog_url=args.blog_url,
            workspace=args.workspace,
            publish=True,
            resume=args.resume,
            dry_run=args.dry_run,
        )

        # Log results
        log.info("Pipeline complete")
        log.info("  Status: %s", manifest.status)
        log.info("  Run ID: %s", manifest.run_id)
        log.info("  Attempts: %d", manifest.attempts)
        if manifest.video_path:
            log.info("  Video: %s", manifest.video_path)
        if manifest.publish:
            log.info("  Published: %s", manifest.publish.url)
        if manifest.last_error:
            log.error("  Error: %s", manifest.last_error)

        # Print summary
        print("\n" + "=" * 70)
        print(f"Pipeline Status: {manifest.status}")
        print(f"Run ID: {manifest.run_id}")
        if manifest.status == "published":
            print(f"Video URL: {manifest.publish.url}")
            print(f"Video ID: {manifest.publish.video_id}")
        elif manifest.status == "verified":
            print("(Verified and ready for publish, but --dry-run mode)")
            if manifest.video_path:
                print(f"Video: {manifest.video_path}")
        elif manifest.status == "failed":
            print(f"Error: {manifest.last_error}")
        print("=" * 70 + "\n")

        # Exit with appropriate code
        if manifest.status == "published":
            log.info("SUCCESS: Video published")
            sys.exit(0)
        else:
            log.error("FAILURE: Pipeline did not reach published status")
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
