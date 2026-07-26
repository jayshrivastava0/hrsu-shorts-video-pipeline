"""Internet Archive (Archive.org) source — free public-domain industrial footage.

Targets the Prelinger Archives (industrial/educational films, 1930s–70s) and
technical/science collections. yt-dlp has a native archive.org extractor so
clips go through the same download_ranges path as YouTube clips.
"""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)
_API = "https://archive.org/advancedsearch.php"
_HEADERS = {"User-Agent": "HRSU-VideoBot/2.0 (sujay@swastika.co.in)"}
_DETAILS_BASE = "https://archive.org/details"

# Public-domain industrial/educational film collections
_COLLECTION_FILTER = (
    "collection:prelinger OR collection:industrial_films "
    "OR (mediatype:movies AND subject:industrial) "
    "OR (mediatype:movies AND subject:chemistry)"
)


class ArchiveOrgSource(BaseSource):
    name = "archive_org"
    # Good quality public-domain footage, but older aesthetic — lower than Pexels
    authority_weight = 7

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        try:
            r = requests.get(_API, headers=_HEADERS, timeout=15, params={
                "q": f"({query}) AND ({_COLLECTION_FILTER})",
                "output": "json",
                "rows": limit * 2,  # fetch extra; some may lack usable files
                "fl[]": ["identifier", "title", "description", "runtime"],
                "sort[]": "downloads desc",
            })
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
        except Exception as e:
            log.warning("Archive.org search failed for %r: %s", query, e)
            return []

        out: list[RawCandidate] = []
        for doc in docs:
            ident = doc.get("identifier", "")
            if not ident:
                continue
            title = doc.get("title") or ident
            desc = doc.get("description") or ""
            if isinstance(desc, list):
                desc = " ".join(desc)
            caption = f"{title}: {desc.strip()}"[:300] if desc.strip() else title
            duration_s = _parse_runtime(doc.get("runtime")) or 120.0
            out.append(RawCandidate(
                source=self.name,
                url=f"{_DETAILS_BASE}/{ident}",
                caption=caption,
                is_clip=True,
                duration_s=duration_s,
            ))
            if len(out) >= limit:
                break
        return out


def _parse_runtime(runtime) -> float | None:
    """Parse 'MM:SS' or 'HH:MM:SS' runtime string → seconds."""
    if not runtime:
        return None
    if isinstance(runtime, list):
        runtime = runtime[0] if runtime else ""
    try:
        parts = str(runtime).strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return None
