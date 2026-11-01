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
    orientation: str | None = None          # landscape | portrait | square (from --ratio / --format / --orientation)
    angles: list[str] = field(default_factory=list)   # buyer_reasons ids (or free text) the video should lead with
    cta: str | None = None                   # call-to-action override; None = the video type's default
    pace: str = "standard"                    # standard | fast (attention-first cutting, see prompts/pace_fast.md)
    duration_s: int | None = None            # target narration length in seconds; None = the type's default


def brief_id(text: str, variant: str = "") -> str:
    """Stable id: slug of the first 6 words + '-' + 6 hex chars of sha1.

    The hash is taken over whitespace-normalised text, so spacing and
    line-break changes do not alter the id.
    """
    normalised = " ".join(text.split())
    if variant:   # a different angle/format/cta of the same text is a different video; empty keeps old ids
        normalised_for_hash = normalised + "|" + variant
    else:
        normalised_for_hash = normalised
    words = re.findall(r"[a-z0-9]+", normalised.lower())[:6]
    slug = "-".join(words) or "brief"
    digest = hashlib.sha1(normalised_for_hash.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"
