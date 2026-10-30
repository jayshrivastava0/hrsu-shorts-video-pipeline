"""Load brand_facts.yaml into tiered fact lists for brief-driven videos."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "brand_facts.yaml"


@dataclass
class Facts:
    in_place: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    never_claimed: list[str] = field(default_factory=list)
    banned_claims: list[str] = field(default_factory=list)
    cta_lines: list[str] = field(default_factory=list)


def load_facts(path: Path | None = None) -> Facts:
    """Load the fact tiers. Default: repo-root brand_facts.yaml."""
    p = path or _DEFAULT_PATH
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Facts(
        in_place=[str(s) for s in data.get("in_place") or []],
        in_progress=[str(s) for s in data.get("in_progress") or []],
        never_claimed=[str(s) for s in data.get("never_claimed") or []],
        banned_claims=[str(s) for s in data.get("banned_claims") or []],
        cta_lines=[str(s) for s in data.get("cta_lines") or []],
    )
