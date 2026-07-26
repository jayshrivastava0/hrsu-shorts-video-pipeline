"""Cinematographer — emits per-scene cinematography decisions via a flat
{"scenes":[...]} JSON schema on gemma3:4b. Decisions land on
Scene.cinematography. If the LLM call fails or returns invalid JSON, every
scene keeps its default Cinematography object so downstream consumers can
always read its fields safely."""
from __future__ import annotations
import logging

from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard, Cinematography

log = logging.getLogger(__name__)

_PALETTES     = {"red_tension", "cold_blue", "warm_brand",
                 "neutral_doc", "urgent_amber", "clinical_white"}
_TRANSITIONS  = {"cut", "slide_left", "slow_fade", "hard_cut"}
_MOTIONS      = {"slow_push", "hold", "fast_zoom", "drift", "parallax_lr"}
_PROSODIES    = {"hook_emphasis", "urgent_problem", "conversational",
                 "warm_cta", "matter_of_fact"}

_SYSTEM = """You are a Cinematographer for a 30-55s vertical B2B chemistry video.
For each scene, choose one value from each allowed set:
  color_grade   : red_tension | cold_blue | warm_brand | neutral_doc | urgent_amber | clinical_white
  transition_in : cut | hard_cut | slow_fade | slide_left
  motion        : slow_push | hold | fast_zoom | drift | parallax_lr
  voice_prosody : hook_emphasis | urgent_problem | conversational | warm_cta | matter_of_fact

Guidance:
  hook beat    → hard_cut, fast_zoom or parallax_lr, hook_emphasis, red_tension or urgent_amber
  stakes beat  → slow_fade, slow_push, urgent_problem, red_tension or urgent_amber
  mechanism beat → cut, hold or slow_push, conversational, cold_blue or clinical_white
  proof beat   → slow_fade, slow_push, matter_of_fact, neutral_doc or cold_blue
  cta beat     → slide_left, slow_push, warm_cta, warm_brand

Respond ONLY with raw JSON — no prose, no markdown:
{"scenes":[
  {"index":0,"color_grade":"...","transition_in":"...","motion":"...","voice_prosody":"..."},
  ...
]}
Include every scene index from 0 to N-1.
"""


class Cinematographer:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        # Seed every scene with defaults so consumers can always read fields.
        for s in sb.scenes:
            if s.cinematography is None:
                s.cinematography = Cinematography()

        prompt = self._build_prompt(sb)
        try:
            result = self.client.generate_json(prompt, system=_SYSTEM)
        except OllamaError as e:
            log.warning("Cinematographer: LLM call failed (%s); "
                        "all scenes will use default fallback cinematography", e)
            return sb

        scenes_data = result.get("scenes") if isinstance(result, dict) else None
        if not isinstance(scenes_data, list):
            log.warning("Cinematographer: response missing 'scenes' list (got %r); "
                        "using defaults", type(result))
            return sb

        applied = 0
        for entry in scenes_data:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(sb.scenes):
                continue
            cin = sb.scenes[idx].cinematography
            scene_applied = False

            cg = entry.get("color_grade")
            if cg in _PALETTES:
                cin.color_grade = cg
                scene_applied = True

            tr = entry.get("transition_in")
            if tr in _TRANSITIONS:
                cin.transition_in = tr
                scene_applied = True

            mo = entry.get("motion")
            if mo in _MOTIONS:
                cin.motion = mo
                scene_applied = True

            vp = entry.get("voice_prosody")
            if vp in _PROSODIES:
                cin.voice_prosody = vp
                scene_applied = True

            if scene_applied:
                applied += 1

        log.info("Cinematographer: applied decisions to %d/%d scenes",
                 applied, len(sb.scenes))
        return sb

    def _build_prompt(self, sb: Storyboard) -> str:
        hero = sb.hero_claim.claim_text if sb.hero_claim else ""
        lines = [
            f"Hero claim: {hero}",
            f"Region: {sb.blog.get('region')} | Category: {sb.blog.get('category')}",
            f"Total scenes: {len(sb.scenes)}",
            "Scenes:",
        ]
        for s in sb.scenes:
            lines.append(
                f"  index={s.index} beat={s.beat} duration={s.duration_target_s:.1f}s: "
                f"{s.narration[:120]}"
            )
        lines.append(
            "\nRespond ONLY with raw JSON: "
            '{"scenes":[{"index":0,...},...]} — one entry per scene, no extra text.'
        )
        return "\n".join(lines)
