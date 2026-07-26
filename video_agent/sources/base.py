"""Common types and interface for image/clip sources."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class RawCandidate:
    """What a Source returns before scoring/downloading."""
    source: str
    url: str
    caption: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0          # 0 = unknown until downloaded
    is_clip: bool = False
    duration_s: float | None = None
    extra: dict = field(default_factory=dict)


class BaseSource:
    """Subclasses implement search() returning candidates for a query."""
    name: str = "base"
    authority_weight: int = 0   # 0..10 — Wikimedia/Unsplash high; scrapes low

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        raise NotImplementedError
