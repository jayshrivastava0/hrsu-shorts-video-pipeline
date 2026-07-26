# Video Pipeline v2.1 — Quality & Completeness Fixes

**Status:** Spec
**Date:** 2026-05-14
**Author:** sujay.shrivastava@swastika.co.in (designed with Claude Opus 4.7)
**Predecessor:** [2026-05-10-video-pipeline-redesign-design.md](2026-05-10-video-pipeline-redesign-design.md)

---

## 1. Motivation

The v2 pipeline (shipped 2026-05-11) successfully generates a watchable 9:16 short. Real-world testing on `calcium-nitrate-for-shale-leachate` exposed seven concrete defects that are blocking the pipeline from being production-grade:

1. **Google Images source returns 0 candidates** — Google changed its HTML payload; regex-based scraping is broken.
2. **Images appear blurry or mis-cropped** — no minimum-resolution gate; Ken Burns `zoompan` upscales tiny sources; square aspect-ratio sources lose ~50% to portrait crop.
3. **Scene 4 (CTA / brand scene) is missing from the rendered video** — `compose_short_v2` doesn't redistribute scene durations to match voice duration; ffmpeg `-shortest` truncates the video at the shorter input.
4. **Wrong region shown** — for `region="gulf"` (HRSU's Middle East target), a US-mainland map was rendered. Critic flagged `voice_visual_mismatch` but Reviser only rewrote text fields, never replaced the asset.
5. **Source attribution invisible** — Unsplash is actually used, but logs don't surface this, so the user can't tell which source produced which scene.
6. **No outro played** — `compose_short_v2` skips the `_concat_intro_outro` step; the existing legacy `compose_short` has it.
7. **Watermarks and embedded copyright text on chosen images** — clash with burned-in subtitles; no detection in scoring.

This spec defines the **v2.1 patch release** that addresses all seven, plus a handful of related quality improvements.

---

## 2. Goals

- **G1** — Fix all seven defects above so a fresh `python scripts/make_video.py <url>` produces a complete, in-region, watermark-free short with a clean CTA outro.
- **G2** — Make the pipeline self-healing: zero manual setup commands beyond the one entry point. Outro auto-renders on demand; missing optional assets log a warning but never crash.
- **G3** — Increase observability: per-scene source attribution in logs, plus a machine-readable `quality_report.json` alongside the final MP4.
- **G4** — No new external API dependencies. All AI work continues on the local Ollama instance (gemma3:4b).
- **G5** — Patch-scope only. The v2 architecture (Strategist → Storyboarder → Sourcer → Critics → Reviser) is unchanged in shape; we tighten each agent's behaviour and add one new optional stage (NarrationPolisher) between Storyboarder and Sourcer.

## 3. Non-goals

- Per-region music library automation — the user is sourcing `gulf.mp3` manually. The composer continues to warn-and-ship voice-only when a region's MP3 is missing.
- Switching off Ollama for any agent. All LLM calls stay local.
- New visual moods beyond what's specified in §10.

---

## 4. Pipeline diagram (post-v2.1)

```
Blog HTML
    │
    ▼
[1/6] Strategist        (Ollama; region-aware prompt)
    │
    ▼
[2/6] Storyboarder      (Ollama; region-aware prompt)
    │
    ▼
[2.5/6] NarrationPolisher  (Ollama; NEW — tightens narration, ensures clean CTA close)
    │
    ▼
[3/6] Sourcer           (parallel fan-out across 7 sources)
    │
    ├── UnsplashSource         weight 8
    ├── PexelsSource           weight 8   (NEW)
    ├── WikimediaSource        weight 7
    ├── BingSource             weight 6
    ├── GoogleImagesBrowserSource  weight 5   (REPLACES old regex scraper)
    ├── DuckDuckGoSource       weight 4
    └── YouTubeSource          weight 3
    │
    ▼ candidates scored by scoring.score_candidate()
    │   + dimension adjustment (NEW)
    │   + watermark OCR check (NEW)
    │
    ▼
[4/6] Critics           (LocalCritic per scene + GlobalDirector across arc)
    │
    ▼
[5/6] Reviser           (text revisions + NEW re-source on voice_visual_mismatch
                         + NEW structural rewrite when GlobalDirector suggests one)
    │
    ▼
[6/6] Compose           (NEW step 0 — proportional duration redistribution
                         step 1-5 — per-scene clips, transitions, voice/music mix, subtitles
                         step 6 — NEW auto-render outro if missing, concat outro,
                                  safe-zone validate)
    │
    ▼
output/videos/<slug>/video_short.mp4
                    /quality_report.json   (NEW)
```

---

## 5. Critical-bug fixes (Priority 1)

### 5.1 — Voice/video duration mismatch (defect #3)

**Symptom:** Voice = 45.1 s, scenes summed to 35.5 s. ffmpeg `-shortest` truncated output to 35.5 s. Scene 4 was rendered but its frames played under the tail of scene 3's voiceover, and the actual scene 4 voiceover (last 9.6 s including the CTA line) never reached the output.

**Fix:** Insert a new **step 0** at the top of `compose_short_v2`:

```python
# video_agent/composer.py — compose_short_v2

def compose_short_v2(sb, voice_path, subtitle_path, output_path, workspace, fps=30):
    workspace = Path(workspace)
    output_path = Path(output_path)

    # STEP 0 (NEW): probe voice, redistribute scene durations proportionally
    voice_duration = _probe_audio_duration(voice_path)
    target_total = voice_duration + 0.3       # 0.3s tail before outro overlap
    scaled = _redistribute_durations(
        [{"duration_target_s": s.duration_target_s} for s in sb.scenes],
        target_total,
    )
    for s, new in zip(sb.scenes, scaled):
        s.duration_target_s = new["duration_target_s"]
    log.info("Redistributed scene durations to match voice "
             "(voice=%.2fs, scenes_total=%.2fs)", voice_duration,
             sum(s.duration_target_s for s in sb.scenes))

    # ... existing steps 1-5 unchanged
```

`_redistribute_durations` already exists in `composer.py` (used by the legacy `compose_short`). `_probe_audio_duration` is a small wrapper around `ffprobe -show_entries format=duration` — add it if not present.

**Acceptance:** `sum(s.duration_target_s for s in sb.scenes) == voice_duration + 0.3` (± 0.05).

### 5.2 — Missing outro (defect #6)

**Symptom:** `compose_short_v2` writes the final MP4 directly with no intro/outro concat. Result: video ends abruptly mid-thought.

**Fix:** New **step 6** in `compose_short_v2`:

```python
# After subtitle burn, rename final mux output:
subs_mp4 = workspace / "_with_subs.mp4"     # was output_path
subprocess.run([
    "ffmpeg", "-y", "-loglevel", "error",
    "-i", str(concat), "-i", str(voice_with_music),
    "-vf", vf_subs, "-map", "0:v", "-map", "1:a",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-c:a", "aac", "-b:a", "192k",
    "-pix_fmt", "yuv420p", "-shortest", str(subs_mp4),
], check=True)

# STEP 6 (NEW): ensure outro exists, concat outro only (no intro)
outro_path = Path(OUTRO_VIDEO_PATH)
expected_version = f"_v{OUTRO_VERSION}.mp4"
if not outro_path.exists() or not outro_path.name.endswith(expected_version):
    from video_agent.tools.render_brand_assets import render_outro
    log.info("Outro missing or stale — rendering once at %s", outro_path)
    outro_path.parent.mkdir(parents=True, exist_ok=True)
    render_outro(outro_path)
_concat_intro_outro(subs_mp4, intro_mp4=None, outro_mp4=outro_path,
                    output_mp4=output_path)

# STEP 7 (existing): safe-zone validation
problems = _validate_safe_zone(output_path)
if problems:
    raise RuntimeError(f"Safe-zone violations: {problems}")
```

**Versioning.** Add to `config.py`:
```python
OUTRO_VERSION = 2
OUTRO_VIDEO_PATH = "asset_library/brand/outro_5s_v2.mp4"
```
Bumping `OUTRO_VERSION` triggers automatic re-render on next pipeline run — no user action required, ever. The user only ever runs `python scripts/make_video.py <url>`.

**Intro is intentionally skipped** in v2.1 (the user only complained about the missing outro). The plumbing (`_concat_intro_outro` signature accepts `intro_mp4=None`) leaves room for re-enabling later.

---

## 6. Image-sourcing fixes (defects #1, #2, #7)

### 6.1 — Replace regex-based Google Images with Playwright (defect #1)

**Decision recap (from brainstorming):** Use Playwright as primary + Pexels as fallback. Both wired into the parallel source fan-out; the multi-source design means if Playwright returns `[]` (CAPTCHA, layout drift, timeout), other sources cover.

**New file:** `video_agent/sources/google_images_browser.py`

```python
"""Google Images via Playwright headless browser.
Replaces the regex-based google_images.py — far more resilient to layout
changes, at the cost of ~2-3s startup per Sourcer instance.

The browser is launched ONCE per Sourcer (not per query) and reused.
"""
from __future__ import annotations
import logging
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)


class GoogleImagesBrowserSource(BaseSource):
    name = "google_images"
    authority_weight = 5

    def __init__(self):
        self._pw = None
        self._browser = None
        self._ctx = None
        self._init_browser()

    def _init_browser(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._ctx = self._browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/121.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            log.info("GoogleImagesBrowserSource: Playwright initialised")
        except Exception as e:
            log.warning("GoogleImagesBrowserSource: Playwright init failed (%s); "
                        "source will return [] until process restart", e)
            self._ctx = None

    def search(self, query, limit=5):
        if self._ctx is None:
            return []
        page = self._ctx.new_page()
        try:
            page.goto(
                f"https://www.google.com/search?q={query}&tbm=isch&safe=active&hl=en",
                timeout=10000, wait_until="domcontentloaded",
            )
            page.wait_for_selector("img", timeout=5000)
            srcs = page.eval_on_selector_all(
                "img",
                """(imgs, lim) => imgs
                    .map(i => ({src: i.src || i.getAttribute('data-src'),
                                alt: i.alt,
                                w: i.naturalWidth, h: i.naturalHeight}))
                    .filter(o => o.src && o.src.startsWith('http') && o.w >= 200)
                    .slice(0, lim)""",
                limit * 4,    # over-fetch; many are thumbnails
            )
        except Exception as e:
            log.warning("GoogleImagesBrowserSource: search failed for %r (%s)", query, e)
            srcs = []
        finally:
            page.close()
        results = [s for s in srcs if s["w"] >= 300][:limit]
        out = [RawCandidate(source=self.name, url=s["src"],
                            caption=s["alt"] or "",
                            width=s["w"], height=s["h"]) for s in results]
        if not out:
            log.info("GoogleImagesBrowserSource: 0 candidates for %r "
                     "(CAPTCHA or layout change — other sources will cover)", query)
        return out

    def __del__(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
```

**Delete the old scraper:** `video_agent/sources/google_images.py` — drop the file. Update `orchestrator._build_sourcer` to import the browser version.

### 6.2 — Add Pexels source (defect #2 mitigation)

**New file:** `video_agent/sources/pexels.py`

```python
"""Pexels Photo Search API. Free 200 req/hour with API key."""
from __future__ import annotations
import logging
import requests
from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.config import PEXELS_API_KEY

log = logging.getLogger(__name__)
_API = "https://api.pexels.com/v1/search"


class PexelsSource(BaseSource):
    name = "pexels"
    authority_weight = 8

    def __init__(self, api_key=PEXELS_API_KEY):
        self.api_key = api_key

    def search(self, query, limit=5):
        if not self.api_key:
            log.debug("Pexels skipped — no API key set")
            return []
        try:
            r = requests.get(
                _API,
                params={"query": query, "per_page": limit,
                        "orientation": "landscape", "size": "large"},
                headers={"Authorization": self.api_key},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("Pexels search failed for %r: %s", query, e)
            return []
        out = []
        for item in r.json().get("photos", [])[:limit]:
            urls = item.get("src", {})
            url = urls.get("large2x", "") or urls.get("original", "")
            out.append(RawCandidate(
                source=self.name,
                url=url,
                caption=item.get("alt", "") or item.get("photographer", ""),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                extra={"photographer": item.get("photographer", "")},
            ))
        return [c for c in out if c.url]
```

**Config:** add `PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")` to `video_agent/config.py`.

### 6.3 — Dimension + aspect-ratio gates in scoring (defect #2)

**File:** `video_agent/sources/scoring.py`

```python
# New constants (mirror in video_agent/config.py)
MIN_IMAGE_LONG_EDGE = 1280     # hard floor — reject below this
IDEAL_IMAGE_LONG_EDGE = 1920   # bonus threshold

def _dimension_adjustment(c: RawCandidate) -> tuple[int, bool]:
    """Returns (score_delta, hard_reject)."""
    long_edge = max(c.width, c.height)
    short_edge = min(c.width, c.height)
    if long_edge < MIN_IMAGE_LONG_EDGE:
        return (0, True)
    aspect = c.width / c.height if c.height else 1.0
    delta = 0
    if long_edge >= IDEAL_IMAGE_LONG_EDGE:
        delta += 10
    if 0.9 <= aspect <= 1.1:        # square-ish — loses ~50% to portrait crop
        delta -= 15
    if aspect >= 1.6:               # widescreen — ideal for Ken Burns pan
        delta += 10
    if aspect <= 0.65:              # portrait — minimal crop loss
        delta += 8
    return (delta, False)

# In existing score_candidate(c, query):
delta, hard_reject = _dimension_adjustment(c)
if hard_reject:
    return -1                       # Sourcer drops scores below min_score
score += delta
```

`Sourcer._download_candidate` already filters `score < min_score`; `-1` is dropped cleanly.

### 6.4 — Slower Ken Burns zoom on `mechanism` scenes (defect #2 — chemistry diagrams)

**File:** `video_agent/motion/ken_burns.py`

```python
# In plan_ken_burns:
if mood == "mechanism":
    s0, s1 = 1.0, 1.05      # was 1.0, 1.18 — slower zoom keeps whole diagram visible
    ...
```

Same for any other future "explanatory" moods.

### 6.5 — Watermark / embedded-text rejection (defect #7)

**New file:** `video_agent/sources/watermark.py`

```python
"""Reject images whose bottom strip contains visible watermarks or stock-photo text.

Uses Tesseract via pytesseract. If the binary is missing, the check skips
gracefully (returns False, "tesseract_unavailable").

Results are cached by file content hash to avoid re-OCRing on retry.
"""
from __future__ import annotations
import hashlib, json, logging, re
from pathlib import Path
from PIL import Image

log = logging.getLogger(__name__)

_BLOCKLIST = re.compile(
    r"(copyright|©|getty|shutter|alamy|istock|dreamstime|"
    r"123rf|adobestock|depositphotos|watermark|stock\s*photo|"
    r"all\s*rights\s*reserved)",
    re.IGNORECASE,
)
_MIN_FLAGGED_CHARS = 8
_TESSERACT_OK: bool | None = None       # lazy import flag


def _ensure_tesseract() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _TESSERACT_OK = True
    except Exception as e:
        log.warning("Tesseract unavailable (%s); watermark check disabled. "
                    "Install: winget install UB-Mannheim.TesseractOCR", e)
        _TESSERACT_OK = False
    return _TESSERACT_OK


def is_watermarked(img_path: Path, cache_root: Path) -> tuple[bool, str]:
    """Returns (is_watermarked, reason)."""
    img_path = Path(img_path)
    digest = hashlib.sha1(img_path.read_bytes()).hexdigest()
    cache_file = Path(cache_root) / "watermark" / f"{digest}.json"
    if cache_file.exists():
        try:
            d = json.loads(cache_file.read_text())
            return (d["watermarked"], d["reason"])
        except Exception:
            pass
    if not _ensure_tesseract():
        return (False, "tesseract_unavailable")
    import pytesseract
    try:
        with Image.open(img_path) as im:
            w, h = im.size
            strip = im.crop((0, int(h * 0.75), w, h))
            text = pytesseract.image_to_string(strip, config="--psm 6").strip()
    except Exception as e:
        log.debug("Watermark OCR failed for %s (%s)", img_path, e)
        return (False, "ocr_error")
    watermarked = False
    reason = ""
    m = _BLOCKLIST.search(text)
    if m:
        watermarked, reason = True, f"blocklist_match:{m.group(0).lower()}"
    elif len(text) >= _MIN_FLAGGED_CHARS:
        watermarked, reason = True, f"text_density:{len(text)}chars"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"watermarked": watermarked, "reason": reason}))
    return (watermarked, reason)
```

**Wire into Sourcer (`agents/sourcer.py`):**

```python
from video_agent.sources.watermark import is_watermarked

# In _download_candidate, after the existing PIL verify:
watermarked, reason = is_watermarked(dest, self.cache.root)
if watermarked:
    log.info("Scene candidate %s rejected: watermark (%s)", c.url, reason)
    dest.unlink(missing_ok=True)
    return None
return dest
```

**Dependency note:** `pytesseract>=0.3.10` added to `requirements.txt`. The Tesseract binary itself must be installed on the host. On Windows: `winget install UB-Mannheim.TesseractOCR`. If missing, the watermark check skips gracefully and logs once.

---

## 7. Region semantics fix (defect #4 — Gulf of Mexico for `region=gulf`)

### 7.1 — Strategist + Storyboarder system-prompt update

**Files:** `video_agent/agents/strategist.py`, `video_agent/agents/storyboarder.py`

Append the following block to both system prompts:

```
REGION SEMANTICS — read carefully:

The blog's `region` field uses internal codes, NOT colloquial geography. Map them as:
  australia  → Australia / Oceania
  usa        → United States (mainland)
  eu         → European Union (continental Europe + UK)
  germany    → Germany (DACH region)
  east_asia  → Singapore / Southeast Asia
  gulf       → Persian Gulf / GCC states (UAE, Saudi Arabia, Qatar, Kuwait,
               Bahrain, Oman) — NOT Gulf of Mexico

When generating narration, on-screen text, or visual_concept queries that
reference geography, follow these rules:

1. ALWAYS qualify ambiguous place-names with the country or sub-region.
   - "Persian Gulf coastline", not "Gulf coast"
   - "Saudi Arabia oil refinery", not "regional oil refinery"
   - "Australian outback mining", not "outback mining"

2. If a place-name could refer to multiple locations (Gulf, Georgia, Cordoba,
   Newcastle, Birmingham, Naples, Tripoli, etc.) include a disambiguating
   qualifier — country name, region adjective, or nearby landmark.

3. Visual queries (visual_concept.subject) MUST include a region-locking word:
   country name, region adjective ("Middle Eastern"), or famous landmark
   from that region.

4. When in doubt, prefer the country name. "Saudi Arabia" is always safer
   than "the Gulf" when describing visuals.
```

**Storyboarder-only addendum:**

```
For mood="proof" scenes that reference data, studies, or maps, prepend the
visual_concept.subject with the resolved region's primary country.
Examples:
  region=gulf, subject="oil refinery"
      → "Saudi Arabia oil refinery"
  region=australia, subject="ANFO mining operation"
      → "Western Australia ANFO mining operation"
  region=germany, subject="wastewater plant"
      → "Germany wastewater plant"
```

### 7.2 — NarrationPolisher as belt-and-braces

The NarrationPolisher (§9) also receives the REGION SEMANTICS rules and acts as a second pass — if Strategist or Storyboarder drift, Polisher catches and corrects.

### 7.3 — Reviser-triggered re-source (defect #4 root cause)

**File:** `video_agent/agents/reviser.py`

Current behaviour: when `critic_local` flags `voice_visual_mismatch`, Reviser rewrites text fields only. Asset stays wrong.

New behaviour: when `voice_visual_mismatch` flagged, also call `Sourcer.re_source_scene()` to swap the asset.

```python
# video_agent/agents/reviser.py

class Reviser:
    def __init__(self, sourcer=None, max_resource_attempts: int = 2):
        self.sourcer = sourcer
        self.max_resource_attempts = max_resource_attempts

    def run(self, sb):
        for scene in sb.scenes:
            flags = set(scene.critic_flags or [])
            # ... existing text-revision logic ...

            if "voice_visual_mismatch" in flags and self.sourcer:
                self._re_source(scene, sb)

            # NEW: handle director-suggested structural rewrite (§8)
            if sb.director_suggestion and sb.director_suggestion.get("weakest_beat") == scene.index:
                self._structural_rewrite(scene, sb)
        return sb

    def _re_source(self, scene, sb):
        excluded = {scene.chosen_asset.url} if scene.chosen_asset else set()
        for attempt in range(self.max_resource_attempts):
            self.sourcer.re_source_scene(scene, sb.blog.get("category", ""),
                                         exclude_urls=excluded)
            if scene.chosen_asset and scene.chosen_asset.url not in excluded:
                log.info("Scene %d: re-sourced after voice_visual_mismatch "
                         "(attempt %d, new source=%s)",
                         scene.index, attempt + 1, scene.chosen_asset.source)
                return
            excluded.add(scene.chosen_asset.url if scene.chosen_asset else "")
        log.warning("Scene %d: re-source exhausted after %d attempts; keeping original",
                    scene.index, self.max_resource_attempts)
```

**New method on Sourcer (`agents/sourcer.py`):**

```python
def re_source_scene(self, scene, blog_category, exclude_urls):
    """Re-runs source-and-pick for a single scene, skipping previously-used URLs.
    Updates scene.chosen_asset in place if a fresh candidate is found."""
    queries = _build_queries(scene.visual_concept, blog_category)
    all_raw = []
    for q in queries:
        raws = self._search_all_sources(q)
        all_raw.extend(r for r in raws if r.url not in exclude_urls)
        if len(all_raw) >= 5:
            break
    if not all_raw:
        return
    primary_q = queries[0]
    scored = sorted(((score_candidate(c, primary_q), c) for c in all_raw),
                    key=lambda t: -t[0])
    for s, c in scored[:5]:
        if s < self.min_score:
            continue
        local = self._download_candidate(c, scene.index)
        if local is None or self._is_dup(local):
            continue
        scene.chosen_asset = AssetCandidate(
            source=c.source, url=c.url, score=s,
            local_path=str(local), caption=c.caption,
            width=c.width, height=c.height,
            is_clip=c.is_clip, duration_s=c.duration_s,
        )
        scene.degraded = False
        return
```

---

## 8. Director-triggered structural rewrite

**File:** `video_agent/agents/reviser.py`

When `GlobalDirector` returns a `weakest_beat` index with a structural suggestion, Reviser now:
1. Calls Storyboarder for **a single beat regeneration** (new narration + on_screen_text + visual_concept).
2. Calls Sourcer's `re_source_scene` for that beat.
3. Logs before/after to the quality report.

```python
# video_agent/agents/reviser.py

def _structural_rewrite(self, scene, sb):
    suggestion = sb.director_suggestion.get("suggestion", "")
    if not suggestion:
        return
    log.info("Scene %d: structural rewrite triggered (%s)",
             scene.index, suggestion[:80])
    # One-shot Storyboarder call scoped to this scene
    from video_agent.agents.storyboarder import Storyboarder
    rewritten = Storyboarder().regenerate_beat(
        sb, scene.index, director_suggestion=suggestion,
    )
    if rewritten:
        scene.narration = rewritten.get("narration", scene.narration)
        scene.on_screen_text = rewritten.get("on_screen_text", scene.on_screen_text)
        scene.visual_concept = rewritten.get("visual_concept", scene.visual_concept)
        if self.sourcer:
            self.sourcer.re_source_scene(scene, sb.blog.get("category", ""),
                                         exclude_urls=set())
```

**New method on Storyboarder:** `regenerate_beat(sb, scene_index, director_suggestion)` — same prompt style as the main `run()` but scoped to one scene, with the director suggestion injected.

Drops the `Director suggested structural rewrite (deferred to v1.1)` log line — that pathway is now active.

---

## 9. NarrationPolisher — new optional stage

**Purpose:** Catch narration issues before they reach voice synthesis:
- Match spoken duration to `duration_target_s` (≈150 wpm).
- Ensure the final scene ends with a complete CTA sentence — never mid-thought.
- Cross-check region semantics one more time.
- Smooth scene-to-scene transitions.

**New file:** `video_agent/agents/narration_polisher.py`

```python
from __future__ import annotations
import json
import logging
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard

log = logging.getLogger(__name__)


class NarrationPolisher:
    """Local-Ollama final pass on scene narrations. No external API."""

    SYSTEM = """You are a video script polisher for an industrial-chemicals
brand video. Given a list of scene narrations, return a JSON array of the
SAME LENGTH with each narration polished to:

1. Match its target spoken duration (≈150 words per minute) — trim or expand
   the text to fit. Do NOT add new facts; only refine wording.

2. Flow smoothly into the next scene — no abrupt topic jumps.

3. End the FINAL scene with a clean CTA close. Acceptable closes include:
     "Source your calcium nitrate from HRSU at hrsuindore.com."
     "Visit hrsuindore.com to learn how HRSU can help."
   NEVER end mid-sentence or with an open question.

4. Use region-correct geography. Map internal region codes carefully:
     gulf → Persian Gulf / GCC (Saudi Arabia, UAE, Qatar) — NOT Gulf of Mexico
     usa  → United States (mainland)
     eu   → European Union
     germany → Germany (DACH)
     australia → Australia / Oceania
     east_asia → Singapore / Southeast Asia
   Qualify ambiguous place-names with the country.

5. Sound natural when read aloud (no abbreviations like 'e.g.', no
   tongue-twisters, no parenthetical asides).

Return ONLY JSON: [{"index": 0, "narration": "..."}, {"index": 1, ...}, ...]
"""

    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        if not sb.scenes:
            return sb
        payload = [{
            "index": s.index,
            "duration_target_s": s.duration_target_s,
            "mood": s.visual_concept.mood,
            "narration": s.narration,
        } for s in sb.scenes]
        prompt = (
            f"Polish these {len(payload)} scene narrations for a "
            f"{sb.blog.get('region', 'default')} region "
            f"{sb.blog.get('category', 'industrial')} video.\n\n"
            f"Scenes:\n{json.dumps(payload, indent=2)}"
        )
        try:
            polished = self.ollama.generate_json(prompt, system=self.SYSTEM)
        except OllamaError as e:
            log.warning("NarrationPolisher: Ollama failed (%s); "
                        "keeping original narration", e)
            return sb
        if not isinstance(polished, list) or len(polished) != len(sb.scenes):
            log.warning("NarrationPolisher: returned %s items (expected %d); "
                        "keeping original narration",
                        len(polished) if isinstance(polished, list) else "non-list",
                        len(sb.scenes))
            return sb
        for orig, new in zip(sb.scenes, polished):
            if isinstance(new, dict) and isinstance(new.get("narration"), str) \
                    and new["narration"].strip():
                orig.narration = new["narration"].strip()
        log.info("NarrationPolisher: polished %d scene narrations", len(sb.scenes))
        return sb
```

**Wired into `orchestrator.build_storyboard`** between Storyboarder and Sourcer:

```python
log.info("[2.5/6] NarrationPolisher")
NarrationPolisher().run(sb)
save_storyboard(sb, workspace / "storyboard.json")
```

Step counters in all log lines update from `[1/5]…[5/5]` to `[1/6]…[6/6]` (counting NarrationPolisher as step 3 and bumping everything after).

---

## 10. Mood-aware visual treatments

**New file:** `video_agent/motion/color_grade.py`

```python
"""Per-mood color grading: subtle vignettes that match the narrative tone."""
from __future__ import annotations
from pathlib import Path
import subprocess


def grade_filter_for_mood(mood: str) -> str | None:
    """Returns an ffmpeg filter chain for the mood, or None for no grading."""
    if mood == "problem":
        # 12% red tint + soft vignette — sense of tension
        return ("colorchannelmixer=rr=1.12:rg=0.0:rb=0.0:"
                "gr=0.0:gg=0.96:gb=0.0:br=0.0:bg=0.0:bb=0.96,"
                "vignette=PI/5")
    if mood in ("brand", "cta"):
        # 8% warm tint + gentle vignette — conclusive feel
        return ("colorchannelmixer=rr=1.06:gg=1.02:bb=0.94,"
                "vignette=PI/6:eval=init")
    return None


def apply_grade(clip: Path, mood: str) -> Path:
    """Renders an in-place graded copy; returns the same path."""
    flt = grade_filter_for_mood(mood)
    if not flt:
        return clip
    out = clip.with_name(clip.stem + "_graded.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip),
        "-vf", flt, "-c:v", "libx264", "-preset", "fast",
        "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    clip.unlink()
    out.rename(clip)
    return clip
```

**Wired in `_render_scene_clip` (`composer.py`)** after `_overlay_on_screen_text`:

```python
from video_agent.motion.color_grade import apply_grade
...
_overlay_on_screen_text(out, scene, fps=fps)
apply_grade(out, scene.visual_concept.mood)   # NEW
return out
```

**Transition addition (`motion/transitions.py`):**

```python
def transition_between(prev_beat: str, next_beat: str) -> str:
    if prev_beat == "proof" and next_beat in ("brand", "cta"):
        return "dissolve_with_flash"      # NEW — 250ms white flash midpoint
    # ... existing cases unchanged
```

**In `_concat_with_transitions` (`composer.py`)** handle the new transition:
```python
if kind == "dissolve_with_flash":
    ffx = "fadewhite"      # ffmpeg xfade supports fadewhite/fadeblack natively
    dur = 0.25
```

---

## 11. Outro re-render with stronger CTA

**File:** `video_agent/tools/render_brand_assets.py` → rewrite `render_outro`

New 5-second outro design:

| Time (s) | Layer | Content |
|---|---|---|
| 0.0 – 1.0 | Background | Brand navy `#0a192f` with subtle radial gradient toward top |
| 0.0 – 1.0 | Logo | HRSU gold logo (300px wide, centred horizontally, y=720) — fade in from black |
| 0.0 – 1.0 | Tagline | `"Industrial Chemicals. Engineered Trust."` (white, 36pt, centred below logo) |
| 1.0 – 4.0 | CTA line 1 | `"Source your calcium nitrate at"` (white, 56pt) fade-up |
| 1.0 – 4.0 | CTA line 2 | `"hrsuindore.com"` (BRAND_GOLD, 80pt) fade-up below line 1 |
| 1.0 – 4.0 | Logo | Slow zoom-out (1.05 → 1.0) |
| 4.0 – 5.0 | All | Hold; subtle 1.5px upward drift on CTA block |

**Implementation:**

```python
def render_outro(output_mp4: Path, duration_s: float = 5.0) -> Path:
    """Renders the v2 outro: logo + tagline + strong CTA + subtle motion."""
    import tempfile
    from video_agent.motion.ken_burns import MotionPlan, render_motion_clip
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "outro.png"
        _draw_outro_card(png)
        # Ken Burns–style slow zoom-out on the composed card
        plan = MotionPlan(direction="out", start_xy=(0, 0), end_xy=(0, 0),
                          start_scale=1.05, end_scale=1.0)
        render_motion_clip(png, plan, output_mp4, duration_s, fps=30)
    return output_mp4


def _draw_outro_card(out_png: Path):
    """Composes the static outro layout: gradient + logo + tagline + CTA block.

    Uses the existing brand constants from video_agent/config.py — do NOT
    invent new colour names. Available constants:
        BRAND_GOLD       = "#d4af37"
        BRAND_DARK_NAVY  = "#0a192f"   (use as the background base)
        BRAND_NAVY_2     = "#0a1428"   (slightly darker — use for gradient edge)
        BRAND_TEXT_LIGHT = "#ccd6f6"   (use for white-ish body text)
        BRAND_LOGO_GOLD_PATH, BRAND_FONT_BODY, BRAND_FONT_HEADING
    """
    from PIL import Image, ImageDraw, ImageFont
    from video_agent.config import (
        BRAND_LOGO_GOLD_PATH, BRAND_FONT_BODY, BRAND_FONT_HEADING,
        BRAND_GOLD, BRAND_DARK_NAVY, BRAND_NAVY_2, BRAND_TEXT_LIGHT,
    )
    from video_agent.safezone import FRAME_W, FRAME_H

    # Base background
    img = Image.new("RGB", (FRAME_W, FRAME_H), BRAND_DARK_NAVY)

    # Vertical gradient overlay: lighter at top, darker at bottom (radial-like).
    # Implementation: blend BRAND_DARK_NAVY → BRAND_NAVY_2 line-by-line.
    grad = Image.new("RGB", (FRAME_W, FRAME_H))
    g_draw = ImageDraw.Draw(grad)
    def _hex_to_rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    top = _hex_to_rgb(BRAND_DARK_NAVY)
    bot = _hex_to_rgb(BRAND_NAVY_2)
    for y in range(FRAME_H):
        t = y / (FRAME_H - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        g_draw.line([(0, y), (FRAME_W, y)], fill=c)
    img = grad

    draw = ImageDraw.Draw(img)

    # Logo (gold, centred, 300px tall)
    logo = Image.open(BRAND_LOGO_GOLD_PATH).convert("RGBA")
    logo.thumbnail((300, 300))
    logo_y = 720
    img.paste(logo, ((FRAME_W - logo.width) // 2, logo_y), logo)

    # Tagline
    tagline_font = ImageFont.truetype(BRAND_FONT_BODY, 36)
    tagline = "Industrial Chemicals. Engineered Trust."
    bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
    tw = bbox[2] - bbox[0]
    draw.text(((FRAME_W - tw) // 2, logo_y + logo.height + 30),
              tagline, font=tagline_font, fill=BRAND_TEXT_LIGHT)

    # CTA block — line 1
    cta1_font = ImageFont.truetype(BRAND_FONT_BODY, 56)
    cta1 = "Source your calcium nitrate at"
    bbox1 = draw.textbbox((0, 0), cta1, font=cta1_font)
    tw1 = bbox1[2] - bbox1[0]
    draw.text(((FRAME_W - tw1) // 2, 1120), cta1, font=cta1_font,
              fill=BRAND_TEXT_LIGHT)

    # CTA block — line 2 (URL, gold, larger)
    cta2_font = ImageFont.truetype(BRAND_FONT_HEADING, 80)
    cta2 = "hrsuindore.com"
    bbox2 = draw.textbbox((0, 0), cta2, font=cta2_font)
    tw2 = bbox2[2] - bbox2[0]
    draw.text(((FRAME_W - tw2) // 2, 1200), cta2, font=cta2_font, fill=BRAND_GOLD)

    img.save(out_png)
```

**Auto-render on demand** — covered by the cache-version check in §5.2. The user never invokes `render_brand_assets.py` directly.

---

## 12. Observability

### 12.1 — Per-scene source attribution in logs

In `orchestrator.build_storyboard` after the Sourcer stage:

```python
log.info("Source attribution:")
for s in sb.scenes:
    if s.chosen_asset:
        log.info("  Scene %d: source=%s score=%d res=%dx%d%s",
                 s.index, s.chosen_asset.source, s.chosen_asset.score,
                 s.chosen_asset.width, s.chosen_asset.height,
                 " [DEGRADED]" if s.degraded else "")
    else:
        log.warning("  Scene %d: NO ASSET (degraded fallback to brand card)",
                    s.index)
```

### 12.2 — `quality_report.json`

Written next to the final MP4 by `compose_short_v2` as the final step (after safe-zone validate):

```python
import json

def _write_quality_report(sb, workspace, voice_duration, video_duration,
                          outro_concatenated):
    report = {
        "video_duration_s": round(video_duration, 2),
        "voice_duration_s": round(voice_duration, 2),
        "durations_redistributed": True,
        "intro_concatenated": False,
        "outro_concatenated": outro_concatenated,
        "scenes": [
            {
                "index": s.index,
                "mood": s.visual_concept.mood,
                "duration_s": round(s.duration_target_s, 2),
                "source": (s.chosen_asset.source if s.chosen_asset else None),
                "score": (s.chosen_asset.score if s.chosen_asset else None),
                "width": (s.chosen_asset.width if s.chosen_asset else 0),
                "height": (s.chosen_asset.height if s.chosen_asset else 0),
                "watermark_check": "passed",   # if check ran successfully
                "critic_flags": list(s.critic_flags or []),
                "re_sourced": getattr(s, "_re_sourced", False),
                "narration_polished": True,
                "degraded": s.degraded,
            } for s in sb.scenes
        ],
    }
    (workspace / "quality_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
```

Mark `scene._re_sourced = True` inside `Reviser._re_source` so the report reflects which scenes were swapped.

---

## 13. Error-handling guarantees

Pipeline must degrade gracefully — never crash — for any of these failure modes:

| Failure mode | Behaviour |
|---|---|
| Playwright init fails / browser launch error | GoogleImagesBrowserSource returns `[]`, log once at init |
| Playwright loads but Google serves CAPTCHA / empty result | Returns `[]`, log info, other sources cover via parallel fan-out |
| Pexels API key missing | Returns `[]` silently (key is optional) |
| Pexels rate-limit hit | Returns `[]`, log warning |
| Tesseract binary missing | Log one-time warning, watermark check returns `(False, "tesseract_unavailable")` — images pass through |
| Tesseract OCR errors on a specific image | Log debug, return `(False, "ocr_error")` — image passes through |
| All sources return 0 candidates for a scene | `scene.degraded = True`, fallback to brand-coloured card (existing behaviour) |
| Ollama down during NarrationPolisher | Skip polish, keep original narration, log warning, pipeline continues |
| Ollama returns malformed JSON during polish | Same — caught by `generate_json` retry loop, then graceful skip |
| Polisher returns wrong number of scenes | Skip, log warning |
| Reviser re-source exhausts max attempts | Keep original asset, log warning |
| Director-suggested rewrite fails to parse | Skip rewrite, keep original beat, log warning |
| Storyboarder.regenerate_beat fails | Skip, keep original beat, log warning |
| Color-grade filter fails on a scene | Render scene without grade, log warning |
| Outro file missing or version-stale | Auto-render inline, then concat (no manual command required) |
| Outro render itself fails | Log error, ship video without outro (`_concat_intro_outro` with both args None is a no-op copy) |
| Voice duration < total scene target | `_redistribute_durations` scales scenes down proportionally (existing behaviour) |
| Per-region music file missing | Voice-only mix, log warning once (existing behaviour) |
| Safe-zone validation fails after all fixes | Raise — this is a real bug, do not ship a broken video |

---

## 14. Testing strategy

### 14.1 — Unit tests

| File | Tests |
|---|---|
| `tests/video_agent/sources/test_scoring.py` (extend) | `test_min_resolution_hard_rejects` <br> `test_square_aspect_penalty` <br> `test_landscape_bonus` <br> `test_portrait_bonus` |
| `tests/video_agent/sources/test_watermark.py` (NEW) | `test_blocklist_match` <br> `test_text_density_threshold` <br> `test_cache_hit` <br> `test_missing_tesseract_graceful_skip` (mock import error) |
| `tests/video_agent/sources/test_pexels.py` (NEW) | `test_empty_key_returns_empty` <br> `test_extracts_large2x_url` <br> `test_handles_api_failure_gracefully` |
| `tests/video_agent/sources/test_google_images_browser.py` (NEW) | `test_init_failure_returns_empty` (mock Playwright import fail) <br> `test_empty_results_returns_empty` (mock page with no `img` elements) — does NOT make real network calls |
| `tests/video_agent/agents/test_reviser.py` (extend) | `test_resource_on_voice_visual_mismatch` <br> `test_structural_rewrite_on_director_suggestion` <br> `test_resource_respects_max_attempts` |
| `tests/video_agent/agents/test_narration_polisher.py` (NEW) | `test_polishes_all_scenes` (mock Ollama) <br> `test_ollama_failure_keeps_original` <br> `test_wrong_shape_keeps_original` |

### 14.2 — Integration test

**File:** `tests/video_agent/test_compose_v2.py` (NEW)

Builds a minimal Storyboard with 3 scenes + a 10-second voice WAV (synthesised offline with `pyttsx3` if available, else a pre-recorded fixture). Runs `compose_short_v2`. Asserts:

- Output MP4 duration is within 0.5s of `voice_duration + outro_duration`
- `quality_report.json` exists, has `outro_concatenated: true` and `durations_redistributed: true`
- ffprobe reports the video is `>= voice_duration`
- No safe-zone violations

If `pyttsx3` proves flaky on Windows CI, fall back to a checked-in 10-second silent WAV fixture (still validates the duration-redistribution and outro-concat paths).

### 14.3 — Manual smoke test

After implementation:

1. Run `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
2. Verify in the rendered MP4:
   - Outro plays at the end with the new CTA design
   - Voice is not cut off mid-sentence
   - The Gulf scene uses a Middle East / Saudi Arabia / Persian Gulf image — not US mainland
   - No watermark text behind subtitles on any scene
   - Image quality is sharp (no obvious upscaling artifacts)
3. Inspect `quality_report.json` — confirm:
   - `outro_concatenated: true`
   - At least one scene shows `re_sourced: true` if the Gulf scene was originally wrong
   - No scene has `width < 1280` or `height < 1280` simultaneously

---

## 15. Migration / rollout

This is a patch release. No data migration required.

**One-shot user action after merge:** none. The first `python scripts/make_video.py` invocation after merge will:
- Auto-render the new outro (version v2)
- Auto-create cache directories
- Log clear warnings if Tesseract / Pexels key / region music are missing

**Manual installs (one-time, optional):**
- Tesseract binary on Windows: `winget install UB-Mannheim.TesseractOCR` (skipped gracefully if absent)
- Pexels API key in `.env`: `PEXELS_API_KEY=...` (skipped gracefully if absent)

**`requirements.txt` additions:**
```
pytesseract>=0.3.10
```
(`playwright` is already installed for LinkedIn/Facebook automation.)

---

## 16. File-change summary

| File | Action | Notes |
|---|---|---|
| `video_agent/composer.py` | Modify | Step 0 duration redistribution; step 6 auto-render + concat outro; write quality_report.json |
| `video_agent/sources/scoring.py` | Modify | `_dimension_adjustment` helper |
| `video_agent/sources/watermark.py` | **NEW** | OCR-based watermark detection |
| `video_agent/sources/google_images.py` | **DELETE** | Replaced by browser version |
| `video_agent/sources/google_images_browser.py` | **NEW** | Playwright-based scraper |
| `video_agent/sources/pexels.py` | **NEW** | Pexels Search API |
| `video_agent/agents/sourcer.py` | Modify | Integrate watermark check; new `re_source_scene` method |
| `video_agent/agents/reviser.py` | Modify | Re-source on `voice_visual_mismatch`; structural rewrite on director suggestion |
| `video_agent/agents/strategist.py` | Modify | REGION SEMANTICS in system prompt |
| `video_agent/agents/storyboarder.py` | Modify | REGION SEMANTICS + proof-mood country prefix; new `regenerate_beat` method |
| `video_agent/agents/narration_polisher.py` | **NEW** | Ollama-based final narration pass |
| `video_agent/motion/ken_burns.py` | Modify | Slower zoom for `mechanism` mood (1.0→1.05) |
| `video_agent/motion/color_grade.py` | **NEW** | Mood-aware vignette + tint |
| `video_agent/motion/transitions.py` | Modify | New `dissolve_with_flash` for proof→cta |
| `video_agent/tools/render_brand_assets.py` | Modify | New outro design with strong CTA block |
| `video_agent/orchestrator.py` | Modify | Wire NarrationPolisher; swap GoogleImagesSource → GoogleImagesBrowserSource; add Pexels; source-attribution log block; pass Sourcer into Reviser |
| `video_agent/config.py` | Modify | `PEXELS_API_KEY`, `MIN_IMAGE_LONG_EDGE`, `IDEAL_IMAGE_LONG_EDGE`, `OUTRO_VERSION`, `OUTRO_VIDEO_PATH` (point to versioned filename). Reuse existing `BRAND_DARK_NAVY`, `BRAND_NAVY_2`, `BRAND_TEXT_LIGHT`, `BRAND_GOLD` — do not add new colour names. |
| `requirements.txt` | Modify | `pytesseract>=0.3.10` |
| `tests/video_agent/sources/test_scoring.py` | Modify | Dimension test cases |
| `tests/video_agent/sources/test_watermark.py` | **NEW** | Watermark detection tests |
| `tests/video_agent/sources/test_pexels.py` | **NEW** | Pexels source tests |
| `tests/video_agent/sources/test_google_images_browser.py` | **NEW** | Playwright source tests (mocked) |
| `tests/video_agent/agents/test_reviser.py` | Modify | Re-source + structural rewrite tests |
| `tests/video_agent/agents/test_narration_polisher.py` | **NEW** | Polisher tests (mocked Ollama) |
| `tests/video_agent/test_compose_v2.py` | **NEW** | End-to-end composer integration test |

Total: 4 new source modules + 1 new agent + 1 new motion module + 4 new test files + 9 file modifications.

---

## 17. Implementation order

Recommended sequencing to keep each PR self-contained and reviewable:

1. **PR-1 — Critical bugs (§5):** duration redistribution + outro auto-render + concat. Smallest scope; biggest user-visible fix. Acceptance: scene 4 and outro both appear in next render.
2. **PR-2 — Image quality (§6.3, §6.4, §6.5):** dimension gates + slow mechanism zoom + watermark OCR. Acceptance: no more square crops, no more watermarks.
3. **PR-3 — Sources (§6.1, §6.2):** Playwright Google + Pexels. Acceptance: log shows both attempted; source attribution diverse across scenes.
4. **PR-4 — Region semantics (§7):** prompt updates + Reviser re-source. Acceptance: gulf blog renders Middle East imagery.
5. **PR-5 — Narration polish + structural rewrite (§8, §9):** NarrationPolisher + director-triggered rewrites. Acceptance: final scene ends with CTA close in every render.
6. **PR-6 — Visual treatments + outro (§10, §11):** mood color grade + transition + new outro design. Acceptance: rendered video matches new outro spec.
7. **PR-7 — Observability (§12):** logs + quality_report.json. Acceptance: report file produced and readable.

Each PR ships independently. Manual smoke test (§14.3) after PR-1, PR-4, and PR-6.
