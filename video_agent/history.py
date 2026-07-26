"""Atomic JSON read/write for video_history.json."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from video_agent.config import HISTORY_FILE

HISTORY_PATH = Path(HISTORY_FILE)


def load() -> dict:
    if not HISTORY_PATH.exists():
        return {"videos": []}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"videos": []}


def save_atomic(history: dict) -> None:
    """Tempfile + rename. Atomic on POSIX & Windows."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(HISTORY_PATH.parent or "."),
        prefix=".video_history.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp).replace(HISTORY_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_by_blog_id(blog_id: str) -> dict | None:
    for v in load().get("videos", []):
        if v.get("blog_id") == blog_id:
            return v
    return None


def append_video(record: dict) -> None:
    data = load()
    if "created_at" not in record:
        record["created_at"] = datetime.now(timezone.utc).isoformat()
    data.setdefault("videos", []).append(record)
    save_atomic(data)


def stats(days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    data = load()
    recent = []
    for v in data.get("videos", []):
        try:
            ts = datetime.fromisoformat(v.get("created_at", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            recent.append(v)
    return {
        "count": len(recent),
        "by_region": _count_by(recent, "region"),
        "by_platform": _platform_counts(recent),
    }


def _count_by(records: list, key: str) -> dict:
    out: dict = {}
    for r in records:
        k = r.get(key, "unknown")
        out[k] = out.get(k, 0) + 1
    return out


def _platform_counts(records: list) -> dict:
    counts: dict = {}
    for r in records:
        for plat, res in (r.get("publish_results") or {}).items():
            if res and res.get("success"):
                counts[plat] = counts.get(plat, 0) + 1
    return counts
