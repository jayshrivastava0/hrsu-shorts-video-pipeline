"""Stage 10 — PUBLISH: resumable YouTube upload via the reused publisher.
WITHOUT --publish this runs as dry_run (metadata validation only) — the
default flow holds at the contact sheet for human review."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _publish_to_youtube(package, video_path: str, workspace: str,
                        dry_run: bool = False):
    from video_agent.publishers.youtube_publisher import publish_to_youtube
    return publish_to_youtube(package, video_path, workspace, dry_run=dry_run)


def run(ctx) -> dict[str, str]:
    from video_agent.harness.manifest import PublishPackage
    ws = Path(ctx.workspace)
    raw = json.loads((ws / "publish_package.json").read_text(encoding="utf-8"))
    pkg = PublishPackage(**raw)
    dry_run = not bool(ctx.flags.get("publish", False))
    result = _publish_to_youtube(pkg, str(ws / "video_short.mp4"), str(ws),
                                 dry_run=dry_run)
    (ws / "publish_result.json").write_text(json.dumps({
        "platform": getattr(result, "platform", "youtube"),
        "video_id": getattr(result, "video_id", ""),
        "url": getattr(result, "url", ""),
        "dry_run": dry_run,
    }, indent=2), encoding="utf-8")
    logger.info("publish: %s (dry_run=%s)",
                getattr(result, "video_id", "?"), dry_run)
    return {"publish_result": "publish_result.json"}
