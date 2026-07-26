"""Reviser — exactly ONE rewrite pass; no loops."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient
from video_agent.storyboard import Storyboard, Scene

log = logging.getLogger(__name__)

_SCENE_FIELD_REWRITE_SYSTEM = """You rewrite ONE field of ONE scene based on
critic feedback.  Return strict JSON with only the keys the user asks for.
Be terse — match the original's voice, don't expand it."""


class Reviser:
    def __init__(self, sourcer=None, max_resource_attempts: int = 2,
                 client: OllamaClient | None = None):
        self.sourcer = sourcer
        self.max_resource_attempts = max_resource_attempts
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        for scene in sb.scenes:
            notes = scene.critic_notes
            if notes.alignment_score >= 7:
                continue
            flags = set(notes.flags or [])
            # Any of these mean the picked visual is wrong for the narration —
            # re-source before rewriting text.
            if flags & {"voice_visual_mismatch", "off_hero_claim",
                        "visual_mismatch", "degraded_visual"}:
                self._re_source(scene, sb)
            self._rewrite_scene(scene, sb)
            # Director-suggested structural rewrite for the weakest beat
            director = getattr(sb, "director_suggestion", None) or {}
            if director.get("weakest_beat") == scene.index:
                self._structural_rewrite(scene, sb)
        return sb

    def _structural_rewrite(self, scene, sb):
        """Called when GlobalDirector flags this scene as weakest_beat with a
        rewrite suggestion. Calls Storyboarder.regenerate_beat scoped to this
        scene, then re-sources the asset if the visual_concept changed."""
        director = getattr(sb, "director_suggestion", None) or {}
        suggestion = director.get("suggestion", "")
        if not suggestion:
            return
        log.info("Scene %d: structural rewrite triggered (%s)",
                 scene.index, suggestion[:80])
        from video_agent.agents.storyboarder import Storyboarder
        rewritten = Storyboarder().regenerate_beat(
            sb, scene.index, director_suggestion=suggestion,
        )
        if not rewritten:
            return
        if isinstance(rewritten.get("narration"), str):
            scene.narration = rewritten["narration"]
        if isinstance(rewritten.get("on_screen_text"), str):
            scene.on_screen_text = rewritten["on_screen_text"]
        vc = rewritten.get("visual_concept")
        if isinstance(vc, dict):
            from video_agent.storyboard import VisualConcept
            scene.visual_concept = VisualConcept(
                subject=vc.get("subject", scene.visual_concept.subject),
                modifier=vc.get("modifier", scene.visual_concept.modifier),
                type=vc.get("type", scene.visual_concept.type),
                mood=vc.get("mood", scene.visual_concept.mood),
                style_hint=vc.get("style_hint", scene.visual_concept.style_hint),
            )
        # Re-source the asset since visual_concept may have changed
        if self.sourcer:
            thread = (sb.narrative_thread[scene.index]
                      if sb.narrative_thread and scene.index < len(sb.narrative_thread)
                      else [])
            hero = sb.hero_claim.claim_text if sb.hero_claim else ""
            self.sourcer.re_source_scene(
                scene, sb.blog.get("category", ""), exclude_urls=set(),
                thread_keywords=thread, hero_claim=hero,
            )

    def _re_source(self, scene, sb):
        if not self.sourcer:
            return
        thread = (sb.narrative_thread[scene.index]
                  if sb.narrative_thread and scene.index < len(sb.narrative_thread)
                  else [])
        hero = sb.hero_claim.claim_text if sb.hero_claim else ""
        excluded = {scene.chosen_asset.url} if scene.chosen_asset else set()
        for attempt in range(self.max_resource_attempts):
            self.sourcer.re_source_scene(
                scene, sb.blog.get("category", ""),
                exclude_urls=excluded,
                thread_keywords=thread, hero_claim=hero,
            )
            if scene.chosen_asset and scene.chosen_asset.url not in excluded:
                log.info("Scene %d: re-sourced (attempt %d, new source=%s)",
                         scene.index, attempt + 1, scene.chosen_asset.source)
                return
            if scene.chosen_asset:
                excluded.add(scene.chosen_asset.url)
        log.warning("Scene %d: re-source exhausted after %d attempts; keeping original",
                    scene.index, self.max_resource_attempts)

    def _rewrite_scene(self, scene: Scene, sb: Storyboard) -> None:
        flags = scene.critic_notes.flags
        wants_text = bool({"text_duplicates_voice", "text_unrelated"} & set(flags))
        wants_visual = bool({"voice_visual_mismatch", "off_hero_claim"} & set(flags))
        wants_narration = "weak_transition" in flags or scene.critic_notes.alignment_score < 4

        request_fields: list[str] = []
        if wants_text:
            request_fields.append("on_screen_text")
        if wants_narration:
            request_fields.append("narration")
        if wants_visual:
            request_fields.append("visual_concept")
        if not request_fields:
            request_fields = ["on_screen_text"]

        prompt = (
            f"Hero claim: {sb.hero_claim.claim_text if sb.hero_claim else '?'}\n"
            f"Beat: {scene.beat}\n"
            f"Current narration: {scene.narration}\n"
            f"Current on-screen text: {scene.on_screen_text}\n"
            f"Critic note: {scene.critic_notes.revision}\n\n"
            f"Rewrite ONLY these fields and return JSON: {request_fields}"
        )
        try:
            out = self.client.generate_json(prompt,
                                            system=_SCENE_FIELD_REWRITE_SYSTEM)
        except Exception as e:
            log.warning("Reviser failed on scene %d: %s", scene.index, e)
            return
        if not isinstance(out, dict):
            return
        if "narration" in out:
            scene.narration = str(out["narration"])
        if "on_screen_text" in out:
            scene.on_screen_text = str(out["on_screen_text"])[:60]
        if "visual_concept" in out and isinstance(out["visual_concept"], dict):
            for k, v in out["visual_concept"].items():
                if hasattr(scene.visual_concept, k):
                    setattr(scene.visual_concept, k, v)
            log.info("Scene %d: visual concept changed; re-sourcing", scene.index)
            self.sourcer._source_scene(scene, sb.blog.get("category", ""))
        log.info("Scene %d: revised %s", scene.index, request_fields)
