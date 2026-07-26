"""User-provided footage matcher.

Drop .mp4/.mov clips into asset_library/factory/ and describe each one in
asset_library/factory/manifest.json. This module scores each manifest entry
against the current scene (category, subcategory, on_screen_text, narration,
visual_spec.query) and returns the best match — or None if nothing scores
above the minimum threshold.

Manifest schema (JSON list):
    [
        {
            "id":           "wastewater_tank_aerial_01",
            "filename":     "wastewater_tank_aerial_01.mp4",
            "tags":         ["wastewater", "tank", "aerial", "industrial"],
            "categories":   ["wastewater_treatment", "water_treatment"],
            "description":  "Drone shot of clarifier tanks at municipal STP",
            "duration_s":   12.4,
            "good_for":     ["hook", "establishing", "broll"]
        },
        ...
    ]
"""
# DEPRECATED: superseded by video_agent/vision/footage_index.py. Do not extend.
from __future__ import annotations
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

FACTORY_DIR = Path("asset_library/factory")
MANIFEST_PATH = FACTORY_DIR / "manifest.json"

# Below this score we treat the match as "no match" and let dispatcher fall back.
MIN_SCORE = 2

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "at",
    "with", "is", "are", "was", "were", "this", "that", "it", "we", "our",
    "your", "you", "they", "their", "from", "as", "by", "be",
}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)
            if m.group(0).lower() not in _STOPWORDS and len(m.group(0)) > 2}


@lru_cache(maxsize=1)
def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        log.warning("Factory manifest malformed (%s); ignoring", e)
        return []
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("filename")
        if not fn:
            continue
        path = FACTORY_DIR / fn
        if not path.exists():
            log.warning("Factory manifest references missing file: %s", path)
            continue
        if path.suffix.lower() not in _VIDEO_EXTS:
            continue
        entry = dict(entry)
        entry["path"] = path
        entry.setdefault("id", path.stem)
        entry.setdefault("tags", [])
        entry.setdefault("categories", [])
        entry.setdefault("description", "")
        entry.setdefault("good_for", [])
        cleaned.append(entry)
    return cleaned


def reset_cache() -> None:
    """Force the manifest to reload on next call (useful for tests)."""
    _load_manifest.cache_clear()


def _score(entry: dict, scene_tokens: set[str], category: str | None,
           visual_type: str) -> int:
    score = 0
    entry_tags = {str(t).lower() for t in entry.get("tags", [])}
    entry_cats = {str(c).lower() for c in entry.get("categories", [])}
    entry_desc_tokens = _tokenize(entry.get("description", ""))

    if category and category.lower() in entry_cats:
        score += 5
    score += 2 * len(scene_tokens & entry_tags)
    score += len(scene_tokens & entry_desc_tokens)
    good_for = {str(g).lower() for g in entry.get("good_for", [])}
    if visual_type == "hrsu_edge" and ("hrsu" in entry_tags or "factory" in entry_tags
                                        or "establishing" in good_for):
        score += 3
    if visual_type == "stock" and "broll" in good_for:
        score += 1
    return score


def find_footage(scene: dict) -> dict | None:
    """Return the best-matching manifest entry for `scene`, or None.

    Scene fields used: visual_type, visual_spec.query, on_screen_text,
    narration, category (optional, falls through scene["category"]).
    """
    manifest = _load_manifest()
    if not manifest:
        return None

    spec = scene.get("visual_spec") or {}
    text_blob = " ".join(filter(None, [
        scene.get("narration", ""),
        scene.get("on_screen_text", ""),
        spec.get("query", ""),
        spec.get("fallback_text", ""),
    ]))
    scene_tokens = _tokenize(text_blob)
    category = scene.get("category") or scene.get("blog_category")
    visual_type = scene.get("visual_type", "")

    scored = []
    for entry in manifest:
        s = _score(entry, scene_tokens, category, visual_type)
        if s >= MIN_SCORE:
            scored.append((s, entry["id"], entry))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[1]))
    best = scored[0][2]
    log.info("Factory footage match for scene %s -> %s (score %d)",
             scene.get("index"), best["id"], scored[0][0])
    return best
