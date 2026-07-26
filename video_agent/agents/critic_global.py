"""Global Director — evaluates the whole arc, suggests structural rewrites."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient
from video_agent.storyboard import Storyboard, DirectorNotes

log = logging.getLogger(__name__)

_SYSTEM = """You are the Director reviewing a 30-55s B2B video storyboard.
The video must say ONE thing — the hero claim — across 5 beats:
hook → stakes → mechanism → proof → cta.

Evaluate:
  - arc_quality (0-10): does the arc build toward the CTA?
  - hero_claim_supported (bool): does every beat reinforce it?
  - weakest_beat (int 0-4 | null): which one is dragging?
  - missing (list of strings): what's absent? (e.g., "regional anchor")
  - redundant (list of beat indices 0-4): which beats overlap?
  - ending_strength (0-10): how compelling is the CTA tie-back?
  - revision_for_strategist (string | null): one structural change to make,
    or null if arc_quality >= 7

Respond as strict JSON.
"""


class GlobalDirector:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        outline = "\n".join(
            f"  {i}. [{s.beat}] narration={s.narration[:80]!r}  "
            f"text={s.on_screen_text!r}  visual={s.visual_concept.subject!r}"
            for i, s in enumerate(sb.scenes)
        )
        hero = sb.hero_claim.claim_text if sb.hero_claim else "(none)"
        prompt = f"Hero claim: {hero}\n\nStoryboard:\n{outline}\n\nReview it."
        try:
            out = self.client.generate_json(prompt, system=_SYSTEM)
        except Exception as e:
            log.warning("Global Director failed: %s", e)
            return sb
        if not isinstance(out, dict):
            return sb
        sb.director_notes = DirectorNotes(
            arc_quality=int(out.get("arc_quality", 10)),
            hero_claim_supported=bool(out.get("hero_claim_supported", True)),
            weakest_beat=out.get("weakest_beat"),
            missing=list(out.get("missing") or []),
            redundant=list(out.get("redundant") or []),
            ending_strength=int(out.get("ending_strength", 10)),
            revision_for_strategist=out.get("revision_for_strategist"),
        )
        log.info("Director: arc_quality=%d weakest_beat=%s missing=%s",
                 sb.director_notes.arc_quality,
                 sb.director_notes.weakest_beat,
                 sb.director_notes.missing)
        return sb
