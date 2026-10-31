"""Video formats and ratios, read from formats.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_PATH = Path(__file__).resolve().parent / "formats.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def format_names() -> list[str]:
    return list(_load().get("formats", {}))


def ratio_names() -> list[str]:
    return list(_load().get("ratios", {}))


def resolve(fmt: str | None, ratio: str | None, duration_s: int | None) -> tuple[str | None, int | None]:
    """(orientation, duration_s) for a format/ratio/duration choice; explicit ratio and duration win over the format."""
    data = _load()
    preset = data.get("formats", {}).get(fmt) if fmt else None
    if fmt and preset is None:
        raise ValueError(f"unknown format {fmt!r}; valid formats: {', '.join(format_names())}")
    ratio = ratio or (preset or {}).get("ratio")
    if ratio and ratio not in data.get("ratios", {}):
        raise ValueError(f"unknown ratio {ratio!r}; valid ratios: {', '.join(ratio_names())}")
    orientation = data["ratios"][ratio] if ratio else None
    return orientation, duration_s or (preset or {}).get("duration_s")
