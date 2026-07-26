"""Storyboarder — fills in scenes[] one per beat."""
from __future__ import annotations
import logging
import re
from video_agent.ollama_client import OllamaClient, OllamaError, smart_client
from video_agent.storyboard import Storyboard, Scene, VisualConcept

log = logging.getLogger(__name__)

_SYSTEM = """You write per-beat scenes for a 30-55 second B2B chemistry video.
The HERO CLAIM is the ONE thing the entire video says. Every scene must
reinforce it.

For each beat in the arc, return a scene with:
  - narration: verbatim sentence(s) the voiceover will say (~2.5 wps)
  - on_screen_text: ≤6 words; MUST add a number, brand, or contrast NOT
    already in the narration. Never paraphrase the voice.
  - visual_concept: {subject, modifier, type, mood, style_hint}
      type ∈ {"photo", "diagram", "clip", "chart_data"}
      mood ∈ {"problem", "mechanism", "proof", "brand"}

Per-beat assignments — each beat MUST cover its own territory, no overlap:
  * hook (scene 0): State the hero stat + the pain it represents. ALL CAPS
    on_screen_text. This is the ONLY scene allowed to mention the hero stat.
  * stakes (scene 1): The downstream CONSEQUENCE of the problem — regulatory
    fines, environmental damage, supply-chain risk, production stoppage.
    Talk about what happens IF you don't act. NO hero stat. NO restating
    the hook.
  * mechanism (scene 2): The chemistry / process — HOW calcium nitrate
    actually works. Reaction, ion exchange, pH chemistry, dosing. Concrete
    technical detail. NO stats from the hook or proof. Use type="diagram"
    or type="chart_data".
  * proof (scene 3): ONE specific case study with DIFFERENT numbers than the
    hook — a real site, a real trial, a real treated volume. The proof
    must add a NEW quantitative fact, not echo the hero stat.
  * cta (scene 4): HRSU brand + hrsuindore.com. A short value invitation —
    no stats, no chemistry, no problem-restatement.

ANTI-REPETITION CHECKLIST — run mentally before emitting JSON:
  1. Does the hero stat (number from the hook) appear in any scene other
     than scene 0? If YES, that scene is WRONG — rewrite it with a
     different angle entirely.
  2. Do scenes 1, 2, 3 each introduce something the previous scenes did
     NOT say? Stakes ≠ mechanism, mechanism ≠ proof. If two scenes feel
     like the same idea worded differently, the later one is wrong.
  3. Across the five scenes, are there at least 3 distinct quantitative
     facts (one in hook, one in proof, ideally one in mechanism)? Numbers
     must NOT repeat across scenes.
  4. Are diagram visual concepts SPECIFIC enough to retrieve a labelled,
     readable chart? "pH neutralization reaction" beats "chemical diagram".

Respond as JSON: {"scenes": [<scene>, ...]} with exactly len(arc) entries
in arc order.

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

For mood="proof" scenes that reference data, studies, or maps, prepend the
visual_concept.subject with the resolved region's primary country.
Examples:
  region=gulf, subject="oil refinery"
      → "Saudi Arabia oil refinery"
  region=australia, subject="ANFO mining operation"
      → "Western Australia ANFO mining operation"
  region=germany, subject="wastewater plant"
      → "Germany wastewater plant"
"""

_NUMERIC_RE = re.compile(r"\d")
_BRAND_TOKENS = {"hrsu", "reach", "epa", "iso", "anfo", "can", "h2s",
                 "h₂s", "h2so4", "co2", "ppm"}
_LEAKED_PREFIX_RE = re.compile(r"^\[.*?[\]:]", re.IGNORECASE)


def _clean_on_screen_text(text: str) -> str:
    """Strip model-leaked prefixes like '[Visual: ...]' and chars that break ffmpeg drawtext."""
    text = _LEAKED_PREFIX_RE.sub("", text).strip()
    # Remove chars that act as ffmpeg filtergraph separators or break drawtext parsing
    text = text.replace("[", "").replace("]", "").replace(",", " ")
    return " ".join(text.split())[:60]


_STAT_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _extract_stat_tokens(stat: str) -> list[str]:
    """Pull the distinctive numeric token(s) out of a hero stat string.
    'AMD costs $1.4 billion' -> ['1.4'], 'pH 4.5' -> ['4.5'], '90%' -> ['90'].
    Used to detect when a downstream scene parrots the hook's number."""
    if not stat:
        return []
    nums = _STAT_NUMBER_RE.findall(stat)
    # Drop trivially small numbers that recur naturally (0, 1, 2) — only
    # those are below 3.0 AND have no decimal. "4.5", "90", "1.4" all stay.
    out = []
    for n in nums:
        try:
            f = float(n.replace(",", "."))
        except ValueError:
            continue
        if f >= 3 or "." in n or "," in n:
            out.append(n)
    return out


def _narration_contains_stat(narration: str, stat_tokens: list[str]) -> bool:
    if not narration or not stat_tokens:
        return False
    found_nums = _STAT_NUMBER_RE.findall(narration)
    return any(n in found_nums for n in stat_tokens)


def _validate_on_screen_text(text: str, narration: str) -> str | None:
    """Returns a flag string if invalid, else None."""
    if len(text.split()) > 6:
        return "text_too_long"
    text_words = {w.lower().strip(".,;:!?") for w in text.split() if w}
    narr_words = {w.lower().strip(".,;:!?") for w in narration.split() if w}
    if text_words and (text_words & narr_words) and len(text_words - narr_words) <= 1:
        return "text_duplicates_voice"
    has_number = bool(_NUMERIC_RE.search(text))
    has_brand = bool(text_words & _BRAND_TOKENS)
    has_contrast = "vs" in text.lower() or "→" in text or "->" in text
    if not (has_number or has_brand or has_contrast):
        return "text_unrelated"
    return None


class Storyboarder:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or smart_client()

    def run(self, sb: Storyboard) -> Storyboard:
        if not sb.hero_claim or not sb.arc:
            raise RuntimeError("Storyboarder needs hero_claim and arc to be populated")
        arc_outline = "\n".join(
            f"  {i}. {b.beat} ({b.duration_target_s}s) — {b.purpose}"
            for i, b in enumerate(sb.arc)
        )
        prompt = (
            f"Blog title: {sb.blog.get('title', '')}\n"
            f"Category: {sb.blog.get('category', 'industrial')} | "
            f"Region: {sb.blog.get('region')} | "
            f"Audience: {sb.blog.get('persona', 'procurement')}\n\n"
            f"Hero claim: {sb.hero_claim.claim_text}\n"
            f"Hero stat: {sb.hero_claim.stat}\n"
            f"Source quote: {sb.hero_claim.source_quote}\n\n"
            f"Arc:\n{arc_outline}\n\n"
            f"Supporting facts (use in mechanism/proof; do not give them "
            f"their own scene):\n"
            + "\n".join(f"  - {f.get('value','')} {f.get('unit','')}: "
                        f"{f.get('claim','')}"
                        for f in sb.supporting_facts[:5])
            + "\n\nWrite the scenes."
        )
        out = self.client.generate_json(prompt, system=_SYSTEM)
        scenes_raw = out.get("scenes", []) if isinstance(out, dict) else []
        if len(scenes_raw) != len(sb.arc):
            log.warning("Storyboarder returned %d scenes for %d beats; "
                        "padding/truncating", len(scenes_raw), len(sb.arc))
        scenes_raw = (scenes_raw + [{}] * len(sb.arc))[:len(sb.arc)]
        sb.scenes = []
        for i, (beat, raw) in enumerate(zip(sb.arc, scenes_raw)):
            vc_raw = raw.get("visual_concept") or {}
            sb.scenes.append(Scene(
                index=i, beat=beat.beat,
                narration=raw.get("narration", ""),
                on_screen_text=_clean_on_screen_text(raw.get("on_screen_text", "")),
                visual_concept=VisualConcept(
                    subject=vc_raw.get("subject", ""),
                    modifier=vc_raw.get("modifier", ""),
                    type=vc_raw.get("type", "photo"),
                    mood=vc_raw.get("mood", "problem"),
                    style_hint=vc_raw.get("style_hint", ""),
                ),
                duration_target_s=beat.duration_target_s,
                transition_in="cut" if i == 0 else "fade",
            ))
        # Validate on-screen text
        for scene in sb.scenes:
            flag = _validate_on_screen_text(scene.on_screen_text, scene.narration)
            if flag:
                scene.critic_notes.flags.append(flag)
                scene.critic_notes.alignment_score = min(
                    scene.critic_notes.alignment_score, 6)
                log.debug("Scene %d on_screen_text invalid: %s", scene.index, flag)

        # Repetition guard: scan scenes 1-4 for the hero stat; rewrite any
        # offender. Gemma reliably ignores the "stat only in hook" rule in
        # the system prompt, so we enforce it programmatically.
        self._rewrite_stat_repeaters(sb)

        log.info("Storyboarder: %d scenes generated", len(sb.scenes))
        return sb

    def _rewrite_stat_repeaters(self, sb: Storyboard) -> None:
        """Scenes 1+ that echo the hook's hero stat get rewritten with a
        fresh-angle prompt. Best-effort: failures leave the scene unchanged."""
        if not sb.hero_claim or not sb.scenes or len(sb.scenes) < 2:
            return
        stat_tokens = _extract_stat_tokens(sb.hero_claim.stat)
        if not stat_tokens:
            return
        for scene in sb.scenes[1:]:
            if not _narration_contains_stat(scene.narration, stat_tokens):
                continue
            log.info("Scene %d echoes hero stat %r — rewriting for fresh angle",
                     scene.index, sb.hero_claim.stat)
            try:
                fresh = self._rewrite_for_fresh_angle(sb, scene, stat_tokens)
            except OllamaError as e:
                log.warning("Scene %d stat-repeat rewrite failed (%s); keeping",
                            scene.index, e)
                continue
            if fresh:
                scene.narration = fresh.get("narration", scene.narration)
                scene.on_screen_text = _clean_on_screen_text(
                    fresh.get("on_screen_text", scene.on_screen_text)
                )

    def _rewrite_for_fresh_angle(self, sb: Storyboard, scene: Scene,
                                  stat_tokens: list[str]) -> dict | None:
        beat_focus = {
            "stakes": "the downstream consequence of inaction — regulatory "
                      "fines, environmental cost, supply-chain risk. NO numbers "
                      "from the hook.",
            "mechanism": "the chemistry — reaction equation, ion exchange, "
                         "or dosing principle. Concrete technical detail, "
                         "no statistics from the hook.",
            "proof": "ONE specific case study with a DIFFERENT number than "
                     "the hook (a treated volume, a percent removal, a real "
                     "site name).",
            "cta": "HRSU brand promise + hrsuindore.com. No stats, no chemistry.",
        }.get(scene.beat, "a fresh angle on the problem that the hook did "
                          "not cover.")
        prompt = (
            f"Rewrite this scene. The current narration repeats the hero "
            f"stat ({sb.hero_claim.stat}) which already appeared in the "
            f"hook (scene 0). The hero stat MUST NOT appear in any other "
            f"scene.\n\n"
            f"Beat: {scene.beat}\n"
            f"Beat focus: {beat_focus}\n"
            f"Current (bad) narration: {scene.narration}\n\n"
            f"Forbidden tokens in your new narration (these are the hero "
            f"stat): {', '.join(stat_tokens)}\n\n"
            f'Return ONLY JSON: {{"narration": "...", "on_screen_text": "..."}}'
        )
        result = self.client.generate_json(prompt, system=_SYSTEM)
        if not isinstance(result, dict):
            return None
        new_narr = result.get("narration", "")
        if _narration_contains_stat(new_narr, stat_tokens):
            log.warning("Scene %d rewrite still contains stat tokens; giving up",
                        scene.index)
            return None
        return result

    def regenerate_beat(self, sb, scene_index: int, director_suggestion: str) -> dict | None:
        """One-shot regeneration for a single beat, scoped to the index given.

        Returns a dict with keys: narration, on_screen_text, visual_concept
        (dataclass-shaped dict), or None on failure.

        Used by Reviser when GlobalDirector flags a weakest_beat with a
        structural rewrite suggestion."""
        scene = sb.scenes[scene_index]
        prompt = (
            f"You wrote this scene previously. The director thinks it's weak and "
            f"has suggested a structural change. Rewrite ONLY this scene's "
            f"narration, on_screen_text, and visual_concept based on the suggestion.\n\n"
            f"Director's suggestion:\n{director_suggestion}\n\n"
            f"Current scene (index {scene_index}, mood={scene.visual_concept.mood}, "
            f"duration_target_s={scene.duration_target_s}):\n"
            f"  narration: {scene.narration}\n"
            f"  on_screen_text: {scene.on_screen_text}\n"
            f"  visual_concept.subject: {scene.visual_concept.subject}\n"
            f"  visual_concept.modifier: {scene.visual_concept.modifier}\n\n"
            f"Blog region: {sb.blog.get('region', 'default')}\n"
            f"Blog category: {sb.blog.get('category', 'industrial')}\n\n"
            f"Return ONLY JSON: "
            '{{"narration": "...", "on_screen_text": "...", '
            '"visual_concept": {{"subject": "...", "modifier": "...", "mood": "..."}}}}'
        )
        try:
            result = self.client.generate_json(prompt, system=_SYSTEM)
        except Exception as e:
            log.warning("regenerate_beat: Ollama failed (%s); keeping original", e)
            return None
        if not isinstance(result, dict):
            return None
        return result
