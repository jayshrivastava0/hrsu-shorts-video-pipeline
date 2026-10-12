"""Persistent per-URL pipeline state (JSON, atomic writes)."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

STATUSES = ("seeded", "queued", "text_done", "held", "directed", "failed_gate", "failed", "published")
DEFAULT_STATE_PATH = Path("data") / "video_state.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path | str = DEFAULT_STATE_PATH, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8")).get("posts", {})

    def exists(self) -> bool:
        return self.path.exists()

    def get(self, url: str) -> Optional[dict[str, Any]]:
        return self.entries.get(url)

    def __contains__(self, url: str) -> bool:
        return url in self.entries

    def ensure(self, url: str, status: str) -> dict[str, Any]:
        """Create the entry with `status` if missing (does not change an existing one)."""
        if url not in self.entries:
            ts = now_iso()
            self.entries[url] = {"status": status, "run_dir": None, "video_id": None,
                                 "privacy_actual": None, "attempts": 0, "last_error": None,
                                 "created_at": ts, "updated_at": ts}
        return self.entries[url]

    def update(self, url: str, **fields: Any) -> dict[str, Any]:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        entry = self.ensure(url, fields.get("status", "queued"))
        entry.update(fields)
        entry["updated_at"] = now_iso()
        self.save()
        return entry

    def seed(self, urls: list[str]) -> int:
        """Mark every URL not yet tracked as `seeded` (never produces a video). Returns count added."""
        added = 0
        for url in urls:
            if url not in self.entries:
                self.ensure(url, "seeded")
                added += 1
        self.save()
        return added

    def count_published_on(self, day: str) -> int:
        """Entries published on `day` (YYYY-MM-DD, local)."""
        return sum(1 for e in self.entries.values()
                   if e.get("status") == "published" and str(e.get("published_at", "")).startswith(day))

    def save(self) -> None:
        if self.readonly:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "posts": self.entries}, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
