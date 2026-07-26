"""Per-scene Local Critic — evaluates voice/visual/text/transition coherence."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient
from video_agent.storyboard import Storyboard, Scene, CriticNotes
import video_agent.config as _cfg

log = logging.getLogger(__name__)

_SYSTEM = """You evaluate ONE scene of a 5-beat B2B chemistry video.
Your job: catch coherence problems before render.

Score 0-10 based on:
  - Does the visual MATCH what the voice describes?
  - Does on_screen_text ADD information (not duplicate the voice)?
  - Does this scene logically follow the previous one?
  - Does the scene serve the hero claim?

Use these flags (pick all that apply, or empty list if perfect):
  voice_visual_mismatch | text_duplicates_voice | text_unrelated |
  weak_transition | degraded_visual | off_hero_claim | unit_confusion

Respond as JSON:
{
  "alignment_score": <0-10 integer>,
  "flags": ["..."],
  "revision": "<one sentence on what to change, or null if score >= 7>"
}
"""


class LocalCritic:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        for i, scene in enumerate(sb.scenes):
            prev_narr = sb.scenes[i - 1].narration if i > 0 else "(none)"
            self._critique(scene, sb.hero_claim, prev_narr)
        return sb

    def _critique(self, scene: Scene, hero, prev_narr: str) -> None:
        asset_caption = (scene.chosen_asset.caption
                         if scene.chosen_asset else "(no image — fell back)")
        prompt = (
            f"Hero claim: {hero.claim_text if hero else '?'}\n"
            f"Beat role: {scene.beat}\n"
            f"Previous scene narration: {prev_narr}\n\n"
            f"Narration: {scene.narration}\n"
            f"On-screen text: {scene.on_screen_text}\n"
            f"Visual concept: {scene.visual_concept.subject} "
            f"({scene.visual_concept.modifier})\n"
            f"Chosen image caption: {asset_caption}\n"
            f"Degraded (no real image): {scene.degraded}"
        )
        try:
            out = self.client.generate_json(prompt, system=_SYSTEM)
        except Exception as e:
            log.warning("Local Critic failed for scene %d: %s", scene.index, e)
            return
        if not isinstance(out, dict):
            return
        flags = list(out.get("flags") or [])
        alignment_score = int(out.get("alignment_score", 10))

        # Vision-first pixel check: if the chosen image scored below the minimum
        # trust threshold, the critic flags it regardless of what the LLM says.
        # This ensures the reviser's re-source loop picks up low-quality visuals.
        if _cfg.VISION_FIRST_ENABLED and scene.chosen_asset is not None:
            vs = getattr(scene.chosen_asset, "vision_score", -1)
            if vs >= 0 and vs < _cfg.VISION_SELECT_MIN:
                if "visual_mismatch" not in flags:
                    flags.append("visual_mismatch")
                # Cap alignment score so the reviser triggers a re-source
                alignment_score = min(alignment_score, 4)
                log.info("Scene %d: vision_score %d < %d → forcing visual_mismatch",
                         scene.index, vs, _cfg.VISION_SELECT_MIN)

        scene.critic_notes = CriticNotes(
            alignment_score=alignment_score,
            flags=flags,
            revision=out.get("revision"),
        )
        log.info("Scene %d: score=%d flags=%s",
                 scene.index, scene.critic_notes.alignment_score,
                 scene.critic_notes.flags)
