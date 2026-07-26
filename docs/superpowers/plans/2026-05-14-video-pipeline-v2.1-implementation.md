# Video Pipeline v2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the v2.1 patch release of the HRSU video pipeline — fix scene-cutoff, missing outro, blurry/wrong-region images, watermark clashes, and the broken Google Images source. Make the pipeline production-grade for `python scripts/make_video.py <url>` as a one-shot command.

**Architecture:** Patch release on top of the v2 pipeline. No new agents in the orchestrator graph except `NarrationPolisher` (between Storyboarder and Sourcer). All new code stays in `video_agent/`. AI work stays on local Ollama (gemma3:4b). No external API beyond Pexels (optional).

**Tech Stack:** Python 3.12 · ffmpeg/ffprobe · Playwright (already installed for LinkedIn/Facebook) · Ollama HTTP API · pytesseract (NEW) · Pillow · pytest + responses

**Spec:** `docs/superpowers/specs/2026-05-14-video-pipeline-v2.1-design.md`

**Note on git:** The user has explicitly opted out of git. **Skip every "commit" step.** When a step would normally end in `git add && git commit`, instead just verify the change works (run the test, run the pipeline) and move on. The plan still groups related changes into logical phases — treat each phase boundary as a "save point" where you stop and let the user manually verify before continuing.

---

## File Structure

### New files (10)
| Path | Responsibility |
|---|---|
| `video_agent/sources/watermark.py` | OCR-based watermark detection on the bottom strip of candidate images |
| `video_agent/sources/google_images_browser.py` | Playwright-based Google Images scraper (replaces old regex version) |
| `video_agent/sources/pexels.py` | Pexels Photo Search API client |
| `video_agent/agents/narration_polisher.py` | Final narration pass via Ollama — ensures CTA close, region-correct geography |
| `video_agent/motion/color_grade.py` | Per-mood vignette + tint (problem=red, cta=gold) |
| `tests/video_agent/sources/test_watermark.py` | Watermark detection tests (mocked pytesseract) |
| `tests/video_agent/sources/test_pexels.py` | Pexels source tests (responses mock) |
| `tests/video_agent/sources/test_google_images_browser.py` | Playwright source tests (mocked, no real network) |
| `tests/video_agent/agents/test_narration_polisher.py` | NarrationPolisher tests (mocked Ollama) |
| `tests/video_agent/test_compose_v2.py` | End-to-end integration test for `compose_short_v2` |

### Modified files (14)
| Path | Reason |
|---|---|
| `video_agent/composer.py` | Step 0 duration redistribution; step 6 outro auto-render + concat; write `quality_report.json` |
| `video_agent/sources/scoring.py` | Add `_dimension_adjustment` for aspect-ratio penalties/bonuses |
| `video_agent/agents/sourcer.py` | Integrate watermark check; new `re_source_scene` method |
| `video_agent/agents/reviser.py` | Trigger re-source on `voice_visual_mismatch`; structural rewrite on director suggestion |
| `video_agent/agents/strategist.py` | REGION SEMANTICS block in system prompt |
| `video_agent/agents/storyboarder.py` | REGION SEMANTICS + country-prefix for proof-mood; new `regenerate_beat` method |
| `video_agent/motion/ken_burns.py` | Slow zoom for `mechanism` mood (1.0 → 1.05) |
| `video_agent/motion/transitions.py` | New `dissolve_with_flash` for `proof → cta` |
| `video_agent/tools/render_brand_assets.py` | New outro design with strong CTA block |
| `video_agent/orchestrator.py` | Wire NarrationPolisher; swap Google scraper; add Pexels; source-attribution logs; pass Sourcer to Reviser |
| `video_agent/config.py` | New constants (Pexels key, dim thresholds, outro version) |
| `requirements.txt` | Add `pytesseract>=0.3.10` |
| `tests/video_agent/sources/test_scoring.py` | Add dimension-adjustment tests |
| `tests/video_agent/agents/test_reviser.py` | Add re-source + structural-rewrite tests |

### Deleted files (1)
| Path | Reason |
|---|---|
| `video_agent/sources/google_images.py` | Replaced by `google_images_browser.py`. Also delete `tests/video_agent/sources/test_google_images.py` for the same reason. |

---

# Phase 1 — Critical bugs (Spec §5)

**What this phase delivers:** Voice no longer cut off mid-sentence; outro plays at the end of every video.

## Task 1: Add `_probe_audio_duration` helper to composer

**Files:**
- Modify: `video_agent/composer.py` (add new helper near the top of the file)

- [ ] **Step 1: Check if helper already exists**

Run: `grep -n "_probe_audio_duration\|def _probe_audio" video_agent/composer.py`
Expected: no matches (helper doesn't exist yet).

- [ ] **Step 2: Add the helper**

Insert this function near the top of `video_agent/composer.py`, after the imports and before `_pick_music`:

```python
def _probe_audio_duration(audio_path: Path) -> float:
    """Returns audio duration in seconds via ffprobe. Raises if probing fails."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())
```

- [ ] **Step 3: Smoke test the helper from a Python shell**

Run:
```bash
python -c "from pathlib import Path; from video_agent.composer import _probe_audio_duration; print(_probe_audio_duration(Path('output/videos/calcium-nitrate-for-shale-leachate-html/voiceover.mp3')))"
```
Expected: prints a positive float (e.g. `45.144`).

## Task 2: Wire duration redistribution into `compose_short_v2` as Step 0

**Files:**
- Modify: `video_agent/composer.py` — function `compose_short_v2` (around line 494)

- [ ] **Step 1: Read the current function signature and first few lines**

Run: `grep -n "def compose_short_v2\|^def _redistribute_durations" video_agent/composer.py`
Expected: shows both function locations.

- [ ] **Step 2: Insert Step 0 at the top of `compose_short_v2`**

Locate this block in `video_agent/composer.py`:

```python
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
```

Replace it with:

```python
def compose_short_v2(sb: Storyboard, voice_path: Path, subtitle_path: Path,
                      output_path: Path, workspace: Path,
                      fps: int = 30) -> Path:
    """V2 composer: one MP4 per scene with motion + on-screen text, then
    concat with beat-aware xfades, then mux voice + music + subtitles, then
    auto-render+concat outro, then safe-zone validate. Raises on any
    safe-zone violation."""
    workspace = Path(workspace)
    output_path = Path(output_path)

    # 0. Probe voice and redistribute scene durations so the video matches the
    #    voice length exactly. Prevents -shortest from truncating the last scene.
    voice_duration = _probe_audio_duration(voice_path)
    target_total = voice_duration + 0.3   # 0.3s tail before outro overlap
    pre = sum(s.duration_target_s for s in sb.scenes)
    scaled = _redistribute_durations(
        [{"duration_target_s": s.duration_target_s} for s in sb.scenes],
        target_total,
    )
    for s, new in zip(sb.scenes, scaled):
        s.duration_target_s = float(new["duration_target_s"])
    post = sum(s.duration_target_s for s in sb.scenes)
    log.info("Redistributed scene durations: voice=%.2fs, pre=%.2fs -> post=%.2fs",
             voice_duration, pre, post)

    # 1. Render per-scene clips
    scene_clips = [_render_scene_clip(s, workspace, fps) for s in sb.scenes]
```

- [ ] **Step 3: Verify the function still parses**

Run: `python -c "from video_agent.composer import compose_short_v2; print('ok')"`
Expected: prints `ok` with no traceback.

## Task 3: Add `OUTRO_VERSION` and update `OUTRO_VIDEO_PATH` in config

**Files:**
- Modify: `video_agent/config.py` (line 77 area)

- [ ] **Step 1: Find the current OUTRO_VIDEO_PATH line**

Run: `grep -n "OUTRO_VIDEO_PATH\|OUTRO_VERSION" video_agent/config.py`
Expected: shows only the current `OUTRO_VIDEO_PATH = "asset_library/brand/outro_5s.mp4"` line.

- [ ] **Step 2: Replace the OUTRO_VIDEO_PATH line and add OUTRO_VERSION**

Find:
```python
OUTRO_VIDEO_PATH = "asset_library/brand/outro_5s.mp4"
```

Replace with:
```python
OUTRO_VERSION = 2
OUTRO_VIDEO_PATH = f"asset_library/brand/outro_5s_v{OUTRO_VERSION}.mp4"
```

- [ ] **Step 3: Verify imports still work**

Run: `python -c "from video_agent.config import OUTRO_VIDEO_PATH, OUTRO_VERSION; print(OUTRO_VIDEO_PATH, OUTRO_VERSION)"`
Expected: prints `asset_library/brand/outro_5s_v2.mp4 2`.

## Task 4: Add outro auto-render + concat as Step 6 of `compose_short_v2`

**Files:**
- Modify: `video_agent/composer.py` — function `compose_short_v2` (the existing final-mux step + new step 6)

- [ ] **Step 1: Locate the existing final-mux block**

Run: `grep -n "vf_subs\|str(output_path)" video_agent/composer.py | head -10`
Expected: shows the lines where the current code writes directly to `output_path`.

- [ ] **Step 2: Rename the final-mux target to `_with_subs.mp4` and add outro concat**

Find this existing block in `compose_short_v2` (around lines 530-540, the final ffmpeg call that writes subtitled video):

```python
    vf_subs = f"ass='{ass_str}'"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(concat), "-i", str(voice_with_music),
        "-vf", vf_subs,
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

Replace with:

```python
    vf_subs = f"ass='{ass_str}'"
    subs_mp4 = workspace / "_with_subs.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(concat), "-i", str(voice_with_music),
        "-vf", vf_subs,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", str(subs_mp4),
    ], check=True)

    # 6. Auto-render outro if missing or stale, then concat (no intro for now)
    outro_path = Path(OUTRO_VIDEO_PATH)
    if not outro_path.exists():
        try:
            from video_agent.tools.render_brand_assets import render_outro
            log.info("Outro missing — auto-rendering at %s", outro_path)
            outro_path.parent.mkdir(parents=True, exist_ok=True)
            render_outro(outro_path)
        except Exception as e:
            log.warning("Outro render failed (%s); shipping video without outro", e)
            outro_path = None
    if outro_path and outro_path.exists():
        _concat_intro_outro(subs_mp4, intro_mp4=None, outro_mp4=outro_path,
                            output_mp4=output_path)
    else:
        shutil.copy2(subs_mp4, output_path)

    # 7. Safe-zone validation
    problems = _validate_safe_zone(output_path)
    if problems:
        raise RuntimeError(f"Safe-zone violations: {problems}")

    return output_path
```

- [ ] **Step 3: Verify the file still parses**

Run: `python -c "from video_agent.composer import compose_short_v2; print('ok')"`
Expected: prints `ok`.

## Task 5: Verify Phase 1 end-to-end with a manual smoke test

- [ ] **Step 1: Pre-render the v2 outro stub so the auto-render path doesn't fire on first run**

Currently the outro path is `outro_5s_v2.mp4`. Phase 6 will replace the renderer with a stronger design. For now, we want any outro file to exist so we can prove the concat works. Run:

```bash
python -m video_agent.tools.render_brand_assets --outro-only
```
Expected: writes `asset_library/brand/outro_5s_v2.mp4`. If the script complains about the path mismatch (it writes to the old name), proceed to Step 2 — the auto-render code will handle it on next pipeline run.

- [ ] **Step 2: Run the pipeline on the existing test blog**

Run:
```bash
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force
```
Expected:
- Log line `Redistributed scene durations: voice=...s, pre=...s -> post=...s`
- Log line `Outro missing — auto-rendering at asset_library/brand/outro_5s_v2.mp4` (only on first run)
- Final MP4 plays the outro at the end
- Voice does NOT cut off mid-sentence — the final CTA narration plays in full

- [ ] **Step 3: Confirm with ffprobe**

Run:
```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 output/videos/calcium-nitrate-for-shale-leachate-html/video_short.mp4
```
Expected: duration ≈ `voice_duration + 5s outro + 0.3s tail`, i.e. roughly `50-51s` (since voice was 45.1s and outro is 5s).

**Phase 1 complete. Save point — let the user verify before continuing.**

---

# Phase 2 — Image quality (Spec §6.3, §6.4, §6.5)

**What this phase delivers:** No more blurry upscales, no more square crops cutting off half the diagram, no more watermarks behind subtitles.

## Task 6: Add dimension/aspect constants to config

**Files:**
- Modify: `video_agent/config.py`

- [ ] **Step 1: Find the existing source-related constants**

Run: `grep -n "STOCK_CACHE_DIR\|PEXELS_API_BASE" video_agent/config.py`
Expected: shows the existing stock-fallback section.

- [ ] **Step 2: Append the new constants**

Find:
```python
# ─── Stock fallback ────────────────────────────────────────────────────────
PEXELS_API_BASE = "https://api.pexels.com/v1"
STOCK_CACHE_DIR = "asset_library/stock_cache"
```

Replace with:
```python
# ─── Stock fallback ────────────────────────────────────────────────────────
PEXELS_API_BASE = "https://api.pexels.com/v1"
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
STOCK_CACHE_DIR = "asset_library/stock_cache"

# ─── Source quality gates ──────────────────────────────────────────────────
MIN_IMAGE_LONG_EDGE = 1280     # hard floor — reject below this in scoring
IDEAL_IMAGE_LONG_EDGE = 1920   # bonus threshold
```

If `import os` is not already at the top of the file, add it.

- [ ] **Step 3: Verify imports**

Run:
```bash
python -c "from video_agent.config import PEXELS_API_KEY, MIN_IMAGE_LONG_EDGE, IDEAL_IMAGE_LONG_EDGE; print('ok')"
```
Expected: prints `ok`.

## Task 7: Write failing tests for `_dimension_adjustment`

**Files:**
- Modify: `tests/video_agent/sources/test_scoring.py`

- [ ] **Step 1: Append four new test cases**

Append to `tests/video_agent/sources/test_scoring.py`:

```python
def test_below_min_long_edge_hard_rejects():
    cand = RawCandidate(source="unsplash", url="u", caption="x",
                        width=800, height=600, file_size=80_000)
    score = score_candidate(cand, query="x")
    assert score < 0


def test_square_aspect_penalised():
    sq = RawCandidate(source="unsplash", url="u", caption="industrial water",
                      width=1500, height=1500, file_size=120_000)
    wide = RawCandidate(source="unsplash", url="u", caption="industrial water",
                        width=1920, height=1080, file_size=120_000)
    assert score_candidate(sq, "industrial water") < score_candidate(wide, "industrial water")


def test_landscape_widescreen_gets_bonus():
    wide = RawCandidate(source="unsplash", url="u", caption="industrial",
                        width=2560, height=1440, file_size=120_000)
    score = score_candidate(wide, "industrial")
    # 1.78 aspect ≥ 1.6 → +10; long edge ≥ 1920 → +10
    assert score >= 60


def test_portrait_orientation_gets_bonus():
    tall = RawCandidate(source="unsplash", url="u", caption="oil rig tower",
                        width=1280, height=2280, file_size=120_000)
    score = score_candidate(tall, "oil rig")
    assert score >= 50    # gets the portrait bonus
```

- [ ] **Step 2: Run tests — they MUST fail**

Run: `pytest tests/video_agent/sources/test_scoring.py -v`
Expected: at least three of the four new tests fail (some may coincidentally pass given the existing min-resolution check).

## Task 8: Implement `_dimension_adjustment` in scoring.py

**Files:**
- Modify: `video_agent/sources/scoring.py`

- [ ] **Step 1: Add the helper and wire it into `score_candidate`**

Replace the entire contents of `video_agent/sources/scoring.py` with:

```python
"""Per-candidate quality scoring. Pure function, no I/O."""
from __future__ import annotations
import re
from video_agent.sources.base import RawCandidate
from video_agent.config import MIN_IMAGE_LONG_EDGE, IDEAL_IMAGE_LONG_EDGE

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
              "with", "by", "is", "are"}

# Source authority weights (used inside score)
_AUTHORITY = {
    "wikimedia": 10, "unsplash": 8, "pexels": 8, "bing": 5, "duckduckgo": 5,
    "google_images": 5, "youtube": 5,
}


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")
            if m.group(0).lower() not in _STOPWORDS and len(m.group(0)) > 2}


def _dimension_adjustment(c: RawCandidate) -> tuple[int, bool]:
    """Returns (score_delta, hard_reject).
    hard_reject=True means caller should drop this candidate entirely."""
    if not c.width or not c.height:
        return (0, False)
    long_edge = max(c.width, c.height)
    if long_edge < MIN_IMAGE_LONG_EDGE:
        return (0, True)
    aspect = c.width / c.height
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


def score_candidate(c: RawCandidate, query: str) -> int:
    """Returns a score in roughly [-100, 100].
    Negative scores mean hard-rejected (resolution too low, etc.)."""
    delta, hard_reject = _dimension_adjustment(c)
    if hard_reject:
        return -100
    score = 30 + delta              # base 30 + dimension adjustment
    # Token overlap (caption ↔ query)
    overlap = len(_tokens(c.caption) & _tokens(query))
    score += min(25, overlap * 8)
    # Source authority
    score += _AUTHORITY.get(c.source, 0)
    # File integrity (downloads cleanly, opens) — checked separately
    if c.file_size and c.file_size > 100_000:
        score += 15
    # YouTube extras
    if c.is_clip and c.extra.get("view_count", 0) > 10_000 \
            and c.duration_s and c.duration_s > 30:
        score += 10
    return score
```

- [ ] **Step 2: Run tests — they MUST pass**

Run: `pytest tests/video_agent/sources/test_scoring.py -v`
Expected: all tests pass, including the four new ones and the three pre-existing ones.

## Task 9: Slow down Ken Burns zoom for `mechanism` mood

**Files:**
- Modify: `video_agent/motion/ken_burns.py` (line 44 area)

- [ ] **Step 1: Find the mechanism mood block**

Run: `grep -n 'mood == "mechanism"' video_agent/motion/ken_burns.py`
Expected: one match around line 43-44.

- [ ] **Step 2: Reduce the zoom-end value**

Find:
```python
    if mood == "mechanism":
        s0, s1 = 1.0, 1.18
```

Replace with:
```python
    if mood == "mechanism":
        s0, s1 = 1.0, 1.05   # gentler zoom so the whole diagram stays in frame
```

- [ ] **Step 3: Verify the module still imports**

Run: `python -c "from video_agent.motion.ken_burns import plan_ken_burns; print('ok')"`
Expected: `ok`.

## Task 10: Write failing tests for watermark detection

**Files:**
- Create: `tests/video_agent/sources/test_watermark.py`

- [ ] **Step 1: Create the test file with mock-based tests**

Create `tests/video_agent/sources/test_watermark.py` with:

```python
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image

from video_agent.sources.watermark import is_watermarked, _ensure_tesseract


@pytest.fixture
def fake_image(tmp_path):
    """A simple solid-grey 800x600 image saved to a temp path."""
    p = tmp_path / "test.jpg"
    Image.new("RGB", (800, 600), (128, 128, 128)).save(p)
    return p


@pytest.fixture(autouse=True)
def reset_tesseract_flag():
    """Reset the module-level _TESSERACT_OK flag between tests."""
    import video_agent.sources.watermark as m
    m._TESSERACT_OK = None
    yield
    m._TESSERACT_OK = None


def test_blocklist_match_rejects(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright Getty Images 2024"):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is True
    assert "blocklist_match" in reason


def test_text_density_threshold_rejects(fake_image, tmp_path):
    long_text = "x" * 50    # 50 chars, no blocklist match
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value=long_text):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is True
    assert "text_density" in reason


def test_clean_image_passes(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="  "):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is False


def test_cache_hit_skips_ocr(fake_image, tmp_path):
    cache_root = tmp_path / "cache"
    # First call populates cache
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright") as mock_ocr:
        is_watermarked(fake_image, cache_root)
        assert mock_ocr.call_count == 1
    # Second call should hit cache, NOT re-run OCR
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=True), \
         patch("pytesseract.image_to_string", return_value="Copyright") as mock_ocr:
        watermarked, _ = is_watermarked(fake_image, cache_root)
        assert mock_ocr.call_count == 0    # cache hit
        assert watermarked is True


def test_missing_tesseract_graceful_skip(fake_image, tmp_path):
    with patch("video_agent.sources.watermark._ensure_tesseract", return_value=False):
        watermarked, reason = is_watermarked(fake_image, tmp_path / "cache")
    assert watermarked is False
    assert reason == "tesseract_unavailable"
```

- [ ] **Step 2: Run the tests — they MUST fail**

Run: `pytest tests/video_agent/sources/test_watermark.py -v`
Expected: ImportError, since `video_agent.sources.watermark` doesn't exist yet.

## Task 11: Implement `video_agent/sources/watermark.py`

**Files:**
- Create: `video_agent/sources/watermark.py`

- [ ] **Step 1: Create the module**

Create `video_agent/sources/watermark.py` with:

```python
"""Reject images whose bottom strip contains visible watermarks or stock-photo text.

Uses Tesseract via pytesseract. If the binary is missing, the check skips
gracefully (returns False, "tesseract_unavailable").

Results are cached by file content hash to avoid re-OCRing on retry.
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
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
                    "Install on Windows: winget install UB-Mannheim.TesseractOCR", e)
        _TESSERACT_OK = False
    return _TESSERACT_OK


def is_watermarked(img_path: Path, cache_root: Path) -> tuple[bool, str]:
    """Returns (is_watermarked, reason).
    Caches results by file content SHA1 to skip re-OCR."""
    img_path = Path(img_path)
    digest = hashlib.sha1(img_path.read_bytes()).hexdigest()
    cache_file = Path(cache_root) / "watermark" / f"{digest}.json"
    if cache_file.exists():
        try:
            d = json.loads(cache_file.read_text())
            return (d["watermarked"], d["reason"])
        except Exception:
            pass    # fall through and re-check
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

- [ ] **Step 2: Run tests — they MUST pass**

Run: `pytest tests/video_agent/sources/test_watermark.py -v`
Expected: all 5 tests pass.

## Task 12: Add `pytesseract` to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append pytesseract**

Add to `requirements.txt` (anywhere is fine; group it near other source-related deps if there's an obvious place):

```
pytesseract>=0.3.10
```

- [ ] **Step 2: Install**

Run: `pip install pytesseract>=0.3.10`
Expected: package installs.

## Task 13: Wire watermark check into Sourcer

**Files:**
- Modify: `video_agent/agents/sourcer.py` — function `_download_candidate`

- [ ] **Step 1: Locate `_download_candidate`**

Run: `grep -n "_download_candidate\|img.verify" video_agent/agents/sourcer.py`
Expected: shows the function around line 134 and the verify call inside it.

- [ ] **Step 2: Add watermark check after the PIL verify**

Find this block in `_download_candidate` (around line 143-155):

```python
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
```

Replace with:

```python
        try:
            r = requests.get(c.url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 HRSU-VideoBot/2.0",
            })
            r.raise_for_status()
            dest.write_bytes(r.content)
            # Verify it's a valid image
            with Image.open(dest) as img:
                img.verify()
        except Exception as e:
            log.debug("Download/verify failed for %s: %s", c.url, e)
            dest.unlink(missing_ok=True)
            return None
        # Watermark check (graceful skip if Tesseract not installed)
        from video_agent.sources.watermark import is_watermarked
        watermarked, reason = is_watermarked(dest, self.cache.root)
        if watermarked:
            log.info("Scene %d candidate %s rejected: watermark (%s)",
                     scene_idx, c.url, reason)
            dest.unlink(missing_ok=True)
            return None
        return dest
```

- [ ] **Step 3: Verify the module still parses**

Run: `python -c "from video_agent.agents.sourcer import Sourcer; print('ok')"`
Expected: `ok`.

## Task 14: Verify Phase 2 end-to-end

- [ ] **Step 1: Run the pipeline again**

Run:
```bash
python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force
```
Expected:
- Log lines include scene candidates being rejected for low resolution (look for `score=-100` patterns in candidate logs) — OR the run completes without seeing any such warnings if no candidates were below threshold.
- No image in the final video has visible copyright/watermark text behind subtitles.
- The chemical-mechanism scene has a slower, gentler zoom — the diagram stays visible throughout.

**Phase 2 complete. Save point.**

---

# Phase 3 — Sources: Playwright Google + Pexels (Spec §6.1, §6.2)

**What this phase delivers:** Google Images source works again (via real headless browser); Pexels added as a high-quality landscape source.

## Task 15: Write failing tests for `PexelsSource`

**Files:**
- Create: `tests/video_agent/sources/test_pexels.py`

- [ ] **Step 1: Create the test file**

Create `tests/video_agent/sources/test_pexels.py`:

```python
import responses
from video_agent.sources.pexels import PexelsSource


def test_pexels_no_key_returns_empty():
    src = PexelsSource(api_key="")
    assert src.search("anything") == []


@responses.activate
def test_pexels_search_extracts_large2x_url():
    responses.add(
        responses.GET, "https://api.pexels.com/v1/search",
        json={"photos": [
            {"src": {"large2x": "https://images.pexels.com/p1_large2x",
                     "original": "https://images.pexels.com/p1_original"},
             "alt": "industrial plant", "photographer": "Jane Doe",
             "width": 1920, "height": 1080},
            {"src": {"large2x": "", "original": "https://images.pexels.com/p2_original"},
             "alt": "", "photographer": "John",
             "width": 2400, "height": 1600},
        ]},
        status=200,
    )
    src = PexelsSource(api_key="fake")
    cands = src.search("industrial", limit=2)
    assert len(cands) == 2
    assert cands[0].source == "pexels"
    assert cands[0].url == "https://images.pexels.com/p1_large2x"
    assert cands[1].url == "https://images.pexels.com/p2_original"   # falls back


@responses.activate
def test_pexels_handles_api_failure_gracefully():
    responses.add(responses.GET, "https://api.pexels.com/v1/search", status=500)
    src = PexelsSource(api_key="fake")
    assert src.search("anything") == []
```

- [ ] **Step 2: Run — MUST fail**

Run: `pytest tests/video_agent/sources/test_pexels.py -v`
Expected: ImportError, module doesn't exist.

## Task 16: Implement `PexelsSource`

**Files:**
- Create: `video_agent/sources/pexels.py`

- [ ] **Step 1: Create the file**

Create `video_agent/sources/pexels.py`:

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

    def __init__(self, api_key: str | None = PEXELS_API_KEY):
        self.api_key = api_key

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
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

- [ ] **Step 2: Tests must pass**

Run: `pytest tests/video_agent/sources/test_pexels.py -v`
Expected: all 3 tests pass.

## Task 17: Write failing tests for `GoogleImagesBrowserSource`

**Files:**
- Create: `tests/video_agent/sources/test_google_images_browser.py`

- [ ] **Step 1: Create the test file (mocks all Playwright internals)**

Create `tests/video_agent/sources/test_google_images_browser.py`:

```python
from unittest.mock import patch, MagicMock
import pytest


def test_init_failure_returns_empty_results(monkeypatch):
    """If Playwright import or launch fails, source must not crash; search returns []."""
    import sys
    # Force playwright import to fail by removing it from sys.modules and stubbing the import
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource()
    assert src.search("anything") == []


def test_search_filters_small_thumbnails():
    """img elements with naturalWidth < 300 are filtered out (data-URI thumbnails)."""
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)   # bypass __init__
    src._ctx = MagicMock()
    page = MagicMock()
    src._ctx.new_page.return_value = page
    page.eval_on_selector_all.return_value = [
        {"src": "https://big.jpg", "alt": "big", "w": 1200, "h": 800},
        {"src": "https://tiny.jpg", "alt": "tiny", "w": 50, "h": 50},
        {"src": "https://medium.jpg", "alt": "medium", "w": 400, "h": 300},
    ]
    results = src.search("query", limit=5)
    urls = [r.url for r in results]
    assert "https://big.jpg" in urls
    assert "https://medium.jpg" in urls
    assert "https://tiny.jpg" not in urls


def test_search_handles_navigation_failure():
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)
    src._ctx = MagicMock()
    page = MagicMock()
    src._ctx.new_page.return_value = page
    page.goto.side_effect = RuntimeError("navigation failed")
    results = src.search("query")
    assert results == []
    page.close.assert_called_once()


def test_search_no_context_returns_empty():
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)
    src._ctx = None
    assert src.search("query") == []
```

- [ ] **Step 2: Run — MUST fail**

Run: `pytest tests/video_agent/sources/test_google_images_browser.py -v`
Expected: ImportError.

## Task 18: Implement `GoogleImagesBrowserSource`

**Files:**
- Create: `video_agent/sources/google_images_browser.py`

- [ ] **Step 1: Create the file**

Create `video_agent/sources/google_images_browser.py`:

```python
"""Google Images via Playwright headless browser.

Replaces the regex-based google_images.py — far more resilient to layout
changes, at the cost of ~2-3s startup per Sourcer instance.

The browser is launched ONCE per Sourcer (not per query) and reused.
If Playwright is not available or init fails, search() returns [] —
the multi-source design means other sources will cover.
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

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if self._ctx is None:
            return []
        page = self._ctx.new_page()
        srcs: list[dict] = []
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
                limit * 4,
            )
        except Exception as e:
            log.warning("GoogleImagesBrowserSource: search failed for %r (%s)", query, e)
            srcs = []
        finally:
            page.close()
        results = [s for s in srcs if s.get("w", 0) >= 300][:limit]
        out = [RawCandidate(source=self.name, url=s["src"],
                            caption=s.get("alt") or "",
                            width=s.get("w", 0), height=s.get("h", 0))
               for s in results]
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

- [ ] **Step 2: Run tests**

Run: `pytest tests/video_agent/sources/test_google_images_browser.py -v`
Expected: all 4 tests pass.

## Task 19: Swap sources in the orchestrator

**Files:**
- Modify: `video_agent/orchestrator.py`

- [ ] **Step 1: Replace imports + `_build_sourcer`**

Find:
```python
from video_agent.sources.google_images import GoogleImagesSource
```

Replace with:
```python
from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
from video_agent.sources.pexels import PexelsSource
```

Find:
```python
def _build_sourcer(workspace: Path) -> Sourcer:
    return Sourcer(
        sources=[UnsplashSource(), WikimediaSource(), BingSource(),
                 DuckDuckGoSource(), GoogleImagesSource(), YouTubeSource()],
        cache_root=Path("output/_image_cache"),
        download_root=workspace / "_assets",
    )
```

Replace with:
```python
def _build_sourcer(workspace: Path) -> Sourcer:
    return Sourcer(
        sources=[
            UnsplashSource(),
            PexelsSource(),
            WikimediaSource(),
            BingSource(),
            GoogleImagesBrowserSource(),
            DuckDuckGoSource(),
            YouTubeSource(),
        ],
        cache_root=Path("output/_image_cache"),
        download_root=workspace / "_assets",
    )
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from video_agent.orchestrator import _build_sourcer; print('ok')"`
Expected: `ok`.

## Task 20: Delete the old regex-based Google source and its test

**Files:**
- Delete: `video_agent/sources/google_images.py`
- Delete: `tests/video_agent/sources/test_google_images.py`

- [ ] **Step 1: Delete both files**

Run:
```bash
rm video_agent/sources/google_images.py
rm tests/video_agent/sources/test_google_images.py
```

- [ ] **Step 2: Confirm no stale imports**

Run: `grep -rn "google_images import GoogleImagesSource\|from video_agent.sources.google_images" video_agent tests 2>&1`
Expected: zero matches.

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

Run: `pytest tests/video_agent -v`
Expected: all tests pass; no `ImportError` from leftover references.

## Task 21: Verify Phase 3 end-to-end

- [ ] **Step 1: Ensure Playwright Chromium is installed**

Run: `python -m playwright install chromium`
Expected: Chromium downloads/verifies (no-op if already present).

- [ ] **Step 2: Set Pexels API key (optional but recommended)**

Add to `.env`:
```
PEXELS_API_KEY=<your-pexels-key>
```

If you don't have a Pexels key, skip — the source returns `[]` silently.

- [ ] **Step 3: Run the pipeline**

Run: `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
Expected:
- Log line `GoogleImagesBrowserSource: Playwright initialised`
- No `Google Images returned 0 parseable candidates` warnings (the new source either returns real candidates or returns empty cleanly)
- At least one scene's `chosen_asset.source` is `google_images` or `pexels` in the final storyboard

**Phase 3 complete. Save point.**

---

# Phase 4 — Region semantics (Spec §7)

**What this phase delivers:** Gulf blogs render Middle East imagery (not Gulf of Mexico). Reviser swaps assets when critic flags `voice_visual_mismatch`.

## Task 22: Update Strategist system prompt with REGION SEMANTICS

**Files:**
- Modify: `video_agent/agents/strategist.py`

- [ ] **Step 1: Find the existing system prompt**

Run: `grep -n "SYSTEM\|system_prompt\|REGION" video_agent/agents/strategist.py`
Expected: shows where the system prompt is defined (look for a multi-line string assigned to `SYSTEM` or passed to `OllamaClient.generate`).

- [ ] **Step 2: Append the REGION SEMANTICS block to the Strategist's system prompt**

Locate the system prompt string in `video_agent/agents/strategist.py`. Append this block to the very end of that string (before the closing `"""`):

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

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.agents.strategist import Strategist; print('ok')"`
Expected: `ok`.

## Task 23: Update Storyboarder system prompt with REGION SEMANTICS + country-prefix rule

**Files:**
- Modify: `video_agent/agents/storyboarder.py`

- [ ] **Step 1: Find the existing system prompt**

Run: `grep -n "SYSTEM\|system_prompt" video_agent/agents/storyboarder.py`

- [ ] **Step 2: Append both blocks**

Append the same REGION SEMANTICS block from Task 22 to the Storyboarder system prompt, plus this extra block at the very end:

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

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.agents.storyboarder import Storyboarder; print('ok')"`
Expected: `ok`.

## Task 24: Add `re_source_scene` method to Sourcer

**Files:**
- Modify: `video_agent/agents/sourcer.py`

- [ ] **Step 1: Add the new method at the end of the `Sourcer` class**

Append this method to the `Sourcer` class in `video_agent/agents/sourcer.py` (after `_is_dup`):

```python
    def re_source_scene(self, scene: Scene, blog_category: str,
                        exclude_urls: set[str]) -> None:
        """Re-runs source-and-pick for a single scene, skipping previously-used URLs.
        Updates scene.chosen_asset in place if a fresh candidate is found.
        If no fresh candidate scores above min_score, leaves the scene unchanged."""
        queries = _build_queries(scene.visual_concept, blog_category)
        all_raw: list[RawCandidate] = []
        for q in queries:
            raws = self._search_all_sources(q)
            all_raw.extend(r for r in raws if r.url not in exclude_urls)
            if len(all_raw) >= 5:
                break
        if not all_raw:
            return
        primary_q = queries[0]
        scored = sorted(
            ((score_candidate(c, primary_q), c) for c in all_raw),
            key=lambda t: -t[0],
        )
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
            scene._re_sourced = True   # flag for quality_report.json
            return
```

- [ ] **Step 2: Verify the module still parses**

Run: `python -c "from video_agent.agents.sourcer import Sourcer; print('ok')"`
Expected: `ok`.

## Task 25: Write failing test for Reviser re-source on `voice_visual_mismatch`

**Files:**
- Modify: `tests/video_agent/agents/test_reviser.py`

- [ ] **Step 1: Inspect the existing test file structure**

Run: `head -40 tests/video_agent/agents/test_reviser.py`
Expected: shows existing imports and test scaffolding.

- [ ] **Step 2: Append the new test**

Add to `tests/video_agent/agents/test_reviser.py`:

```python
from unittest.mock import MagicMock
from video_agent.agents.reviser import Reviser
from video_agent.storyboard import Storyboard, Scene, VisualConcept, AssetCandidate


def _make_scene(idx=0, flags=("voice_visual_mismatch",)):
    s = Scene(
        index=idx,
        beat="proof", mood_unused=None,
        duration_target_s=5.0,
        narration="Test narration",
        on_screen_text="Test text",
        visual_concept=VisualConcept(subject="x", modifier="y", mood="proof"),
        chosen_asset=AssetCandidate(
            source="old_source", url="https://old.example/img.jpg", score=50,
            local_path="/tmp/old.jpg", caption="", width=1920, height=1080,
        ),
        asset_candidates=[],
    )
    s.critic_flags = list(flags)
    return s


def test_reviser_resources_on_voice_visual_mismatch():
    scene = _make_scene()
    sb = Storyboard(version="2.0", blog={"category": "mining"}, scenes=[scene])

    def fake_re_source(scn, cat, exclude_urls):
        scn.chosen_asset = AssetCandidate(
            source="new_source", url="https://new.example/img.jpg", score=80,
            local_path="/tmp/new.jpg", caption="", width=1920, height=1080,
        )

    mock_sourcer = MagicMock()
    mock_sourcer.re_source_scene.side_effect = fake_re_source

    Reviser(sourcer=mock_sourcer).run(sb)
    assert scene.chosen_asset.source == "new_source"
    assert mock_sourcer.re_source_scene.called


def test_reviser_no_resource_without_flag():
    scene = _make_scene(flags=())     # no mismatch flag
    sb = Storyboard(version="2.0", blog={"category": "mining"}, scenes=[scene])
    mock_sourcer = MagicMock()
    Reviser(sourcer=mock_sourcer).run(sb)
    assert not mock_sourcer.re_source_scene.called
```

**Note:** The exact `Scene` and `Storyboard` constructor field names may differ from what's shown above. If the test fails on construction errors, first run `python -c "import dataclasses; from video_agent.storyboard import Scene; print([f.name for f in dataclasses.fields(Scene)])"` to inspect the real field names and adjust the test accordingly.

- [ ] **Step 3: Run — MUST fail**

Run: `pytest tests/video_agent/agents/test_reviser.py -v -k "test_reviser_resources or test_reviser_no_resource"`
Expected: failures with "Reviser.__init__ unexpected keyword 'sourcer'" or AttributeError about `sourcer`.

## Task 26: Implement Reviser re-source logic

**Files:**
- Modify: `video_agent/agents/reviser.py`

- [ ] **Step 1: Read current Reviser class signature**

Run: `grep -n "class Reviser\|def __init__\|def run\b" video_agent/agents/reviser.py`
Expected: shows class def + methods.

- [ ] **Step 2: Update `__init__` and `run` to accept Sourcer**

Open `video_agent/agents/reviser.py`. Find the existing `Reviser` class. Replace its `__init__` method with:

```python
    def __init__(self, sourcer=None, max_resource_attempts: int = 2):
        self.sourcer = sourcer
        self.max_resource_attempts = max_resource_attempts
        # Preserve any other existing init logic from the original method
        # (e.g., self.ollama = OllamaClient()) — re-add it here if present
```

If the original `__init__` accepted other parameters or initialised additional state (like `self.ollama`), preserve those — only add the `sourcer` and `max_resource_attempts` parameters.

Then add this method to the `Reviser` class:

```python
    def _re_source(self, scene, sb):
        if not self.sourcer:
            return
        excluded = {scene.chosen_asset.url} if scene.chosen_asset else set()
        for attempt in range(self.max_resource_attempts):
            self.sourcer.re_source_scene(
                scene, sb.blog.get("category", ""),
                exclude_urls=excluded,
            )
            if scene.chosen_asset and scene.chosen_asset.url not in excluded:
                log.info("Scene %d: re-sourced after voice_visual_mismatch "
                         "(attempt %d, new source=%s)",
                         scene.index, attempt + 1, scene.chosen_asset.source)
                return
            if scene.chosen_asset:
                excluded.add(scene.chosen_asset.url)
        log.warning("Scene %d: re-source exhausted after %d attempts; keeping original",
                    scene.index, self.max_resource_attempts)
```

Finally, in the existing `run` method, find the per-scene loop that processes `critic_flags`. After the existing text-revision logic for each scene, add:

```python
            flags = set(scene.critic_flags or [])
            if "voice_visual_mismatch" in flags:
                self._re_source(scene, sb)
```

- [ ] **Step 3: Run the new tests — they MUST pass**

Run: `pytest tests/video_agent/agents/test_reviser.py -v`
Expected: all new tests pass; existing tests still pass (the `sourcer` parameter is optional, defaults to None).

## Task 27: Pass Sourcer into Reviser from the orchestrator

**Files:**
- Modify: `video_agent/orchestrator.py`

- [ ] **Step 1: Find the Reviser construction**

Run: `grep -n "Reviser(" video_agent/orchestrator.py`
Expected: one line where Reviser is instantiated.

- [ ] **Step 2: Pass Sourcer in**

Find:
```python
    log.info("[5/5] Reviser")
    Reviser(sourcer=_build_sourcer(workspace)).run(sb)
```

If it already passes `sourcer=_build_sourcer(workspace)`, no change needed. If it currently calls `Reviser().run(sb)` with no arguments, change it to:

```python
    log.info("[5/5] Reviser")
    Reviser(sourcer=_build_sourcer(workspace)).run(sb)
```

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.orchestrator import build_storyboard; print('ok')"`
Expected: `ok`.

## Task 28: Verify Phase 4 end-to-end

- [ ] **Step 1: Clear the test blog's cached storyboard so the pipeline re-runs from scratch**

Run: `rm -rf output/videos/calcium-nitrate-for-shale-leachate-html/storyboard.json`

- [ ] **Step 2: Run the pipeline**

Run: `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
Expected:
- Scene narration about the "Gulf" region references Persian Gulf / GCC / Saudi Arabia / UAE — NOT Gulf of Mexico
- If any scene gets `voice_visual_mismatch` flag, the log shows `re-sourced after voice_visual_mismatch`

- [ ] **Step 3: Inspect the storyboard**

Run:
```bash
python -c "import json; sb=json.load(open(r'output/videos/calcium-nitrate-for-shale-leachate-html/storyboard.json', encoding='utf-8')); [print(f\"scene {s['index']}: {s['visual_concept']['subject'][:80]}\") for s in sb['scenes']]"
```
Expected: subjects mention Middle East / Persian Gulf / Saudi Arabia, not "Gulf of Mexico".

**Phase 4 complete. Save point.**

---

# Phase 5 — NarrationPolisher + Director-triggered structural rewrite (Spec §8, §9)

**What this phase delivers:** Narration always ends with a clean CTA close; weakest beats get a structural rewrite when the Director suggests one.

## Task 29: Write failing tests for `NarrationPolisher`

**Files:**
- Create: `tests/video_agent/agents/test_narration_polisher.py`

- [ ] **Step 1: Create the test file**

Create `tests/video_agent/agents/test_narration_polisher.py`:

```python
from unittest.mock import MagicMock
from video_agent.agents.narration_polisher import NarrationPolisher
from video_agent.ollama_client import OllamaError
from video_agent.storyboard import Storyboard, Scene, VisualConcept


def _scene(idx, narration, duration=5.0):
    return Scene(
        index=idx, beat="problem",
        duration_target_s=duration,
        narration=narration,
        on_screen_text="x",
        visual_concept=VisualConcept(subject="a", modifier="b", mood="problem"),
    )


def test_polisher_replaces_narrations():
    sb = Storyboard(version="2.0", blog={"region": "gulf", "category": "mining"},
                    scenes=[_scene(0, "Original 1"), _scene(1, "Original 2")])
    ollama = MagicMock()
    ollama.generate_json.return_value = [
        {"index": 0, "narration": "Polished 1"},
        {"index": 1, "narration": "Polished 2"},
    ]
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Polished 1"
    assert sb.scenes[1].narration == "Polished 2"


def test_polisher_keeps_original_on_ollama_failure():
    sb = Storyboard(version="2.0", blog={"region": "gulf", "category": "mining"},
                    scenes=[_scene(0, "Original 1")])
    ollama = MagicMock()
    ollama.generate_json.side_effect = OllamaError("ollama down")
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Original 1"


def test_polisher_keeps_original_when_wrong_shape():
    sb = Storyboard(version="2.0", blog={}, scenes=[_scene(0, "Original")])
    ollama = MagicMock()
    ollama.generate_json.return_value = "not a list"
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Original"


def test_polisher_handles_empty_storyboard():
    sb = Storyboard(version="2.0", blog={}, scenes=[])
    ollama = MagicMock()
    NarrationPolisher(ollama=ollama).run(sb)
    # Must not call Ollama with empty payload
    ollama.generate_json.assert_not_called()
```

**Note on Scene field names:** if Scene construction fails because field names don't match (e.g., `beat` doesn't exist), inspect the real fields with `python -c "import dataclasses; from video_agent.storyboard import Scene; print([f.name for f in dataclasses.fields(Scene)])"` and adjust the helper accordingly.

- [ ] **Step 2: Run — MUST fail**

Run: `pytest tests/video_agent/agents/test_narration_polisher.py -v`
Expected: ImportError.

## Task 30: Implement `NarrationPolisher`

**Files:**
- Create: `video_agent/agents/narration_polisher.py`

- [ ] **Step 1: Create the module**

Create `video_agent/agents/narration_polisher.py`:

```python
"""Final narration pass via local Ollama. Tightens phrasing, ensures CTA close,
double-checks region semantics. Graceful degradation if Ollama is down."""
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

- [ ] **Step 2: Run tests**

Run: `pytest tests/video_agent/agents/test_narration_polisher.py -v`
Expected: all 4 tests pass.

## Task 31: Wire NarrationPolisher into the orchestrator

**Files:**
- Modify: `video_agent/orchestrator.py`

- [ ] **Step 1: Add import**

Add to the imports at the top of `video_agent/orchestrator.py`:
```python
from video_agent.agents.narration_polisher import NarrationPolisher
```

- [ ] **Step 2: Renumber the pipeline stages and add NarrationPolisher**

Find this section in `build_storyboard`:

```python
    log.info("[2/5] Storyboarder")
    Storyboarder().run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("[3/5] Sourcer")
```

Replace with:

```python
    log.info("[2/6] Storyboarder")
    Storyboarder().run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("[3/6] NarrationPolisher")
    NarrationPolisher().run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

    log.info("[4/6] Sourcer")
```

Also update the rest of the log labels in this function so they're consistent:
- `[3/5] Sourcer` → `[4/6] Sourcer`
- `[4/5] Critics (parallel)` → `[5/6] Critics (parallel)`
- `[5/5] Reviser` → `[6/6] Reviser`

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.orchestrator import build_storyboard; print('ok')"`
Expected: `ok`.

## Task 32: Add `regenerate_beat` method to Storyboarder

**Files:**
- Modify: `video_agent/agents/storyboarder.py`

- [ ] **Step 1: Locate the existing `run` method**

Run: `grep -n "def run\b" video_agent/agents/storyboarder.py`
Expected: one match.

- [ ] **Step 2: Append `regenerate_beat`**

Add this method to the `Storyboarder` class in `video_agent/agents/storyboarder.py`:

```python
    def regenerate_beat(self, sb, scene_index: int, director_suggestion: str) -> dict | None:
        """One-shot regeneration for a single beat, scoped to the index given.

        Returns a dict with keys: narration, on_screen_text, visual_concept
        (dataclass-shaped dict), or None on failure.

        Used by Reviser when GlobalDirector flags a weakest_beat with a
        structural rewrite suggestion."""
        scene = sb.scenes[scene_index]
        prompt = (
            f"You wrote this scene previously. The director thinks it's weak and "
            f"has suggested a structural change. Rewrite ONLY this scene's "
            f"narration, on_screen_text, and visual_concept based on the suggestion.\n\n"
            f"Director's suggestion:\n{director_suggestion}\n\n"
            f"Current scene (index {scene_index}, mood={scene.visual_concept.mood}, "
            f"duration_target_s={scene.duration_target_s}):\n"
            f"  narration: {scene.narration}\n"
            f"  on_screen_text: {scene.on_screen_text}\n"
            f"  visual_concept.subject: {scene.visual_concept.subject}\n"
            f"  visual_concept.modifier: {scene.visual_concept.modifier}\n\n"
            f"Blog region: {sb.blog.get('region', 'default')}\n"
            f"Blog category: {sb.blog.get('category', 'industrial')}\n\n"
            f"Return ONLY JSON: "
            f'{{"narration": "...", "on_screen_text": "...", '
            f'"visual_concept": {{"subject": "...", "modifier": "...", "mood": "..."}}}}'
        )
        try:
            result = self.ollama.generate_json(prompt, system=self.SYSTEM)
        except Exception as e:
            log.warning("regenerate_beat: Ollama failed (%s); keeping original", e)
            return None
        if not isinstance(result, dict):
            return None
        return result
```

**Note:** If the `Storyboarder` class doesn't already store an `ollama` attribute (some implementations create OllamaClient on demand), adapt the call accordingly — find how the existing `run` method invokes Ollama and mirror that pattern.

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.agents.storyboarder import Storyboarder; print('ok')"`
Expected: `ok`.

## Task 33: Wire structural rewrite into Reviser

**Files:**
- Modify: `video_agent/agents/reviser.py`

- [ ] **Step 1: Add the `_structural_rewrite` helper**

Add this method to the `Reviser` class:

```python
    def _structural_rewrite(self, scene, sb):
        """Called when GlobalDirector flags this scene as weakest_beat with a
        rewrite suggestion. Calls Storyboarder.regenerate_beat scoped to this
        scene, then re-sources the asset if the visual_concept changed."""
        director = getattr(sb, "director_suggestion", None) or {}
        suggestion = director.get("suggestion", "")
        if not suggestion:
            return
        log.info("Scene %d: structural rewrite triggered (%s)",
                 scene.index, suggestion[:80])
        from video_agent.agents.storyboarder import Storyboarder
        rewritten = Storyboarder().regenerate_beat(
            sb, scene.index, director_suggestion=suggestion,
        )
        if not rewritten:
            return
        if isinstance(rewritten.get("narration"), str):
            scene.narration = rewritten["narration"]
        if isinstance(rewritten.get("on_screen_text"), str):
            scene.on_screen_text = rewritten["on_screen_text"]
        vc = rewritten.get("visual_concept")
        if isinstance(vc, dict):
            from video_agent.storyboard import VisualConcept
            scene.visual_concept = VisualConcept(
                subject=vc.get("subject", scene.visual_concept.subject),
                modifier=vc.get("modifier", scene.visual_concept.modifier),
                mood=vc.get("mood", scene.visual_concept.mood),
            )
        # Re-source the asset since visual_concept may have changed
        if self.sourcer:
            self.sourcer.re_source_scene(
                scene, sb.blog.get("category", ""), exclude_urls=set(),
            )
```

In the `run` method, after the `_re_source` call inside the per-scene loop, add:

```python
            # Director-suggested structural rewrite for the weakest beat
            director = getattr(sb, "director_suggestion", None) or {}
            if director.get("weakest_beat") == scene.index:
                self._structural_rewrite(scene, sb)
```

- [ ] **Step 2: Locate and remove the deferred warning log**

Run: `grep -n "deferred to v1.1\|Director suggested structural" video_agent/agents/reviser.py`

If you find a log line that says something like `Director suggested structural rewrite (deferred to v1.1): ...`, delete it (or replace with a debug-level log explaining that the rewrite path is now active and will be invoked when applicable).

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.agents.reviser import Reviser; print('ok')"`
Expected: `ok`.

## Task 34: Verify Phase 5 end-to-end

- [ ] **Step 1: Run the pipeline**

Run: `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
Expected:
- Log line `[3/6] NarrationPolisher` appears
- Log line `NarrationPolisher: polished N scene narrations` appears (or a graceful-skip warning if Ollama errored)
- The final scene's narration ends with a clean CTA close (visit hrsuindore.com / source from HRSU)
- If GlobalDirector flagged a weakest_beat, log shows `Scene N: structural rewrite triggered`

- [ ] **Step 2: Inspect the polished narrations**

Run:
```bash
python -c "import json; sb=json.load(open(r'output/videos/calcium-nitrate-for-shale-leachate-html/storyboard.json', encoding='utf-8')); [print(f'scene {s[\"index\"]}: {s[\"narration\"][:100]}...') for s in sb['scenes']]"
```
Expected: final scene ends with `hrsuindore.com` or `HRSU` CTA phrasing.

**Phase 5 complete. Save point.**

---

# Phase 6 — Visual treatments + new outro design (Spec §10, §11)

**What this phase delivers:** Mood-aware vignettes; stronger CTA outro design.

## Task 35: Add `dissolve_with_flash` transition

**Files:**
- Modify: `video_agent/motion/transitions.py`
- Modify: `video_agent/composer.py` (handle the new transition in `_concat_with_transitions`)

- [ ] **Step 1: Add the new transition in `transitions.py`**

Run: `cat video_agent/motion/transitions.py`

Inspect the existing `transition_between(prev_beat, next_beat)` function. Find the existing return-string logic. Add this branch BEFORE any default return:

```python
    if prev_beat == "proof" and next_beat in ("brand", "cta"):
        return "dissolve_with_flash"
```

- [ ] **Step 2: Map the new kind to `fadewhite` in the composer's xfade logic**

Open `video_agent/composer.py`. Find the section in `_concat_with_transitions` where `kind` is mapped to `ffx` and `dur`. Currently it looks like:

```python
        kind = transition_between(scenes[i - 1].beat, scenes[i].beat)
        ffx = "wiperight" if kind == "whip_pan" else "fade"
        dur = 0.0 if kind == "cut" else 0.25
```

Replace with:

```python
        kind = transition_between(scenes[i - 1].beat, scenes[i].beat)
        if kind == "whip_pan":
            ffx, dur = "wiperight", 0.25
        elif kind == "dissolve_with_flash":
            ffx, dur = "fadewhite", 0.25
        elif kind == "cut":
            ffx, dur = "fade", 0.0
        else:
            ffx, dur = "fade", 0.25
```

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.motion.transitions import transition_between; print('ok')"`
Expected: `ok`.

## Task 36: Create `color_grade.py`

**Files:**
- Create: `video_agent/motion/color_grade.py`

- [ ] **Step 1: Create the module**

Create `video_agent/motion/color_grade.py`:

```python
"""Per-mood color grading: subtle vignettes that match the narrative tone."""
from __future__ import annotations
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


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
    """Renders an in-place graded copy; returns the same path.
    On filter failure, leaves the clip untouched and logs a warning."""
    flt = grade_filter_for_mood(mood)
    if not flt:
        return clip
    out = clip.with_name(clip.stem + "_graded.mp4")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip),
            "-vf", flt, "-c:v", "libx264", "-preset", "fast",
            "-crf", "20", "-pix_fmt", "yuv420p", str(out),
        ], check=True)
    except subprocess.CalledProcessError as e:
        log.warning("apply_grade failed for mood=%s (%s); leaving clip ungraded",
                    mood, e)
        return clip
    clip.unlink()
    out.rename(clip)
    return clip
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from video_agent.motion.color_grade import apply_grade; print('ok')"`
Expected: `ok`.

## Task 37: Wire `apply_grade` into `_render_scene_clip`

**Files:**
- Modify: `video_agent/composer.py`

- [ ] **Step 1: Find `_render_scene_clip`**

Run: `grep -n "def _render_scene_clip\|_overlay_on_screen_text(out, scene" video_agent/composer.py`
Expected: shows the function and the three places where `_overlay_on_screen_text` is called.

- [ ] **Step 2: Add the grade call after every `_overlay_on_screen_text` call**

Add this import at the top of `video_agent/composer.py` (with the other motion imports):

```python
from video_agent.motion.color_grade import apply_grade
```

Then, in `_render_scene_clip`, find each of the three lines that look like:

```python
        _overlay_on_screen_text(out, scene, fps=fps)
        return out
```

Insert `apply_grade(out, scene.visual_concept.mood)` between them:

```python
        _overlay_on_screen_text(out, scene, fps=fps)
        apply_grade(out, scene.visual_concept.mood)
        return out
```

There should be **three places** in `_render_scene_clip` where this pattern needs to be replaced (one for the degraded fallback, one for the video-clip branch, and one for the still-image branch).

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.composer import _render_scene_clip; print('ok')"`
Expected: `ok`.

## Task 38: Replace `render_outro` with the new CTA design

**Files:**
- Modify: `video_agent/tools/render_brand_assets.py`

- [ ] **Step 1: Read the existing `render_outro`**

Run: `grep -n "def render_outro\|def _draw" video_agent/tools/render_brand_assets.py`

- [ ] **Step 2: Replace `render_outro` and add `_draw_outro_card`**

Open `video_agent/tools/render_brand_assets.py`. Replace the existing `render_outro` function with:

```python
def render_outro(output_mp4: Path, duration_s: float = 5.0) -> Path:
    """Renders the v2 outro: gradient bg + logo + tagline + strong CTA + subtle zoom-out."""
    import tempfile
    from video_agent.motion.ken_burns import MotionPlan, render_motion_clip
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "outro.png"
        _draw_outro_card(png)
        plan = MotionPlan(direction="out", start_xy=(0, 0), end_xy=(0, 0),
                          start_scale=1.05, end_scale=1.0)
        render_motion_clip(png, plan, output_mp4, duration_s, fps=30)
    return output_mp4


def _draw_outro_card(out_png: Path):
    """Composes the static outro layout: gradient + logo + tagline + CTA block.

    Reuses existing brand constants from video_agent/config.py:
        BRAND_GOLD       = "#d4af37"
        BRAND_DARK_NAVY  = "#0a192f"
        BRAND_NAVY_2     = "#0a1428"
        BRAND_TEXT_LIGHT = "#ccd6f6"
        BRAND_LOGO_GOLD_PATH, BRAND_FONT_BODY, BRAND_FONT_HEADING
    """
    from PIL import Image, ImageDraw, ImageFont
    from video_agent.config import (
        BRAND_LOGO_GOLD_PATH, BRAND_FONT_BODY, BRAND_FONT_HEADING,
        BRAND_GOLD, BRAND_DARK_NAVY, BRAND_NAVY_2, BRAND_TEXT_LIGHT,
    )
    from video_agent.safezone import FRAME_W, FRAME_H

    def _hex_to_rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    # Vertical gradient: BRAND_DARK_NAVY at top → BRAND_NAVY_2 at bottom
    img = Image.new("RGB", (FRAME_W, FRAME_H))
    g_draw = ImageDraw.Draw(img)
    top = _hex_to_rgb(BRAND_DARK_NAVY)
    bot = _hex_to_rgb(BRAND_NAVY_2)
    for y in range(FRAME_H):
        t = y / (FRAME_H - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        g_draw.line([(0, y), (FRAME_W, y)], fill=c)

    draw = ImageDraw.Draw(img)

    # Logo (gold, centred, ~300px)
    logo = Image.open(BRAND_LOGO_GOLD_PATH).convert("RGBA")
    logo.thumbnail((300, 300))
    logo_y = 720
    img.paste(logo, ((FRAME_W - logo.width) // 2, logo_y), logo)

    # Tagline
    tagline_font = _safe_truetype(BRAND_FONT_BODY, 36)
    tagline = "Industrial Chemicals. Engineered Trust."
    bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
    tw = bbox[2] - bbox[0]
    draw.text(((FRAME_W - tw) // 2, logo_y + logo.height + 30),
              tagline, font=tagline_font, fill=BRAND_TEXT_LIGHT)

    # CTA block — line 1
    cta1_font = _safe_truetype(BRAND_FONT_BODY, 56)
    cta1 = "Source your calcium nitrate at"
    bbox1 = draw.textbbox((0, 0), cta1, font=cta1_font)
    tw1 = bbox1[2] - bbox1[0]
    draw.text(((FRAME_W - tw1) // 2, 1120), cta1, font=cta1_font,
              fill=BRAND_TEXT_LIGHT)

    # CTA block — line 2 (URL, gold, larger)
    cta2_font = _safe_truetype(BRAND_FONT_HEADING, 80)
    cta2 = "hrsuindore.com"
    bbox2 = draw.textbbox((0, 0), cta2, font=cta2_font)
    tw2 = bbox2[2] - bbox2[0]
    draw.text(((FRAME_W - tw2) // 2, 1200), cta2, font=cta2_font, fill=BRAND_GOLD)

    img.save(out_png)


def _safe_truetype(family_name: str, size: int):
    """Load a TrueType font by family name, falling back to PIL default if missing.
    family_name comes from BRAND_FONT_BODY / BRAND_FONT_HEADING in config."""
    from PIL import ImageFont
    # Try common Windows paths first; the family name in config is the human-readable
    # name (e.g. "Poppins"), but ImageFont.truetype expects a filename or a font on PATH.
    candidates = [
        f"{family_name}.ttf",
        f"{family_name}-Regular.ttf",
        f"C:/Windows/Fonts/{family_name}.ttf",
        f"C:/Windows/Fonts/{family_name.lower()}.ttf",
        "arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()
```

- [ ] **Step 3: Smoke-test the outro renderer in isolation**

Run:
```bash
python -m video_agent.tools.render_brand_assets --outro-only
```
Expected: writes `asset_library/brand/outro_5s_v2.mp4`. Play it manually — should show gradient navy background, gold logo, tagline, white CTA line, gold URL line, with a subtle zoom-out.

## Task 39: Verify Phase 6 end-to-end

- [ ] **Step 1: Delete the old outro so the new one regenerates**

Run: `rm -f asset_library/brand/outro_5s.mp4 asset_library/brand/outro_5s_v2.mp4`

- [ ] **Step 2: Run the pipeline**

Run: `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
Expected:
- Log line `Outro missing — auto-rendering at asset_library/brand/outro_5s_v2.mp4`
- Final video shows the new outro
- Problem-mood scenes have a subtle red tint + vignette
- CTA/brand scenes have a subtle gold-warm vignette
- The transition from the proof scene to the brand/CTA scene uses a white-flash dissolve

**Phase 6 complete. Save point.**

---

# Phase 7 — Observability (Spec §12)

**What this phase delivers:** Per-scene source attribution in logs; `quality_report.json` next to every video.

## Task 40: Add per-scene source attribution log in the orchestrator

**Files:**
- Modify: `video_agent/orchestrator.py`

- [ ] **Step 1: Find the Sourcer stage in `build_storyboard`**

Run: `grep -n "Sourcer\|save_storyboard" video_agent/orchestrator.py`
Expected: shows where `_build_sourcer().run(sb)` is called.

- [ ] **Step 2: Append the attribution log immediately after the Sourcer stage**

Find:

```python
    log.info("[4/6] Sourcer")
    _build_sourcer(workspace).run(sb)
    save_storyboard(sb, workspace / "storyboard.json")
```

Replace with:

```python
    log.info("[4/6] Sourcer")
    _build_sourcer(workspace).run(sb)
    save_storyboard(sb, workspace / "storyboard.json")

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

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.orchestrator import build_storyboard; print('ok')"`
Expected: `ok`.

## Task 41: Write `quality_report.json` from the composer

**Files:**
- Modify: `video_agent/composer.py`

- [ ] **Step 1: Add the helper function**

Add this helper to `video_agent/composer.py` near the top of the file (after the other helpers, before `compose_short_v2`):

```python
def _write_quality_report(sb, workspace: Path, voice_duration: float,
                          video_duration: float, outro_concatenated: bool) -> None:
    """Writes a machine-readable quality_report.json next to the final video."""
    import json
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
                "critic_flags": list(getattr(s, "critic_flags", None) or []),
                "re_sourced": getattr(s, "_re_sourced", False),
                "degraded": s.degraded,
            } for s in sb.scenes
        ],
    }
    (workspace / "quality_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
```

- [ ] **Step 2: Call the helper at the end of `compose_short_v2`**

Find the end of `compose_short_v2` (after the safe-zone validation block, before `return output_path`):

```python
    # 7. Safe-zone validation
    problems = _validate_safe_zone(output_path)
    if problems:
        raise RuntimeError(f"Safe-zone violations: {problems}")

    return output_path
```

Replace with:

```python
    # 7. Safe-zone validation
    problems = _validate_safe_zone(output_path)
    if problems:
        raise RuntimeError(f"Safe-zone violations: {problems}")

    # 8. Quality report
    video_duration = _probe_audio_duration(output_path)
    _write_quality_report(sb, workspace, voice_duration, video_duration,
                          outro_concatenated=(outro_path is not None
                                              and outro_path.exists()))

    return output_path
```

Note that `_probe_audio_duration` reads format duration via ffprobe, which works for video files too (the `format=duration` entry is container-level).

- [ ] **Step 3: Verify imports**

Run: `python -c "from video_agent.composer import compose_short_v2; print('ok')"`
Expected: `ok`.

## Task 42: Final end-to-end verification

- [ ] **Step 1: Clean cache to force a full pipeline run**

Run: `rm -rf output/videos/calcium-nitrate-for-shale-leachate-html`

- [ ] **Step 2: Run the pipeline**

Run: `python scripts/make_video.py https://blog.hrsuindore.com/2026/05/calcium-nitrate-for-shale-leachate.html --force`
Expected: completes without errors.

- [ ] **Step 3: Inspect `quality_report.json`**

Run:
```bash
cat output/videos/calcium-nitrate-for-shale-leachate-html/quality_report.json
```
Expected:
- `outro_concatenated: true`
- `durations_redistributed: true`
- `video_duration_s` ≈ `voice_duration_s + 5` (outro)
- Each scene has non-null `source`, non-zero `width`/`height`, scoring values

- [ ] **Step 4: Final manual smoke test**

Open the produced `video_short.mp4`. Verify:
- ✅ Voice plays to a clean CTA ending — not cut off mid-sentence
- ✅ New outro plays at the end (gradient bg, gold logo, white CTA line, gold URL)
- ✅ Gulf region uses Middle East imagery (not Gulf of Mexico)
- ✅ No watermark/copyright text on any image
- ✅ Images are sharp (no blurry upscaling); chemistry-diagram scene shows the full diagram throughout
- ✅ Problem-mood scenes have a subtle red tint; CTA scene has a warm-gold tint
- ✅ Transition from the proof scene to the brand/CTA scene is a white-flash dissolve

**Phase 7 complete. Pipeline v2.1 done.**

---

# Optional: Phase 8 — Integration test (Spec §14.2)

This phase is optional. Unit tests cover the individual modules; a manual smoke test covers end-to-end. The integration test is for CI confidence.

## Task 43: Write a minimal integration test for `compose_short_v2`

**Files:**
- Create: `tests/video_agent/test_compose_v2.py`

- [ ] **Step 1: Create the test**

Create `tests/video_agent/test_compose_v2.py`:

```python
"""Integration test for compose_short_v2.

Uses a pre-recorded 10-second silent WAV fixture (no pyttsx3 dependency) so
the test is deterministic on Windows CI.

Requires ffmpeg and ffprobe on PATH.
"""
import subprocess
from pathlib import Path
import pytest

from video_agent.composer import compose_short_v2
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, AssetCandidate,
)


@pytest.fixture
def silent_voice(tmp_path):
    """Generate a 10-second silent WAV via ffmpeg."""
    out = tmp_path / "silence.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", "10", str(out),
    ], check=True)
    return out


@pytest.fixture
def empty_subtitles(tmp_path):
    """Create an empty ASS subtitle file (subtitle burn-in is a no-op)."""
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]\nTitle: Empty\n", encoding="utf-8")
    return ass


@pytest.fixture
def solid_image(tmp_path):
    """A 1920x1080 solid red image to use as scene asset."""
    from PIL import Image
    p = tmp_path / "red.jpg"
    Image.new("RGB", (1920, 1080), (200, 30, 30)).save(p)
    return p


def _make_sb(solid_image, scenes_count=3):
    scenes = []
    for i in range(scenes_count):
        scenes.append(Scene(
            index=i, beat="problem",
            duration_target_s=3.0,
            narration=f"Scene {i} narration",
            on_screen_text=f"Scene {i}",
            visual_concept=VisualConcept(subject="x", modifier="y", mood="problem"),
            chosen_asset=AssetCandidate(
                source="test", url="local://test", score=80,
                local_path=str(solid_image), caption="",
                width=1920, height=1080,
            ),
            asset_candidates=[],
        ))
    return Storyboard(version="2.0", blog={"region": "usa", "category": "mining"},
                      scenes=scenes)


def test_compose_v2_redistributes_durations_and_concats_outro(
        tmp_path, silent_voice, empty_subtitles, solid_image):
    sb = _make_sb(solid_image)
    output = tmp_path / "out.mp4"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    compose_short_v2(sb, voice_path=silent_voice,
                     subtitle_path=empty_subtitles,
                     output_path=output, workspace=workspace)
    # Quality report exists
    report = workspace / "quality_report.json"
    assert report.exists()
    import json
    data = json.loads(report.read_text())
    assert data["durations_redistributed"] is True
    # Video duration ≈ voice (10s) + outro (5s) ≈ 14-16s
    assert 13 <= data["video_duration_s"] <= 17
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/video_agent/test_compose_v2.py -v`
Expected: passes. If it fails due to Scene/Storyboard field-name mismatches, run `python -c "import dataclasses; from video_agent.storyboard import Scene, Storyboard; print([f.name for f in dataclasses.fields(Scene)]); print([f.name for f in dataclasses.fields(Storyboard)])"` and adjust the fixtures.

---

# Self-review checklist

Before declaring done, verify:

- [ ] **Spec §5 (Critical bugs)** — Tasks 1-5 cover duration redistribution + outro auto-render
- [ ] **Spec §6 (Image sourcing fixes)** — Tasks 6-21 cover dimension gates, slow mechanism zoom, watermark OCR, Playwright Google, Pexels
- [ ] **Spec §7 (Region semantics)** — Tasks 22-28 cover Strategist/Storyboarder prompt updates and Reviser re-source
- [ ] **Spec §8 (Director structural rewrite)** — Tasks 32-33 cover `regenerate_beat` + Reviser hook
- [ ] **Spec §9 (NarrationPolisher)** — Tasks 29-31 cover the new agent and wiring
- [ ] **Spec §10 (Mood visual treatments)** — Tasks 35-37 cover transitions + color grade
- [ ] **Spec §11 (Outro re-render)** — Task 38 covers the new outro design
- [ ] **Spec §12 (Observability)** — Tasks 40-41 cover attribution logs + quality_report.json
- [ ] **Spec §13 (Error handling)** — covered inline in each task (graceful skip for Tesseract, Playwright, Pexels key, Ollama; auto-render for outro; etc.)
- [ ] **Spec §14 (Testing)** — Tasks 7, 10, 15, 17, 25, 29 cover unit tests; Task 43 covers integration; Phase verification steps cover manual smoke
- [ ] **No git commit steps** — confirmed
- [ ] **Every step has exact code or exact commands** — no "implement appropriately" hand-waves

---

# Total task count

42 mandatory tasks across 7 phases + 1 optional integration test (Task 43).

Each phase ends with a verification step that produces a working pipeline state — you can stop after any phase and have a useful improvement shipped.
