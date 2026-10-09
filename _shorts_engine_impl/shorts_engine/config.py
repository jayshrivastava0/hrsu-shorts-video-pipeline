"""
Configuration for shorts_engine.

This module imports selectively from video_agent.config to consume shared
constants, then defines shorts_engine-specific configuration.

IMPORTANT: Do NOT re-export video_agent.config to callers. Import internally only.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from shorts_engine.errors import EngineConfigError

logger = logging.getLogger(__name__)

# ── Project structure ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
OUTPUT_BASE = PROJECT_ROOT / "output" / "shorts"
BRAND_FACTS_PATH = PROJECT_ROOT / "brand_facts.yaml"

# ── Import from video_agent.config (internal consumption only) ─────────────
# These are boundary imports: we consume them, but never expose them upstream.
try:
    from sys import path as _sys_path
    if str(PROJECT_ROOT) not in _sys_path:
        _sys_path.insert(0, str(PROJECT_ROOT))

    from video_agent.config import (
        SMART_TEXT_MODEL,
        OLLAMA_MODEL,
        SCRIPT_BANNED_PHRASES,
        BRAND_GOLD,
        BRAND_DARK_NAVY,
        BRAND_NAVY_2,
        BRAND_TEXT_LIGHT,
        BRAND_TEXT_MUTED,
    )
    logger.debug(
        f"Loaded video_agent config: SMART_TEXT_MODEL={SMART_TEXT_MODEL}, "
        f"OLLAMA_MODEL={OLLAMA_MODEL}"
    )
except ImportError as e:
    logger.warning(f"Could not import from video_agent.config: {e}")
    # Provide defaults so tests can run in isolation
    SMART_TEXT_MODEL = "gemma4:31b-cloud"
    OLLAMA_MODEL = "gemma3:4b"
    SCRIPT_BANNED_PHRASES = [
        "as an ai", "in this video", "thanks for watching",
        "hope you enjoyed", "i don't have", "as of my last update",
    ]
    BRAND_GOLD = "#d4af37"
    BRAND_DARK_NAVY = "#0a192f"
    BRAND_NAVY_2 = "#0a1428"
    BRAND_TEXT_LIGHT = "#ccd6f6"
    BRAND_TEXT_MUTED = "#8892b0"

# ── Beat/Scene structure ────────────────────────────────────────────────────
# Per-PURPOSE pacing guidance (not per fixed beat name/position) -- beats are
# now free-form in count/order/naming (2026-08-26 creative-flow redesign); a
# beat's `purpose` tag is what carries pacing intent forward, consumed by
# shotlist.py (type suggestion, CTA-length cap). The SCRIPT stage no longer
# enforces per-purpose word budgets (2026-10-08 explanation-first redesign:
# length follows the verified plan, only the TOTAL_MIN_S floor is gated). "other" is the fallback for any beat whose
# purpose isn't one of the five named ones.
PURPOSE_TEMPLATE: dict[str, dict] = {
    "hook":      {"min_s": 2.0, "max_s": 4.0},
    "stakes":    {"min_s": 4.0, "max_s": 6.0},
    "mechanism": {"min_s": 8.0, "max_s": 12.0},
    "proof":     {"min_s": 6.0, "max_s": 10.0},
    "cta":       {"min_s": 6.0, "max_s": 8.0},
    "other":     {"min_s": 3.0, "max_s": 8.0},
}
MIN_BEATS = 3

# ── Narration timing ────────────────────────────────────────────────────────
# 1.7, not the spec's 2.6: measured empirically on real output. A live run
# synthesized a 95-word script into 53.7s of voice (en-GB-RyanNeural on
# technical B2B vocabulary) = 1.77 words/s narration-only, ~1.73 including
# inter-beat gaps. At 2.6 the total-duration gate demanded >=91 words "for
# 35s", but 91+ words actually produce ~54s of voice -> ~57s videos, busting
# the 50s ceiling -- while the writer's natural 82-89-word drafts (rejected
# five runs straight) would have made correctly-sized videos. The spec's
# INTENT (35-50s final videos) wins over its estimate figure; 1.7 keeps
# estimates ~4% conservative vs. the 1.77 measurement, so downstream shot
# spans and reflow deltas err slightly long rather than clipping.
WORDS_PER_SECOND = 1.7
WORD_BUDGET_TOLERANCE = 0.20  # allow ±20% variance from target word count

# ── Script quality gates ───────────────────────────────────────────────────
# Fear-filler / hype phrases banned on top of SCRIPT_BANNED_PHRASES (spec §4
# Stage 3). Plain (non-regex) substrings, matched case-insensitively against
# beat narration/card_text -- see shorts_engine.stages.script.gate_banned.
FEAR_FILLER_PATTERNS = [
    "is everything", "crippling", "game-changer", "game changer",
    "revolutionary", "catastrophic", "skyrocket",
]

# Citation classification (spec §4 Stage 1). "paper" = publisher/DOI/preprint
# domains or .pdf; "standard" = standards/regulatory bodies; else "web".
PAPER_DOMAINS = [
    "springer.com", "link.springer.com", "sciencedirect.com", "mdpi.com",
    "wiley.com", "onlinelibrary.wiley.com", "tandfonline.com", "nature.com",
    "acs.org", "pubs.acs.org", "rsc.org", "pubs.rsc.org",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "arxiv.org", "doi.org",
]
STANDARD_DOMAINS = ["europa.eu", "eur-lex.europa.eu", "epa.gov", "iso.org"]

# ── LLM behavior ───────────────────────────────────────────────────────────
# 5, not 3: SCRIPT's writer must satisfy both the per-beat word budget AND
# the aggregate TOTAL_MIN_S floor (there is no ceiling) simultaneously
# (gate_total_duration) -- live runs showed it converging (33.5s -> 34.2s ->
# beat-level overshoot while fixing the aggregate) but needing more than 3
# attempts to land inside every constraint at once.
LLM_MAX_RETRIES = 5
LLM_RETRY_DELAY_S = 2  # exponential backoff: 2s, 4s, 8s
LLM_TIMEOUT_S = 60

# ── Model roles (spec 2026-10-08 §8) ───────────────────────────────────────
# Planner/writer = the user's choice (Gemma). Verifier and critic MUST be a
# different model family from both the planner and the writer (a model checking
# its own claims agrees with itself); check_role_independence() enforces that
# at call time.
# Verifier/critic default: nemotron-3-ultra:cloud. glm-5.2:cloud needs a paid
# Ollama plan (HTTP 402), glm-5.1:cloud is retired (410), minimax-m3:cloud 402;
# nemotron-3-ultra:cloud passed the 24-case scripts/eval_verifier.py bake-off
# with false_support_rate 0.0 on 2026-10-09.
# Any role can be overridden with env HRSU_MODEL_<ROLE> (e.g. HRSU_MODEL_VERIFIER).
MODEL_ROLES: dict[str, str] = {
    "planner": SMART_TEXT_MODEL,
    "writer": SMART_TEXT_MODEL,
    "verifier": "nemotron-3-ultra:cloud",
    "critic": "nemotron-3-ultra:cloud",
}
_INDEPENDENT_ROLES = ("verifier", "critic")


def model_for_role(role: str) -> str:
    """Model name for a role; env HRSU_MODEL_<ROLE> overrides the default."""
    override = os.environ.get(f"HRSU_MODEL_{role.upper()}")
    if override:
        return override
    try:
        return MODEL_ROLES[role]
    except KeyError:
        raise EngineConfigError(
            f"unknown model role {role!r}; known: {sorted(MODEL_ROLES)}") from None


def model_family(model: str) -> str:
    """'gemma4:31b-cloud' -> 'gemma'; 'glm-5.2:cloud' -> 'glm'; 'org/gemma-x:7b' -> 'gemma'
    (a namespaced name is reduced to its last path segment first)."""
    name = model.strip().lower().rsplit("/", 1)[-1]
    return re.split(r"[-:0-9._]", name, maxsplit=1)[0]


def check_role_independence(role: str) -> None:
    """Raise EngineConfigError if a verifier/critic shares the family of the writer or
    the planner (the verifier grades the planner's claims, the critic the writer's script)."""
    if role not in _INDEPENDENT_ROLES:
        return
    for graded in ("writer", "planner"):
        if model_family(model_for_role(role)) == model_family(model_for_role(graded)):
            raise EngineConfigError(
                f"role {role!r} uses the same model family as the {graded} "
                f"({model_for_role(role)!r} vs {model_for_role(graded)!r}); a model must "
                f"not grade its own work")


# ── Explanation plan / verification (spec 2026-10-08 §5-7) ─────────────────
MIN_VERIFIED_STEPS = 3
PLAN_MAX_REPAIR_ROUNDS = 2
SCRIPT_MAX_REWRITES = 2          # critic-triggered rewrites before holding
RETRIEVAL_MAX_RESULTS = 5
RETRIEVAL_MAX_FETCHES = 6
RETRIEVAL_MAX_PASSAGES = 3
RETRIEVAL_FETCH_TIMEOUT_S = 10
RETRIEVAL_SEARCH_RETRIES = 2
# YouTube Shorts accepts up to 3 minutes (verify against current YouTube rules);
# anything longer is labelled long-form at packaging time.
SHORT_FORM_MAX_S = 180.0

# ── Canvas & card design system (spec §5) ──────────────────────────────────
CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30
SAFE_TOP_PX = 220      # v3 margins — stricter than video_agent/safezone.py
SAFE_BOTTOM_PX = 420
SAFE_SIDE_PX = 72
BRAND_LOGO_FILE = PROJECT_ROOT / "asset_library" / "brand" / "Logo.png"

# ── Shotlist bounds (spec §4 Stage 4) ──────────────────────────────────────
SHOT_MIN_S = 1.8
SHOT_MAX_S = 4.5
SHOT_TARGET_MIN_S = 2.0
SHOT_TARGET_MAX_S = 3.5
LOGO_CTA_MAX_S = 10.0   # CTA beat is a single end-card shot; exempt from 4.5s
TOTAL_MIN_S = 30.0

# ── Audio (spec §4 Stage 5) ────────────────────────────────────────────────
MIN_SEGMENT_BYTES = 1024          # F10 guard: no zero/near-zero-byte voice files
# 0.65, not 0.15: a live run on real technical B2B content (multi-syllable
# vocabulary -- "denitrification", "wastewater", "optimizing" -- speaks
# measurably slower than WORDS_PER_SECOND=2.6 assumes) measured actual voice
# duration at 1.47x the script estimate on every beat. This guard exists to
# catch a genuinely broken/near-silent synthesis (paired with the separate
# MIN_SEGMENT_BYTES check), not to enforce estimate precision -- ASSEMBLE's
# reflow() already re-flows shot durations against the REAL measured voice
# duration (encoder.probe_duration), not this estimate, so a wide-but-real
# gap here is expected and safely absorbed downstream, not a defect.
AUDIO_DURATION_TOLERANCE = 0.65   # actual voice vs script estimate ±65%
AUDIO_BEAT_GAP_MS = 300           # silence between beats in the stitched track
PROSODY_BY_BEAT = {
    "hook": "hook_emphasis", "stakes": "urgent_problem",
    "mechanism": "conversational", "proof": "matter_of_fact", "cta": "warm_cta",
}

# ── Assemble (spec §4 Stage 7) ─────────────────────────────────────────────
END_CARD_HOLD_S = 1.5
AUDIO_COMPLETENESS_MARGIN_S = 1.4  # rendered duration >= voice + this
TRANSITION_FADE_S = 0.25           # fade-in on first shot of beats 2..5
CARD_RERENDER_EPSILON_S = 0.05     # re-render card if re-flow moved it more

# ── Never-blank content check (VISUALS) ────────────────────────────────────
MIN_CONTENT_PIXELS = 500
LUMA_CONTENT_THRESHOLD = 140

# ── Shot-presence content check (ASSEMBLE) ─────────────────────────────────
# Coarse similarity gate between each shot's own pre-rendered mp4 and the
# assembled video at that shot's on-timeline position -- catches a dropped,
# reordered, or swapped shot that the never-blank check alone would miss
# (any bright content passes never-blank, even the fallback brand mark).
# Deliberately loose: the assembled frame carries caption/progress-bar
# overlays and possibly a transition the source shot mp4 doesn't have, and
# this must not false-positive on legitimate compositions. Mean absolute
# difference is computed on a 16x16 grayscale thumbnail, 0-255 scale.
SHOT_CONTENT_MAX_MEAN_DIFF = 90

# ── Sourcing: acquisition ladder (spec §6) ──────────────────────────────────
DOMAIN_BLACKLIST = [
    "ftcdn.net", "shutterstock.com", "alamy.com", "istockphoto.com",
    "gettyimages.com", "dreamstime.com", "123rf.com", "depositphotos.com",
    "stock.adobe.com", "adobestock.com", "fotolia.com", "bigstockphoto.com",
    "canstockphoto.com", "vectorstock.com",
]
MIN_LONG_EDGE_PX = 1280
PER_TIER_CANDIDATES = 8       # max candidates judged per ladder tier
JUDGE_MIN_OWN = 5             # own asset_library footage (trust bonus)
JUDGE_MIN_BLOG = 6            # blog's own images
JUDGE_MIN_API = 6             # free license-aware APIs
JUDGE_MIN_SCRAPE = 7          # scrape tier: must be CLEARLY right
JUDGE_MIN_FIGURE = 6          # real chart/diagram lifted from an already-cited source
FIGURE_MAX_PDF_PAGES = 5      # cap on PDF pages rendered hunting for a figure
SOURCING_CACHE_DIR = OUTPUT_BASE / "_sourcing_cache"
PAPER_CACHE_DIR = OUTPUT_BASE / "_paper_cache"

# ── Vision judge attach-verification (spec §6.2, fixes F3) ─────────────────
VISION_DESCRIBE_MIN_CHARS = 120
VISION_REFUSAL_PHRASES = [
    "cannot see", "no image", "as an ai", "unable to", "i'm sorry",
    "can't view", "cannot view", "not able to see",
]
WATERMARK_TERMS = [
    "shutterstock", "getty", "alamy", "istock", "dreamstime", "123rf",
    "depositphotos", "adobe stock", "watermark",
]

# ── Verify stage (spec §4 Stage 8) ──────────────────────────────────────────
VERIFY_MAX_REVISE_CYCLES = 2
LEGIBILITY_SHRINK_FACTOR = 0.7   # deterministic text-shorten on legibility fail
# video_agent.harness.verify_heuristic's dark-ribbon check (bottom
# VERIFY_DARK_RIBBON_STRIP_PX=120px, luma floor 24) is reused from an older
# pipeline with lighter typical backgrounds -- it structurally false-positives
# on shorts_engine's intentional navy-branded cards (measured bottom-strip
# luma ~19-20 on live output). The fix is a REAL one, not a threshold
# workaround: a persistent 24px brand-gold accent band at the very bottom,
# under the existing thin moving progress bar. Luma math (BT.601,
# BRAND_GOLD #d4af37 ~= 172): (24*172 + 96*19.5)/120 ~= 38.7, comfortably
# above the 24 floor even accounting for per-card luma variance.
DARK_RIBBON_FIX_BAR_PX = 24

# ── Ensure output directories exist ────────────────────────────────────────
def init_directories() -> None:
    """Create necessary output directories on startup."""
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Output directory ready: {OUTPUT_BASE}")


# Call on import to ensure directories exist
init_directories()
