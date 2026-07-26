"""Re-run a single pipeline stage on an existing storyboard.json.

Usage:
    python -m video_agent.run_stage <stage> <path/to/storyboard.json>

Stages: strategist | storyboarder | sourcer | critics | reviser
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

from video_agent.storyboard import load_storyboard, save_storyboard
from video_agent.agents.strategist import Strategist
from video_agent.agents.storyboarder import Storyboarder

log = logging.getLogger("run_stage")


def replay_stage(stage: str, path: Path) -> None:
    sb = load_storyboard(path)
    workspace = Path(path).parent
    if stage == "strategist":
        import requests
        html = requests.get(sb.blog["url"], timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"}).text
        from video_agent.script_builder import extract_facts
        blog_record = dict(sb.blog)
        blog_record["content_html"] = html
        facts, _ = extract_facts(blog_record)
        Strategist().run(sb, facts, html)
    elif stage == "storyboarder":
        Storyboarder().run(sb)
    elif stage == "sourcer":
        from video_agent.orchestrator import _build_sourcer
        _build_sourcer(workspace).run(sb)
    elif stage == "critics":
        from video_agent.agents.critic_local import LocalCritic
        from video_agent.agents.critic_global import GlobalDirector
        LocalCritic().run(sb)
        GlobalDirector().run(sb)
    elif stage == "reviser":
        from video_agent.agents.reviser import Reviser
        from video_agent.orchestrator import _build_sourcer
        Reviser(sourcer=_build_sourcer(workspace)).run(sb)
    else:
        raise ValueError(f"Unknown stage: {stage!r}")
    save_storyboard(sb, path)
    log.info("Stage %s replayed; storyboard saved", stage)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["strategist", "storyboarder",
                                       "sourcer", "critics", "reviser"])
    ap.add_argument("storyboard_path", type=Path)
    args = ap.parse_args()
    replay_stage(args.stage, args.storyboard_path)


if __name__ == "__main__":
    main()
