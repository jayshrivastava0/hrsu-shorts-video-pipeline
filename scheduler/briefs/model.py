"""Brief model: the input a brief-driven video is built from."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_TYPES = ("explainer", "proof", "esg", "market", "marketing")


@dataclass
class Brief:
    id: str
    text: str
    type: str
    url: str | None = None
    assets: list[Path] = field(default_factory=list)
    orientation: str | None = None


def brief_id(text: str) -> str:
    """Stable id: slug of the first 6 words + '-' + 6 hex chars of sha1.

    The hash is taken over whitespace-normalised text, so spacing and
    line-break changes do not alter the id.
    """
    normalised = " ".join(text.split())
    words = re.findall(r"[a-z0-9]+", normalised.lower())[:6]
    slug = "-".join(words) or "brief"
    digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"
