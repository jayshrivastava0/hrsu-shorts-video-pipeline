"""Maps graded vision defects back to the responsible pipeline stage
(spec: 'caption clipped -> re-RENDER; scene image off-topic -> re-source').
Actions are (kind, scene_index|None) tuples; the runner applies them and then
re-renders. Unknown defect codes produce no action — the runner's hold path
covers them."""
from __future__ import annotations
import logging
from pathlib import Path

from video_agent.harness.manifest import VisionReport
from video_agent.storyboard import save_storyboard

log = logging.getLogger(__name__)

# defect code -> action kind
_RE_SOURCE_CODES = {"visual_mismatch", "off_brand", "low_quality"}
_RE_RENDER_CODES = {"text_clipped", "text_unreadable"}

Action = tuple[str, int | None]


def route_defects(report: VisionReport) -> list[Action]:
    """Map vision defects to pipeline actions.

    Re-source actions are per-scene (scene index provided).
    Re-render is global and deduplicated (scene_index=None).
    Unknown defect codes produce no action.

    Args:
        report: VisionReport with graded scenes and defects

    Returns:
        List of (action_kind, scene_index) tuples
    """
    actions: list[Action] = []
    needs_render = False
    for grade in report.scenes:
        for d in grade.defects:
            code = d.get("code", "")
            if code in _RE_SOURCE_CODES:
                a = ("re_source", grade.index)
                if a not in actions:
                    actions.append(a)
            elif code in _RE_RENDER_CODES:
                needs_render = True
    # A re-source always flows into the runner's re-render afterwards, so one
    # trailing re_render covers both cases.
    if needs_render or any(k == "re_source" for k, _ in actions):
        actions.append(("re_render", None))
    return actions


def apply_actions(actions: list[Action], storyboard, sourcer,
                  workspace: Path) -> bool:
    """Apply re_source actions to the storyboard in place and persist it.

    Processes each re_source action by calling sourcer.re_source_scene()
    with proper context (narrative thread, hero claim, excluded URLs).
    Saves the storyboard after all re-sourcing is complete.

    Args:
        actions: List of (kind, scene_index) tuples from route_defects()
        storyboard: Storyboard object to modify in place
        sourcer: Sourcer agent with re_source_scene() method
        workspace: Path to workspace directory for saving

    Returns:
        True if anything changed (indicates re-render should follow)
    """
    workspace = Path(workspace)
    changed = False
    scenes_by_index = {s.index: s for s in storyboard.scenes}
    hero = (storyboard.hero_claim.claim_text
            if storyboard.hero_claim else "")
    for kind, idx in actions:
        if kind != "re_source":
            continue
        scene = scenes_by_index.get(idx)
        if scene is None:
            continue
        thread = (storyboard.narrative_thread[idx]
                  if storyboard.narrative_thread
                  and idx < len(storyboard.narrative_thread) else [])
        excluded = ({scene.chosen_asset.url}
                    if scene.chosen_asset else set())
        log.info("revise: re-sourcing scene %d", idx)
        sourcer.re_source_scene(
            scene, storyboard.blog.get("category", ""),
            exclude_urls=excluded, thread_keywords=thread, hero_claim=hero)
        changed = True
    if changed:
        save_storyboard(storyboard, workspace / "storyboard.json")
    # re_render with no re_source still counts as a change (re-render only)
    return changed or any(k == "re_render" for k, _ in actions)
