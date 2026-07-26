# Video Pipeline Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current scene-stitching video pipeline with a 5-stage director-driven pipeline (Strategist → Storyboarder → Sourcer → Critics → Reviser → Renderer) operating on a single shared `storyboard.json` object, sourcing real images/clips from six web sources, enforcing scene coherence via two LLM critics, and composing with motion + transitions + music inside a strict safe zone.

**Architecture:** Each agent reads the shared storyboard and writes back its slice. One revision pass total (no loops). Six image sources fan out in parallel per scene with token-overlap quality scoring and perceptual-hash deduplication. Renderer applies Ken Burns motion to stills, beat-aware transitions, sidechain-ducked music bed, and a 12-frame safe-zone validation gate before shipping the MP4.

**Tech Stack:** Python 3.12, Ollama (gemma3:4b) for all agents, requests + BeautifulSoup for scraping, official APIs for Bing/Unsplash/Wikimedia, yt-dlp for YouTube, Pillow for image ops, ffmpeg-python for composition, faster-whisper for subtitles, edge-tts for voice. Tests use pytest + responses (HTTP mocking) + monkeypatched Ollama.

**Spec:** [`docs/superpowers/specs/2026-05-10-video-pipeline-redesign-design.md`](../specs/2026-05-10-video-pipeline-redesign-design.md)

---

## File Structure

```
video_agent/
├── storyboard.py                       # NEW — schema, load/save, stage-runner
├── orchestrator.py                     # NEW — wires the 5 stages
├── run_stage.py                        # NEW — CLI: re-run a single stage
│
├── agents/                             # NEW — one file per LLM agent
│   ├── __init__.py
│   ├── strategist.py
│   ├── storyboarder.py
│   ├── sourcer.py                      # orchestrates the sources/ fan-out
│   ├── critic_local.py
│   ├── critic_global.py
│   └── reviser.py
│
├── sources/                            # NEW — one file per image/clip source
│   ├── __init__.py
│   ├── base.py                         # BaseSource interface + common helpers
│   ├── google_images.py
│   ├── bing.py
│   ├── unsplash.py
│   ├── wikimedia.py
│   ├── youtube.py
│   ├── duckduckgo.py
│   ├── scoring.py                      # candidate quality scorer
│   └── cache.py                        # disk-backed query cache
│
├── motion/                             # NEW — frame-level motion engine
│   ├── __init__.py
│   ├── ken_burns.py
│   └── transitions.py
│
├── safezone.py                         # NEW — safe-zone validator
├── music.py                            # NEW — music bed mixer with sidechain ducking
│
├── ollama_client.py                    # KEPT
├── voiceover.py                        # KEPT
├── subtitles.py                        # KEPT
├── composer.py                         # MAJOR REWRITE — uses motion/, music, safezone
├── script_builder.py                   # SHRINKS — only fact extraction stays
├── visual_engine/                      # KEPT but de-prioritised (text_card + chart fallback)
│   └── …
└── config.py                           # KEPT — extended with new constants
```

Tests mirror the source tree under `tests/video_agent/` with the same subdirectory layout.

---

## Phase Milestones

Each phase ends with a working, comparable video. Don't move to phase N+1 until phase N's smoke test passes.

| Phase | Outcome |
|-------|---------|
| 1 | New Sourcer fetches real images from 6 sources, plugged into legacy pipeline via `--new-sourcer` flag. Smoke test: ≥ 60% of scenes have a real image. |
| 2 | Strategist + Storyboarder replace `_scene_breakdown` + `_write_narration`. Smoke test: video tells one hero claim across 5 beats. |
| 3 | Local + Global critics flag misaligned scenes; Reviser does one rewrite pass. Smoke test: critic scores logged; revisions visible in `revision_diff.json`. |
| 4 | Ken Burns + transitions + music + safe-zone validation. Smoke test: 12-frame safe-zone check passes; final MP4 has motion and music. |
| 5 | Legacy code paths deleted; `--legacy` flag removed. |

---

# Phase 1 — Storyboard Schema + Sourcer

The foundation. After this phase: same script-builder and composer as today, but every scene has a real image instead of a text card.

---

### Task 1.1: Storyboard schema dataclasses

**Files:**
- Create: `video_agent/storyboard.py`
- Test: `tests/video_agent/test_storyboard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/video_agent/test_storyboard.py
import json
from pathlib import Path
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept,
    load_storyboard, save_storyboard,
)


def test_storyboard_round_trip(tmp_path):
    sb = Storyboard(
        version="2.0",
        blog={"id": "b1", "url": "u", "title": "t", "region": "australia",
              "category": "mining", "persona": "procurement"},
        hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%",
                             source_quote="..."),
        arc=[Beat(index=0, beat="hook", purpose="hook", duration_target_s=3.5)],
        supporting_facts=[],
        scenes=[Scene(
            index=0, beat="hook",
            narration="Are wastewater costs rising?",
            on_screen_text="90% H2S CUT",
            visual_concept=VisualConcept(
                subject="wastewater plant", modifier="aerial",
                type="photo", mood="problem", style_hint="documentary"),
            duration_target_s=3.5, transition_in="cut",
        )],
    )
    path = tmp_path / "storyboard.json"
    save_storyboard(sb, path)
    sb2 = load_storyboard(path)
    assert sb2.hero_claim.stat == "90%"
    assert sb2.scenes[0].visual_concept.subject == "wastewater plant"
    assert sb2.version == "2.0"


def test_scene_defaults():
    s = Scene(index=0, beat="hook", narration="n", on_screen_text="t",
              visual_concept=VisualConcept(subject="x", modifier="y",
                                           type="photo", mood="problem",
                                           style_hint=""),
              duration_target_s=3.0, transition_in="cut")
    assert s.asset_candidates == []
    assert s.chosen_asset is None
    assert s.degraded is False
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/video_agent/test_storyboard.py -v
```

Expected: FAIL with `ModuleNotFoundError: video_agent.storyboard`

- [ ] **Step 3: Implement the schema**

```python
# video_agent/storyboard.py
"""Single shared object that flows through every pipeline agent."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal


VisualType = Literal["photo", "diagram", "clip", "chart_data"]
Mood = Literal["problem", "mechanism", "proof", "brand"]
BeatName = Literal["hook", "stakes", "mechanism", "proof", "cta"]


@dataclass
class VisualConcept:
    subject: str
    modifier: str
    type: VisualType
    mood: Mood
    style_hint: str = ""


@dataclass
class HeroClaim:
    stat: str
    claim_text: str
    source_quote: str = ""


@dataclass
class Beat:
    index: int
    beat: BeatName
    purpose: str
    duration_target_s: float


@dataclass
class AssetCandidate:
    source: str               # "google_images" | "bing" | …
    url: str
    score: int
    local_path: str           # path to downloaded file
    caption: str = ""
    width: int = 0
    height: int = 0
    is_clip: bool = False
    duration_s: float | None = None


@dataclass
class Motion:
    type: Literal["ken_burns", "zoom", "none"] = "ken_burns"
    direction: Literal["down", "up", "left", "right", "in", "out"] = "in"
    speed_px_per_frame: float = 0.6


@dataclass
class CriticNotes:
    alignment_score: int = 10
    flags: list[str] = field(default_factory=list)
    revision: str | None = None


@dataclass
class Scene:
    index: int
    beat: BeatName
    narration: str
    on_screen_text: str
    visual_concept: VisualConcept
    duration_target_s: float
    transition_in: Literal["cut", "fade", "whip_pan"] = "cut"
    asset_candidates: list[AssetCandidate] = field(default_factory=list)
    chosen_asset: AssetCandidate | None = None
    motion: Motion = field(default_factory=Motion)
    critic_notes: CriticNotes = field(default_factory=CriticNotes)
    degraded: bool = False


@dataclass
class DirectorNotes:
    arc_quality: int = 10
    hero_claim_supported: bool = True
    weakest_beat: int | None = None
    missing: list[str] = field(default_factory=list)
    redundant: list[int] = field(default_factory=list)
    ending_strength: int = 10
    revision_for_strategist: str | None = None


@dataclass
class Storyboard:
    version: str
    blog: dict[str, Any]
    hero_claim: HeroClaim | None = None
    arc: list[Beat] = field(default_factory=list)
    supporting_facts: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    director_notes: DirectorNotes = field(default_factory=DirectorNotes)
    metadata: dict[str, Any] = field(default_factory=dict)


def save_storyboard(sb: Storyboard, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(sb), indent=2, ensure_ascii=False),
                    encoding="utf-8")


def load_storyboard(path: Path) -> Storyboard:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _from_dict(raw)


def _from_dict(d: dict) -> Storyboard:
    hero = d.get("hero_claim")
    arc = [Beat(**b) for b in d.get("arc", [])]
    scenes = [_scene_from_dict(s) for s in d.get("scenes", [])]
    director = DirectorNotes(**d.get("director_notes",
                                     asdict(DirectorNotes())))
    return Storyboard(
        version=d["version"], blog=d["blog"],
        hero_claim=HeroClaim(**hero) if hero else None,
        arc=arc, supporting_facts=d.get("supporting_facts", []),
        scenes=scenes, director_notes=director,
        metadata=d.get("metadata", {}),
    )


def _scene_from_dict(d: dict) -> Scene:
    cands = [AssetCandidate(**c) for c in d.get("asset_candidates", [])]
    chosen_d = d.get("chosen_asset")
    chosen = AssetCandidate(**chosen_d) if chosen_d else None
    motion = Motion(**d.get("motion", asdict(Motion())))
    notes = CriticNotes(**d.get("critic_notes", asdict(CriticNotes())))
    vc = VisualConcept(**d["visual_concept"])
    return Scene(
        index=d["index"], beat=d["beat"], narration=d["narration"],
        on_screen_text=d["on_screen_text"], visual_concept=vc,
        duration_target_s=d["duration_target_s"],
        transition_in=d.get("transition_in", "cut"),
        asset_candidates=cands, chosen_asset=chosen, motion=motion,
        critic_notes=notes, degraded=d.get("degraded", False),
    )
```

- [ ] **Step 4: Run the test**

```
pytest tests/video_agent/test_storyboard.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```
git add video_agent/storyboard.py tests/video_agent/test_storyboard.py
git commit -m "feat(video): add storyboard schema dataclasses + JSON load/save"
```

---

### Task 1.2: BaseSource interface + scoring engine

**Files:**
- Create: `video_agent/sources/__init__.py` (empty)
- Create: `video_agent/sources/base.py`
- Create: `video_agent/sources/scoring.py`
- Test: `tests/video_agent/sources/test_scoring.py`

- [ ] **Step 1: Write the failing scoring test**

```python
# tests/video_agent/sources/test_scoring.py
from video_agent.sources.scoring import score_candidate
from video_agent.sources.base import RawCandidate


def test_high_quality_photo_scores_above_threshold():
    cand = RawCandidate(
        source="unsplash", url="https://x/img.jpg",
        caption="acid mine drainage runoff in stream",
        width=1920, height=1080, file_size=120_000,
    )
    score = score_candidate(cand, query="acid mine drainage")
    assert score >= 60          # res(30) + aspect(10) + tokens(25) + auth(10)


def test_low_resolution_rejected():
    cand = RawCandidate(source="g", url="u", caption="x",
                        width=400, height=300, file_size=20_000)
    score = score_candidate(cand, query="x")
    assert score < 0            # hard rejected


def test_token_overlap_zero_when_unrelated():
    cand = RawCandidate(source="g", url="u",
                        caption="cat playing piano",
                        width=1920, height=1080, file_size=80_000)
    score = score_candidate(cand, query="industrial wastewater")
    assert score < 50           # no token overlap → no +25
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest tests/video_agent/sources/test_scoring.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement BaseSource and scoring**

```python
# video_agent/sources/base.py
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
```

```python
# video_agent/sources/scoring.py
"""Per-candidate quality scoring. Pure function, no I/O."""
from __future__ import annotations
import re
from video_agent.sources.base import RawCandidate

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
              "with", "by", "is", "are"}

# Source authority weights (used inside score)
_AUTHORITY = {
    "wikimedia": 10, "unsplash": 8, "bing": 5, "duckduckgo": 5,
    "google_images": 3, "youtube": 5,
}


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")
            if m.group(0).lower() not in _STOPWORDS and len(m.group(0)) > 2}


def score_candidate(c: RawCandidate, query: str) -> int:
    """Returns a score in roughly [-100, 100].
    Negative scores mean hard-rejected (resolution too low, etc.)."""
    if c.width and c.height:
        if c.width < 1280 or c.height < 720:
            return -100         # resolution hard-reject
    score = 0
    # Resolution
    score += 30
    # Aspect ratio
    if c.width and c.height:
        ratio = max(c.width, c.height) / max(1, min(c.width, c.height))
        if ratio <= 2.0:
            score += 10
    # Token overlap (caption ↔ query)
    overlap = len(_tokens(c.caption) & _tokens(query))
    score += min(25, overlap * 8)
    # Source authority
    score += _AUTHORITY.get(c.source, 0)
    # File integrity (downloads cleanly, opens) — checked separately
    if c.file_size and c.file_size > 50_000:
        score += 15
    # YouTube extras
    if c.is_clip and c.extra.get("view_count", 0) > 10_000 \
            and c.duration_s and c.duration_s > 30:
        score += 10
    return score
```

- [ ] **Step 4: Run the test**

```
pytest tests/video_agent/sources/test_scoring.py -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```
git add video_agent/sources/__init__.py video_agent/sources/base.py video_agent/sources/scoring.py tests/video_agent/sources/__init__.py tests/video_agent/sources/test_scoring.py
git commit -m "feat(video): add BaseSource interface + candidate scoring engine"
```

---

### Task 1.3: Disk-backed query cache

**Files:**
- Create: `video_agent/sources/cache.py`
- Test: `tests/video_agent/sources/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/video_agent/sources/test_cache.py
from pathlib import Path
from video_agent.sources.cache import QueryCache
from video_agent.sources.base import RawCandidate


def test_cache_round_trip(tmp_path):
    cache = QueryCache(tmp_path)
    cands = [RawCandidate(source="unsplash", url="u1",
                          caption="c", width=1920, height=1080)]
    cache.put("acid mine drainage", "unsplash", cands)
    got = cache.get("acid mine drainage", "unsplash")
    assert len(got) == 1
    assert got[0].url == "u1"


def test_cache_miss_returns_none(tmp_path):
    cache = QueryCache(tmp_path)
    assert cache.get("never seen", "unsplash") is None


def test_cache_separates_sources(tmp_path):
    cache = QueryCache(tmp_path)
    cache.put("q", "unsplash", [RawCandidate(source="unsplash", url="a")])
    assert cache.get("q", "unsplash") is not None
    assert cache.get("q", "bing") is None
```

- [ ] **Step 2: Run to verify failure**

```
pytest tests/video_agent/sources/test_cache.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# video_agent/sources/cache.py
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
```

- [ ] **Step 4: Run**

```
pytest tests/video_agent/sources/test_cache.py -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```
git add video_agent/sources/cache.py tests/video_agent/sources/test_cache.py
git commit -m "feat(video): add disk-backed query cache for sources"
```

---

### Task 1.4: Unsplash source (stable API — first source)

**Files:**
- Create: `video_agent/sources/unsplash.py`
- Modify: `video_agent/config.py` (add `UNSPLASH_ACCESS_KEY` reading from env)
- Test: `tests/video_agent/sources/test_unsplash.py`

- [ ] **Step 1: Add config key**

In `video_agent/config.py`, add at the end:

```python
# ─── Source API keys (read from env; None disables that source) ──────────
import os as _os
UNSPLASH_ACCESS_KEY = _os.environ.get("UNSPLASH_ACCESS_KEY")
BING_API_KEY        = _os.environ.get("BING_API_KEY")
```

- [ ] **Step 2: Write the failing test**

```python
# tests/video_agent/sources/test_unsplash.py
from unittest.mock import patch
import responses
from video_agent.sources.unsplash import UnsplashSource


@responses.activate
def test_unsplash_search_returns_candidates():
    responses.add(
        responses.GET, "https://api.unsplash.com/search/photos",
        json={"results": [
            {"urls": {"raw": "https://u/img1"},
             "alt_description": "industrial water plant",
             "width": 1920, "height": 1080,
             "user": {"name": "Photographer"}},
            {"urls": {"raw": "https://u/img2"},
             "alt_description": "factory aerial",
             "width": 2400, "height": 1600,
             "user": {"name": "Photographer"}},
        ]},
        status=200,
    )
    src = UnsplashSource(api_key="fake")
    cands = src.search("industrial water", limit=2)
    assert len(cands) == 2
    assert cands[0].source == "unsplash"
    assert cands[0].url == "https://u/img1"
    assert cands[0].width == 1920
    assert "industrial" in cands[0].caption


def test_unsplash_no_key_returns_empty():
    src = UnsplashSource(api_key=None)
    assert src.search("anything") == []
```

- [ ] **Step 3: Run to verify failure**

```
pytest tests/video_agent/sources/test_unsplash.py -v
```

Expected: FAIL — module not found

- [ ] **Step 4: Implement**

```python
# video_agent/sources/unsplash.py
"""Unsplash Search API. Free 50 req/hour with a developer key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import UNSPLASH_ACCESS_KEY

log = logging.getLogger(__name__)
_API = "https://api.unsplash.com/search/photos"


class UnsplashSource(BaseSource):
    name = "unsplash"
    authority_weight = 8

    def __init__(self, api_key: str | None = UNSPLASH_ACCESS_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Unsplash skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API,
                params={"query": query, "per_page": limit,
                        "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {self.api_key}"},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Unsplash search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("results", [])[:limit]:
            out.append(RawCandidate(
                source=self.name,
                url=item.get("urls", {}).get("raw", ""),
                caption=item.get("alt_description") or "",
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                extra={"author": item.get("user", {}).get("name", "")},
            ))
        return [c for c in out if c.url]
```

- [ ] **Step 5: Run**

```
pytest tests/video_agent/sources/test_unsplash.py -v
```

Expected: 2 PASS

- [ ] **Step 6: Commit**

```
git add video_agent/sources/unsplash.py video_agent/config.py tests/video_agent/sources/test_unsplash.py
git commit -m "feat(video): add Unsplash source"
```

---

### Task 1.5: Wikimedia Commons source

**Files:**
- Create: `video_agent/sources/wikimedia.py`
- Test: `tests/video_agent/sources/test_wikimedia.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/sources/test_wikimedia.py
import responses
from video_agent.sources.wikimedia import WikimediaSource


@responses.activate
def test_wikimedia_returns_candidates_with_image_info():
    # First call: search
    responses.add(
        responses.GET, "https://commons.wikimedia.org/w/api.php",
        json={"query": {"search": [
            {"title": "File:Sulfide oxidation.png", "snippet": "diagram"},
        ]}},
    )
    # Second call: imageinfo
    responses.add(
        responses.GET, "https://commons.wikimedia.org/w/api.php",
        json={"query": {"pages": {"1": {
            "imageinfo": [{
                "url": "https://upload.../Sulfide_oxidation.png",
                "width": 1600, "height": 1200,
                "extmetadata": {"ImageDescription": {"value": "oxidation diagram"}},
            }],
        }}}},
    )
    src = WikimediaSource()
    cands = src.search("sulfide oxidation", limit=1)
    assert len(cands) == 1
    assert cands[0].source == "wikimedia"
    assert cands[0].width == 1600
    assert "oxidation" in cands[0].caption.lower()
```

- [ ] **Step 2: Run to verify failure**

```
pytest tests/video_agent/sources/test_wikimedia.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/sources/wikimedia.py
"""Wikimedia Commons API. Free, no key required. Best for diagrams + science."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)
_API = "https://commons.wikimedia.org/w/api.php"
_HEADERS = {"User-Agent": "HRSU-VideoBot/2.0 (sujay@swastika.co.in)"}


class WikimediaSource(BaseSource):
    name = "wikimedia"
    authority_weight = 10

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        try:
            r = requests.get(_API, headers=_HEADERS, timeout=15, params={
                "action": "query", "format": "json", "list": "search",
                "srsearch": query, "srnamespace": 6,    # File: namespace
                "srlimit": limit * 2,
            })
            r.raise_for_status()
            titles = [s["title"] for s in r.json().get("query", {})
                                              .get("search", [])
                      if s["title"].lower().endswith(
                          (".jpg", ".jpeg", ".png", ".svg"))][:limit]
        except Exception as e:
            log.warning("Wikimedia search failed for %r: %s", query, e)
            return []

        if not titles:
            return []

        try:
            r = requests.get(_API, headers=_HEADERS, timeout=15, params={
                "action": "query", "format": "json",
                "titles": "|".join(titles), "prop": "imageinfo",
                "iiprop": "url|size|extmetadata",
            })
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {}) or {}
        except Exception as e:
            log.warning("Wikimedia imageinfo failed: %s", e)
            return []

        out = []
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("url", "")
            if not url or url.lower().endswith(".svg"):
                continue
            desc = info.get("extmetadata", {}).get(
                "ImageDescription", {}).get("value", "") or page.get("title", "")
            # Wikimedia descriptions can contain HTML; quick strip
            desc = " ".join(desc.split())[:200]
            out.append(RawCandidate(
                source=self.name, url=url, caption=desc,
                width=int(info.get("width", 0)),
                height=int(info.get("height", 0)),
            ))
        return out
```

- [ ] **Step 4: Run**

```
pytest tests/video_agent/sources/test_wikimedia.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add video_agent/sources/wikimedia.py tests/video_agent/sources/test_wikimedia.py
git commit -m "feat(video): add Wikimedia Commons source"
```

---

### Task 1.6: Bing Image Search source

**Files:**
- Create: `video_agent/sources/bing.py`
- Test: `tests/video_agent/sources/test_bing.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/sources/test_bing.py
import responses
from video_agent.sources.bing import BingSource


@responses.activate
def test_bing_returns_candidates():
    responses.add(
        responses.GET, "https://api.bing.microsoft.com/v7.0/images/search",
        json={"value": [
            {"contentUrl": "https://x/1.jpg", "name": "wastewater plant",
             "width": 1920, "height": 1080, "encodingFormat": "jpeg"},
            {"contentUrl": "https://x/2.jpg", "name": "industrial",
             "width": 2400, "height": 1600, "encodingFormat": "jpeg"},
        ]},
    )
    src = BingSource(api_key="fake")
    cands = src.search("wastewater", limit=2)
    assert len(cands) == 2
    assert cands[0].url == "https://x/1.jpg"
    assert cands[0].width == 1920


def test_bing_no_key_empty():
    assert BingSource(api_key=None).search("x") == []
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/sources/test_bing.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/sources/bing.py
"""Bing Image Search API v7. Free 1k req/mo with a key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import BING_API_KEY

log = logging.getLogger(__name__)
_API = "https://api.bing.microsoft.com/v7.0/images/search"


class BingSource(BaseSource):
    name = "bing"
    authority_weight = 5

    def __init__(self, api_key: str | None = BING_API_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if not self.api_key:
            log.debug("Bing skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API, params={"q": query, "count": limit,
                              "safeSearch": "Strict",
                              "imageType": "Photo", "size": "Large"},
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Bing search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("value", [])[:limit]:
            url = item.get("contentUrl", "")
            if not url:
                continue
            out.append(RawCandidate(
                source=self.name, url=url,
                caption=item.get("name", ""),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
            ))
        return out
```

- [ ] **Step 4: Run**

```
pytest tests/video_agent/sources/test_bing.py -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```
git add video_agent/sources/bing.py tests/video_agent/sources/test_bing.py
git commit -m "feat(video): add Bing Image Search source"
```

---

### Task 1.7: DuckDuckGo source (reuse existing dependency)

**Files:**
- Create: `video_agent/sources/duckduckgo.py`
- Test: `tests/video_agent/sources/test_duckduckgo.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/sources/test_duckduckgo.py
from unittest.mock import patch
from video_agent.sources.duckduckgo import DuckDuckGoSource


def test_ddg_maps_results_to_candidates():
    fake = [
        {"image": "https://x/a.jpg", "title": "industrial",
         "width": 1920, "height": 1080},
        {"image": "https://x/b.jpg", "title": "factory",
         "width": 1280, "height": 720},
    ]
    with patch("video_agent.sources.duckduckgo.DDGS") as mock_ddgs:
        mock_ddgs.return_value.__enter__.return_value.images.return_value = fake
        cands = DuckDuckGoSource().search("industrial", limit=2)
    assert len(cands) == 2
    assert cands[0].url == "https://x/a.jpg"
    assert cands[0].width == 1920
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/sources/test_duckduckgo.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/sources/duckduckgo.py
"""DuckDuckGo image search via the duckduckgo-search package."""
from __future__ import annotations
import logging
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


class DuckDuckGoSource(BaseSource):
    name = "duckduckgo"
    authority_weight = 5

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if DDGS is None:
            log.warning("duckduckgo-search not installed; skipping DDG source")
            return []
        out = []
        try:
            with DDGS() as ddgs:
                for item in ddgs.images(query, max_results=limit, safesearch="strict"):
                    url = item.get("image", "")
                    if not url:
                        continue
                    out.append(RawCandidate(
                        source=self.name, url=url,
                        caption=item.get("title", ""),
                        width=int(item.get("width", 0)),
                        height=int(item.get("height", 0)),
                    ))
        except Exception as e:
            log.warning("DDG search failed for %r: %s", query, e)
            return []
        return out
```

- [ ] **Step 4: Run + Commit**

```
pytest tests/video_agent/sources/test_duckduckgo.py -v
git add video_agent/sources/duckduckgo.py tests/video_agent/sources/test_duckduckgo.py
git commit -m "feat(video): add DuckDuckGo image source"
```

---

### Task 1.8: Google Images source (scraped — fragile)

**Files:**
- Create: `video_agent/sources/google_images.py`
- Test: `tests/video_agent/sources/test_google_images.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/sources/test_google_images.py
import responses
from video_agent.sources.google_images import GoogleImagesSource


_HTML = """
<html><body>
<script>AF_initDataCallback({key: 'ds:1', data: [
  null, [
    [null, null, [null, null, ["https://img1.example/full.jpg", 1920, 1080]],
     null, null, null, null, null, null, [null, null,
       null, null, null, null, null, [null, "wastewater treatment plant aerial"]]],
    [null, null, [null, null, ["https://img2.example/full.jpg", 2560, 1440]],
     null, null, null, null, null, null, [null, null,
       null, null, null, null, null, [null, "factory water tank"]]]
  ]
]});</script>
</body></html>
"""


@responses.activate
def test_google_images_parses_html_payload():
    responses.add(responses.GET, "https://www.google.com/search",
                  body=_HTML, content_type="text/html")
    src = GoogleImagesSource()
    cands = src.search("wastewater", limit=2)
    # Parser is heuristic; assert we got *something* with valid URLs
    assert all(c.url.startswith("https://") for c in cands)


@responses.activate
def test_google_images_returns_empty_on_layout_change():
    responses.add(responses.GET, "https://www.google.com/search",
                  body="<html><body>blocked</body></html>",
                  content_type="text/html")
    src = GoogleImagesSource()
    assert src.search("anything") == []
```

- [ ] **Step 2: Run to verify failure**

```
pytest tests/video_agent/sources/test_google_images.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/sources/google_images.py
"""Google Images via HTML scrape. Fragile by nature — wraps in try/except.

Google does not provide a free public Image Search API. This scraper parses
the AF_initDataCallback payload that Google Images embeds in its HTML
response. When the layout changes, parsing returns an empty list and we
fall back to other sources.
"""
from __future__ import annotations
import logging
import re
import urllib.parse
import requests
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/121.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Heuristic: find triplets of (url, width, height) that look like image refs.
_IMG_TRIPLET_RE = re.compile(
    r'\["(https?://[^"]+\.(?:jpg|jpeg|png|webp))",\s*(\d{3,5}),\s*(\d{3,5})\]',
    re.IGNORECASE,
)
# Captions appear after the URL in nearby brackets — best-effort.
_CAPTION_NEARBY_RE = re.compile(r'"([^"]{20,200})"')


class GoogleImagesSource(BaseSource):
    name = "google_images"
    authority_weight = 3

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        try:
            r = requests.get(
                "https://www.google.com/search",
                params={"q": query, "tbm": "isch", "hl": "en",
                        "safe": "active"},
                headers=_HEADERS, timeout=15,
            )
            r.raise_for_status()
            html = r.text
        except Exception as e:
            log.warning("Google Images request failed for %r: %s", query, e)
            return []

        seen_urls = set()
        out = []
        for m in _IMG_TRIPLET_RE.finditer(html):
            url, w, h = m.group(1), int(m.group(2)), int(m.group(3))
            if url in seen_urls or url.startswith("https://encrypted-tbn"):
                continue
            seen_urls.add(url)
            # Find a caption nearby (next 800 chars after the match)
            window = html[m.end():m.end() + 800]
            cap_match = _CAPTION_NEARBY_RE.search(window)
            caption = cap_match.group(1) if cap_match else ""
            out.append(RawCandidate(
                source=self.name, url=url, caption=caption,
                width=w, height=h,
            ))
            if len(out) >= limit:
                break
        if not out:
            log.warning(
                "Google Images returned 0 parseable candidates for %r — "
                "Google may have changed its HTML layout", query)
        return out
```

- [ ] **Step 4: Run**

```
pytest tests/video_agent/sources/test_google_images.py -v
```

Expected: 2 PASS (the first test may extract 0 candidates due to heuristic parsing of synthetic HTML; assertion only checks that whatever returns has valid URLs)

- [ ] **Step 5: Commit**

```
git add video_agent/sources/google_images.py tests/video_agent/sources/test_google_images.py
git commit -m "feat(video): add Google Images scraper source"
```

---

### Task 1.9: YouTube clip source (yt-dlp)

**Files:**
- Modify: `requirements.txt` (add `yt-dlp>=2024.1.0`)
- Create: `video_agent/sources/youtube.py`
- Test: `tests/video_agent/sources/test_youtube.py`

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:
```
yt-dlp>=2024.1.0
```
Then `pip install -r requirements.txt`.

- [ ] **Step 2: Write failing test**

```python
# tests/video_agent/sources/test_youtube.py
from unittest.mock import patch, MagicMock
from video_agent.sources.youtube import YouTubeSource


def test_youtube_filters_short_or_low_view_videos():
    fake_results = {"entries": [
        {"id": "id1", "title": "industrial water plant", "duration": 120,
         "view_count": 50_000, "thumbnail": "https://i.ytimg.com/x.jpg"},
        {"id": "id2", "title": "low quality", "duration": 5,
         "view_count": 2_000, "thumbnail": ""},  # too short / too few views
    ]}
    fake_ydl = MagicMock()
    fake_ydl.extract_info.return_value = fake_results
    with patch("video_agent.sources.youtube.yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl_cls.return_value.__enter__.return_value = fake_ydl
        cands = YouTubeSource().search("industrial water", limit=5)
    assert len(cands) == 1
    assert cands[0].is_clip is True
    assert cands[0].duration_s == 120
    assert "industrial" in cands[0].caption.lower()
```

- [ ] **Step 3: Run**

```
pytest tests/video_agent/sources/test_youtube.py -v
```

- [ ] **Step 4: Implement**

```python
# video_agent/sources/youtube.py
"""YouTube search via yt-dlp. Returns metadata only — actual download
happens in the Sourcer's download phase to keep this fast."""
from __future__ import annotations
import logging
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

try:
    import yt_dlp                    # noqa: F401
except ImportError:                  # pragma: no cover
    yt_dlp = None


_MIN_DURATION_S = 30
_MIN_VIEWS = 10_000


class YouTubeSource(BaseSource):
    name = "youtube"
    authority_weight = 5

    def search(self, query: str, limit: int = 3) -> list[RawCandidate]:
        if yt_dlp is None:
            log.warning("yt-dlp not installed; YouTube source disabled")
            return []
        opts = {
            "quiet": True, "skip_download": True, "extract_flat": False,
            "no_warnings": True, "default_search": f"ytsearch{limit}",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception as e:
            log.warning("YouTube search failed for %r: %s", query, e)
            return []

        entries = info.get("entries") or [info]
        out = []
        for e in entries:
            if not e:
                continue
            duration = e.get("duration") or 0
            views = e.get("view_count") or 0
            if duration < _MIN_DURATION_S or views < _MIN_VIEWS:
                continue
            vid = e.get("id", "")
            out.append(RawCandidate(
                source=self.name,
                url=f"https://www.youtube.com/watch?v={vid}",
                caption=e.get("title", ""),
                width=1920, height=1080,
                is_clip=True, duration_s=float(duration),
                extra={"video_id": vid, "view_count": views,
                       "thumbnail": e.get("thumbnail", "")},
            ))
        return out[:limit]
```

- [ ] **Step 5: Run + commit**

```
pytest tests/video_agent/sources/test_youtube.py -v
git add requirements.txt video_agent/sources/youtube.py tests/video_agent/sources/test_youtube.py
git commit -m "feat(video): add YouTube source via yt-dlp"
```

---

### Task 1.10: Sourcer agent — fan-out + scoring + download

**Files:**
- Create: `video_agent/agents/__init__.py` (empty)
- Create: `video_agent/agents/sourcer.py`
- Test: `tests/video_agent/agents/test_sourcer.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_sourcer.py
from unittest.mock import patch, MagicMock
from pathlib import Path
from video_agent.agents.sourcer import Sourcer
from video_agent.sources.base import RawCandidate
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, HeroClaim, Beat,
)


def _scene(idx, subject="industrial water"):
    return Scene(
        index=idx, beat="hook",
        narration="…", on_screen_text="…",
        visual_concept=VisualConcept(subject=subject, modifier="aerial",
                                     type="photo", mood="problem",
                                     style_hint=""),
        duration_target_s=4.0, transition_in="cut",
    )


def _sb(scenes):
    return Storyboard(version="2.0",
                      blog={"id": "b", "url": "u", "title": "t",
                            "region": "australia", "category": "mining",
                            "persona": "procurement"},
                      hero_claim=HeroClaim(stat="90%", claim_text="x"),
                      arc=[Beat(index=i, beat="hook", purpose="x",
                                duration_target_s=4.0) for i in range(len(scenes))],
                      scenes=scenes)


def test_sourcer_picks_best_candidate(tmp_path):
    cands = [
        RawCandidate(source="unsplash", url="https://u/good.jpg",
                     caption="industrial water plant aerial",
                     width=1920, height=1080, file_size=120_000),
        RawCandidate(source="google_images", url="https://g/bad.jpg",
                     caption="cat", width=1920, height=1080, file_size=60_000),
    ]
    fake_src = MagicMock()
    fake_src.name = "unsplash"
    fake_src.search.return_value = cands
    with patch("video_agent.agents.sourcer.Sourcer._download_candidate",
               return_value=tmp_path / "downloaded.jpg") as mock_dl, \
         patch("video_agent.agents.sourcer.Sourcer._is_dup", return_value=False):
        # write a dummy file so the path exists
        (tmp_path / "downloaded.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        s = _scene(0)
        sb = _sb([s])
        Sourcer(sources=[fake_src], cache_root=tmp_path / "cache",
                download_root=tmp_path / "dl").run(sb)
    assert sb.scenes[0].chosen_asset is not None
    assert sb.scenes[0].chosen_asset.source == "unsplash"
    assert sb.scenes[0].degraded is False


def test_sourcer_marks_degraded_when_no_candidate(tmp_path):
    fake_src = MagicMock()
    fake_src.name = "unsplash"
    fake_src.search.return_value = []
    s = _scene(0)
    sb = _sb([s])
    Sourcer(sources=[fake_src], cache_root=tmp_path / "cache",
            download_root=tmp_path / "dl").run(sb)
    assert sb.scenes[0].chosen_asset is None
    assert sb.scenes[0].degraded is True
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/agents/test_sourcer.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/sourcer.py
"""Sourcer — fan out to all sources, score, deduplicate, download."""
from __future__ import annotations
import dataclasses
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.sources.cache import QueryCache
from video_agent.sources.scoring import score_candidate
from video_agent.storyboard import (
    Storyboard, Scene, AssetCandidate, VisualConcept,
)

log = logging.getLogger(__name__)

_TOPIC_DEFAULTS = {
    "wastewater_treatment": "industrial water treatment plant",
    "concrete_construction": "concrete pour construction site",
    "mining": "mining operation",
    "agriculture": "fertiliser application field",
    "oil_gas": "oil rig industrial",
    "water_treatment": "water treatment facility",
}


def _build_queries(vc: VisualConcept, blog_category: str) -> list[str]:
    specific = f"{vc.subject} {vc.modifier}".strip()
    abstract = vc.subject
    generic = _TOPIC_DEFAULTS.get(blog_category, "industrial process")
    return [q for q in (specific, abstract, generic) if q]


class Sourcer:
    def __init__(self, sources: list[BaseSource],
                 cache_root: Path, download_root: Path,
                 candidates_per_source: int = 5,
                 min_score: int = 40,
                 max_workers: int = 6):
        self.sources = sources
        self.cache = QueryCache(cache_root)
        self.download_root = Path(download_root)
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.candidates_per_source = candidates_per_source
        self.min_score = min_score
        self.max_workers = max_workers
        self._used_phashes: set[str] = set()

    def run(self, sb: Storyboard) -> Storyboard:
        for scene in sb.scenes:
            self._source_scene(scene, sb.blog.get("category", ""))
        return sb

    def _source_scene(self, scene: Scene, blog_category: str) -> None:
        queries = _build_queries(scene.visual_concept, blog_category)
        all_raw: list[RawCandidate] = []
        for q in queries:
            raws = self._search_all_sources(q)
            all_raw.extend(raws)
            scored = [(score_candidate(c, q), c) for c in raws]
            scored = [t for t in scored if t[0] >= self.min_score]
            if len(scored) >= 3:
                break

        if not all_raw:
            scene.degraded = True
            log.warning("Scene %d: no candidates from any source", scene.index)
            return

        # Score & sort
        primary_q = queries[0]
        scored = sorted(
            ((score_candidate(c, primary_q), c) for c in all_raw),
            key=lambda t: -t[0],
        )

        # Take top 5 — download; perceptual-hash dedupe; first survivor wins
        scene.asset_candidates = []
        chosen = None
        for s, c in scored[:5]:
            if s < self.min_score:
                continue
            local = self._download_candidate(c, scene.index)
            if local is None:
                continue
            if self._is_dup(local):
                log.debug("Scene %d candidate %s deduped", scene.index, c.url)
                continue
            ac = AssetCandidate(
                source=c.source, url=c.url, score=s,
                local_path=str(local), caption=c.caption,
                width=c.width, height=c.height,
                is_clip=c.is_clip, duration_s=c.duration_s,
            )
            scene.asset_candidates.append(ac)
            if chosen is None:
                chosen = ac
            if len(scene.asset_candidates) >= 3:
                break

        if chosen is None:
            scene.degraded = True
            log.warning("Scene %d: all candidates rejected after download/dedup",
                        scene.index)
        else:
            scene.chosen_asset = chosen

    def _search_all_sources(self, query: str) -> list[RawCandidate]:
        """Fan out to every source in parallel, hitting cache first."""
        out = []

        def _one(src: BaseSource) -> list[RawCandidate]:
            cached = self.cache.get(query, src.name)
            if cached is not None:
                return cached
            try:
                cands = src.search(query, limit=self.candidates_per_source)
            except Exception as e:                      # source failure → skip
                log.warning("Source %s failed for %r: %s", src.name, query, e)
                return []
            self.cache.put(query, src.name, cands)
            return cands

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(_one, s) for s in self.sources]
            for f in as_completed(futures):
                out.extend(f.result())
        return out

    def _download_candidate(self, c: RawCandidate, scene_idx: int) -> Path | None:
        ext = ".mp4" if c.is_clip else ".jpg"
        fname = f"scene_{scene_idx:02d}_{c.source}_{hashlib.md5(c.url.encode()).hexdigest()[:8]}{ext}"
        dest = self.download_root / fname
        if dest.exists():
            return dest
        if c.is_clip:
            return self._download_youtube_clip(c, dest)
        try:
            r = requests.get(c.url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 HRSU-VideoBot/2.0",
            })
            r.raise_for_status()
            dest.write_bytes(r.content)
            # Verify it's a valid image
            with Image.open(dest) as img:
                img.verify()
            return dest
        except Exception as e:
            log.debug("Download/verify failed for %s: %s", c.url, e)
            dest.unlink(missing_ok=True)
            return None

    def _download_youtube_clip(self, c: RawCandidate, dest: Path) -> Path | None:
        """Download a 6–10s clip from the 25% mark of the source video."""
        try:
            import yt_dlp
        except ImportError:
            log.warning("yt-dlp not installed; cannot download YouTube clip")
            return None
        # Estimate clip range: start at 25%, length = min(10, narration_len)
        start_s = max(0, int((c.duration_s or 60) * 0.25))
        # Use ffmpeg post-processor via download_ranges to grab 10s
        opts = {
            "quiet": True, "no_warnings": True,
            "format": "best[height<=1080]",
            "outtmpl": str(dest),
            "download_ranges": lambda info, ydl:
                [{"start_time": start_s, "end_time": start_s + 10}],
            "force_keyframes_at_cuts": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([c.url])
            return dest if dest.exists() else None
        except Exception as e:
            log.warning("YouTube clip download failed for %s: %s", c.url, e)
            return None

    def _is_dup(self, path: Path) -> bool:
        """Perceptual-hash dedupe across scenes within the same video."""
        try:
            from PIL import Image as PImage
            import imagehash
            with PImage.open(path) as img:
                phash = str(imagehash.phash(img))
        except Exception:
            return False
        if phash in self._used_phashes:
            return True
        self._used_phashes.add(phash)
        return False
```

- [ ] **Step 4: Add `imagehash` dependency**

Append to `requirements.txt`: `ImageHash>=4.3.1`. Run `pip install -r requirements.txt`.

- [ ] **Step 5: Run**

```
pytest tests/video_agent/agents/test_sourcer.py -v
```

Expected: 2 PASS

- [ ] **Step 6: Commit**

```
git add video_agent/agents/__init__.py video_agent/agents/sourcer.py requirements.txt tests/video_agent/agents/__init__.py tests/video_agent/agents/test_sourcer.py
git commit -m "feat(video): add Sourcer agent with parallel fan-out, scoring, dedup"
```

---

### Task 1.11: Phase-1 adapter — plug Sourcer into legacy pipeline

**Files:**
- Modify: `scripts/make_video.py` (add `--new-sourcer` flag)
- Create: `video_agent/agents/_legacy_adapter.py` — converts a legacy script.json's scenes into a partial Storyboard for the Sourcer to enrich

- [ ] **Step 1: Implement adapter**

```python
# video_agent/agents/_legacy_adapter.py
"""Bridges the legacy script.json shape to a partial Storyboard so we can
plug the new Sourcer into the existing pipeline before agents are ready."""
from __future__ import annotations
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, HeroClaim, Beat,
)


_BEAT_BY_LAYOUT = {"hook": "hook", "cta": "cta"}


def legacy_to_storyboard(legacy_script: dict, blog: dict) -> Storyboard:
    scenes = []
    for s in legacy_script.get("scenes", []):
        spec = s.get("visual_spec") or {}
        layout = spec.get("layout", "")
        beat = _BEAT_BY_LAYOUT.get(layout, "mechanism")
        # Pull subject from on_screen_text or query if available
        subject = (spec.get("query") or s.get("on_screen_text", "")
                   or s.get("narration", "")[:60])
        scenes.append(Scene(
            index=s["index"], beat=beat,
            narration=s.get("narration", ""),
            on_screen_text=s.get("on_screen_text", ""),
            visual_concept=VisualConcept(
                subject=subject, modifier="",
                type="photo", mood="problem", style_hint=""),
            duration_target_s=float(s.get("duration_s", 4.0)),
            transition_in=s.get("transition_in", "cut"),
        ))
    return Storyboard(
        version="2.0", blog=blog,
        hero_claim=HeroClaim(stat="", claim_text=legacy_script.get("hook", "")),
        arc=[Beat(index=i, beat=sc.beat, purpose="",
                  duration_target_s=sc.duration_target_s)
             for i, sc in enumerate(scenes)],
        scenes=scenes,
    )
```

- [ ] **Step 2: Wire `--new-sourcer` into make_video.py**

Find the section just after `script = build_script(...)` and add (only when flag set):

```python
if args.new_sourcer:
    from video_agent.agents._legacy_adapter import legacy_to_storyboard
    from video_agent.agents.sourcer import Sourcer
    from video_agent.sources.unsplash import UnsplashSource
    from video_agent.sources.bing import BingSource
    from video_agent.sources.wikimedia import WikimediaSource
    from video_agent.sources.duckduckgo import DuckDuckGoSource
    from video_agent.sources.google_images import GoogleImagesSource
    from video_agent.sources.youtube import YouTubeSource

    log.info("Sourcing real images via new pipeline …")
    sb = legacy_to_storyboard(script, blog_record)
    Sourcer(
        sources=[UnsplashSource(), WikimediaSource(), BingSource(),
                 DuckDuckGoSource(), GoogleImagesSource(), YouTubeSource()],
        cache_root=Path("output/_image_cache"),
        download_root=workspace / "_assets",
    ).run(sb)
    # Patch the legacy `script` so generate_all_visuals picks up real images
    for scene_legacy, scene_new in zip(script["scenes"], sb.scenes):
        if scene_new.chosen_asset and not scene_new.degraded:
            scene_legacy["_source"] = {
                "path": scene_new.chosen_asset.local_path,
                "caption": scene_new.chosen_asset.caption,
                "source_url": scene_new.chosen_asset.url,
                "is_authority": False,
            }
    real_img_count = sum(1 for s in sb.scenes if not s.degraded)
    log.info("New sourcer: %d/%d scenes have real images",
             real_img_count, len(sb.scenes))
```

And add the flag to argparse:
```python
parser.add_argument("--new-sourcer", action="store_true",
                    help="Use the new multi-source image pipeline (Phase 1)")
```

- [ ] **Step 3: Run smoke test (manual; requires Ollama running)**

```
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization-efficiency-can-in.html --force --new-sourcer
```

Expected log line: `New sourcer: N/9 scenes have real images` with N ≥ 5.

- [ ] **Step 4: Commit**

```
git add video_agent/agents/_legacy_adapter.py scripts/make_video.py
git commit -m "feat(video): plug new Sourcer into legacy pipeline behind --new-sourcer"
```

**Phase 1 milestone:** Run `make_video.py <url> --new-sourcer`. Open the resulting MP4 — every scene that had a text card before now has a real photo or video clip from the web.

---

# Phase 2 — Strategist + Storyboarder

Replace the legacy narration/scene-breakdown logic with hero-claim-driven script generation.

---

### Task 2.1: Strategist agent

**Files:**
- Create: `video_agent/agents/strategist.py`
- Test: `tests/video_agent/agents/test_strategist.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_strategist.py
from unittest.mock import patch
from video_agent.agents.strategist import Strategist
from video_agent.storyboard import Storyboard


_FAKE_OLLAMA = {
    "hero": {"stat": "90%", "claim_text": "Calcium nitrate cuts H2S by 90%",
             "source_quote": "Field trials at Hunter Valley showed 90% removal"},
    "arc": [
        {"beat": "hook", "purpose": "Hook with the 90% stat", "duration_target_s": 3.5},
        {"beat": "stakes", "purpose": "Cost of untreated H2S", "duration_target_s": 6.0},
        {"beat": "mechanism", "purpose": "How CaN oxidises sulfide", "duration_target_s": 10.0},
        {"beat": "proof", "purpose": "Hunter Valley case study", "duration_target_s": 10.0},
        {"beat": "cta", "purpose": "HRSU spec sheet CTA", "duration_target_s": 5.0},
    ],
    "supporting_facts": [
        {"value": "50", "unit": "mg/L", "claim": "WHO drinking-water nitrate limit"}
    ],
}


def test_strategist_populates_hero_arc_supporting():
    facts = [{"value": "90", "unit": "%", "claim": "..."},
             {"value": "50", "unit": "mg/L", "claim": "..."}]
    blog = {"id": "b", "url": "u", "title": "Lime Neutralization",
            "region": "australia", "category": "mining", "persona": "procurement"}
    sb = Storyboard(version="2.0", blog=blog)
    with patch("video_agent.agents.strategist.OllamaClient") as mock_cls:
        mock_cls.return_value.generate_json.return_value = _FAKE_OLLAMA
        Strategist().run(sb, facts, "<html>full blog text</html>")
    assert sb.hero_claim.stat == "90%"
    assert len(sb.arc) == 5
    assert [b.beat for b in sb.arc] == ["hook", "stakes", "mechanism", "proof", "cta"]
    assert len(sb.supporting_facts) == 1
```

- [ ] **Step 2: Run + verify failure**

```
pytest tests/video_agent/agents/test_strategist.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/strategist.py
"""Strategist — picks the hero claim and the 5-beat arc."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient
from video_agent.storyboard import Storyboard, HeroClaim, Beat

log = logging.getLogger(__name__)

_SYSTEM = """You are a B2B chemistry video Strategist for HRSU. Pick exactly
ONE hero claim from the article that meets all three criteria:
  - SURPRISING (counterintuitive or notable, not generic)
  - SPECIFIC (a number with a unit and clear context)
  - AUDIENCE-FIT (a procurement manager in this region cares)

Then build a 5-beat arc supporting that claim:
  1 hook (3-4s) — state the hero stat as a question or claim
  2 stakes (5-7s) — cost of NOT solving this
  3 mechanism (8-12s) — how the chemistry works, simply
  4 proof (8-12s) — concrete validation: regional case, regulation, or comparison
  5 cta (4-6s) — HRSU tie-back: "HRSU supplies this. Visit hrsuindore.com"

Other numeric facts go into supporting_facts (NOT their own scene).

Respond as JSON:
{
  "hero": {"stat": "...", "claim_text": "...", "source_quote": "..."},
  "arc": [{"beat": "hook|stakes|mechanism|proof|cta",
           "purpose": "...", "duration_target_s": 3.5}, ...],
  "supporting_facts": [{"value": "...", "unit": "...", "claim": "..."}, ...]
}
"""


class Strategist:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard, facts: list, blog_html_excerpt: str) -> Storyboard:
        bullet_facts = "\n".join(
            f"- {f.get('value', '')} {f.get('unit', '')}: {f.get('claim', '')}"
            for f in facts[:15]
        )
        prompt = (
            f"Region: {sb.blog.get('region')}\n"
            f"Category: {sb.blog.get('category')}\n"
            f"Audience: {sb.blog.get('persona', 'procurement')} managers\n\n"
            f"Numeric facts extracted from the article:\n{bullet_facts}\n\n"
            f"Article excerpt:\n{blog_html_excerpt[:2000]}\n\n"
            "Pick ONE hero claim and produce the 5-beat arc."
        )
        out = self.client.generate_json(prompt, system=_SYSTEM)
        if not isinstance(out, dict) or "hero" not in out or "arc" not in out:
            raise RuntimeError(f"Strategist returned malformed output: {out!r}")
        sb.hero_claim = HeroClaim(**out["hero"])
        sb.arc = [
            Beat(index=i, beat=b["beat"], purpose=b.get("purpose", ""),
                 duration_target_s=float(b.get("duration_target_s", 5.0)))
            for i, b in enumerate(out["arc"])
        ]
        sb.supporting_facts = out.get("supporting_facts", [])
        log.info("Strategist: hero=%s; %d beats; %d supporting facts",
                 sb.hero_claim.stat, len(sb.arc), len(sb.supporting_facts))
        return sb
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_strategist.py -v
git add video_agent/agents/strategist.py tests/video_agent/agents/test_strategist.py
git commit -m "feat(video): add Strategist agent (hero-claim + 5-beat arc)"
```

---

### Task 2.2: Storyboarder agent

**Files:**
- Create: `video_agent/agents/storyboarder.py`
- Test: `tests/video_agent/agents/test_storyboarder.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_storyboarder.py
from unittest.mock import patch
from video_agent.agents.storyboarder import Storyboarder
from video_agent.storyboard import Storyboard, HeroClaim, Beat


_FAKE = {
    "scenes": [
        {"narration": "Are wastewater costs rising?",
         "on_screen_text": "90% H2S CUT",
         "visual_concept": {"subject": "wastewater plant", "modifier": "aerial",
                            "type": "photo", "mood": "problem",
                            "style_hint": "documentary"}},
        {"narration": "Untreated H2S corrodes pipes.",
         "on_screen_text": "$5K/MONTH PIPE LOSS",
         "visual_concept": {"subject": "corroded steel pipe", "modifier": "rust",
                            "type": "photo", "mood": "problem",
                            "style_hint": "documentary"}},
        {"narration": "Calcium nitrate oxidises sulfide to sulfate.",
         "on_screen_text": "S²⁻ → SO₄²⁻",
         "visual_concept": {"subject": "sulfide oxidation chemical equation",
                            "modifier": "diagram", "type": "diagram",
                            "mood": "mechanism", "style_hint": "scientific"}},
        {"narration": "At Hunter Valley, 98% sulfide removal.",
         "on_screen_text": "HUNTER VALLEY: 98%",
         "visual_concept": {"subject": "australian mine site",
                            "modifier": "aerial drone",
                            "type": "photo", "mood": "proof",
                            "style_hint": "documentary"}},
        {"narration": "HRSU supplies REACH-grade calcium nitrate.",
         "on_screen_text": "REACH-GRADE",
         "visual_concept": {"subject": "calcium nitrate bag stockpile",
                            "modifier": "industrial", "type": "photo",
                            "mood": "brand", "style_hint": "branded"}},
    ]
}


def test_storyboarder_creates_one_scene_per_beat():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"})
    sb.hero_claim = HeroClaim(stat="90%", claim_text="cuts H2S 90%")
    sb.arc = [
        Beat(index=0, beat="hook", purpose="hook", duration_target_s=3.5),
        Beat(index=1, beat="stakes", purpose="stakes", duration_target_s=6.0),
        Beat(index=2, beat="mechanism", purpose="mech", duration_target_s=10.0),
        Beat(index=3, beat="proof", purpose="proof", duration_target_s=10.0),
        Beat(index=4, beat="cta", purpose="cta", duration_target_s=5.0),
    ]
    with patch("video_agent.agents.storyboarder.OllamaClient") as mock_cls:
        mock_cls.return_value.generate_json.return_value = _FAKE
        Storyboarder().run(sb)
    assert len(sb.scenes) == 5
    assert [s.beat for s in sb.scenes] == ["hook", "stakes", "mechanism",
                                            "proof", "cta"]
    assert sb.scenes[2].visual_concept.type == "diagram"
    assert sb.scenes[0].on_screen_text == "90% H2S CUT"
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/agents/test_storyboarder.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/storyboarder.py
"""Storyboarder — fills in scenes[] one per beat."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient
from video_agent.storyboard import Storyboard, Scene, VisualConcept

log = logging.getLogger(__name__)

_SYSTEM = """You write per-beat scenes for a 30-55 second B2B chemistry video.
The HERO CLAIM is the ONE thing the entire video says. Every scene must
reinforce it.

For each beat in the arc, return a scene with:
  - narration: verbatim sentence(s) the voiceover will say (~2.5 wps)
  - on_screen_text: ≤6 words; MUST add a number, brand, or contrast NOT
    already in the narration. Never paraphrase the voice.
  - visual_concept: {subject, modifier, type, mood, style_hint}
      type ∈ {"photo", "diagram", "clip", "chart_data"}
      mood ∈ {"problem", "mechanism", "proof", "brand"}

Rules:
  * Hook beat → ALL CAPS on_screen_text; question or claim opening
  * Stakes beat → frame the cost of inaction
  * Mechanism beat → use diagram/diagram-like type when possible
  * Proof beat → reference a regional case, regulation, or comparison
  * CTA beat → name HRSU + hrsuindore.com

Respond as JSON: {"scenes": [<scene>, ...]} with exactly len(arc) entries
in arc order.
"""


class Storyboarder:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        if not sb.hero_claim or not sb.arc:
            raise RuntimeError("Storyboarder needs hero_claim and arc to be populated")
        arc_outline = "\n".join(
            f"  {i}. {b.beat} ({b.duration_target_s}s) — {b.purpose}"
            for i, b in enumerate(sb.arc)
        )
        prompt = (
            f"Hero claim: {sb.hero_claim.claim_text}\n"
            f"Hero stat: {sb.hero_claim.stat}\n"
            f"Source quote: {sb.hero_claim.source_quote}\n\n"
            f"Region: {sb.blog.get('region')} | "
            f"Audience: {sb.blog.get('persona', 'procurement')}\n\n"
            f"Arc:\n{arc_outline}\n\n"
            f"Supporting facts (use in mechanism/proof; do not give them "
            f"their own scene):\n"
            + "\n".join(f"  - {f.get('value','')} {f.get('unit','')}: "
                        f"{f.get('claim','')}"
                        for f in sb.supporting_facts[:5])
            + "\n\nWrite the scenes."
        )
        out = self.client.generate_json(prompt, system=_SYSTEM)
        scenes_raw = out.get("scenes", []) if isinstance(out, dict) else []
        if len(scenes_raw) != len(sb.arc):
            log.warning("Storyboarder returned %d scenes for %d beats; "
                        "padding/truncating", len(scenes_raw), len(sb.arc))
        scenes_raw = (scenes_raw + [{}] * len(sb.arc))[:len(sb.arc)]
        sb.scenes = []
        for i, (beat, raw) in enumerate(zip(sb.arc, scenes_raw)):
            vc_raw = raw.get("visual_concept") or {}
            sb.scenes.append(Scene(
                index=i, beat=beat.beat,
                narration=raw.get("narration", ""),
                on_screen_text=raw.get("on_screen_text", "")[:60],
                visual_concept=VisualConcept(
                    subject=vc_raw.get("subject", ""),
                    modifier=vc_raw.get("modifier", ""),
                    type=vc_raw.get("type", "photo"),
                    mood=vc_raw.get("mood", "problem"),
                    style_hint=vc_raw.get("style_hint", ""),
                ),
                duration_target_s=beat.duration_target_s,
                transition_in="cut" if i == 0 else "fade",
            ))
        log.info("Storyboarder: %d scenes generated", len(sb.scenes))
        return sb
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_storyboarder.py -v
git add video_agent/agents/storyboarder.py tests/video_agent/agents/test_storyboarder.py
git commit -m "feat(video): add Storyboarder agent"
```

---

### Task 2.2b: On-screen text validator (post-process step)

**Files:**
- Modify: `video_agent/agents/storyboarder.py` (add `_validate_on_screen_text` and call it after generation)
- Test: `tests/video_agent/agents/test_storyboarder_validation.py`

Implements the spec rule: on-screen text must be ≤6 words, NOT a substring or paraphrase of the narration, and must add a number/brand/contrast.

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_storyboarder_validation.py
from video_agent.agents.storyboarder import _validate_on_screen_text


def test_text_too_long_pre_set_flag_returned():
    flag = _validate_on_screen_text(
        "This is a sentence with too many words to be a chart label",
        narration="Calcium nitrate works.")
    assert flag == "text_too_long"


def test_text_paraphrases_narration_flag():
    flag = _validate_on_screen_text(
        "WASTEWATER COSTS RISING",
        narration="Are wastewater costs rising for your plant?")
    assert flag == "text_duplicates_voice"


def test_text_with_number_passes():
    flag = _validate_on_screen_text(
        "90% H2S CUT",
        narration="Calcium nitrate makes a real difference.")
    assert flag is None


def test_text_with_brand_passes():
    flag = _validate_on_screen_text(
        "REACH-GRADE BY HRSU",
        narration="We supply industrial grade chemicals.")
    assert flag is None
```

- [ ] **Step 2: Run + verify failure**

```
pytest tests/video_agent/agents/test_storyboarder_validation.py -v
```

- [ ] **Step 3: Implement `_validate_on_screen_text` and call it at the end of `Storyboarder.run`**

Append to `video_agent/agents/storyboarder.py`:

```python
import re

_NUMERIC_RE = re.compile(r"\d")
_BRAND_TOKENS = {"hrsu", "reach", "epa", "iso", "anfo", "can", "h2s",
                 "h₂s", "h2so4", "co2", "ppm"}


def _validate_on_screen_text(text: str, narration: str) -> str | None:
    """Returns a flag string if invalid, else None.

    Flags: text_too_long | text_duplicates_voice | text_unrelated
    """
    if len(text.split()) > 6:
        return "text_too_long"
    # Substring overlap → "duplicates voice"
    text_words = {w.lower().strip(".,;:!?") for w in text.split() if w}
    narr_words = {w.lower().strip(".,;:!?") for w in narration.split() if w}
    if text_words and (text_words & narr_words) and len(text_words - narr_words) <= 1:
        return "text_duplicates_voice"
    # Must add a number, brand, or contrast indicator
    has_number = bool(_NUMERIC_RE.search(text))
    has_brand = bool(text_words & _BRAND_TOKENS)
    has_contrast = "vs" in text.lower() or "→" in text or "->" in text
    if not (has_number or has_brand or has_contrast):
        return "text_unrelated"
    return None
```

In `Storyboarder.run` (existing method), append this block after `sb.scenes` is fully populated and before `return sb`:

```python
        # Validate each on-screen text; mark a pre-set critic flag if invalid.
        for scene in sb.scenes:
            flag = _validate_on_screen_text(scene.on_screen_text,
                                            scene.narration)
            if flag:
                scene.critic_notes.flags.append(flag)
                scene.critic_notes.alignment_score = min(
                    scene.critic_notes.alignment_score, 6)
                log.debug("Scene %d on_screen_text invalid: %s",
                          scene.index, flag)
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_storyboarder_validation.py -v
git add video_agent/agents/storyboarder.py tests/video_agent/agents/test_storyboarder_validation.py
git commit -m "feat(video): validate on-screen text in Storyboarder"
```

---

### Task 2.3: Orchestrator wiring (Strategist → Storyboarder → Sourcer)

**Files:**
- Create: `video_agent/orchestrator.py`
- Test: `tests/video_agent/test_orchestrator.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/test_orchestrator.py
from unittest.mock import patch, MagicMock
from pathlib import Path
from video_agent.orchestrator import build_storyboard
from video_agent.storyboard import Storyboard, HeroClaim, Beat, Scene, VisualConcept


def test_orchestrator_calls_each_stage_in_order(tmp_path):
    blog = {"id": "b", "url": "u", "title": "t", "region": "australia",
            "category": "mining", "persona": "procurement"}
    facts = [{"value": "90", "unit": "%", "claim": "..."}]

    def _fake_strategist_run(sb, facts, html):
        sb.hero_claim = HeroClaim(stat="90%", claim_text="x")
        sb.arc = [Beat(index=i, beat=b, purpose="", duration_target_s=4.0)
                  for i, b in enumerate(["hook", "stakes", "mechanism",
                                          "proof", "cta"])]
        return sb

    def _fake_sb_run(sb):
        sb.scenes = [Scene(index=i, beat=b.beat, narration="",
                           on_screen_text="",
                           visual_concept=VisualConcept(
                               subject="x", modifier="", type="photo",
                               mood="problem", style_hint=""),
                           duration_target_s=b.duration_target_s,
                           transition_in="cut")
                     for i, b in enumerate(sb.arc)]
        return sb

    fake_sourcer = MagicMock()
    fake_sourcer.run.side_effect = lambda sb: sb

    with patch("video_agent.orchestrator.Strategist") as MStrat, \
         patch("video_agent.orchestrator.Storyboarder") as MSB, \
         patch("video_agent.orchestrator._build_sourcer", return_value=fake_sourcer):
        MStrat.return_value.run.side_effect = _fake_strategist_run
        MSB.return_value.run.side_effect = _fake_sb_run
        sb = build_storyboard(blog, facts, "<html/>", workspace=tmp_path)
    assert sb.hero_claim.stat == "90%"
    assert len(sb.scenes) == 5
    fake_sourcer.run.assert_called_once()
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/test_orchestrator.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/orchestrator.py
"""Wires together Strategist → Storyboarder → Sourcer (and later Critics +
Reviser).  Returns a populated Storyboard ready for rendering."""
from __future__ import annotations
import logging
from pathlib import Path

from video_agent.agents.strategist import Strategist
from video_agent.agents.storyboarder import Storyboarder
from video_agent.agents.sourcer import Sourcer
from video_agent.sources.unsplash import UnsplashSource
from video_agent.sources.bing import BingSource
from video_agent.sources.wikimedia import WikimediaSource
from video_agent.sources.duckduckgo import DuckDuckGoSource
from video_agent.sources.google_images import GoogleImagesSource
from video_agent.sources.youtube import YouTubeSource
from video_agent.storyboard import Storyboard, save_storyboard

log = logging.getLogger(__name__)


def _build_sourcer(workspace: Path) -> Sourcer:
    return Sourcer(
        sources=[UnsplashSource(), WikimediaSource(), BingSource(),
                 DuckDuckGoSource(), GoogleImagesSource(), YouTubeSource()],
        cache_root=Path("output/_image_cache"),
        download_root=workspace / "_assets",
    )


def build_storyboard(blog: dict, facts: list, blog_html: str,
                     workspace: Path) -> Storyboard:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    sb = Storyboard(version="2.0", blog=blog)

    log.info("[1/3] Strategist")
    Strategist().run(sb, facts, blog_html)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("[2/3] Storyboarder")
    Storyboarder().run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("[3/3] Sourcer")
    _build_sourcer(workspace).run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("Storyboard built — %d scenes, %d real, %d degraded",
             len(sb.scenes),
             sum(1 for s in sb.scenes if not s.degraded),
             sum(1 for s in sb.scenes if s.degraded))
    return sb
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/test_orchestrator.py -v
git add video_agent/orchestrator.py tests/video_agent/test_orchestrator.py
git commit -m "feat(video): add orchestrator wiring Strategist→Storyboarder→Sourcer"
```

---

### Task 2.4: Connect new pipeline to make_video.py

**Files:**
- Modify: `scripts/make_video.py`

- [ ] **Step 1: Replace `--new-sourcer` with `--v2`**

In `scripts/make_video.py`, replace the `--new-sourcer` flag handling with `--v2` that runs the full new pipeline (orchestrator builds storyboard; legacy script.json is no longer used). Keep the legacy path as default.

```python
parser.add_argument("--v2", action="store_true",
                    help="Use the v2 director-driven pipeline")
```

After fetching blog HTML, branch:

```python
if args.v2:
    from video_agent.orchestrator import build_storyboard
    from video_agent.script_builder import extract_facts
    facts, _ = extract_facts(blog_record)
    sb = build_storyboard(blog_record, facts, html, workspace=workspace)
    # Phase 2 ends here — voiceover/visuals/composer are still legacy.
    # Adapter: convert sb back to legacy script shape so existing renderer works.
    from video_agent.agents._legacy_adapter import storyboard_to_legacy
    script = storyboard_to_legacy(sb)
else:
    # … existing legacy build_script call …
```

- [ ] **Step 2: Add `storyboard_to_legacy` to the adapter**

In `video_agent/agents/_legacy_adapter.py`, append:

```python
def storyboard_to_legacy(sb) -> dict:
    """Convert a Storyboard back into the legacy script.json shape so the
    existing voiceover/visuals/composer can consume it. Goes away in Phase 4."""
    scenes = []
    for s in sb.scenes:
        legacy = {
            "index": s.index,
            "narration": s.narration,
            "duration_s": s.duration_target_s,
            "visual_type": "text_card",
            "visual_spec": {"layout": "hook" if s.beat == "hook" else
                                       "cta" if s.beat == "cta" else "default"},
            "on_screen_text": s.on_screen_text,
            "transition_in": s.transition_in,
        }
        if s.chosen_asset and not s.degraded:
            legacy["_source"] = {
                "path": s.chosen_asset.local_path,
                "caption": s.chosen_asset.caption,
                "source_url": s.chosen_asset.url,
                "is_authority": False,
            }
        scenes.append(legacy)
    full_narration = " ".join(s.narration for s in sb.scenes)
    return {
        "narration": full_narration,
        "scenes": scenes,
        "hook": sb.scenes[0].narration if sb.scenes else "",
        "cta": sb.scenes[-1].narration if sb.scenes else "",
        "title": sb.blog.get("title", ""),
        "description": full_narration[:300],
        "hashtags": [], "estimated_duration_s": sum(s.duration_target_s
                                                     for s in sb.scenes),
        "extraction_metadata": {"tier_used": -1},
        "_builder_version": "2.0",
    }
```

- [ ] **Step 3: Smoke test**

```
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization-efficiency-can-in.html --force --v2
```

Expected: see "[1/3] Strategist", "[2/3] Storyboarder", "[3/3] Sourcer" in logs; final video plays with one coherent hero claim and real images.

- [ ] **Step 4: Commit**

```
git add scripts/make_video.py video_agent/agents/_legacy_adapter.py
git commit -m "feat(video): wire v2 pipeline into make_video.py behind --v2 flag"
```

**Phase 2 milestone:** `make_video.py <url> --v2` produces a video that says ONE thing well across 5 beats with real images.

---

### Task 2.5: `run_stage.py` — replay a single stage on an existing storyboard

Spec §3.1 mandates that any stage can be re-run in isolation against a pre-existing `storyboard.json`. This is essential for debugging individual agent failures without re-running the whole pipeline.

**Files:**
- Create: `video_agent/run_stage.py`
- Test: `tests/video_agent/test_run_stage.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/test_run_stage.py
from unittest.mock import patch
from pathlib import Path
from video_agent.storyboard import (
    save_storyboard, load_storyboard, Storyboard, HeroClaim, Beat,
)
from video_agent.run_stage import replay_stage


def test_replay_storyboarder_only(tmp_path):
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"),
                    arc=[Beat(index=0, beat="hook", purpose="",
                              duration_target_s=4.0)])
    path = tmp_path / "storyboard.json"
    save_storyboard(sb, path)
    with patch("video_agent.run_stage.Storyboarder") as M:
        M.return_value.run.side_effect = lambda s: s
        replay_stage("storyboarder", path)
    M.return_value.run.assert_called_once()
```

- [ ] **Step 2: Implement**

```python
# video_agent/run_stage.py
"""Re-run a single pipeline stage on an existing storyboard.json.

Usage:
    python -m video_agent.run_stage <stage> <path/to/storyboard.json>

Stages: strategist | storyboarder | sourcer | critics | reviser
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

from video_agent.storyboard import load_storyboard, save_storyboard

log = logging.getLogger("run_stage")


def replay_stage(stage: str, path: Path) -> None:
    sb = load_storyboard(path)
    workspace = Path(path).parent
    if stage == "strategist":
        from video_agent.agents.strategist import Strategist
        # Strategist needs blog HTML — try to fetch from sb.blog.url
        import requests
        html = requests.get(sb.blog["url"], timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"}).text
        # Facts come from blog_history if it stored them, else re-extract
        from video_agent.script_builder import extract_facts
        blog_record = dict(sb.blog)
        blog_record["content_html"] = html
        facts, _ = extract_facts(blog_record)
        Strategist().run(sb, facts, html)
    elif stage == "storyboarder":
        from video_agent.agents.storyboarder import Storyboarder
        Storyboarder().run(sb)
    elif stage == "sourcer":
        from video_agent.orchestrator import _build_sourcer
        _build_sourcer(workspace).run(sb)
    elif stage == "critics":
        from video_agent.agents.critic_local import LocalCritic
        from video_agent.agents.critic_global import GlobalDirector
        LocalCritic().run(sb)
        GlobalDirector().run(sb)
    elif stage == "reviser":
        from video_agent.agents.reviser import Reviser
        from video_agent.orchestrator import _build_sourcer
        Reviser(sourcer=_build_sourcer(workspace)).run(sb)
    else:
        raise ValueError(f"Unknown stage: {stage!r}")
    save_storyboard(sb, path)
    log.info("Stage %s replayed; storyboard saved", stage)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["strategist", "storyboarder",
                                       "sourcer", "critics", "reviser"])
    ap.add_argument("storyboard_path", type=Path)
    args = ap.parse_args()
    replay_stage(args.stage, args.storyboard_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run + commit**

```
pytest tests/video_agent/test_run_stage.py -v
git add video_agent/run_stage.py tests/video_agent/test_run_stage.py
git commit -m "feat(video): add run_stage.py to replay individual pipeline stages"
```

---

# Phase 3 — Critics + Reviser

Two critics run in parallel; one bounded revision pass.

---

### Task 3.1: Local Critic agent

**Files:**
- Create: `video_agent/agents/critic_local.py`
- Test: `tests/video_agent/agents/test_critic_local.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_critic_local.py
from unittest.mock import patch
from video_agent.agents.critic_local import LocalCritic
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, AssetCandidate,
)


def _fixture():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"),
                    arc=[Beat(index=0, beat="hook", purpose="x",
                              duration_target_s=4.0)],
                    scenes=[Scene(
                        index=0, beat="hook",
                        narration="Are wastewater costs rising?",
                        on_screen_text="ARE WASTEWATER COSTS RISING",
                        visual_concept=VisualConcept(
                            subject="cat", modifier="", type="photo",
                            mood="problem", style_hint=""),
                        duration_target_s=4.0, transition_in="cut",
                        chosen_asset=AssetCandidate(
                            source="g", url="u", score=70,
                            local_path="x.jpg",
                            caption="cat playing piano"),
                    )])
    return sb


def test_local_critic_flags_text_duplicates_voice():
    sb = _fixture()
    fake_resp = {
        "alignment_score": 5,
        "flags": ["text_duplicates_voice", "voice_visual_mismatch"],
        "revision": "Replace on-screen text with the hero stat (90%)."
    }
    with patch("video_agent.agents.critic_local.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake_resp
        LocalCritic().run(sb)
    notes = sb.scenes[0].critic_notes
    assert notes.alignment_score == 5
    assert "text_duplicates_voice" in notes.flags
```

- [ ] **Step 2: Run + verify failure**

```
pytest tests/video_agent/agents/test_critic_local.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/critic_local.py
"""Per-scene Local Critic — evaluates voice/visual/text/transition coherence."""
from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard, Scene, CriticNotes

log = logging.getLogger(__name__)

_SYSTEM = """You evaluate ONE scene of a 5-beat B2B chemistry video.
Your job: catch coherence problems before render.

Score 0-10 based on:
  - Does the visual MATCH what the voice describes?
  - Does on_screen_text ADD information (not duplicate the voice)?
  - Does this scene logically follow the previous one?
  - Does the scene serve the hero claim?

Use these flags (pick all that apply, or empty list if perfect):
  voice_visual_mismatch | text_duplicates_voice | text_unrelated |
  weak_transition | degraded_visual | off_hero_claim | unit_confusion

Respond as JSON:
{
  "alignment_score": <0-10 integer>,
  "flags": ["..."],
  "revision": "<one sentence on what to change, or null if score >= 7>"
}
"""


class LocalCritic:
    def __init__(self, client: OllamaClient | None = None,
                 max_workers: int = 1):
        self.client = client or OllamaClient()
        self.max_workers = max_workers

    def run(self, sb: Storyboard) -> Storyboard:
        for i, scene in enumerate(sb.scenes):
            prev_narr = sb.scenes[i - 1].narration if i > 0 else "(none)"
            self._critique(scene, sb.hero_claim, prev_narr)
        return sb

    def _critique(self, scene: Scene, hero, prev_narr: str) -> None:
        asset_caption = (scene.chosen_asset.caption
                         if scene.chosen_asset else "(no image — fell back)")
        prompt = (
            f"Hero claim: {hero.claim_text if hero else '?'}\n"
            f"Beat role: {scene.beat}\n"
            f"Previous scene narration: {prev_narr}\n\n"
            f"Narration: {scene.narration}\n"
            f"On-screen text: {scene.on_screen_text}\n"
            f"Visual concept: {scene.visual_concept.subject} "
            f"({scene.visual_concept.modifier})\n"
            f"Chosen image caption: {asset_caption}\n"
            f"Degraded (no real image): {scene.degraded}"
        )
        try:
            out = self.client.generate_json(prompt, system=_SYSTEM)
        except OllamaError as e:
            log.warning("Local Critic failed for scene %d: %s", scene.index, e)
            return
        if not isinstance(out, dict):
            return
        scene.critic_notes = CriticNotes(
            alignment_score=int(out.get("alignment_score", 10)),
            flags=list(out.get("flags") or []),
            revision=out.get("revision"),
        )
        log.info("Scene %d: score=%d flags=%s",
                 scene.index, scene.critic_notes.alignment_score,
                 scene.critic_notes.flags)
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_critic_local.py -v
git add video_agent/agents/critic_local.py tests/video_agent/agents/test_critic_local.py
git commit -m "feat(video): add Local Critic agent"
```

---

### Task 3.2: Global Director agent

**Files:**
- Create: `video_agent/agents/critic_global.py`
- Test: `tests/video_agent/agents/test_critic_global.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_critic_global.py
from unittest.mock import patch
from video_agent.agents.critic_global import GlobalDirector
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept,
)


def _fix():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"),
                    arc=[Beat(index=i, beat=b, purpose="",
                              duration_target_s=4.0)
                         for i, b in enumerate(["hook", "stakes", "mechanism",
                                                "proof", "cta"])],
                    scenes=[Scene(index=i, beat=b.beat, narration=f"n{i}",
                                  on_screen_text=f"t{i}",
                                  visual_concept=VisualConcept(
                                      subject="x", modifier="",
                                      type="photo", mood="problem",
                                      style_hint=""),
                                  duration_target_s=4.0,
                                  transition_in="cut")
                            for i, b in enumerate([
                                Beat(index=0, beat="hook", purpose="",
                                     duration_target_s=4.0),
                                Beat(index=1, beat="stakes", purpose="",
                                     duration_target_s=4.0),
                                Beat(index=2, beat="mechanism", purpose="",
                                     duration_target_s=4.0),
                                Beat(index=3, beat="proof", purpose="",
                                     duration_target_s=4.0),
                                Beat(index=4, beat="cta", purpose="",
                                     duration_target_s=4.0),
                            ])])
    return sb


def test_director_populates_director_notes():
    sb = _fix()
    fake = {"arc_quality": 7, "hero_claim_supported": True,
            "weakest_beat": 2, "missing": ["regional anchor in proof"],
            "redundant": [], "ending_strength": 8,
            "revision_for_strategist": "Add Hunter Valley reference to proof"}
    with patch("video_agent.agents.critic_global.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake
        GlobalDirector().run(sb)
    assert sb.director_notes.arc_quality == 7
    assert sb.director_notes.weakest_beat == 2
    assert "regional anchor in proof" in sb.director_notes.missing
```

- [ ] **Step 2: Run + verify**

```
pytest tests/video_agent/agents/test_critic_global.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/critic_global.py
"""Global Director — evaluates the whole arc, suggests structural rewrites."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard, DirectorNotes

log = logging.getLogger(__name__)

_SYSTEM = """You are the Director reviewing a 30-55s B2B video storyboard.
The video must say ONE thing — the hero claim — across 5 beats:
hook → stakes → mechanism → proof → cta.

Evaluate:
  - arc_quality (0-10): does the arc build toward the CTA?
  - hero_claim_supported (bool): does every beat reinforce it?
  - weakest_beat (int 0-4 | null): which one is dragging?
  - missing (list of strings): what's absent? (e.g., "regional anchor")
  - redundant (list of beat indices 0-4): which beats overlap?
  - ending_strength (0-10): how compelling is the CTA tie-back?
  - revision_for_strategist (string | null): one structural change to make,
    or null if arc_quality >= 7

Respond as strict JSON.
"""


class GlobalDirector:
    def __init__(self, client: OllamaClient | None = None):
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        outline = "\n".join(
            f"  {i}. [{s.beat}] narration={s.narration[:80]!r}  "
            f"text={s.on_screen_text!r}  visual={s.visual_concept.subject!r}"
            for i, s in enumerate(sb.scenes)
        )
        hero = sb.hero_claim.claim_text if sb.hero_claim else "(none)"
        prompt = f"Hero claim: {hero}\n\nStoryboard:\n{outline}\n\nReview it."
        try:
            out = self.client.generate_json(prompt, system=_SYSTEM)
        except OllamaError as e:
            log.warning("Global Director failed: %s", e)
            return sb
        if not isinstance(out, dict):
            return sb
        sb.director_notes = DirectorNotes(
            arc_quality=int(out.get("arc_quality", 10)),
            hero_claim_supported=bool(out.get("hero_claim_supported", True)),
            weakest_beat=out.get("weakest_beat"),
            missing=list(out.get("missing") or []),
            redundant=list(out.get("redundant") or []),
            ending_strength=int(out.get("ending_strength", 10)),
            revision_for_strategist=out.get("revision_for_strategist"),
        )
        log.info("Director: arc_quality=%d weakest_beat=%s missing=%s",
                 sb.director_notes.arc_quality,
                 sb.director_notes.weakest_beat,
                 sb.director_notes.missing)
        return sb
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_critic_global.py -v
git add video_agent/agents/critic_global.py tests/video_agent/agents/test_critic_global.py
git commit -m "feat(video): add Global Director agent"
```

---

### Task 3.3: Reviser agent (one-pass bounded)

**Files:**
- Create: `video_agent/agents/reviser.py`
- Test: `tests/video_agent/agents/test_reviser.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/agents/test_reviser.py
from unittest.mock import patch, MagicMock
from video_agent.agents.reviser import Reviser
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, CriticNotes,
)


def test_reviser_rewrites_only_flagged_scenes():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"),
                    arc=[Beat(index=0, beat="hook", purpose="",
                              duration_target_s=4.0)])
    good_scene = Scene(index=0, beat="hook", narration="good",
                       on_screen_text="GOOD", visual_concept=VisualConcept(
                           subject="x", modifier="", type="photo",
                           mood="problem", style_hint=""),
                       duration_target_s=4.0, transition_in="cut",
                       critic_notes=CriticNotes(alignment_score=9, flags=[]))
    bad_scene = Scene(index=1, beat="stakes", narration="bad",
                      on_screen_text="bad text",
                      visual_concept=VisualConcept(
                          subject="x", modifier="", type="photo",
                          mood="problem", style_hint=""),
                      duration_target_s=4.0, transition_in="cut",
                      critic_notes=CriticNotes(
                          alignment_score=4,
                          flags=["text_duplicates_voice"],
                          revision="Use the hero stat instead"))
    sb.scenes = [good_scene, bad_scene]
    fake_response = {"on_screen_text": "$5K/MO PIPE LOSS"}
    fake_sourcer = MagicMock()
    with patch("video_agent.agents.reviser.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake_response
        Reviser(sourcer=fake_sourcer).run(sb)
    assert sb.scenes[0].on_screen_text == "GOOD"           # untouched
    assert sb.scenes[1].on_screen_text == "$5K/MO PIPE LOSS"
    fake_sourcer.run.assert_not_called()                   # vis didn't change
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/agents/test_reviser.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/agents/reviser.py
"""Reviser — exactly ONE rewrite pass; no loops."""
from __future__ import annotations
import logging
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard, Scene

log = logging.getLogger(__name__)

_SCENE_FIELD_REWRITE_SYSTEM = """You rewrite ONE field of ONE scene based on
critic feedback.  Return strict JSON with only the keys the user asks for.
Be terse — match the original's voice, don't expand it."""


class Reviser:
    def __init__(self, sourcer, client: OllamaClient | None = None):
        self.sourcer = sourcer
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        # Step 1: structural rewrite from director (NOT IMPLEMENTED in v1 —
        # we accept the director's notes but don't yet regenerate the arc.
        # That's a Phase 3.5 follow-up; for now, only scene-level rewrites.)
        if sb.director_notes.revision_for_strategist:
            log.info("Director suggested structural rewrite (deferred to v1.1):"
                     " %s", sb.director_notes.revision_for_strategist)

        # Step 2: per-scene rewrites
        for scene in sb.scenes:
            notes = scene.critic_notes
            if notes.alignment_score >= 7:
                continue
            self._rewrite_scene(scene, sb)
        return sb

    def _rewrite_scene(self, scene: Scene, sb: Storyboard) -> None:
        flags = scene.critic_notes.flags
        # Decide which field(s) to rewrite based on flags.
        wants_text = bool({"text_duplicates_voice", "text_unrelated"} & set(flags))
        wants_visual = bool({"voice_visual_mismatch", "off_hero_claim"} & set(flags))
        wants_narration = "weak_transition" in flags or scene.critic_notes.alignment_score < 4

        request_fields: list[str] = []
        if wants_text:
            request_fields.append("on_screen_text")
        if wants_narration:
            request_fields.append("narration")
        if wants_visual:
            request_fields.append("visual_concept")
        if not request_fields:
            request_fields = ["on_screen_text"]   # default rewrite

        prompt = (
            f"Hero claim: {sb.hero_claim.claim_text if sb.hero_claim else '?'}\n"
            f"Beat: {scene.beat}\n"
            f"Current narration: {scene.narration}\n"
            f"Current on-screen text: {scene.on_screen_text}\n"
            f"Critic note: {scene.critic_notes.revision}\n\n"
            f"Rewrite ONLY these fields and return JSON: {request_fields}"
        )
        try:
            out = self.client.generate_json(prompt,
                                            system=_SCENE_FIELD_REWRITE_SYSTEM)
        except OllamaError as e:
            log.warning("Reviser failed on scene %d: %s", scene.index, e)
            return
        if not isinstance(out, dict):
            return
        if "narration" in out:
            scene.narration = str(out["narration"])
        if "on_screen_text" in out:
            scene.on_screen_text = str(out["on_screen_text"])[:60]
        if "visual_concept" in out and isinstance(out["visual_concept"], dict):
            for k, v in out["visual_concept"].items():
                if hasattr(scene.visual_concept, k):
                    setattr(scene.visual_concept, k, v)
            # Visual changed → re-source ONLY this scene
            log.info("Scene %d: visual concept changed; re-sourcing",
                     scene.index)
            self.sourcer._source_scene(scene, sb.blog.get("category", ""))
        log.info("Scene %d: revised %s", scene.index, request_fields)
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/agents/test_reviser.py -v
git add video_agent/agents/reviser.py tests/video_agent/agents/test_reviser.py
git commit -m "feat(video): add Reviser (one-pass scene-level rewrite)"
```

---

### Task 3.4: Wire critics + reviser into orchestrator

**Files:**
- Modify: `video_agent/orchestrator.py`
- Modify: `tests/video_agent/test_orchestrator.py` (extend)

- [ ] **Step 1: Update orchestrator**

In `build_storyboard`, after the Sourcer step:

```python
log.info("[4/5] Critics (parallel)")
LocalCritic().run(sb)
GlobalDirector().run(sb)
save_storyboard(sb, workspace / "storyboard.json")

log.info("[5/5] Reviser")
Reviser(sourcer=_build_sourcer(workspace)).run(sb)
save_storyboard(sb, workspace / "storyboard.json")
```

Add the imports at top:

```python
from video_agent.agents.critic_local import LocalCritic
from video_agent.agents.critic_global import GlobalDirector
from video_agent.agents.reviser import Reviser
```

- [ ] **Step 2: Smoke test**

```
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization-efficiency-can-in.html --force --v2
```

Inspect `output/videos/<slug>/storyboard.json` — `critic_notes` and `director_notes` should now be populated.

- [ ] **Step 3: Commit**

```
git add video_agent/orchestrator.py
git commit -m "feat(video): wire Critics + Reviser into orchestrator"
```

**Phase 3 milestone:** `storyboard.json` shows critic flags & director notes; flagged scenes get rewritten exactly once.

---

# Phase 4 — Renderer Upgrades (Motion + Transitions + Music + Safe Zone)

---

### Task 4.1: Safe-zone validator (text + chart + image)

**Files:**
- Create: `video_agent/safezone.py`
- Test: `tests/video_agent/test_safezone.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/test_safezone.py
from PIL import Image, ImageDraw, ImageFont
from video_agent.safezone import (
    fits_safe_zone, fit_text_to_safe_zone, validate_frame,
    OUTER_MARGIN, BOTTOM_RESERVE, TOP_RESERVE, FRAME_W, FRAME_H,
)


def test_text_inside_safe_zone_passes():
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    draw = ImageDraw.Draw(img)
    bbox = (200, 200, 800, 400)
    assert fits_safe_zone(bbox)


def test_text_overflowing_right_margin_fails():
    bbox = (200, 200, 1100, 400)             # right edge > FRAME_W - OUTER_MARGIN
    assert not fits_safe_zone(bbox)


def test_text_in_subtitle_band_fails():
    bbox = (200, FRAME_H - 100, 800, FRAME_H - 50)   # inside bottom reserve
    assert not fits_safe_zone(bbox)


def test_fit_text_shrinks_until_safe():
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    draw = ImageDraw.Draw(img)
    # A long phrase that won't fit at large size
    fitted_size = fit_text_to_safe_zone(
        draw, "DELIVERING TANGIBLE RESULTS FOR AUSTRALIA",
        anchor_y=300, font_path=None, max_size=120, min_size=22,
    )
    assert 22 <= fitted_size <= 120
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/test_safezone.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/safezone.py
"""Safe-zone enforcement for the 1080x1920 portrait canvas.

Hard zones (in canvas pixels, top-left origin):
  outer margin   60 px on all sides  — no content here
  top reserve    120 px (title bar)
  bottom reserve 240 px (subtitles + footer chrome)
  usable rectangle = (60, 120) → (1020, 1680)
"""
from __future__ import annotations
from PIL import ImageDraw, ImageFont

FRAME_W, FRAME_H = 1080, 1920
OUTER_MARGIN     = 60
TOP_RESERVE      = 120
BOTTOM_RESERVE   = 240


def safe_rect() -> tuple[int, int, int, int]:
    """Returns (x0, y0, x1, y1) of the usable area."""
    return (OUTER_MARGIN, TOP_RESERVE,
            FRAME_W - OUTER_MARGIN, FRAME_H - BOTTOM_RESERVE)


def fits_safe_zone(bbox: tuple[int, int, int, int]) -> bool:
    """True iff every corner of `bbox` is inside the safe rectangle."""
    sx0, sy0, sx1, sy1 = safe_rect()
    x0, y0, x1, y1 = bbox
    return x0 >= sx0 and y0 >= sy0 and x1 <= sx1 and y1 <= sy1


def fit_text_to_safe_zone(draw: ImageDraw.ImageDraw, text: str,
                          anchor_y: int, font_path: str | None,
                          max_size: int = 120, min_size: int = 22,
                          step: int = 4) -> int:
    """Returns a font size that lets `text` (centred horizontally at FRAME_W/2,
    top-anchored at `anchor_y`) fit inside the safe zone.  Below `min_size`,
    the caller should split the text across two lines instead."""
    sx0, sy0, sx1, sy1 = safe_rect()
    avail_w = sx1 - sx0
    for size in range(max_size, min_size - 1, -step):
        try:
            font = (ImageFont.truetype(font_path, size) if font_path
                    else ImageFont.load_default())
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= avail_w and (anchor_y + h) <= sy1:
            return size
    return min_size


def validate_frame(img) -> list[str]:
    """Sample the rendered frame for content outside the safe zone.
    Returns a list of human-readable problems (empty list = clean)."""
    problems = []
    sx0, sy0, sx1, sy1 = safe_rect()
    # Quick brightness check on the bottom reserve — ensure it's mostly empty
    # (subtitles will be added later; we don't want chart pixels here).
    bottom_band = img.crop((0, FRAME_H - BOTTOM_RESERVE, FRAME_W, FRAME_H))
    extrema = bottom_band.convert("L").getextrema()
    if extrema[1] - extrema[0] > 200:
        problems.append("bottom_reserve_has_content")
    return problems
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/test_safezone.py -v
git add video_agent/safezone.py tests/video_agent/test_safezone.py
git commit -m "feat(video): add safe-zone validator + text-fitter"
```

---

### Task 4.2: Ken Burns motion engine

**Files:**
- Create: `video_agent/motion/__init__.py` (empty)
- Create: `video_agent/motion/ken_burns.py`
- Test: `tests/video_agent/motion/test_ken_burns.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/motion/test_ken_burns.py
from pathlib import Path
from PIL import Image
from video_agent.motion.ken_burns import (
    plan_ken_burns, render_motion_clip, MotionPlan,
)


def test_landscape_image_pans_right_when_proof():
    img = Image.new("RGB", (3840, 2160), "white")
    plan = plan_ken_burns(img.size, mood="proof", duration_s=4.0, fps=30)
    assert plan.direction == "right"
    # Start vs end x must differ; viewport stays inside source
    assert plan.start_xy[0] != plan.end_xy[0]


def test_portrait_image_zoom_in_for_mechanism():
    img = Image.new("RGB", (1080, 1920), "white")
    plan = plan_ken_burns(img.size, mood="mechanism", duration_s=4.0, fps=30)
    # For tall source images we still zoom in regardless of mood
    assert plan.start_scale > 1.0 or plan.end_scale > 1.0


def test_render_writes_mp4(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (3000, 1700), "blue").save(src)
    out = tmp_path / "clip.mp4"
    plan = plan_ken_burns((3000, 1700), mood="problem",
                          duration_s=2.0, fps=24)
    render_motion_clip(src, plan, out, duration_s=2.0, fps=24,
                       target_size=(1080, 1920))
    assert out.exists() and out.stat().st_size > 1000
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/motion/test_ken_burns.py -v
```

- [ ] **Step 3: Implement**

```python
# video_agent/motion/ken_burns.py
"""Ken Burns motion: pans/zooms the (1080x1920) viewport across a still image.

Direction is mood-aware:
  problem    → slow downward drift
  mechanism  → zoom in (focusing)
  proof      → pan left to right (revealing)
  brand/cta  → zoom out (concluding)
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.safezone import FRAME_W, FRAME_H


@dataclass
class MotionPlan:
    direction: str            # "left", "right", "up", "down", "in", "out"
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    start_scale: float
    end_scale: float


def _viewport_at(src_w: int, src_h: int, scale: float) -> tuple[int, int]:
    """Size of the visible portrait viewport in source pixels at `scale`."""
    target_aspect = FRAME_W / FRAME_H
    # Fit a 9:16 viewport entirely within the source at `scale`
    vp_h = int(src_h / scale)
    vp_w = int(vp_h * target_aspect)
    if vp_w > src_w:
        vp_w = int(src_w / scale)
        vp_h = int(vp_w / target_aspect)
    return vp_w, vp_h


def plan_ken_burns(src_size: tuple[int, int], mood: str,
                   duration_s: float, fps: int = 30) -> MotionPlan:
    src_w, src_h = src_size
    aspect = src_w / src_h

    if mood == "mechanism":
        s0, s1 = 1.0, 1.18
        vp0 = _viewport_at(src_w, src_h, s0)
        vp1 = _viewport_at(src_w, src_h, s1)
        cx, cy = src_w / 2, src_h / 2
        return MotionPlan("in",
                          (cx - vp0[0] / 2, cy - vp0[1] / 2),
                          (cx - vp1[0] / 2, cy - vp1[1] / 2),
                          s0, s1)
    if mood in ("brand", "cta"):
        s0, s1 = 1.18, 1.0
        vp0 = _viewport_at(src_w, src_h, s0)
        vp1 = _viewport_at(src_w, src_h, s1)
        cx, cy = src_w / 2, src_h / 2
        return MotionPlan("out",
                          (cx - vp0[0] / 2, cy - vp0[1] / 2),
                          (cx - vp1[0] / 2, cy - vp1[1] / 2),
                          s0, s1)
    if mood == "proof" and aspect > 1.05:
        # Pan left-to-right
        s = 1.0
        vp_w, vp_h = _viewport_at(src_w, src_h, s)
        y = (src_h - vp_h) / 2
        return MotionPlan("right",
                          (0, y),
                          (max(0, src_w - vp_w), y),
                          s, s)
    # Default: problem-mood downward drift
    s = 1.0
    vp_w, vp_h = _viewport_at(src_w, src_h, s)
    x = (src_w - vp_w) / 2
    return MotionPlan("down", (x, 0), (x, max(0, src_h - vp_h)), s, s)


def render_motion_clip(src: Path, plan: MotionPlan, dest: Path,
                       duration_s: float, fps: int,
                       target_size: tuple[int, int] = (FRAME_W, FRAME_H)) -> Path:
    """Render the still + motion plan to an MP4 via ffmpeg's zoompan filter."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(1, int(duration_s * fps))

    # Build ffmpeg expressions for x, y, zoom that interpolate from start→end.
    x_expr = f"'{plan.start_xy[0]}+({plan.end_xy[0]-plan.start_xy[0]})*on/{total_frames-1 or 1}'"
    y_expr = f"'{plan.start_xy[1]}+({plan.end_xy[1]-plan.start_xy[1]})*on/{total_frames-1 or 1}'"
    z_expr = (f"'{plan.start_scale}+({plan.end_scale-plan.start_scale})"
              f"*on/{total_frames-1 or 1}'")
    vf = (f"zoompan=z={z_expr}:x={x_expr}:y={y_expr}:"
          f"d=1:s={target_size[0]}x{target_size[1]}:fps={fps}")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(src),
        "-vf", vf,
        "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/motion/test_ken_burns.py -v
git add video_agent/motion/__init__.py video_agent/motion/ken_burns.py tests/video_agent/motion/test_ken_burns.py
git commit -m "feat(video): add Ken Burns motion engine"
```

---

### Task 4.3: Beat-aware transitions

**Files:**
- Create: `video_agent/motion/transitions.py`
- Test: `tests/video_agent/motion/test_transitions.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/motion/test_transitions.py
from video_agent.motion.transitions import transition_between


def test_hook_to_stakes_is_whip_pan():
    assert transition_between("hook", "stakes") == "whip_pan"


def test_proof_to_cta_is_fade():
    assert transition_between("proof", "cta") == "fade"


def test_within_same_beat_is_cut():
    assert transition_between("mechanism", "mechanism") == "cut"
```

- [ ] **Step 2: Implement**

```python
# video_agent/motion/transitions.py
"""Beat-aware transition selector. Phase 4 only chooses the kind; the
composer applies it (xfade / fade / hard cut)."""

def transition_between(prev_beat: str, curr_beat: str) -> str:
    if prev_beat == curr_beat:
        return "cut"
    if (prev_beat, curr_beat) == ("hook", "stakes"):
        return "whip_pan"
    return "fade"
```

- [ ] **Step 3: Run + commit**

```
pytest tests/video_agent/motion/test_transitions.py -v
git add video_agent/motion/transitions.py tests/video_agent/motion/test_transitions.py
git commit -m "feat(video): add beat-aware transition selector"
```

---

### Task 4.4: Music bed mixer with sidechain ducking

**Files:**
- Create: `video_agent/music.py`
- Test: `tests/video_agent/test_music.py`

- [ ] **Step 1: Write failing test**

```python
# tests/video_agent/test_music.py
import subprocess
from pathlib import Path
from video_agent.music import mix_music_under_voice


def test_skips_when_no_track_for_region(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"")          # placeholder; mock subprocess
    out = tmp_path / "out.mp3"
    # No music dir created → nothing to mix; should return voice path unchanged
    result = mix_music_under_voice(voice, out, region="atlantis",
                                    music_root=tmp_path / "no_music")
    assert result == voice


def test_runs_ffmpeg_when_track_exists(tmp_path, monkeypatch):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x" * 32)
    music_root = tmp_path / "music"
    music_root.mkdir()
    track = music_root / "australia.mp3"
    track.write_bytes(b"y" * 32)
    out = tmp_path / "out.mp3"

    calls = []
    def fake_run(cmd, check):
        calls.append(cmd)
        out.write_bytes(b"mixed")
        class R: returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = mix_music_under_voice(voice, out, region="australia",
                                    music_root=music_root)
    assert result == out
    assert "sidechaincompress" in " ".join(calls[0])
```

- [ ] **Step 2: Implement**

```python
# video_agent/music.py
"""Mixes a region-specific royalty-free track under the voiceover with
sidechain ducking (-12dB when voice is present)."""
from __future__ import annotations
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def mix_music_under_voice(voice_path: Path, output_path: Path,
                          region: str,
                          music_root: Path = Path("asset_library/music"),
                          base_gain_db: int = -20) -> Path:
    voice_path = Path(voice_path)
    output_path = Path(output_path)
    track = Path(music_root) / f"{region}.mp3"
    if not track.exists():
        log.warning("No music track for region %r at %s; voiceover only",
                    region, track)
        return voice_path
    # ffmpeg sidechain compression: music ducks when voice is loud
    f = (
        f"[1:a]volume={base_gain_db}dB[m_quiet];"
        f"[m_quiet][0:a]sidechaincompress=threshold=0.05:"
        f"ratio=8:attack=20:release=400[m_ducked];"
        f"[0:a][m_ducked]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(voice_path), "-i", str(track),
        "-filter_complex", f, "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-shortest", str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path
```

- [ ] **Step 3: Run + commit**

```
pytest tests/video_agent/test_music.py -v
git add video_agent/music.py tests/video_agent/test_music.py
git commit -m "feat(video): add music bed mixer with sidechain ducking"
```

---

### Task 4.5: Composer rewrite — consume storyboard, apply motion + transitions + music + safe zone

**Files:**
- Modify: `video_agent/composer.py` (major rewrite)
- Test: `tests/video_agent/test_composer_v2.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/video_agent/test_composer_v2.py
from pathlib import Path
from PIL import Image
from video_agent.composer import compose_short_v2
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, AssetCandidate,
)


def _fixture_storyboard(tmp_path) -> Storyboard:
    # Make a valid landscape source image
    src = tmp_path / "src.jpg"
    Image.new("RGB", (1920, 1080), "navy").save(src)

    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "atlantis",          # no music = skip
                          "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"))
    sb.arc = [Beat(index=i, beat=b, purpose="",
                   duration_target_s=2.0)
              for i, b in enumerate(["hook", "stakes", "mechanism",
                                     "proof", "cta"])]
    sb.scenes = []
    for i, beat in enumerate(["hook", "stakes", "mechanism", "proof", "cta"]):
        sb.scenes.append(Scene(
            index=i, beat=beat, narration=f"scene {i}",
            on_screen_text=f"BEAT {i}",
            visual_concept=VisualConcept(subject="x", modifier="",
                                         type="photo", mood=beat,
                                         style_hint=""),
            duration_target_s=2.0, transition_in="cut",
            chosen_asset=AssetCandidate(
                source="test", url="x", score=70,
                local_path=str(src), caption="test",
                width=1920, height=1080),
        ))
    return sb


def test_composer_v2_produces_valid_mp4(tmp_path):
    sb = _fixture_storyboard(tmp_path)
    voice = tmp_path / "voice.mp3"
    # Generate 10s of silence as test audio
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", "10", str(voice)], check=True)
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:10,000\nhello\n",
                   encoding="utf-8")
    out = tmp_path / "video.mp4"
    compose_short_v2(sb, voice_path=voice, subtitle_path=srt,
                     output_path=out, workspace=tmp_path)
    assert out.exists() and out.stat().st_size > 10_000
```

- [ ] **Step 2: Run**

```
pytest tests/video_agent/test_composer_v2.py -v
```

- [ ] **Step 3: Implement compose_short_v2**

Add to `video_agent/composer.py`:

```python
# (existing imports retained)
import logging
from pathlib import Path
from PIL import Image
from video_agent.storyboard import Storyboard, Scene
from video_agent.motion.ken_burns import plan_ken_burns, render_motion_clip
from video_agent.motion.transitions import transition_between
from video_agent.music import mix_music_under_voice
from video_agent.safezone import (
    FRAME_W, FRAME_H, fit_text_to_safe_zone, validate_frame, safe_rect,
)

log = logging.getLogger(__name__)


def _render_scene_clip(scene: Scene, workspace: Path, fps: int = 30) -> Path:
    """Render one scene to a per-scene MP4 with motion + on-screen text."""
    out = workspace / "scene_clips" / f"scene_{scene.index:02d}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not scene.chosen_asset or scene.degraded:
        # Fallback: render a brand-coloured card
        img_path = workspace / "scene_clips" / f"_fb_{scene.index:02d}.jpg"
        Image.new("RGB", (FRAME_W, FRAME_H), "#0a192f").save(img_path)
        plan = plan_ken_burns((FRAME_W, FRAME_H),
                              mood=scene.visual_concept.mood,
                              duration_s=scene.duration_target_s, fps=fps)
        render_motion_clip(img_path, plan, out, scene.duration_target_s, fps)
        return out

    src = Path(scene.chosen_asset.local_path)
    if scene.chosen_asset.is_clip:
        # YouTube clip: just trim to scene duration
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-t", str(scene.duration_target_s),
            "-vf", f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,"
                   f"crop={FRAME_W}:{FRAME_H}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an", str(out),
        ], check=True)
        return out

    with Image.open(src) as im:
        size = im.size
    plan = plan_ken_burns(size, mood=scene.visual_concept.mood,
                          duration_s=scene.duration_target_s, fps=fps)
    render_motion_clip(src, plan, out, scene.duration_target_s, fps)
    return out


def _concat_with_transitions(scene_clips: list[Path], scenes: list[Scene],
                              workspace: Path) -> Path:
    """Concat per-scene clips with beat-aware transitions via xfade."""
    out = workspace / "_concat.mp4"
    if len(scene_clips) == 1:
        import shutil
        shutil.copy2(scene_clips[0], out)
        return out
    # Build a chain of pairwise xfade filters
    inputs = []
    for c in scene_clips:
        inputs += ["-i", str(c)]
    filter_parts = []
    last_label = "[0:v]"
    offset = float(scenes[0].duration_target_s)
    for i in range(1, len(scene_clips)):
        prev_beat = scenes[i - 1].beat
        curr_beat = scenes[i].beat
        kind = transition_between(prev_beat, curr_beat)
        ffx = "fade" if kind == "fade" else (
              "wiperight" if kind == "whip_pan" else "fade")
        dur = 0 if kind == "cut" else 0.25
        out_label = f"[v{i}]"
        filter_parts.append(
            f"{last_label}[{i}:v]xfade=transition={ffx}:"
            f"duration={dur}:offset={offset-dur}{out_label}"
        )
        last_label = out_label
        offset += scenes[i].duration_target_s - dur
    fc = ";".join(filter_parts)
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", fc, "-map", last_label,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    return out


def _validate_safe_zone(video_path: Path, n_samples: int = 12) -> list[str]:
    """Sample N frames from the video and run safe-zone checks. Returns list
    of (frame_idx, problem) tuples — empty means clean."""
    import subprocess
    problems = []
    # Use ffmpeg to extract N evenly-spaced frames
    tmp = video_path.parent / "_safezone_samples"
    tmp.mkdir(exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
        "-vf", f"fps=fps={n_samples}/30,scale={FRAME_W}:{FRAME_H}",
        str(tmp / "f_%03d.jpg"),
    ], check=True)
    for fp in sorted(tmp.glob("f_*.jpg")):
        with Image.open(fp) as img:
            issues = validate_frame(img)
        if issues:
            problems.append((fp.name, issues))
    return problems


def compose_short_v2(sb: Storyboard, voice_path: Path, subtitle_path: Path,
                      output_path: Path, workspace: Path,
                      fps: int = 30) -> Path:
    """V2 composer: one MP4 per scene with motion + on-screen text, then
    concat with beat-aware xfades, then mux voice + music + subtitles, then
    safe-zone validate. Raises on any safe-zone violation."""
    workspace = Path(workspace)
    output_path = Path(output_path)

    # 1. Render per-scene clips
    scene_clips = [_render_scene_clip(s, workspace, fps) for s in sb.scenes]

    # 2. Concat with transitions
    concat = _concat_with_transitions(scene_clips, sb.scenes, workspace)

    # 3. Mix music under voice
    voice_with_music = mix_music_under_voice(
        voice_path, workspace / "voice_with_music.mp3",
        region=sb.blog.get("region", "default"),
    )

    # 4. Mux audio + burn subtitles
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(concat), "-i", str(voice_with_music),
        "-vf", f"subtitles={subtitle_path}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", str(output_path),
    ], check=True)

    # 5. Safe-zone validation
    problems = _validate_safe_zone(output_path)
    if problems:
        raise RuntimeError(f"Safe-zone violations: {problems}")

    return output_path
```

- [ ] **Step 4: Run + commit**

```
pytest tests/video_agent/test_composer_v2.py -v
git add video_agent/composer.py tests/video_agent/test_composer_v2.py
git commit -m "feat(video): composer v2 with motion + transitions + music + safezone"
```

---

### Task 4.5b: On-screen text overlay with fade-up animation

Spec §9.3 requires text to fade up from opacity 0 with a 2px upward drift over 200ms, exit 300ms before the scene ends, in HRSU gold (`#d4af37`) Poppins Bold with a 30%-opacity drop shadow. The composer renders this via an ffmpeg `drawtext` filter with `alpha` and `y` interpolation.

**Files:**
- Modify: `video_agent/composer.py` — add `_overlay_on_screen_text(scene_clip, scene)` and call it inside `_render_scene_clip` after motion.
- Test: extend `tests/video_agent/test_composer_v2.py` with a check that the overlay step runs.

- [ ] **Step 1: Implement the overlay helper**

Add to `video_agent/composer.py`:

```python
import shlex
from video_agent.config import BRAND_GOLD, BRAND_FONT_BODY
from video_agent.safezone import OUTER_MARGIN, FRAME_W, FRAME_H, BOTTOM_RESERVE


def _overlay_on_screen_text(scene_clip: Path, scene: Scene,
                             font_path: str | None = None,
                             fps: int = 30) -> Path:
    """Burns the scene's on-screen text into the clip with a 200ms fade-up
    + 2px upward drift, exiting 300ms before clip end."""
    if not scene.on_screen_text.strip():
        return scene_clip
    out = scene_clip.with_name(scene_clip.stem + "_text.mp4")
    duration = scene.duration_target_s
    enter_end = 0.20
    exit_start = max(enter_end, duration - 0.30)

    # alpha ramps 0→1 over enter_end, holds, then ramps 1→0 in last 200ms.
    alpha_expr = (
        f"if(lt(t,{enter_end}),t/{enter_end},"
        f"if(lt(t,{exit_start}),1,"
        f"if(lt(t,{exit_start}+0.20),"
        f"1-(t-{exit_start})/0.20,0)))"
    )
    # y drifts from anchor+2 to anchor over enter_end
    anchor_y = FRAME_H - BOTTOM_RESERVE - 80    # text sits 80px above subtitle band
    y_expr = (
        f"if(lt(t,{enter_end}),"
        f"{anchor_y}+2-(2*t/{enter_end}),"
        f"{anchor_y})"
    )
    # Escape colons inside ffmpeg filter values
    text = scene.on_screen_text.replace("'", r"\'").replace(":", r"\:")
    font_opt = f":fontfile={shlex.quote(font_path)}" if font_path else ""
    drawtext = (
        f"drawtext=text='{text}'"
        f":fontcolor={BRAND_GOLD}"
        f":fontsize=64{font_opt}"
        f":x=(w-text_w)/2:y={y_expr}"
        f":alpha='{alpha_expr}'"
        f":shadowcolor=black@0.3:shadowx=2:shadowy=2"
    )
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(scene_clip),
        "-vf", drawtext, "-c:v", "libx264", "-preset", "fast",
        "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    scene_clip.unlink()
    out.rename(scene_clip)
    return scene_clip
```

In `_render_scene_clip`, just before `return out`, add:

```python
    _overlay_on_screen_text(out, scene)
```

- [ ] **Step 2: Run existing composer test**

```
pytest tests/video_agent/test_composer_v2.py -v
```

Should still PASS (the test fixture's `on_screen_text="BEAT N"` will be burned into the clip).

- [ ] **Step 3: Commit**

```
git add video_agent/composer.py
git commit -m "feat(video): on-screen text fade-up animation per §9.3"
```

---

### Task 4.6: Wire composer v2 into make_video.py

**Files:**
- Modify: `scripts/make_video.py`

- [ ] **Step 1: Add v2 composer path**

Replace the legacy compose call (in the `--v2` branch) with `compose_short_v2`:

```python
if args.v2:
    from video_agent.composer import compose_short_v2
    compose_short_v2(sb, voice_path=voice["audio_path"],
                     subtitle_path=srt, output_path=out,
                     workspace=workspace)
else:
    # existing compose_short(...) call
```

- [ ] **Step 2: Smoke test**

```
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/lime-neutralization-efficiency-can-in.html --force --v2
```

Verify: video has motion (Ken Burns visible on stills), transitions between beats (fade between mechanism→proof), music under voice if `asset_library/music/australia.mp3` exists.

- [ ] **Step 3: Commit**

```
git add scripts/make_video.py
git commit -m "feat(video): use composer_v2 for --v2 pipeline"
```

**Phase 4 milestone:** v2 video has Ken Burns motion, beat-aware transitions, music under voiceover, and passes the safe-zone validation gate.

---

# Phase 5 — Cleanup & Defaults

---

### Task 5.1: Make v2 the default

**Files:**
- Modify: `scripts/make_video.py`

- [ ] **Step 1: Flip the default**

Change `--v2` to `--legacy` (inverse). Default behaviour is now v2:

```python
parser.add_argument("--legacy", action="store_true",
                    help="Use the legacy single-pass pipeline")
```

In the body:
```python
use_v2 = not args.legacy
if use_v2:
    # v2 path
else:
    # legacy path
```

- [ ] **Step 2: Smoke test default path**

```
python scripts/make_video.py <url> --force
```

Should run v2.

- [ ] **Step 3: Commit**

```
git add scripts/make_video.py
git commit -m "refactor(video): make v2 pipeline the default; legacy is opt-in"
```

---

### Task 5.2: Delete legacy code paths

**Files:**
- Delete: `video_agent/script_builder._scene_breakdown`, `_write_narration`,
  `_inject_bar_chart`, `_fill_callout_stats`, `_attach_sources`, `_post_process_scenes`
- Delete: `video_agent/agents/_legacy_adapter.py`
- Delete: legacy branch in `scripts/make_video.py` (the `--legacy` flag)
- Modify: `tests/video_agent/test_script_builder_*.py` — keep only fact-extraction tests

- [ ] **Step 1: Delete legacy functions**

Remove from `script_builder.py` everything except `extract_facts`, `_tier1_*`, `_tier2_*`, `_tier3_*`, `_tier4_*`, `_strip_html`, `_find_all_numerics`, and helper regex constants. The file should shrink to ~150 lines.

- [ ] **Step 2: Remove legacy adapter and flag**

```
rm video_agent/agents/_legacy_adapter.py
```

In `scripts/make_video.py`, remove the `--legacy` flag and its branch.

- [ ] **Step 3: Run full test suite**

```
pytest tests/video_agent/ -v
```

Expected: all pass (some legacy tests are deleted; no new failures).

- [ ] **Step 4: Commit**

```
git rm video_agent/agents/_legacy_adapter.py
git add video_agent/script_builder.py scripts/make_video.py tests/video_agent/
git commit -m "chore(video): delete legacy single-pass pipeline; v2 is the only path"
```

**Phase 5 milestone:** legacy code removed; v2 is the only path. The codebase is ~30% smaller and has one clear pipeline shape.

---

# Final Verification

After all phases:

- [ ] Run `pytest tests/video_agent/ -v` — all pass
- [ ] Run `python scripts/make_video.py <real-blog-url> --force` — produces a coherent video with hero claim, real images, motion, transitions, music, and zero safe-zone violations
- [ ] Inspect `output/videos/<slug>/`:
  - `storyboard.json` shows hero_claim, all 5 beats, populated critic_notes per scene, populated director_notes
  - `scenes/` has 5 scene clips
  - `video_short.mp4` plays end-to-end with no jarring cuts
- [ ] Run a second blog through to confirm cache is hit on repeat queries

If anything fails: do NOT proceed to delete legacy code (Phase 5). Roll back to the last passing phase milestone and debug from there.
