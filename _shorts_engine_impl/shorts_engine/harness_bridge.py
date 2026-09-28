"""Shared bridge into the Node harness's real headless-agent turn (`harness/scripts/
run-visual-authoring.mts`) — the mechanism `cli.py`'s `visuals`/`assemble` stages and
`verify.py`'s revise-cycle reassembly both need. Factored out of `cli.py` rather than
imported from it: `cli.py` imports `shorts_engine.stages.verify`, so `verify.py` importing
back from `cli.py` would be circular.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError


def run_creative_stage(ctx, phase: str) -> dict[str, str]:
    """Run the `visuals` or `assemble` phase through the real harness agent.

    `author_visual_scene`/`author_assembly_composition` require a live parent agent to spawn
    their scene-authoring subagent from, so this shells out to `harness/scripts/
    run-visual-authoring.mts`, which boots `harness/cordis.yml` and drives one real agent turn.
    """
    workspace = Path(ctx.workspace)
    harness_dir = Path(config.PROJECT_ROOT) / "harness"
    bridge = harness_dir / "scripts" / "run-visual-authoring.mts"
    result = subprocess.run(
        ["node", "--import", "tsx/esm", str(bridge), phase, str(workspace.resolve())],
        cwd=harness_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise EngineError(f"{phase}: harness bridge failed: {result.stderr[-2000:]}")
    # Must mirror exactly what stage_cli.py's visuals-finalize/assemble-finalize checkpoint —
    # the runner records these as the run's artifacts, so anything listed here that the bridge
    # doesn't actually write makes run_manifest.json lie. (`captions.ass` used to be listed for
    # assemble; it was an ffmpeg-ASS artifact of the retired assemble.py and the HyperFrames
    # path never produces it. package.py builds its own subtitles.srt.)
    if phase == "visuals":
        return {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
    return {"video": "video_short.mp4", "assemble_report": "assemble_report.json"}
