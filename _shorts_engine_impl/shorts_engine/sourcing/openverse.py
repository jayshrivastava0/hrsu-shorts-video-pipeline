"""Openverse adapter (spec §6.1 tier 3) — keyless, CC-licensed corpus."""
from __future__ import annotations

import logging

import requests

from video_agent.sources.base import BaseSource, RawCandidate

logger = logging.getLogger(__name__)

_API = "https://api.openverse.org/v1/images/"


class OpenverseSource(BaseSource):
    name = "openverse"
    authority_weight = 6

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        try:
            resp = requests.get(_API, params={
                "q": query, "license_type": "commercial", "page_size": limit,
            }, timeout=15, headers={"User-Agent": "hrsu-shorts-engine/1.0"})
            if resp.status_code != 200:
                logger.warning("openverse HTTP %s", resp.status_code)
                return []
            results = resp.json().get("results", [])
        except Exception as e:  # noqa: BLE001 — a dead tier contributes nothing
            logger.warning("openverse search failed: %s", e)
            return []
        out = []
        for r in results[:limit]:
            if not r.get("url"):
                continue
            out.append(RawCandidate(
                source=self.name, url=r["url"], caption=r.get("title") or "",
                width=int(r.get("width") or 0), height=int(r.get("height") or 0),
                extra={"license": r.get("license"),
                       "foreign_landing_url": r.get("foreign_landing_url")},
            ))
        return out
