"""Final narration pass via local Ollama. Tightens phrasing, ensures CTA close,
double-checks region semantics. Graceful degradation if Ollama is down."""
from __future__ import annotations
import json
import logging
from video_agent.ollama_client import OllamaClient, OllamaError, smart_client
from video_agent.storyboard import Storyboard

log = logging.getLogger(__name__)


class NarrationPolisher:
    """Local-Ollama final pass on scene narrations. No external API."""

    SYSTEM = """You are a video script polisher for an industrial-chemicals
brand video aimed at PROCUREMENT MANAGERS who already know the chemistry.
Given a list of scene narrations, return a JSON array of the SAME LENGTH
with each narration polished to:

1. STRICT WORD LIMIT: each scene has a `max_words` field — you MUST NOT
   exceed it. Count words carefully. Trim ruthlessly; do NOT add new facts.

2. SCENE 0 (THE HOOK) — must grab attention in the first 8 seconds.
   The viewer is technical: do NOT explain what calcium nitrate is. Do NOT
   use generic openers like "In today's industrial landscape...". The hook
   MUST do one of:
     (a) Lead with a hard procurement number — cost, dosage, % improvement,
         tonnage, or compliance threshold (e.g. "Acid mine drainage costs
         Australian operators $1.4B a year.")
     (b) State a sharp problem the procurement manager faces RIGHT NOW
         (e.g. "If you're sourcing accelerator admixture below 0°C, you
         already know the standard chloride blend is killing your rebar.")
     (c) Ask a direct procurement question (e.g. "Why are you still paying
         18% premium on EU-imported calcium nitrate?")
   No fluffy "did you know" framings.

3. Flow smoothly into the next scene — no abrupt topic jumps.

4. End the FINAL scene with a clean CTA close. Acceptable closes include:
     "Source your calcium nitrate from HRSU at hrsuindore.com."
     "Visit hrsuindore.com to learn how HRSU can help."
   NEVER end mid-sentence or with an open question.

5. Use region-correct geography. Map internal region codes carefully:
     gulf → Persian Gulf / GCC (Saudi Arabia, UAE, Qatar) — NOT Gulf of Mexico
     usa  → United States (mainland)
     eu   → European Union
     germany → Germany (DACH)
     australia → Australia / Oceania
     east_asia → Singapore / Southeast Asia
   Qualify ambiguous place-names with the country.

6. Sound natural when read aloud (no abbreviations like 'e.g.', no
   tongue-twisters, no parenthetical asides).

BITE RULES — mandatory polish criteria:
  - Every claim must be concrete: a number, a standard (REACH, NPDES,
    CAS 10124-37-5), a named consequence (NPDES violation, rebar corrosion,
    demolding time). Ban vague intensifiers (very, highly, significantly) and
    hedges (can, may, might, could).
  - Voice: confident technical peer talking to a buyer, not a marketer. Short
    declarative sentences. One idea per sentence.
  - End on the CTA exactly as written; do not pad after the URL.

Return ONLY JSON: [{"index": 0, "narration": "..."}, {"index": 1, ...}, ...]
"""

    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or smart_client()

    def run(self, sb: Storyboard) -> Storyboard:
        if not sb.scenes:
            return sb
        payload = [{
            "index": s.index,
            "duration_target_s": s.duration_target_s,
            "max_words": max(8, round(s.duration_target_s * 2.5)),
            "mood": s.visual_concept.mood,
            "narration": s.narration,
        } for s in sb.scenes]
        prompt = (
            f"Polish these {len(payload)} scene narrations for a "
            f"{sb.blog.get('region', 'default')} region "
            f"{sb.blog.get('category', 'industrial')} video.\n\n"
            f"Scenes:\n{json.dumps(payload, indent=2)}"
        )
        try:
            polished = self.ollama.generate_json(prompt, system=self.SYSTEM)
        except OllamaError as e:
            log.warning("NarrationPolisher: Ollama failed (%s); "
                        "keeping original narration", e)
            return sb
        if not isinstance(polished, list) or len(polished) != len(sb.scenes):
            log.warning("NarrationPolisher: returned %s items (expected %d); "
                        "keeping original narration",
                        len(polished) if isinstance(polished, list) else "non-list",
                        len(sb.scenes))
            return sb
        for orig, new in zip(sb.scenes, polished):
            if isinstance(new, dict) and isinstance(new.get("narration"), str) \
                    and new["narration"].strip():
                text = new["narration"].strip()
                max_w = max(8, round(orig.duration_target_s * 2.5))
                words = text.split()
                if len(words) > max_w:
                    text = " ".join(words[:max_w])
                    log.debug("NarrationPolisher: scene %d truncated %d->%d words",
                              orig.index, len(words), max_w)
                orig.narration = text

        # Dedup pass: if two consecutive narrations are >70% word-identical,
        # blank the duplicate so the storyboarder/reviser is forced to fix it.
        for i in range(1, len(sb.scenes)):
            prev_words = set(sb.scenes[i-1].narration.lower().split())
            cur_words = set(sb.scenes[i].narration.lower().split())
            if not prev_words or not cur_words:
                continue
            overlap = len(prev_words & cur_words) / max(1, len(cur_words))
            if overlap > 0.7:
                log.warning("NarrationPolisher: scene %d narration %.0f%% "
                            "overlaps with scene %d — flagging duplicate",
                            i, overlap * 100, i-1)
                sb.scenes[i].critic_notes.flags.append("narration_duplicate")

        log.info("NarrationPolisher: polished %d scene narrations", len(sb.scenes))
        return sb
