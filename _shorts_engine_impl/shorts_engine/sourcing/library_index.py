"""Own-footage index: vision-describe each asset once, query by token match.
Own HRSU footage beats stock — it enters the ladder as tier 1 with a lower
judge threshold (JUDGE_MIN_OWN)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shorts_engine import config

logger = logging.getLogger(__name__)

_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_STOPWORDS = {"the", "a", "an", "of", "at", "in", "on", "with", "and", "or",
              "to", "for", "from", "by"}

_describe = None  # seam → vision_judge.describe


def _library_root() -> Path:
    return config.PROJECT_ROOT / "asset_library"


def index_path() -> Path:
    return _library_root() / "index.json"


def _load_index() -> dict:
    p = index_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("index.json corrupt — rebuilding")
    return {}


def _resolve_describe():
    global _describe
    if _describe is not None:
        return _describe
    from shorts_engine.llm.vision_judge import describe
    return describe


def build_index(force: bool = False) -> dict:
    root = _library_root()
    idx = {} if force else _load_index()
    describe_fn = _resolve_describe()
    for sub in ("factory", "footage"):
        d = root / sub
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in _EXTS:
                continue
            rel = f.relative_to(root).as_posix()
            mtime = f.stat().st_mtime
            entry = idx.get(rel)
            if entry and entry.get("mtime") == mtime and not (
                    force and entry.get("failed")):
                continue
            desc = describe_fn(f)
            if isinstance(desc, dict) and desc.get("description"):
                idx[rel] = {"description": desc["description"],
                            "visible_text": desc.get("visible_text", ""),
                            "mtime": mtime}
            else:
                idx[rel] = {"description": "", "failed": True, "mtime": mtime}
                logger.warning("library index: describe failed for %s", rel)
    index_path().parent.mkdir(parents=True, exist_ok=True)
    index_path().write_text(json.dumps(idx, indent=2), encoding="utf-8")
    return idx


def _tokens(text: str) -> set[str]:
    return {w for w in text.lower().split() if w not in _STOPWORDS and len(w) > 2}


def query(wish: str, limit: int = 8) -> list[dict]:
    idx = _load_index()
    want = _tokens(wish)
    scored = []
    for rel, entry in idx.items():
        have = _tokens(entry.get("description", ""))
        overlap = len(want & have)
        if overlap >= 2:
            scored.append({"path": str(_library_root() / rel),
                           "description": entry.get("description", ""),
                           "score_hint": overlap})
    scored.sort(key=lambda e: -e["score_hint"])
    return scored[:limit]
