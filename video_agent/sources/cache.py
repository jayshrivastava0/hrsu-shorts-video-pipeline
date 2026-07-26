"""Disk-backed query → candidates cache. Keyed by sha1(query|source)."""
from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from video_agent.sources.base import RawCandidate

log = logging.getLogger(__name__)


class QueryCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, query: str, source: str) -> Path:
        h = hashlib.sha1(f"{source}|{query.strip().lower()}".encode()).hexdigest()
        return self.root / h[:2] / f"{h}.json"

    def get(self, query: str, source: str) -> list[RawCandidate] | None:
        p = self._key(query, source)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [RawCandidate(**c) for c in data]
        except Exception as e:
            log.warning("Corrupt cache %s (%s); ignoring", p, e)
            return None

    def put(self, query: str, source: str, cands: list[RawCandidate]) -> None:
        p = self._key(query, source)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([asdict(c) for c in cands], indent=2,
                                ensure_ascii=False), encoding="utf-8")
