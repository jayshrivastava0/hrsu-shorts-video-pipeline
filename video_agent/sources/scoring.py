"""Per-candidate quality scoring. Pure function, no I/O."""
from __future__ import annotations
import re
from video_agent.sources.base import RawCandidate
from video_agent.config import MIN_IMAGE_LONG_EDGE, IDEAL_IMAGE_LONG_EDGE

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
              "with", "by", "is", "are", "this", "that", "these", "those",
              "it", "its", "we", "our", "you", "your", "be", "been", "was",
              "were", "as", "at", "from", "into", "than", "then", "more",
              "most", "less", "least", "can", "will", "may", "should"}

# Source authority weights (used inside score)
_AUTHORITY = {
    "wikimedia": 10, "unsplash": 8, "pexels": 8, "bing": 5, "duckduckgo": 5,
    "google_images": 5, "youtube": 5,
}

# Caption patterns that almost always mean content irrelevant to industrial
# B-roll. Any match in caption → context score 0 (hard reject downstream).
_CAPTION_REJECT = re.compile(
    r"\b(movie|film|trailer|episode|drama|series|"
    r"song|lyrics|music\s*video|cover|remix|dance|mashup|"
    r"interview|podcast|vlog|reaction|reacts?|"
    r"news|breaking|bulletin|debate|press\s*conference|"
    r"tutorial|how\s*to|lesson|crash\s*course|"
    r"hindi|bollywood|nepali|garhwali|kumaoni|bhojpuri|punjabi|tamil|"
    r"telugu|malayalam|kannada|marathi|gujarati|bengali|urdu|"
    r"prank|funny|status\s*video|whatsapp)\b",
    re.IGNORECASE,
)
_NON_LATIN = re.compile(r"[ऀ-ॿঀ-৿਀-੿஀-௿ఀ-౿ഀ-ൿ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")

# Captions that suggest a chemical reaction diagram or scientific figure — not
# a real-world photo. These look wrong on screen and break viewer trust.
_DIAGRAM_REJECT = re.compile(
    r"\b(reaction\s*(scheme|mechanism|pathway|diagram)|"
    r"chemical\s*(structure|equation|formula|reaction)|"
    r"molecular\s*structure|synthesis\s*route|"
    r"mechanism\s*of\s*(action|reaction)|"
    r"schematic\s*(diagram|representation)|"
    r"flow\s*chart|flowchart|"
    r"fig(ure)?\s*\d|scheme\s*\d|equation\s*\d)\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")
            if m.group(0).lower() not in _STOPWORDS and len(m.group(0)) > 2}


def context_match_score(caption: str, narration: str,
                        thread_keywords: list[str] | None = None,
                        hero_claim: str | None = None) -> int:
    """Score a candidate's caption against the scene's narration plus the
    cumulative narrative thread (keywords from neighbouring scenes) and the
    hero claim. Cumulative context catches off-topic-but-on-word matches
    that a per-scene score misses.

    Returns an integer roughly in [0, 100]. 0 means hard-reject.
    Anything < 30 should not be used.
    """
    if not caption or not narration:
        return 0

    # Hard rejects FIRST — junk caption ⇒ score 0
    if _CAPTION_REJECT.search(caption):
        return 0
    if _DIAGRAM_REJECT.search(caption):
        return 0
    non_latin = len(_NON_LATIN.findall(caption))
    latin = len(_LATIN_LETTER.findall(caption))
    if non_latin > 0 and non_latin > latin * 0.15:
        return 0

    nar_toks = _tokens(narration)
    cap_toks = _tokens(caption)
    if not nar_toks or not cap_toks:
        return 10

    # Cumulative context: thread keywords + hero claim tokens
    thread_toks: set[str] = set()
    if thread_keywords:
        for kw in thread_keywords:
            thread_toks |= _tokens(kw)
    hero_toks = _tokens(hero_claim or "")

    domain_anchors = {
        "calcium", "nitrate", "chemical", "chemistry", "industrial",
        "industry", "plant", "factory", "manufacturing", "facility",
        "mining", "mine", "tailings", "leachate", "drainage", "amd",
        "concrete", "cement", "construction", "admixture", "accelerator",
        "wastewater", "water", "treatment", "effluent", "sewage",
        "fertilizer", "fertiliser", "agriculture", "farm", "crop", "soil",
        "oil", "gas", "refinery", "drilling", "well",
        "ph", "acid", "alkaline", "neutralization", "neutralisation",
        "process", "reactor", "pipe", "tank", "dosing", "pump",
        "explosive", "anfo", "blast", "ore",
    }
    nar_anchored = nar_toks & domain_anchors
    cap_anchored = cap_toks & domain_anchors

    overlap = nar_toks & cap_toks
    score = min(50, len(overlap) * 12)

    if cap_anchored:
        score += 20
    if nar_anchored & cap_anchored:
        score += 30

    # Cumulative bonuses — new contribution from narrative thread
    if thread_toks and (cap_toks & thread_toks):
        score += 15
    if hero_toks and (cap_toks & hero_toks):
        score += 10

    return min(100, score)


def _dimension_adjustment(c: RawCandidate) -> tuple[int, bool]:
    """Returns (score_delta, hard_reject).
    hard_reject=True means caller should drop this candidate entirely."""
    if not c.width or not c.height:
        return (0, False)
    long_edge = max(c.width, c.height)
    if long_edge < MIN_IMAGE_LONG_EDGE:
        return (0, True)
    aspect = c.width / c.height
    delta = 0
    if long_edge >= IDEAL_IMAGE_LONG_EDGE:
        delta += 10
    if 0.9 <= aspect <= 1.1:        # square-ish — loses ~50% to portrait crop
        delta -= 15
    if aspect >= 1.6:               # widescreen — ideal for Ken Burns pan
        delta += 10
    if aspect <= 0.65:              # portrait — minimal crop loss
        delta += 8
    return (delta, False)


def score_candidate(c: RawCandidate, query: str) -> int:
    """Returns a score in roughly [-100, 100].
    Negative scores mean hard-rejected (resolution too low, etc.)."""
    delta, hard_reject = _dimension_adjustment(c)
    if hard_reject:
        return -100
    score = 30 + delta              # base 30 + dimension adjustment
    # Token overlap (caption ↔ query)
    overlap = len(_tokens(c.caption) & _tokens(query))
    score += min(25, overlap * 8)
    # Source authority
    score += _AUTHORITY.get(c.source, 0)
    # File integrity (downloads cleanly, opens) — checked separately
    if c.file_size and c.file_size > 100_000:
        score += 15
    # YouTube extras
    if c.is_clip and c.extra.get("view_count", 0) > 10_000 \
            and c.duration_s and c.duration_s > 30:
        score += 10
    return score
