"""Strategist — picks the hero claim and the 5-beat arc."""
from __future__ import annotations
import logging
import re
from video_agent.ollama_client import OllamaClient, smart_client
from video_agent.storyboard import Storyboard, HeroClaim, Beat

log = logging.getLogger(__name__)


def _is_grounded(hero_stat: str, facts: list, article_blob: str) -> bool:
    """Verify hero.stat is numerically grounded in the provided facts/article.

    Works by checking:
    1. The stat must contain at least one number.
    2. That number must appear in the facts list or article text.
    3. At least one content word (>5 chars) from the stat must appear in context.
    """
    numbers = re.findall(r"\d+(?:[.,]\d+)?", hero_stat)
    if not numbers:
        log.debug("Grounding check FAIL: no numbers in hero stat: %r", hero_stat)
        return False

    facts_blob = " ".join(
        f"{f.get('value', '')} {f.get('unit', '')} {f.get('claim', '')}"
        for f in facts
    )
    context = (facts_blob + " " + article_blob[:3000]).lower()

    if not any(n in context for n in numbers):
        log.debug("Grounding check FAIL: numbers %s not found in facts/article", numbers)
        return False

    # At least one substantive word from the stat must appear in context
    stat_words = [
        w.lower().strip(".,;:%()[]") for w in hero_stat.split() if len(w) > 5
    ]
    if stat_words and not any(w in context for w in stat_words):
        log.debug("Grounding check FAIL: no content words from stat found in context")
        return False

    return True

_SYSTEM = """You are a B2B chemistry video Strategist for HRSU. Pick exactly
ONE hero claim from the article that meets all three criteria:
  - SURPRISING (counterintuitive or notable, not generic)
  - SPECIFIC (a number with a unit and clear context)
  - AUDIENCE-FIT (a procurement manager in this region cares)

GROUNDING RULE: hero.stat must come from the "Numeric facts" list — the number
and unit must match one of those facts. You may rephrase the surrounding words
to make the stat punchy, but do NOT invent numbers. hero.claim_text should be
your strongest, most compelling framing of why that stat matters.

BITE RULES — apply to every beat and every narration line:
  - Open scene 1 (hook) with a specific, surprising, concrete fact or a sharp
    question a procurement manager would stop scrolling for. Never open with a
    definition or "In this video".
  - Every claim must be concrete: a number, a standard (REACH, NPDES,
    CAS 10124-37-5), a named consequence (NPDES violation, rebar corrosion,
    demolding time). Ban vague intensifiers (very, highly, significantly) and
    hedges (can, may, might, could).
  - Voice: confident technical peer talking to a buyer, not a marketer. Short
    declarative sentences. One idea per sentence.
  - End on the CTA exactly as written; do not pad after the URL.

Build a 5-beat arc supporting that claim:
  1 hook (3-4s) — state the hero stat as a question or claim
  2 stakes (5-7s) — cost of NOT solving this
  3 mechanism (8-12s) — how the chemistry works, simply
  4 proof (8-12s) — concrete validation: regional case, regulation, or comparison
  5 cta (4-6s) — HRSU tie-back: "HRSU supplies this. Visit hrsuindore.com"

Other numeric facts go into supporting_facts (NOT their own scene).

Respond as JSON:
{
  "hero": {"stat": "...", "claim_text": "...", "source_quote": "..."},
  "arc": [{"beat": "hook|stakes|mechanism|proof|cta",
           "purpose": "...", "duration_target_s": 3.5}, ...],
  "supporting_facts": [{"value": "...", "unit": "...", "claim": "..."}, ...]
}

REGION SEMANTICS — read carefully:
The blog's `region` field uses internal codes, NOT colloquial geography. Map them as:
  australia  → Australia / Oceania
  usa        → United States (mainland)
  eu         → European Union (continental Europe + UK)
  germany    → Germany (DACH region)
  east_asia  → Singapore / Southeast Asia
  gulf       → Persian Gulf / GCC states (UAE, Saudi Arabia, Qatar, Kuwait,
               Bahrain, Oman) — NOT Gulf of Mexico

When generating narration, on-screen text, or visual_concept queries that
reference geography, follow these rules:

1. ALWAYS qualify ambiguous place-names with the country or sub-region.
   - "Persian Gulf coastline", not "Gulf coast"
   - "Saudi Arabia oil refinery", not "regional oil refinery"
   - "Australian outback mining", not "outback mining"

2. If a place-name could refer to multiple locations (Gulf, Georgia, Cordoba,
   Newcastle, Birmingham, Naples, Tripoli, etc.) include a disambiguating
   qualifier — country name, region adjective, or nearby landmark.

3. Visual queries (visual_concept.subject) MUST include a region-locking word:
   country name, region adjective ("Middle Eastern"), or famous landmark
   from that region.

4. When in doubt, prefer the country name. "Saudi Arabia" is always safer
   than "the Gulf" when describing visuals.
"""


class Strategist:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or smart_client()

    _MAX_ATTEMPTS = 3

    def run(self, sb: Storyboard, facts: list, blog_html_excerpt: str) -> Storyboard:
        numbered_facts = "\n".join(
            f"  [{i+1}] {f.get('value', '')} {f.get('unit', '')}: {f.get('claim', '')}"
            for i, f in enumerate(facts[:15])
        )
        base_prompt = (
            f"Region: {sb.blog.get('region')}\n"
            f"Category: {sb.blog.get('category')}\n"
            f"Audience: {sb.blog.get('persona', 'procurement')} managers\n\n"
            f"Numeric facts extracted from the article "
            f"(hero.stat MUST be copied verbatim from this list):\n"
            f"{numbered_facts}\n\n"
            f"Article excerpt:\n{blog_html_excerpt[:2000]}\n\n"
            "Pick ONE hero claim and produce the 5-beat arc."
        )

        last_err: str | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            prompt = base_prompt
            if attempt > 1:
                # Escalate constraint: name the allowed values explicitly
                allowed = "; ".join(
                    f"{f.get('value', '')} {f.get('unit', '')}"
                    for f in facts[:5]
                )
                prompt = (
                    f"PREVIOUS ATTEMPT FAILED GROUNDING CHECK ({last_err}).\n"
                    f"You MUST use one of these exact values as hero.stat: {allowed}\n\n"
                ) + base_prompt

            try:
                out = self.client.generate_json(prompt, system=_SYSTEM)
            except Exception as e:
                raise RuntimeError(f"Strategist Ollama call failed: {e}") from e

            if not isinstance(out, dict) or "hero" not in out or "arc" not in out:
                last_err = f"malformed JSON (attempt {attempt})"
                log.warning("Strategist malformed output (attempt %d): %r", attempt, out)
                continue

            hero_dict = out["hero"] if isinstance(out["hero"], dict) else {}
            hero_raw = {k: hero_dict.get(k, "") for k in ("stat", "claim_text", "source_quote")}
            hero = HeroClaim(**hero_raw)
            if not _is_grounded(hero.stat, facts, blog_html_excerpt):
                last_err = f"hero stat not grounded in facts: {hero.stat!r}"
                log.warning("Strategist grounding check FAILED (attempt %d): %s",
                            attempt, hero.stat)
                if attempt < self._MAX_ATTEMPTS:
                    continue
                raise RuntimeError(
                    f"Strategist failed to produce a grounded hero claim after "
                    f"{self._MAX_ATTEMPTS} attempts. Last stat: {hero.stat!r}"
                )

            sb.hero_claim = hero
            sb.arc = [
                Beat(index=i, beat=b["beat"], purpose=b.get("purpose", ""),
                     duration_target_s=float(b.get("duration_target_s", 5.0)))
                for i, b in enumerate(out["arc"])
            ]
            sb.supporting_facts = out.get("supporting_facts", [])
            log.info(
                "Strategist: hero=%s (attempt %d); %d beats; %d supporting facts",
                sb.hero_claim.stat, attempt, len(sb.arc), len(sb.supporting_facts),
            )
            return sb

        raise RuntimeError(f"Strategist exhausted all attempts. Last error: {last_err}")
