# Shorts Engine — Plan 2 of 3: Cards + Shotlist + Audio + Assemble (spec Phases 3–4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the working Plan-1 pipeline (INGEST→FACTS→SCRIPT) with four new stages — SHOTLIST → AUDIO → VISUALS → ASSEMBLE — plus the branded card design system, so that `python -m shorts_engine <blog_url> --until assemble` produces a complete, watchable, all-designed 35–50s portrait video with voiceover, burned captions, music ducking, logo bug, progress bar, and end-card hold. This is spec Phases 3–4; the **torture criterion** (all-designed video with sound, zero web assets) is the ship gate.

**Architecture:** Cards are PIL frame sequences piped to ffmpeg (`cards/encoder.py`); no moviepy. Every card module exposes `frame_at(payload, t, duration) -> PIL.Image` (pure, unit-testable without ffmpeg) and `render(payload, duration, out_path, fade_in_s=0)` (encodes mp4). SHOTLIST is pure code (no LLM). VISUALS dispatches shot types to renderers; BROLL/PAPER_CARD resolve to their declared fallback cards in this plan (acquisition ladder is Plan 3) — never-blank holds by construction. AUDIO wraps `video_agent/voiceover.py` per beat (per-beat prosody + durations) and adds a `transcribe_words` extension to `video_agent/subtitles.py`. ASSEMBLE re-flows shot durations to real audio, re-renders cards, concats losslessly, burns ASS captions inside safe margins, muxes without `-shortest`, and asserts `video ≥ voice + 1.4s`.

**Tech Stack:** Python 3.11+, Pillow, numpy, pydub, ffmpeg/ffprobe (on PATH, verified), faster-whisper (via `video_agent/subtitles.py`), edge-tts/kokoro (via `video_agent/voiceover.py`), pytest.

## Global Constraints

- **No git.** Task steps end with test runs, never commits.
- **Workspace:** all engine code lives in `E:\Projects\HRSU Blog\_shorts_engine_impl` (`shorts_engine/` + `tests/shorts_engine/`). Run all commands from there unless a step says otherwise. `python`, not `python3`.
- **Real project root** (`E:\Projects\HRSU Blog`) holds `video_agent/`, `asset_library/`, `brand_facts.yaml`. `shorts_engine.config.PROJECT_ROOT` already resolves there.
- **Canvas:** 1080×1920 @ 30fps. **Safe margins: top 220px, bottom 420px, sides 72px** (spec §5 — do NOT use `video_agent/safezone.py`'s older 60/120/240 values).
- **Palette:** navy `#0a192f` → `#0a1428` gradient, gold `#d4af37`, text `#ccd6f6`, muted `#8892b0`. Fonts Playfair Display (headings) / Poppins (body) with a resolution ladder falling back to Georgia/Arial then PIL default — **no test may require brand fonts to be installed** (they are not on this machine).
- **Shot bounds:** target 2.0–3.5s, hard 1.8–4.5s; **LOGO_CTA is exempt from the 4.5s cap** (CTA beat is one shot of 6–8s + end-card hold; cap 10.0s). Total 35–50s.
- **Duration law:** final video length = final audio length + 1.5s end-card hold. `-shortest` is banned in the final video+audio mux. Assert rendered duration ≥ voice duration + 1.4s.
- **Never-blank:** every shot renders a designed card; VISUALS additionally samples a mid-shot frame and fails loudly if it has fewer than `MIN_CONTENT_PIXELS` bright pixels. There is no `degraded` state.
- **Never-unverified is already enforced** by Plan-1 SCRIPT gates; SHOTLIST's linter re-verifies STAT payload digits against the referenced fact's quote digits.
- **Transitions:** cut by default; the first shot of beats 2–5 gets a 0.25s fade-in rendered into the clip (this plan's deterministic reading of "0.25s fade at beat boundaries only"). Lossless demuxer concat — no xfade, so duration math stays exact.
- **Beat→prosody:** hook→`hook_emphasis`, stakes→`urgent_problem`, mechanism→`conversational`, proof→`matter_of_fact`, cta→`warm_cta` (presets already exist in `video_agent/voiceover.py`).
- **LLM:** unchanged Plan-1 rules — `gemma4:31b-cloud`, schema-validated, retry-with-echo, no silent fallback. SHOTLIST/AUDIO/VISUALS/ASSEMBLE make **zero** LLM calls.
- **Test command:** `python -m pytest tests/shorts_engine -q` green at the end of every task. Plan start baseline: 299 passed. Tasks that touch `video_agent/` (Task 9 only) must also run the root suite from `E:\Projects HRSU Blog`… (exact command in that task) with no regressions.
- All new modules start with `from __future__ import annotations` and use `logging.getLogger(__name__)`. Tests follow the repo's class-based `TestXxx` organization.

## Known Plan-1 divergences this plan fixes (Task 1)

1. `shorts_engine/config.py` sys.path hack inserts `PROJECT_ROOT.parent` (`E:\Projects`) — wrong; `video_agent` lives under `PROJECT_ROOT`. Consequence today: `video_agent` never imports, fallbacks silently used. Fix: insert `str(PROJECT_ROOT)`.
2. `PAPER_DOMAINS`/`STANDARD_DOMAINS` drifted from spec (news sites listed as "standards"). Fix to spec §4 Stage 1 lists; update the ingest/boundary tests that assert the old values.

---

### Task 1: Config fixes + Phase-3/4 constants + `cards/encoder.py`

**Files:**
- Modify: `shorts_engine/config.py`
- Create: `shorts_engine/cards/__init__.py`
- Create: `shorts_engine/cards/encoder.py`
- Modify: `tests/shorts_engine/test_ingest.py` (only the assertions naming old STANDARD/PAPER domains, if any)
- Test: `tests/shorts_engine/test_config_phase2.py`
- Test: `tests/shorts_engine/test_encoder.py`

**Interfaces:**
- Consumes: existing `config.PROJECT_ROOT`, `EngineError`.
- Produces (used by every later task): config constants `CANVAS_W=1080, CANVAS_H=1920, FPS=30, SAFE_TOP_PX=220, SAFE_BOTTOM_PX=420, SAFE_SIDE_PX=72, SHOT_MIN_S=1.8, SHOT_MAX_S=4.5, SHOT_TARGET_MIN_S=2.0, SHOT_TARGET_MAX_S=3.5, LOGO_CTA_MAX_S=10.0, TOTAL_MIN_S=35.0, TOTAL_MAX_S=50.0, END_CARD_HOLD_S=1.5, AUDIO_COMPLETENESS_MARGIN_S=1.4, AUDIO_DURATION_TOLERANCE=0.15, MIN_SEGMENT_BYTES=1024, AUDIO_BEAT_GAP_MS=300, TRANSITION_FADE_S=0.25, CARD_RERENDER_EPSILON_S=0.05, MIN_CONTENT_PIXELS=500, LUMA_CONTENT_THRESHOLD=140, BRAND_LOGO_FILE (Path), PROSODY_BY_BEAT (dict)`; brand color/font constants `BRAND_GOLD="#d4af37", BRAND_DARK_NAVY="#0a192f", BRAND_NAVY_2="#0a1428", BRAND_TEXT_LIGHT="#ccd6f6", BRAND_TEXT_MUTED="#8892b0"` (imported from `video_agent.config` when reachable, identical literals as fallback); corrected `PAPER_DOMAINS`/`STANDARD_DOMAINS`.
- Produces: `encoder.write_frames_to_mp4(frames: Iterable[PIL.Image], out_path: Path, fps: int = 30) -> int` (returns frame count, raises `EngineError` on ffmpeg failure) and `encoder.probe_duration(path: Path) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_config_phase2.py
from __future__ import annotations
from pathlib import Path


class TestPhase2Constants:
    def test_canvas_and_margins(self):
        from shorts_engine import config
        assert (config.CANVAS_W, config.CANVAS_H, config.FPS) == (1080, 1920, 30)
        assert config.SAFE_TOP_PX == 220
        assert config.SAFE_BOTTOM_PX == 420
        assert config.SAFE_SIDE_PX == 72

    def test_shot_bounds_and_duration_law(self):
        from shorts_engine import config
        assert (config.SHOT_MIN_S, config.SHOT_MAX_S) == (1.8, 4.5)
        assert (config.SHOT_TARGET_MIN_S, config.SHOT_TARGET_MAX_S) == (2.0, 3.5)
        assert config.LOGO_CTA_MAX_S == 10.0
        assert (config.TOTAL_MIN_S, config.TOTAL_MAX_S) == (35.0, 50.0)
        assert config.END_CARD_HOLD_S == 1.5
        assert config.AUDIO_COMPLETENESS_MARGIN_S == 1.4
        assert config.AUDIO_DURATION_TOLERANCE == 0.15
        assert config.MIN_SEGMENT_BYTES == 1024
        assert config.TRANSITION_FADE_S == 0.25

    def test_brand_colors(self):
        from shorts_engine import config
        assert config.BRAND_GOLD.lower() == "#d4af37"
        assert config.BRAND_DARK_NAVY.lower() == "#0a192f"
        assert config.BRAND_NAVY_2.lower() == "#0a1428"
        assert config.BRAND_TEXT_LIGHT.lower() == "#ccd6f6"

    def test_prosody_map_covers_all_beats(self):
        from shorts_engine import config
        assert set(config.PROSODY_BY_BEAT) == {"hook", "stakes", "mechanism", "proof", "cta"}
        assert config.PROSODY_BY_BEAT["cta"] == "warm_cta"

    def test_video_agent_import_actually_works(self):
        # Regression for the sys.path bug: PROJECT_ROOT (not its parent) must be
        # on sys.path so video_agent.config is importable.
        import sys
        from shorts_engine import config
        assert str(config.PROJECT_ROOT) in sys.path
        import video_agent.config as vac
        assert vac.SMART_TEXT_MODEL == config.SMART_TEXT_MODEL

    def test_domain_lists_match_spec(self):
        from shorts_engine import config
        for d in ("springer.com", "mdpi.com", "wiley.com", "arxiv.org", "doi.org",
                  "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com"):
            assert d in config.PAPER_DOMAINS, d
        for d in ("europa.eu", "epa.gov", "iso.org"):
            assert d in config.STANDARD_DOMAINS, d
        # news sites are NOT standards bodies
        assert "bbc.com" not in config.STANDARD_DOMAINS
        assert "forbes.com" not in config.STANDARD_DOMAINS

    def test_logo_file_points_at_asset_library(self):
        from shorts_engine import config
        assert config.BRAND_LOGO_FILE == config.PROJECT_ROOT / "asset_library" / "brand" / "Logo.png"
```

```python
# tests/shorts_engine/test_encoder.py
from __future__ import annotations
from pathlib import Path
import pytest
from PIL import Image


class TestEncoder:
    def test_write_frames_produces_probeable_mp4(self, tmp_path):
        from shorts_engine.cards import encoder
        frames = [Image.new("RGB", (1080, 1920), (10, 25, 47)) for _ in range(15)]
        out = tmp_path / "clip.mp4"
        n = encoder.write_frames_to_mp4(iter(frames), out, fps=30)
        assert n == 15
        assert out.exists() and out.stat().st_size > 1000
        dur = encoder.probe_duration(out)
        assert abs(dur - 0.5) < 0.15

    def test_empty_frames_raises(self, tmp_path):
        from shorts_engine.cards import encoder
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            encoder.write_frames_to_mp4(iter([]), tmp_path / "e.mp4")

    def test_probe_missing_file_raises(self, tmp_path):
        from shorts_engine.cards import encoder
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            encoder.probe_duration(tmp_path / "nope.mp4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_config_phase2.py tests/shorts_engine/test_encoder.py -v`
Expected: FAIL/ERROR (missing constants, missing `shorts_engine.cards.encoder`, sys.path assertion fails).

- [ ] **Step 3: Fix config.py and add constants**

In `shorts_engine/config.py`:

(a) Fix the sys.path insert (the try-block currently inserts `PROJECT_ROOT.parent`):

```python
    from sys import path as _sys_path
    if str(PROJECT_ROOT) not in _sys_path:
        _sys_path.insert(0, str(PROJECT_ROOT))
```

(b) Extend the `video_agent.config` import to also pull brand values, with identical fallbacks:

```python
    from video_agent.config import (
        SMART_TEXT_MODEL, OLLAMA_MODEL, SCRIPT_BANNED_PHRASES,
        BRAND_GOLD, BRAND_DARK_NAVY, BRAND_NAVY_2,
        BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
    )
```
and in the `except ImportError` branch add:
```python
    BRAND_GOLD = "#d4af37"
    BRAND_DARK_NAVY = "#0a192f"
    BRAND_NAVY_2 = "#0a1428"
    BRAND_TEXT_LIGHT = "#ccd6f6"
    BRAND_TEXT_MUTED = "#8892b0"
```

(c) Replace `PAPER_DOMAINS` and `STANDARD_DOMAINS` with the spec lists:

```python
# Citation classification (spec §4 Stage 1). "paper" = publisher/DOI/preprint
# domains or .pdf; "standard" = standards/regulatory bodies; else "web".
PAPER_DOMAINS = [
    "springer.com", "link.springer.com", "sciencedirect.com", "mdpi.com",
    "wiley.com", "onlinelibrary.wiley.com", "tandfonline.com", "nature.com",
    "acs.org", "pubs.acs.org", "rsc.org", "pubs.rsc.org",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "arxiv.org", "doi.org",
]
STANDARD_DOMAINS = ["europa.eu", "eur-lex.europa.eu", "epa.gov", "iso.org"]
```

(d) Append the Phase-3/4 block at the end (before `init_directories`):

```python
# ── Canvas & card design system (spec §5) ──────────────────────────────────
CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30
SAFE_TOP_PX = 220      # v3 margins — stricter than video_agent/safezone.py
SAFE_BOTTOM_PX = 420
SAFE_SIDE_PX = 72
BRAND_LOGO_FILE = PROJECT_ROOT / "asset_library" / "brand" / "Logo.png"

# ── Shotlist bounds (spec §4 Stage 4) ──────────────────────────────────────
SHOT_MIN_S = 1.8
SHOT_MAX_S = 4.5
SHOT_TARGET_MIN_S = 2.0
SHOT_TARGET_MAX_S = 3.5
LOGO_CTA_MAX_S = 10.0   # CTA beat is a single end-card shot; exempt from 4.5s
TOTAL_MIN_S = 35.0
TOTAL_MAX_S = 50.0

# ── Audio (spec §4 Stage 5) ────────────────────────────────────────────────
MIN_SEGMENT_BYTES = 1024          # F10 guard: no zero/near-zero-byte voice files
AUDIO_DURATION_TOLERANCE = 0.15   # actual voice vs script estimate ±15%
AUDIO_BEAT_GAP_MS = 300           # silence between beats in the stitched track
PROSODY_BY_BEAT = {
    "hook": "hook_emphasis", "stakes": "urgent_problem",
    "mechanism": "conversational", "proof": "matter_of_fact", "cta": "warm_cta",
}

# ── Assemble (spec §4 Stage 7) ─────────────────────────────────────────────
END_CARD_HOLD_S = 1.5
AUDIO_COMPLETENESS_MARGIN_S = 1.4  # rendered duration >= voice + this
TRANSITION_FADE_S = 0.25           # fade-in on first shot of beats 2..5
CARD_RERENDER_EPSILON_S = 0.05     # re-render card if re-flow moved it more

# ── Never-blank content check (VISUALS) ────────────────────────────────────
MIN_CONTENT_PIXELS = 500
LUMA_CONTENT_THRESHOLD = 140
```

(e) Create `shorts_engine/cards/__init__.py`:

```python
"""Branded card renderers — PIL frame sequences piped to ffmpeg (no moviepy)."""
```

(f) Create `shorts_engine/cards/encoder.py`:

```python
"""Frames→mp4 via ffmpeg rawvideo pipe, plus ffprobe duration helper."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Iterable

from PIL import Image

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def write_frames_to_mp4(frames: Iterable[Image.Image], out_path: Path,
                        fps: int = config.FPS) -> int:
    """Pipe RGB frames (must all be CANVAS_W×CANVAS_H) into libx264. Returns
    the frame count. Raises EngineError on zero frames or encoder failure."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{config.CANVAS_W}x{config.CANVAS_H}", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    try:
        for img in frames:
            if img.size != (config.CANVAS_W, config.CANVAS_H):
                raise EngineError(f"frame {n} is {img.size}, expected "
                                  f"{(config.CANVAS_W, config.CANVAS_H)}")
            if img.mode != "RGB":
                img = img.convert("RGB")
            proc.stdin.write(img.tobytes())
            n += 1
    finally:
        proc.stdin.close()
        proc.wait()
    if n == 0:
        out_path.unlink(missing_ok=True)
        raise EngineError("write_frames_to_mp4: no frames supplied")
    if proc.returncode != 0 or not out_path.exists():
        raise EngineError(f"ffmpeg encode failed (rc={proc.returncode}) for {out_path}")
    return n


def probe_duration(path: Path) -> float:
    """Container duration in seconds via ffprobe. EngineError if unreadable."""
    path = Path(path)
    if not path.exists():
        raise EngineError(f"probe_duration: missing file {path}")
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(res.stdout.strip())
    except ValueError as e:
        raise EngineError(f"probe_duration: unparseable ffprobe output for {path}: "
                          f"{res.stdout!r} / {res.stderr!r}") from e
```

- [ ] **Step 4: Fix any ingest/boundary tests asserting the old domain lists**

Run: `python -m pytest tests/shorts_engine -q`. If failures name domains (e.g., a test asserting `wikipedia.org` classifies as `standard` or `researchgate.net` as paper), update those test expectations to the spec lists — e.g. classification tests should use `link.springer.com` / `mdpi.com` for `paper` and `epa.gov` / `europa.eu` for `standard`. Do not weaken any other assertion.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: all pass (299 baseline + 10 new, minus nothing).

---

### Task 2: `cards/theme.py` — design system core

**Files:**
- Create: `shorts_engine/cards/theme.py`
- Test: `tests/shorts_engine/test_theme.py`

**Interfaces:**
- Consumes: `config` constants from Task 1; `encoder.write_frames_to_mp4`.
- Produces (used by every card renderer):
  - `hex_to_rgb(s: str) -> tuple[int, int, int]`
  - `GOLD, NAVY, NAVY2, TEXT, MUTED: tuple[int,int,int]` (module-level RGB)
  - `resolve_font(kind: str, size: int)` → `ImageFont` (`kind ∈ {"heading","body"}`; ladder: `asset_library/fonts/` → `C:/Windows/Fonts` stand-ins → PIL default; cached)
  - `ease_out_cubic(p: float) -> float`
  - `background(t: float) -> PIL.Image` (1080×1920 gradient + drift + grain)
  - `fade_rise(t: float, index: int, rise_px: int = 26) -> tuple[float, int]` → `(alpha 0..1, dy)` with 300ms anim, 80ms stagger
  - `draw_citation_chip(img: Image, text: str) -> None` (gold pill, bottom-left, inside safe zone)
  - `fit_text(draw, text: str, kind: str, max_w: int, max_size: int, min_size: int = 28, max_lines: int = 4)` → `(font, lines: list[str], size: int)`
  - `paste_text_block(img, lines, font, y_top, color, align="center") -> int` (returns block height)
  - `render_card(frame_fn, payload: dict, duration: float, out_path: Path, fade_in_s: float = 0.0) -> Path` — shared encode loop all cards use.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_theme.py
from __future__ import annotations
import numpy as np
from PIL import Image, ImageDraw


class TestPalette:
    def test_hex_to_rgb(self):
        from shorts_engine.cards import theme
        assert theme.hex_to_rgb("#d4af37") == (212, 175, 55)
        assert theme.GOLD == (212, 175, 55)
        assert theme.NAVY == (10, 25, 47)


class TestFonts:
    def test_resolve_font_never_raises_and_caches(self):
        from shorts_engine.cards import theme
        f1 = theme.resolve_font("heading", 80)
        f2 = theme.resolve_font("heading", 80)
        assert f1 is f2  # cached
        assert theme.resolve_font("body", 40) is not None

    def test_unknown_kind_raises(self):
        import pytest
        from shorts_engine.cards import theme
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            theme.resolve_font("comic", 40)


class TestBackground:
    def test_size_and_navyish(self):
        from shorts_engine.cards import theme
        img = theme.background(0.0)
        assert img.size == (1080, 1920)
        arr = np.asarray(img)
        assert arr.mean() < 40  # dark navy overall

    def test_gradient_drifts_over_time(self):
        from shorts_engine.cards import theme
        a = np.asarray(theme.background(0.0)).astype(int)
        b = np.asarray(theme.background(2.0)).astype(int)
        assert np.abs(a - b).sum() > 0


class TestMotion:
    def test_fade_rise_stagger(self):
        from shorts_engine.cards import theme
        a0, dy0 = theme.fade_rise(0.0, 0)
        assert a0 == 0.0 and dy0 > 0
        a_done, dy_done = theme.fade_rise(1.0, 0)
        assert a_done == 1.0 and dy_done == 0
        # element 1 starts 80ms later: at t=0.30 element 0 is done, element 1 is not
        assert theme.fade_rise(0.30, 0)[0] == 1.0
        assert theme.fade_rise(0.30, 1)[0] < 1.0

    def test_ease_monotonic(self):
        from shorts_engine.cards import theme
        vals = [theme.ease_out_cubic(p / 10) for p in range(11)]
        assert vals == sorted(vals) and vals[0] == 0.0 and vals[-1] == 1.0


class TestChipAndText:
    def test_citation_chip_inside_safe_zone(self):
        from shorts_engine.cards import theme
        from shorts_engine import config
        img = theme.background(0.0)
        before = np.asarray(img).copy()
        theme.draw_citation_chip(img, "Source [1] — mdpi.com")
        arr = np.asarray(img)
        diff = np.argwhere((arr.astype(int) - before.astype(int)).sum(axis=2) != 0)
        assert len(diff) > 50  # something drew
        ys, xs = diff[:, 0], diff[:, 1]
        assert xs.min() >= config.SAFE_SIDE_PX
        assert ys.max() <= config.CANVAS_H - config.SAFE_BOTTOM_PX
        assert ys.min() >= config.CANVAS_H - config.SAFE_BOTTOM_PX - 200

    def test_fit_text_shrinks_and_wraps(self):
        from shorts_engine.cards import theme
        img = Image.new("RGB", (1080, 1920))
        d = ImageDraw.Draw(img)
        long = "calcium nitrate dosing keeps European effluent inside directive limits"
        font, lines, size = theme.fit_text(d, long, "heading", max_w=936, max_size=110)
        assert 1 <= len(lines) <= 4
        assert all(d.textlength(l, font=font) <= 936 for l in lines)
        short_font, short_lines, short_size = theme.fit_text(d, "Hi", "heading", 936, 110)
        assert short_size >= size


class TestRenderCard:
    def test_render_card_encodes_and_fades_in(self, tmp_path):
        from shorts_engine.cards import theme, encoder
        def frame_fn(payload, t, duration):
            img = theme.background(t)
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle([300, 800, 780, 1100], fill=theme.TEXT)
            return img
        out = tmp_path / "c.mp4"
        theme.render_card(frame_fn, {}, 0.6, out, fade_in_s=0.25)
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
        # fade-in: first frame darker than a late frame
        first = theme.render_frame_with_fade(frame_fn, {}, 0.0, 0.6, 0.25)
        late = theme.render_frame_with_fade(frame_fn, {}, 0.5, 0.6, 0.25)
        assert np.asarray(first).mean() < np.asarray(late).mean() * 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_theme.py -v`
Expected: ERROR `No module named 'shorts_engine.cards.theme'`.

- [ ] **Step 3: Implement `shorts_engine/cards/theme.py`**

```python
"""Brand design system: palette, fonts, background, motion, chip, text fitting.

Cards call `render_card(frame_fn, payload, duration, out_path, fade_in_s)`;
`frame_fn(payload, t, duration) -> PIL.Image` must be pure so tests can assert
on single frames without ffmpeg.
"""
from __future__ import annotations

import functools
import logging
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from shorts_engine import config
from shorts_engine.cards import encoder
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


GOLD = hex_to_rgb(config.BRAND_GOLD)
NAVY = hex_to_rgb(config.BRAND_DARK_NAVY)
NAVY2 = hex_to_rgb(config.BRAND_NAVY_2)
TEXT = hex_to_rgb(config.BRAND_TEXT_LIGHT)
MUTED = hex_to_rgb(config.BRAND_TEXT_MUTED)

# Font ladder: brand ttf dropped into asset_library/fonts/ wins; otherwise
# Windows serif/sans stand-ins; otherwise PIL default (tests still pass).
_FONT_CANDIDATES = {
    "heading": ["PlayfairDisplay-Bold.ttf", "PlayfairDisplay-SemiBold.ttf",
                "georgiab.ttf", "georgia.ttf", "timesbd.ttf"],
    "body": ["Poppins-SemiBold.ttf", "Poppins-Medium.ttf", "Poppins-Regular.ttf",
             "arialbd.ttf", "arial.ttf", "segoeuib.ttf"],
}
_FONT_DIRS = [config.PROJECT_ROOT / "asset_library" / "fonts",
              Path("C:/Windows/Fonts")]


@functools.lru_cache(maxsize=64)
def resolve_font(kind: str, size: int):
    from PIL import ImageFont
    if kind not in _FONT_CANDIDATES:
        raise EngineError(f"unknown font kind {kind!r}")
    for d in _FONT_DIRS:
        for name in _FONT_CANDIDATES[kind]:
            p = d / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:  # corrupt font file — try next
                    continue
    logger.warning("no truetype font found for %s — using PIL default", kind)
    return ImageFont.load_default()


def ease_out_cubic(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


# Deterministic film grain (≈2%), rolled per frame for cheap variation.
_GRAIN = np.random.default_rng(42).normal(0.0, 5.0, (config.CANVAS_H, config.CANVAS_W, 1))


def background(t: float) -> Image.Image:
    """Vertical navy gradient with an 8s midpoint drift loop + film grain."""
    top = np.array(NAVY, dtype=float)
    bot = np.array(NAVY2, dtype=float)
    mid = 0.5 + 0.12 * math.sin(2 * math.pi * t / 8.0)
    ys = np.linspace(0.0, 1.0, config.CANVAS_H)[:, None, None]
    m = np.clip(ys / (2 * mid), 0.0, 1.0)
    arr = top * (1 - m) + bot * m
    arr = arr + np.roll(_GRAIN, int(t * config.FPS) % config.CANVAS_H, axis=0)
    arr = np.broadcast_to(arr, (config.CANVAS_H, config.CANVAS_W, 3)).copy() \
        if arr.shape[1] == 1 else arr
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def fade_rise(t: float, index: int, rise_px: int = 26) -> tuple[float, int]:
    """300ms fade+rise, staggered 80ms per element index."""
    start = 0.08 * index
    p = ease_out_cubic((t - start) / 0.30)
    return p, int(round(rise_px * (1 - p)))


def draw_citation_chip(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    f = resolve_font("body", 30)
    pad = 16
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x0 = config.SAFE_SIDE_PX
    y1 = config.CANVAS_H - config.SAFE_BOTTOM_PX - 16
    y0 = y1 - h - 2 * pad
    d.rounded_rectangle([x0, y0, x0 + w + 2 * pad, y1],
                        radius=(h + 2 * pad) // 2, outline=GOLD, width=2)
    d.text((x0 + pad, y0 + pad - bbox[1]), text, font=f, fill=GOLD)


def fit_text(draw: ImageDraw.ImageDraw, text: str, kind: str, max_w: int,
             max_size: int, min_size: int = 28, max_lines: int = 4):
    """Largest size at which `text` wraps into ≤max_lines lines of ≤max_w px."""
    lines: list[str] = [text]
    for size in range(max_size, min_size - 1, -4):
        f = resolve_font(kind, size)
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=f) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(
                draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, size
    return resolve_font(kind, min_size), lines, min_size


def paste_text_block(img: Image.Image, lines: list[str], font, y_top: int,
                     color: tuple[int, int, int], align: str = "center") -> int:
    d = ImageDraw.Draw(img)
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (24, 8)
    line_h = int((ascent + descent) * 1.18)
    y = y_top
    for line in lines:
        w = d.textlength(line, font=font)
        x = (config.CANVAS_W - w) // 2 if align == "center" else config.SAFE_SIDE_PX
        d.text((x, y), line, font=font, fill=color)
        y += line_h
    return y - y_top


def render_frame_with_fade(frame_fn, payload: dict, t: float, duration: float,
                           fade_in_s: float) -> Image.Image:
    img = frame_fn(payload, t, duration)
    if fade_in_s > 0 and t < fade_in_s:
        img = ImageEnhance.Brightness(img).enhance(ease_out_cubic(t / fade_in_s))
    return img


def render_card(frame_fn, payload: dict, duration: float, out_path: Path,
                fade_in_s: float = 0.0) -> Path:
    n = max(1, round(duration * config.FPS))
    frames = (render_frame_with_fade(frame_fn, payload, i / config.FPS,
                                     duration, fade_in_s) for i in range(n))
    encoder.write_frames_to_mp4(frames, Path(out_path))
    return Path(out_path)
```

Note on `background`: `_GRAIN` has shape `(H, W, 1)`; `top*(1-m)+bot*m` broadcasts to `(H, 1, 3)` — adding grain broadcasts to `(H, W, 3)` already, so the defensive `broadcast_to` line only guards the degenerate case. If the implementation ends up always producing `(H, W, 3)`, drop the guard line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_theme.py -v`
Expected: all pass.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q` — all green.

---

### Task 3: `cards/headline_card.py`

**Files:**
- Create: `shorts_engine/cards/headline_card.py`
- Test: `tests/shorts_engine/test_headline_card.py`

**Interfaces:**
- Consumes: `theme` (Task 2).
- Produces (used by VISUALS Task 10): `frame_at(payload: dict, t: float, duration: float) -> Image` and `render(payload: dict, duration: float, out_path: Path, fade_in_s: float = 0.0) -> Path`. Payload: `{"text": str, "accent": str (optional — word to color gold; default = first numeric token else longest word)}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_headline_card.py
from __future__ import annotations
import numpy as np
from shorts_engine import config

PAYLOAD = {"text": "EU nitrate limits are tightening", "accent": "tightening"}


def _content_pixels(img, bg):
    a = np.asarray(img).astype(int)
    b = np.asarray(bg).astype(int)
    return np.argwhere(np.abs(a - b).sum(axis=2) > 30)


class TestHeadlineFrames:
    def test_text_drawn_inside_safe_zone(self):
        from shorts_engine.cards import headline_card, theme
        img = headline_card.frame_at(PAYLOAD, 2.0, 3.0)
        diff = _content_pixels(img, theme.background(2.0))
        assert len(diff) > 200
        ys, xs = diff[:, 0], diff[:, 1]
        assert xs.min() >= config.SAFE_SIDE_PX - 2
        assert xs.max() <= config.CANVAS_W - config.SAFE_SIDE_PX + 2
        assert ys.min() >= config.SAFE_TOP_PX - 2
        assert ys.max() <= config.CANVAS_H - config.SAFE_BOTTOM_PX + 2

    def test_accent_word_is_gold(self):
        from shorts_engine.cards import headline_card, theme
        img = headline_card.frame_at(PAYLOAD, 2.0, 3.0)
        arr = np.asarray(img).astype(int)
        gold = np.array(theme.GOLD)
        near_gold = (np.abs(arr - gold).sum(axis=2) < 90).sum()
        assert near_gold > 50

    def test_default_accent_prefers_numeric(self):
        from shorts_engine.cards import headline_card
        assert headline_card.pick_accent("dosing at 1.5 kg per cubic meter") == "1.5"
        assert headline_card.pick_accent("nitrate compliance window") == "compliance"

    def test_animation_reveals_over_time(self):
        from shorts_engine.cards import headline_card, theme
        early = _content_pixels(headline_card.frame_at(PAYLOAD, 0.02, 3.0),
                                theme.background(0.02))
        late = _content_pixels(headline_card.frame_at(PAYLOAD, 1.0, 3.0),
                               theme.background(1.0))
        assert len(late) > len(early)


class TestHeadlineRender:
    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import headline_card, encoder
        out = headline_card.render(PAYLOAD, 0.6, tmp_path / "h.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_headline_card.py -v` → ERROR (module missing).

- [ ] **Step 3: Implement `shorts_engine/cards/headline_card.py`**

```python
"""HEADLINE_CARD: big Playfair statement, one gold accent word, fade+rise."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def pick_accent(text: str) -> str:
    m = _NUM.search(text)
    if m:
        return m.group(0)
    words = [w.strip(".,;:!?") for w in text.split()]
    return max(words, key=len) if words else ""


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    text = payload["text"].strip()
    accent = (payload.get("accent") or pick_accent(text)).lower().strip(".,;:!?")
    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    font, lines, _ = theme.fit_text(d, text, "heading", max_w, max_size=104)
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (24, 8)
    line_h = int((ascent + descent) * 1.18)
    block_h = line_h * len(lines)
    y0 = max(config.SAFE_TOP_PX,
             (config.CANVAS_H - block_h) // 2 - 120)
    for i, line in enumerate(lines):
        alpha, dy = theme.fade_rise(t, i)
        if alpha <= 0:
            continue
        # draw word-by-word so the accent word can be gold
        total_w = d.textlength(line, font=font)
        x = (config.CANVAS_W - total_w) // 2
        y = y0 + i * line_h + dy
        for word in line.split(" "):
            color = theme.GOLD if word.lower().strip(".,;:!?") == accent else theme.TEXT
            if alpha < 1.0:
                color = tuple(int(c * alpha + bg * (1 - alpha))
                              for c, bg in zip(color, theme.NAVY))
            d.text((x, y), word, font=font, fill=color)
            x += d.textlength(word + " ", font=font)
    # gold underline sweep beneath the block after text lands
    p = theme.ease_out_cubic((t - 0.35) / 0.5)
    if p > 0:
        w = int(220 * p)
        cy = y0 + block_h + 28
        d.rectangle([(config.CANVAS_W - w) // 2, cy,
                     (config.CANVAS_W + w) // 2, cy + 6], fill=theme.GOLD)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_headline_card.py -v` → all pass.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q` — all green.

---

### Task 4: `cards/stat_card.py`

**Files:**
- Create: `shorts_engine/cards/stat_card.py`
- Test: `tests/shorts_engine/test_stat_card.py`

**Interfaces:**
- Consumes: `theme`.
- Produces: `frame_at/render` (same contract as Task 3). Payload: `{"value": str, "unit": str, "label": str, "citation": str (optional chip text, e.g. "Source [1] — mdpi.com")}`. Behavior: if `value` parses as a single float (`"150"`, `"2.5"`) → 800ms count-up with the same decimal places; otherwise (ranges like `"1.5–3"`) → fade+rise, shown verbatim. Gold underline sweep under the value over the first 800ms.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_stat_card.py
from __future__ import annotations
import numpy as np

RANGE_PAYLOAD = {"value": "1.5–3", "unit": "kg/m³", "label": "typical dosing window",
                 "citation": "Source [1] — mdpi.com"}
SCALAR_PAYLOAD = {"value": "150", "unit": "mg/L", "label": "limit"}


class TestCountUp:
    def test_format_value_keeps_decimals(self):
        from shorts_engine.cards import stat_card
        assert stat_card.format_value(1.5, "2.5") == "1.5"
        assert stat_card.format_value(120.0, "150") == "120"

    def test_scalar_counts_up(self):
        from shorts_engine.cards import stat_card
        assert stat_card.display_value(SCALAR_PAYLOAD["value"], 0.2) != "150"
        assert stat_card.display_value(SCALAR_PAYLOAD["value"], 1.2) == "150"

    def test_range_shown_verbatim_always(self):
        from shorts_engine.cards import stat_card
        assert stat_card.display_value("1.5–3", 0.1) == "1.5–3"
        assert stat_card.display_value("1.5–3", 2.0) == "1.5–3"


class TestStatFrames:
    def test_value_unit_label_chip_present_late(self):
        from shorts_engine.cards import stat_card, theme
        img = stat_card.frame_at(RANGE_PAYLOAD, 2.0, 4.0)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 500_000  # large value + label + chip drawn

    def test_underline_sweep_grows(self):
        from shorts_engine.cards import stat_card, theme
        def gold_count(t):
            img = stat_card.frame_at(SCALAR_PAYLOAD, t, 4.0)
            arr = np.asarray(img).astype(int)
            return (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum()
        assert gold_count(1.0) > gold_count(0.15)

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import stat_card, encoder
        out = stat_card.render(RANGE_PAYLOAD, 0.6, tmp_path / "s.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/cards/stat_card.py`**

```python
"""STAT_CARD: huge number (count-up when scalar), unit, label, citation chip."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

COUNT_UP_S = 0.8


def _as_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def format_value(current: float, template: str) -> str:
    decimals = len(template.split(".")[1]) if "." in template else 0
    return f"{current:.{decimals}f}"


def display_value(value: str, t: float) -> str:
    """Scalar values count up over COUNT_UP_S; ranges/text render verbatim."""
    target = _as_float(value)
    if target is None:
        return value
    p = theme.ease_out_cubic(t / COUNT_UP_S)
    return format_value(target * p, value)


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    value = display_value(str(payload["value"]), t)
    unit = str(payload.get("unit") or "")
    label = str(payload.get("label") or "")

    vfont = theme.resolve_font("body", 190)
    ufont = theme.resolve_font("body", 56)
    v_w = d.textlength(value, font=vfont)
    u_w = d.textlength(" " + unit, font=ufont) if unit else 0
    x0 = (config.CANVAS_W - (v_w + u_w)) // 2
    y_val = 700
    alpha, dy = theme.fade_rise(t, 0)
    if alpha > 0:
        col = tuple(int(c * alpha + n * (1 - alpha))
                    for c, n in zip(theme.TEXT, theme.NAVY))
        d.text((x0, y_val + dy), value, font=vfont, fill=col)
        if unit:
            d.text((x0 + v_w, y_val + dy + 110), " " + unit, font=ufont,
                   fill=theme.MUTED)

    # gold underline sweep under the value
    p = theme.ease_out_cubic(t / COUNT_UP_S)
    if p > 0:
        w = int(max(v_w, 200) * p)
        cy = y_val + 250
        d.rectangle([(config.CANVAS_W - w) // 2, cy,
                     (config.CANVAS_W + w) // 2, cy + 8], fill=theme.GOLD)

    if label:
        max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        lfont, lines, _ = theme.fit_text(d, label, "heading", max_w, max_size=64)
        la, ldy = theme.fade_rise(t, 2)
        if la > 0:
            col = tuple(int(c * la + n * (1 - la))
                        for c, n in zip(theme.TEXT, theme.NAVY))
            theme.paste_text_block(img, lines, lfont, y_val + 310 + ldy, col)

    chip = payload.get("citation")
    if chip:
        theme.draw_citation_chip(img, chip)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_stat_card.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 5: `cards/diagram_card.py` — 4 templates

**Files:**
- Create: `shorts_engine/cards/diagram_card.py`
- Test: `tests/shorts_engine/test_diagram_card.py`

**Interfaces:**
- Consumes: `theme`.
- Produces: `frame_at/render`. Payload by template:
  - `{"template": "flow", "labels": ["Effluent", "Ca(NO₃)₂ dosing", "Denitrifying filter", "Clear discharge"], "reveal_stage": 1, "reveal_total": 3}` — 2–4 nodes as rounded boxes stacked vertically with gold arrows; `reveal_stage k of m` shows the first `ceil(n·k/m)` nodes; the newest node animates in, earlier ones are static (progressive build across consecutive shots).
  - `{"template": "before_after", "before": ["…", "…"], "after": ["…", "…"]}` — two panels (top muted "BEFORE", bottom gold-tinted "AFTER").
  - `{"template": "comparison", "left": {"title": str, "items": [str]}, "right": {...}}` — two columns.
  - `{"template": "dosing_scale", "lo": "1.5", "hi": "3", "min": "0", "max": "5", "unit": "kg/m³", "label": str}` — horizontal scale bar with a gold band from lo→hi.
  - Unknown template → `EngineError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_diagram_card.py
from __future__ import annotations
import numpy as np
import pytest

FLOW = {"template": "flow",
        "labels": ["Effluent in", "Calcium nitrate dosing", "Denitrifying filter",
                   "Clear discharge"],
        "reveal_stage": 3, "reveal_total": 3}


def _diff(img, bg):
    return np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()


class TestFlow:
    def test_all_nodes_visible_at_final_stage(self):
        from shorts_engine.cards import diagram_card, theme
        img = diagram_card.frame_at(FLOW, 2.5, 3.0)
        assert _diff(img, theme.background(2.5)) > 400_000

    def test_reveal_stage_gates_node_count(self):
        from shorts_engine.cards import diagram_card
        assert diagram_card.visible_nodes(4, stage=1, total=3) == 2  # ceil(4/3)
        assert diagram_card.visible_nodes(4, stage=2, total=3) == 3
        assert diagram_card.visible_nodes(4, stage=3, total=3) == 4

    def test_stage1_draws_less_than_stage3(self):
        from shorts_engine.cards import diagram_card, theme
        s1 = dict(FLOW, reveal_stage=1)
        a = _diff(diagram_card.frame_at(s1, 2.5, 3.0), theme.background(2.5))
        b = _diff(diagram_card.frame_at(FLOW, 2.5, 3.0), theme.background(2.5))
        assert b > a

    def test_flow_needs_2_to_4_labels(self):
        from shorts_engine.cards import diagram_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            diagram_card.frame_at({"template": "flow", "labels": ["only"]}, 0.5, 3.0)


class TestOtherTemplates:
    def test_before_after_renders(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "before_after", "before": ["High nitrate load"],
             "after": ["Compliant discharge"]}
        assert _diff(diagram_card.frame_at(p, 2.0, 3.0), theme.background(2.0)) > 200_000

    def test_comparison_renders(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "comparison",
             "left": {"title": "Granular", "items": ["slow dissolve"]},
             "right": {"title": "Powder", "items": ["fast dissolve"]}}
        assert _diff(diagram_card.frame_at(p, 2.0, 3.0), theme.background(2.0)) > 200_000

    def test_dosing_scale_band_is_gold(self):
        from shorts_engine.cards import diagram_card, theme
        p = {"template": "dosing_scale", "lo": "1.5", "hi": "3", "min": "0",
             "max": "5", "unit": "kg/m³", "label": "dosing window"}
        img = diagram_card.frame_at(p, 2.0, 3.0)
        arr = np.asarray(img).astype(int)
        assert (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum() > 500

    def test_unknown_template_raises(self):
        from shorts_engine.cards import diagram_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            diagram_card.frame_at({"template": "pie"}, 0.5, 3.0)

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import diagram_card, encoder
        out = diagram_card.render(FLOW, 0.6, tmp_path / "d.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/cards/diagram_card.py`**

```python
"""DIAGRAM card: flow / before_after / comparison / dosing_scale templates."""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_X0 = config.SAFE_SIDE_PX
_X1 = config.CANVAS_W - config.SAFE_SIDE_PX
_MAXW = _X1 - _X0


def visible_nodes(n: int, stage: int, total: int) -> int:
    stage = max(1, min(stage, total))
    return math.ceil(n * stage / total)


def _node_box(d: ImageDraw.ImageDraw, img, label: str, y: int, h: int,
              alpha: float, accent: bool) -> None:
    col = theme.GOLD if accent else theme.TEXT
    col = tuple(int(c * alpha + n * (1 - alpha)) for c, n in zip(col, theme.NAVY))
    d.rounded_rectangle([_X0 + 40, y, _X1 - 40, y + h], radius=22,
                        outline=col, width=3)
    f, lines, _ = theme.fit_text(d, label, "body", _MAXW - 160, max_size=46,
                                 max_lines=2)
    ty = y + (h - len(lines) * 52) // 2
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((config.CANVAS_W - w) // 2, ty), line, font=f, fill=col)
        ty += 52


def _flow(img, d, payload, t):
    labels = payload.get("labels") or []
    if not 2 <= len(labels) <= 4:
        raise EngineError(f"flow diagram needs 2–4 labels, got {len(labels)}")
    stage = int(payload.get("reveal_stage", 1))
    total = int(payload.get("reveal_total", 1))
    n_show = visible_nodes(len(labels), stage, total)
    n_prev = visible_nodes(len(labels), stage - 1, total) if stage > 1 else 0
    box_h, gap = 150, 96
    block = len(labels) * box_h + (len(labels) - 1) * gap
    y = max(config.SAFE_TOP_PX + 40, (config.CANVAS_H - block) // 2 - 60)
    for i, label in enumerate(labels[:n_show]):
        if i < n_prev:
            alpha = 1.0  # carried over from earlier shot — static
        else:
            alpha, _ = theme.fade_rise(t, i - n_prev)
        _node_box(d, img, label, y, box_h, max(alpha, 0.0), accent=(i == len(labels) - 1))
        if i < n_show - 1:
            ay = y + box_h + gap // 2
            acol = tuple(int(c * alpha) for c in theme.GOLD)
            d.polygon([(540 - 16, ay - 12), (540 + 16, ay - 12), (540, ay + 18)],
                      fill=acol)
        y += box_h + gap


def _panel(img, d, title, items, y0, y1, accent):
    col = theme.GOLD if accent else theme.MUTED
    d.rounded_rectangle([_X0, y0, _X1, y1], radius=24, outline=col, width=3)
    f = theme.resolve_font("body", 40)
    d.text((_X0 + 36, y0 + 24), title.upper(), font=f, fill=col)
    body = theme.resolve_font("body", 44)
    ty = y0 + 100
    for it in items:
        d.text((_X0 + 36, ty), f"• {it}", font=body, fill=theme.TEXT)
        ty += 62


def _before_after(img, d, payload, t):
    mid = config.CANVAS_H // 2
    _panel(img, d, "Before", payload.get("before") or [], config.SAFE_TOP_PX + 60,
           mid - 40, accent=False)
    _panel(img, d, "After", payload.get("after") or [], mid + 40,
           config.CANVAS_H - config.SAFE_BOTTOM_PX - 60, accent=True)


def _comparison(img, d, payload, t):
    left, right = payload.get("left") or {}, payload.get("right") or {}
    midx = config.CANVAS_W // 2
    for side, x0, x1, accent in ((left, _X0, midx - 20, False),
                                 (right, midx + 20, _X1, True)):
        col = theme.GOLD if accent else theme.MUTED
        d.rounded_rectangle([x0, 500, x1, 1300], radius=24, outline=col, width=3)
        f = theme.resolve_font("body", 42)
        d.text((x0 + 28, 530), str(side.get("title", "")).upper(), font=f, fill=col)
        body = theme.resolve_font("body", 36)
        ty = 620
        for it in side.get("items", []):
            d.text((x0 + 28, ty), f"• {it}", font=body, fill=theme.TEXT)
            ty += 54


def _dosing_scale(img, d, payload, t):
    lo, hi = float(payload["lo"]), float(payload["hi"])
    mn, mx = float(payload.get("min", 0)), float(payload.get("max", max(hi * 1.5, hi + 1)))
    y = 980
    d.rectangle([_X0, y, _X1, y + 14], fill=theme.MUTED)
    span = mx - mn or 1.0
    bx0 = _X0 + int((_X1 - _X0) * (lo - mn) / span)
    bx1 = _X0 + int((_X1 - _X0) * (hi - mn) / span)
    p = theme.ease_out_cubic(t / 0.8)
    bx1p = bx0 + int((bx1 - bx0) * p)
    d.rectangle([bx0, y - 10, max(bx0 + 4, bx1p), y + 24], fill=theme.GOLD)
    f = theme.resolve_font("body", 44)
    d.text((bx0 - 20, y - 80), str(payload["lo"]), font=f, fill=theme.GOLD)
    d.text((bx1 - 20, y - 80), str(payload["hi"]), font=f, fill=theme.GOLD)
    d.text((_X1 - 140, y + 40), str(payload.get("unit", "")), font=f, fill=theme.MUTED)
    label = payload.get("label")
    if label:
        lf, lines, _ = theme.fit_text(d, label, "heading", _MAXW, max_size=60)
        theme.paste_text_block(img, lines, lf, 620, theme.TEXT)


_TEMPLATES = {"flow": _flow, "before_after": _before_after,
              "comparison": _comparison, "dosing_scale": _dosing_scale}


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    template = payload.get("template")
    fn = _TEMPLATES.get(template)
    if fn is None:
        raise EngineError(f"unknown diagram template {template!r}")
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    fn(img, d, payload, t)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_diagram_card.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 6: `cards/quote_card.py` + `cards/logo_cta_card.py`

**Files:**
- Create: `shorts_engine/cards/quote_card.py`
- Create: `shorts_engine/cards/logo_cta_card.py`
- Test: `tests/shorts_engine/test_quote_logo_cards.py`

**Interfaces:**
- Consumes: `theme`; `config.BRAND_LOGO_FILE`.
- Produces: both modules expose `frame_at/render`.
  - quote payload: `{"quote": str, "source": str (chip text, optional)}`; `trim_quote(q: str, limit: int = 120) -> str` trims at a word boundary and appends `…` when trimmed.
  - logo_cta payload: `{"differentiator": str, "cta_line": str, "domain": str}`; if `BRAND_LOGO_FILE` missing, a gold "HRSU" wordmark renders instead (never blank).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_quote_logo_cards.py
from __future__ import annotations
import numpy as np


class TestQuoteCard:
    def test_trim_quote_at_word_boundary(self):
        from shorts_engine.cards import quote_card
        q = "x" * 50 + " " + "y" * 100
        out = quote_card.trim_quote(q, limit=120)
        assert len(out) <= 121 and out.endswith("…")
        assert quote_card.trim_quote("short quote") == "short quote"

    def test_frame_has_quote_and_chip(self):
        from shorts_engine.cards import quote_card, theme
        p = {"quote": "the optimal dosage range of 1.5 to 3 kg per cubic meter",
             "source": "Source [2] — springer.com"}
        img = quote_card.frame_at(p, 2.0, 3.5)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 300_000

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import quote_card, encoder
        out = quote_card.render({"quote": "q"}, 0.6, tmp_path / "q.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15


class TestLogoCta:
    PAYLOAD = {"differentiator": "Consistent high-purity calcium nitrate powder",
               "cta_line": "Full technical guide on the HRSU blog",
               "domain": "hrsuindore.com"}

    def test_frame_has_content_and_gold_domain(self):
        from shorts_engine.cards import logo_cta_card, theme
        img = logo_cta_card.frame_at(self.PAYLOAD, 2.0, 7.0)
        arr = np.asarray(img).astype(int)
        assert (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum() > 300

    def test_wordmark_fallback_when_logo_missing(self, monkeypatch, tmp_path):
        from shorts_engine.cards import logo_cta_card, theme
        monkeypatch.setattr(logo_cta_card, "_logo_path", tmp_path / "missing.png")
        logo_cta_card._load_logo.cache_clear()
        img = logo_cta_card.frame_at(self.PAYLOAD, 2.0, 7.0)
        bg = theme.background(2.0)
        diff = np.abs(np.asarray(img).astype(int) - np.asarray(bg).astype(int)).sum()
        assert diff > 200_000
        logo_cta_card._load_logo.cache_clear()

    def test_render_mp4(self, tmp_path):
        from shorts_engine.cards import logo_cta_card, encoder
        out = logo_cta_card.render(self.PAYLOAD, 0.6, tmp_path / "l.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail** — modules missing.

- [ ] **Step 3: Implement both modules**

```python
# shorts_engine/cards/quote_card.py
"""QUOTE_CARD: verbatim blog sentence (≤120 chars) + source chip."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)


def trim_quote(q: str, limit: int = 120) -> str:
    q = " ".join(q.split())
    if len(q) <= limit:
        return q
    cut = q[:limit].rsplit(" ", 1)[0].rstrip(".,;: ")
    return cut + "…"


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    mark_font = theme.resolve_font("heading", 220)
    a0, dy0 = theme.fade_rise(t, 0)
    if a0 > 0:
        col = tuple(int(c * a0) for c in theme.GOLD)
        d.text((config.SAFE_SIDE_PX, config.SAFE_TOP_PX + 60), "“",
               font=mark_font, fill=col)
    quote = trim_quote(str(payload.get("quote", "")))
    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    f, lines, _ = theme.fit_text(d, quote, "heading", max_w, max_size=66)
    a1, dy1 = theme.fade_rise(t, 1)
    if a1 > 0:
        col = tuple(int(c * a1 + n * (1 - a1)) for c, n in zip(theme.TEXT, theme.NAVY))
        theme.paste_text_block(img, lines, f, 640 + dy1, col)
    src = payload.get("source")
    if src:
        theme.draw_citation_chip(img, str(src))
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

```python
# shorts_engine/cards/logo_cta_card.py
"""LOGO_CTA end card: logo (or wordmark), one differentiator, CTA, domain."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw

from shorts_engine import config
from shorts_engine.cards import theme

logger = logging.getLogger(__name__)

_logo_path: Path = config.BRAND_LOGO_FILE


@functools.lru_cache(maxsize=1)
def _load_logo() -> Image.Image | None:
    try:
        img = Image.open(_logo_path).convert("RGBA")
        w = 420
        h = int(img.height * w / img.width)
        return img.resize((w, h))
    except Exception:
        logger.warning("brand logo unreadable at %s — using wordmark", _logo_path)
        return None


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    img = theme.background(t)
    d = ImageDraw.Draw(img)
    y = config.SAFE_TOP_PX + 150
    logo = _load_logo()
    a0, dy0 = theme.fade_rise(t, 0)
    if logo is not None:
        if a0 > 0:
            faded = logo.copy()
            alpha = faded.getchannel("A").point(lambda px: int(px * a0))
            faded.putalpha(alpha)
            img.paste(faded, ((config.CANVAS_W - logo.width) // 2, y + dy0), faded)
        y += logo.height + 90
    else:
        wf = theme.resolve_font("heading", 160)
        col = tuple(int(c * a0) for c in theme.GOLD)
        w = d.textlength("HRSU", font=wf)
        d.text(((config.CANVAS_W - w) // 2, y + dy0), "HRSU", font=wf, fill=col)
        y += 260

    max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
    for i, (text, kind, color, size) in enumerate([
            (payload.get("differentiator", ""), "heading", theme.TEXT, 58),
            (payload.get("cta_line", ""), "body", theme.MUTED, 46)]):
        if not text:
            continue
        f, lines, _ = theme.fit_text(d, str(text), kind, max_w, max_size=size)
        a, dy = theme.fade_rise(t, i + 1)
        if a > 0:
            col = tuple(int(c * a + n * (1 - a)) for c, n in zip(color, theme.NAVY))
            y += theme.paste_text_block(img, lines, f, y + dy, col) + 56

    domain = payload.get("domain", "")
    if domain:
        f = theme.resolve_font("body", 64)
        a, dy = theme.fade_rise(t, 3)
        if a > 0:
            col = tuple(int(c * a + n * (1 - a)) for c, n in zip(theme.GOLD, theme.NAVY))
            w = d.textlength(domain, font=f)
            yd = config.CANVAS_H - config.SAFE_BOTTOM_PX - 140 + dy
            d.text(((config.CANVAS_W - w) // 2, yd), domain, font=f, fill=col)
            d.rectangle([(config.CANVAS_W - w) // 2, yd + 88,
                         (config.CANVAS_W + w) // 2, yd + 94], fill=col)
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_quote_logo_cards.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 7: `cards/broll_frame.py` — real assets, never crop-panned

**Files:**
- Create: `shorts_engine/cards/broll_frame.py`
- Test: `tests/shorts_engine/test_broll_frame.py`

**Interfaces:**
- Consumes: `theme`.
- Produces: `frame_at(payload, t, duration)`/`render(...)` with payload `{"image_path": str, "caption": str (optional), "layout": "auto"|"inset"|"blurfill" (default "auto")}`; pure helpers used by tests and later by the Plan-3 judge (`focal_hint` feeds layout):
  - `is_portrait(w: int, h: int) -> bool` — True iff `h / w >= 1.25` (aspect ≥ 4:5 portrait).
  - `placement(img_w: int, img_h: int) -> tuple[int, int, int, int]` — the (x0, y0, x1, y1) box where the **entire** image lands on the canvas, aspect preserved, fitted inside `1080−2·72` wide × `1100` tall, centered at y=880. Never crops.
  - `kenburns_window(img_w, img_h, t, duration, max_zoom=1.08) -> tuple[int,int,int,int]` — source crop window for portrait assets only; window always inside the source.
- Behavior: portrait assets → Ken Burns (zoom 1.0→1.08, center); landscape/square → `blurfill` (blurred cover layer behind the intact image) or `inset` (branded matte + caption strip). `auto` = blurfill for landscape, kenburns for portrait. **Landscape sources are never cropped** — the full image is always visible.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_broll_frame.py
from __future__ import annotations
import numpy as np
import pytest
from PIL import Image
from shorts_engine import config


@pytest.fixture()
def landscape(tmp_path):
    p = tmp_path / "land.png"
    img = Image.new("RGB", (1600, 900), (200, 30, 30))
    img.paste(Image.new("RGB", (200, 200), (30, 200, 30)), (0, 0))       # TL green
    img.paste(Image.new("RGB", (200, 200), (30, 30, 200)), (1400, 700))  # BR blue
    img.save(p)
    return p


@pytest.fixture()
def portrait(tmp_path):
    p = tmp_path / "port.png"
    Image.new("RGB", (900, 1600), (120, 60, 200)).save(p)
    return p


class TestGeometry:
    def test_is_portrait(self):
        from shorts_engine.cards import broll_frame
        assert broll_frame.is_portrait(900, 1600)
        assert broll_frame.is_portrait(1000, 1250)
        assert not broll_frame.is_portrait(1600, 900)
        assert not broll_frame.is_portrait(1000, 1000)

    def test_placement_preserves_aspect_and_fits(self):
        from shorts_engine.cards import broll_frame
        x0, y0, x1, y1 = broll_frame.placement(1600, 900)
        w, h = x1 - x0, y1 - y0
        assert abs((w / h) - (1600 / 900)) < 0.02      # aspect preserved
        assert w <= config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        assert x0 >= config.SAFE_SIDE_PX and x1 <= config.CANVAS_W - config.SAFE_SIDE_PX

    def test_kenburns_window_stays_inside_source(self):
        from shorts_engine.cards import broll_frame
        for t in (0.0, 1.0, 2.9):
            x0, y0, x1, y1 = broll_frame.kenburns_window(900, 1600, t, 3.0)
            assert 0 <= x0 < x1 <= 900
            assert 0 <= y0 < y1 <= 1600


class TestNeverCropped:
    def test_landscape_corners_both_visible(self, landscape):
        """The ¼-crop defect killer: both corner markers of a landscape source
        must be present in the rendered frame."""
        from shorts_engine.cards import broll_frame
        img = broll_frame.frame_at({"image_path": str(landscape)}, 1.5, 3.0)
        arr = np.asarray(img).astype(int)
        green = (np.abs(arr - np.array([30, 200, 30])).sum(axis=2) < 60).sum()
        blue = (np.abs(arr - np.array([30, 30, 200])).sum(axis=2) < 60).sum()
        assert green > 100 and blue > 100

    def test_blurfill_background_is_not_flat_navy(self, landscape):
        from shorts_engine.cards import broll_frame, theme
        img = broll_frame.frame_at(
            {"image_path": str(landscape), "layout": "blurfill"}, 1.5, 3.0)
        top_strip = np.asarray(img)[:100, :, :].astype(int)
        navy = np.array(theme.NAVY)
        assert np.abs(top_strip - navy).sum(axis=2).mean() > 30

    def test_portrait_kenburns_moves(self, portrait):
        from shorts_engine.cards import broll_frame
        a = np.asarray(broll_frame.frame_at({"image_path": str(portrait)}, 0.0, 3.0))
        b = np.asarray(broll_frame.frame_at({"image_path": str(portrait)}, 2.9, 3.0))
        assert np.abs(a.astype(int) - b.astype(int)).sum() > 0

    def test_missing_file_raises(self):
        from shorts_engine.cards import broll_frame
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            broll_frame.frame_at({"image_path": "Z:/nope.png"}, 0.5, 3.0)

    def test_render_mp4(self, landscape, tmp_path):
        from shorts_engine.cards import broll_frame, encoder
        out = broll_frame.render({"image_path": str(landscape)}, 0.6,
                                 tmp_path / "b.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/cards/broll_frame.py`**

```python
"""Real-asset framing. Landscape/square are NEVER crop-panned (inset matte or
blur-fill keeps the whole image visible); Ken Burns only on portrait assets."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_MAX_W = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
_MAX_H = 1100
_CENTER_Y = 880
KEN_BURNS_MAX_ZOOM = 1.08


def is_portrait(w: int, h: int) -> bool:
    return h / w >= 1.25


def placement(img_w: int, img_h: int) -> tuple[int, int, int, int]:
    scale = min(_MAX_W / img_w, _MAX_H / img_h)
    w, h = int(img_w * scale), int(img_h * scale)
    x0 = (config.CANVAS_W - w) // 2
    y0 = _CENTER_Y - h // 2
    return (x0, y0, x0 + w, y0 + h)


def kenburns_window(img_w: int, img_h: int, t: float, duration: float,
                    max_zoom: float = KEN_BURNS_MAX_ZOOM) -> tuple[int, int, int, int]:
    p = min(1.0, max(0.0, t / duration)) if duration > 0 else 0.0
    zoom = 1.0 + (max_zoom - 1.0) * p
    w, h = int(img_w / zoom), int(img_h / zoom)
    x0, y0 = (img_w - w) // 2, (img_h - h) // 2
    return (x0, y0, x0 + w, y0 + h)


@functools.lru_cache(maxsize=16)
def _load(path_str: str) -> Image.Image:
    p = Path(path_str)
    if not p.exists():
        raise EngineError(f"broll asset missing: {p}")
    return Image.open(p).convert("RGB")


def _kenburns_frame(src: Image.Image, t: float, duration: float) -> Image.Image:
    win = kenburns_window(src.width, src.height, t, duration)
    crop = src.crop(win)
    # cover-fit portrait crop to canvas (portrait→portrait: minimal edge loss ≤8%)
    scale = max(config.CANVAS_W / crop.width, config.CANVAS_H / crop.height)
    w, h = int(crop.width * scale), int(crop.height * scale)
    crop = crop.resize((w, h))
    x = (w - config.CANVAS_W) // 2
    y = (h - config.CANVAS_H) // 2
    return crop.crop((x, y, x + config.CANVAS_W, y + config.CANVAS_H))


def _blurfill_frame(src: Image.Image, t: float) -> Image.Image:
    scale = max(config.CANVAS_W / src.width, config.CANVAS_H / src.height)
    bg = src.resize((int(src.width * scale) + 1, int(src.height * scale) + 1))
    x = (bg.width - config.CANVAS_W) // 2
    y = (bg.height - config.CANVAS_H) // 2
    bg = bg.crop((x, y, x + config.CANVAS_W, y + config.CANVAS_H))
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    dark = Image.new("RGB", bg.size, theme.NAVY)
    bg = Image.blend(bg, dark, 0.35)
    box = placement(src.width, src.height)
    inset = src.resize((box[2] - box[0], box[3] - box[1]))
    bg.paste(inset, (box[0], box[1]))
    d = ImageDraw.Draw(bg)
    d.rectangle(box, outline=theme.GOLD, width=3)
    return bg


def _inset_frame(src: Image.Image, caption: str, t: float) -> Image.Image:
    img = theme.background(t)
    box = placement(src.width, src.height)
    inset = src.resize((box[2] - box[0], box[3] - box[1]))
    img.paste(inset, (box[0], box[1]))
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline=theme.GOLD, width=3)
    if caption:
        f, lines, _ = theme.fit_text(d, caption, "body", _MAX_W, max_size=40,
                                     max_lines=2)
        theme.paste_text_block(img, lines, f, box[3] + 30, theme.MUTED)
    return img


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    src = _load(str(payload["image_path"]))
    layout = payload.get("layout", "auto")
    if layout == "auto":
        layout = "kenburns" if is_portrait(src.width, src.height) else "blurfill"
    if layout == "kenburns" and not is_portrait(src.width, src.height):
        logger.warning("Ken Burns requested for landscape asset — using blurfill")
        layout = "blurfill"
    if layout == "kenburns":
        return _kenburns_frame(src, t, duration)
    if layout == "inset":
        return _inset_frame(src, str(payload.get("caption", "")), t)
    return _blurfill_frame(src, t)


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_broll_frame.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 8: SHOTLIST stage — deterministic expansion + linter (+ optional `diagram_labels` from the writer)

**Files:**
- Create: `shorts_engine/stages/shotlist.py`
- Modify: `shorts_engine/stages/script.py` (SCRIPT_SCHEMA: add **optional** `diagram_labels`; writer prompt: one added instruction; `gate_numbers`: also scan `diagram_labels`)
- Test: `tests/shorts_engine/test_shotlist.py`
- Test: extend `tests/shorts_engine/test_script_gates.py` (one new test class for diagram_labels in gate_numbers)

**Interfaces:**
- Consumes: `script.json` beats `{beat, narration, fact_ids, card_text, broll_wish, diagram_labels?}`; `factsheet.json` facts (`id, verbatim_quote, value, unit, citation_marker`); `post.json` citations (`marker, url, kind`); `brand.load_brand_facts()`; `script.extract_numeric_tokens`; config bounds.
- Produces: `shotlist.json` = `{"shots": [SHOT...], "total_s": float}` where SHOT = `{"id": "s00", "beat": str, "type": str, "duration_s": float, "narration_span": str, "payload": dict, "fallback": {"type": str, "payload": dict} | None}`. Types: `HEADLINE_CARD, STAT_CARD, DIAGRAM, QUOTE_CARD, PAPER_CARD, LOGO_CTA` (no `BROLL` emitted in Plan 2 — sourcing lands in Plan 3; `broll_wish` is carried in shot payloads as `"wish"` for Plan 3 to consume).
- Pure functions (unit-tested): `split_phrases(text) -> list[str]`; `estimate_s(text) -> float`; `pack_phrases(phrases) -> list[str]` (greedy spans targeting 2.0–3.5s); `plan_beat_shots(beat, facts_by_id, cites_by_marker, brand) -> list[SHOT]`; `lint_shotlist(shots, factsheet) -> list[str]`; `run(ctx) -> {"shotlist": "shotlist.json"}` (raises `GateFailure(errors)` if the linter reports any).
- **Beat→type mapping (deterministic):**
  - hook → 1 HEADLINE_CARD (text=card_text).
  - stakes → STAT_CARD (first referenced fact) if the beat references a fact with a numeric value, else HEADLINE_CARD; if the packed narration yields 2 spans, the second span becomes a HEADLINE_CARD with text=card_text (accent shifts to spare a stutter: accent = pick_accent of the span).
  - mechanism → DIAGRAM `flow` in 1–3 reveal shots. Labels: beat's `diagram_labels` when present (2–4 strings), else fallback = first 3 phrases of the narration, each trimmed to its first 4 words. `reveal_total` = shot count, shot k has `reveal_stage=k+1`… (stage numbering 1..total).
  - proof → if any referenced fact has `citation_marker` whose citation `kind == "paper"` → shot 1 = `PAPER_CARD` `{"marker", "url", "highlight": card_text, "wish": broll_wish}` with **required** fallback `{type: QUOTE_CARD, payload: {quote: fact.verbatim_quote, source: "Source [m] — <domain>"}}`; shot 2 = STAT_CARD for that fact. Else STAT_CARD + QUOTE_CARD.
  - cta → 1 LOGO_CTA `{differentiator: <text of the one differentiator id in beat.fact_ids>, cta_line: brand.cta_lines[0], domain: brand.domain}` spanning the whole beat (exempt from 4.5s cap).
- **Linter errors (each a string):** PAPER_CARD without fallback; STAT payload value digits ⊄ digits of the referenced fact's `verbatim_quote`; any non-LOGO_CTA shot outside `[1.8, 4.5]`; LOGO_CTA outside `[1.8, 10.0]`; DIAGRAM flow with <2 or >4 labels; total outside `[35, 50]`; unknown type.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_shotlist.py
from __future__ import annotations
import json
import pytest

FACTS = {
    "facts": [
        {"id": "f1", "verbatim_quote": "optimal dosage range of 1.5 to 3 kg per cubic meter",
         "value": "1.5–3", "unit": "kg/m³", "citation_marker": 2},
        {"id": "f2", "verbatim_quote": "denitrifying filters removed 92 percent of nitrate",
         "value": "92", "unit": "%", "citation_marker": 5},
    ],
    "brand_facts": {"differentiators": [{"id": "b_purity", "text": "high-purity powder"}],
                    "cta_lines": ["Full guide on the HRSU blog"], "domain": "hrsuindore.com"},
}
CITES = [{"marker": 2, "url": "https://www.mdpi.com/2073-4441/12/5/1234", "kind": "paper"},
         {"marker": 5, "url": "https://example.com/report", "kind": "web"}]

BEATS = [
    {"beat": "hook", "narration": "Your effluent nitrate is creeping toward the limit.",
     "fact_ids": [], "card_text": "Nitrate limits are tightening", "broll_wish": "aeration basin"},
    {"beat": "stakes", "narration": "Plants dose one point five to three kilograms per cubic "
     "meter to stay compliant, every single day.", "fact_ids": ["f1"],
     "card_text": "The dosing window that works", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Calcium nitrate feeds denitrifying bacteria, so they "
     "strip oxygen from nitrate, turning it into harmless nitrogen gas before discharge, "
     "no retrofit required.", "fact_ids": ["f1"], "card_text": "Bacteria do the removal",
     "broll_wish": "", "diagram_labels": ["Effluent in", "Calcium nitrate dosing",
                                          "Denitrifying bacteria", "N2 out"]},
    {"beat": "proof", "narration": "Published trials report ninety two percent nitrate removal "
     "with this approach across municipal plants.", "fact_ids": ["f2"],
     "card_text": "92 percent removal", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU ships high-purity powder with batch level QC. The full "
     "dosing guide is on the HRSU blog at hrsuindore dot com.", "fact_ids": ["b_purity"],
     "card_text": "Get the dosing guide", "broll_wish": ""},
]


class TestPhrasePacking:
    def test_split_phrases(self):
        from shorts_engine.stages import shotlist
        ph = shotlist.split_phrases("One, two. Three; four")
        assert ph == ["One", "two", "Three", "four"]

    def test_estimate_uses_words_per_second(self):
        from shorts_engine.stages import shotlist
        assert abs(shotlist.estimate_s("one two three four five") - 5 / 2.6) < 1e-6

    def test_pack_respects_target_bounds(self):
        from shorts_engine.stages import shotlist
        words = "word " * 26  # 10s of narration
        spans = shotlist.pack_phrases(shotlist.split_phrases(
            ", ".join([words[:30]] * 6)))
        from shorts_engine import config
        for s in spans:
            assert shotlist.estimate_s(s) <= config.SHOT_MAX_S + 0.01


class TestBeatMapping:
    def _facts_by_id(self):
        return {f["id"]: f for f in FACTS["facts"]}

    def _cites(self):
        return {c["marker"]: c for c in CITES}

    def _brand(self):
        from shorts_engine.brand import BrandFacts
        return BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                          differentiators=[{"id": "b_purity", "text": "high-purity powder"}],
                          cta_lines=["Full guide on the HRSU blog"], banned_claims=[])

    def test_hook_is_headline(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[0], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "HEADLINE_CARD"
        assert shots[0]["payload"]["text"] == "Nitrate limits are tightening"

    def test_stakes_uses_stat_from_fact(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[1], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "STAT_CARD"
        assert shots[0]["payload"]["value"] == "1.5–3"
        assert "mdpi.com" in shots[0]["payload"]["citation"]

    def test_mechanism_flow_reveal_stages(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[2], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert all(s["type"] == "DIAGRAM" for s in shots)
        assert 1 <= len(shots) <= 3
        stages = [s["payload"]["reveal_stage"] for s in shots]
        assert stages == list(range(1, len(shots) + 1))
        assert all(s["payload"]["reveal_total"] == len(shots) for s in shots)
        assert shots[0]["payload"]["labels"] == BEATS[2]["diagram_labels"]

    def test_proof_paper_card_with_quote_fallback(self):
        from shorts_engine.stages import shotlist
        beat = dict(BEATS[3], fact_ids=["f1"])  # f1 cites marker 2 = paper
        shots = shotlist.plan_beat_shots(beat, self._facts_by_id(), self._cites(),
                                         self._brand())
        assert shots[0]["type"] == "PAPER_CARD"
        fb = shots[0]["fallback"]
        assert fb["type"] == "QUOTE_CARD"
        assert "1.5 to 3 kg" in fb["payload"]["quote"]

    def test_proof_without_paper_is_stat_plus_quote(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[3], self._facts_by_id(), self._cites(),
                                         self._brand())  # f2 cites web
        assert [s["type"] for s in shots] == ["STAT_CARD", "QUOTE_CARD"]

    def test_cta_single_logo_shot(self):
        from shorts_engine.stages import shotlist
        shots = shotlist.plan_beat_shots(BEATS[4], self._facts_by_id(), self._cites(),
                                         self._brand())
        assert len(shots) == 1 and shots[0]["type"] == "LOGO_CTA"
        assert shots[0]["payload"]["differentiator"] == "high-purity powder"
        assert shots[0]["payload"]["domain"] == "hrsuindore.com"


class TestLinter:
    def test_paper_card_without_fallback_flagged(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                  "narration_span": "x", "payload": {}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("fallback" in e for e in errs)

    def test_stat_digits_must_trace_to_fact(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "proof", "type": "STAT_CARD", "duration_s": 3.0,
                  "narration_span": "x", "fallback": None,
                  "payload": {"value": "97", "unit": "%", "label": "l",
                              "fact_id": "f2"}}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("97" in e for e in errs)

    def test_duration_bounds_flagged(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "hook", "type": "HEADLINE_CARD",
                  "duration_s": 9.0, "narration_span": "x",
                  "payload": {"text": "t"}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("9.0" in e for e in errs)

    def test_logo_cta_exempt_up_to_10s(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "cta", "type": "LOGO_CTA", "duration_s": 8.0,
                  "narration_span": "x", "payload": {}, "fallback": None}]
        assert not [e for e in shotlist.lint_shotlist(shots, FACTS) if "8.0" in e]


class TestRun:
    def test_run_writes_shotlist(self, tmp_path):
        from shorts_engine.stages import shotlist
        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = tmp_path / m.workspace if not str(m.workspace).startswith(str(tmp_path)) \
            else __import__("pathlib").Path(m.workspace)
        (ws / "script.json").write_text(json.dumps({"beats": BEATS}), encoding="utf-8")
        (ws / "factsheet.json").write_text(json.dumps(FACTS), encoding="utf-8")
        (ws / "post.json").write_text(json.dumps({"citations": CITES}), encoding="utf-8")
        ctx = StageContext(manifest=m, workspace=ws, flags={})
        arts = shotlist.run(ctx)
        data = json.loads((ws / arts["shotlist"]).read_text(encoding="utf-8"))
        assert data["shots"][0]["beat"] == "hook"
        assert data["shots"][-1]["type"] == "LOGO_CTA"
        from shorts_engine import config
        assert config.TOTAL_MIN_S <= data["total_s"] <= config.TOTAL_MAX_S
```

Add to `tests/shorts_engine/test_script_gates.py`:

```python
class TestDiagramLabelsGate:
    def test_gate_numbers_scans_diagram_labels(self):
        from shorts_engine.stages import script
        from shorts_engine.brand import BrandFacts
        brand = BrandFacts(company="c", domain="hrsuindore.com", tagline="t",
                           differentiators=[{"id": "b_purity", "text": "pure"}],
                           cta_lines=["cta"], banned_claims=[])
        factsheet = {"facts": [{"id": "f1", "verbatim_quote": "uses 2 stages",
                                "value": "2", "unit": ""}]}
        beats = [{"beat": "mechanism", "narration": "no numbers here",
                  "fact_ids": ["f1"], "card_text": "clean",
                  "diagram_labels": ["Stage 99 boost", "output"]}]
        errs = script.gate_numbers(beats, factsheet, brand)
        assert any("99" in e for e in errs)
```

- [ ] **Step 2: Run tests to verify they fail** → shotlist module missing; new gate test fails.

- [ ] **Step 3a: Extend `shorts_engine/stages/script.py`**

1. In `SCRIPT_SCHEMA`'s beat item properties add (NOT in `required`):
```python
"diagram_labels": {"type": "array", "items": {"type": "string"},
                    "minItems": 2, "maxItems": 4},
```
2. In `gate_numbers`, extend the per-beat source loop to include diagram labels:
```python
        for source in ("narration", "card_text"):
            ...
        for label in b.get("diagram_labels") or []:
            for tok in extract_numeric_tokens(label):
                if not _token_in_pool(tok, pool):
                    errors.append(
                        f"beat '{b.get('beat')}': diagram label number '{tok}' "
                        f"does not trace to any referenced fact")
```
(match the existing untraced-number error wording/style and the existing pool-membership helper — if `gate_numbers` checks membership inline rather than via a helper, replicate that inline check.)
3. In the writer prompt (`_writer_prompt` or equivalent), add one line to the mechanism-beat rules: `On the mechanism beat you MAY add "diagram_labels": 2-4 short process-step labels (3 words or fewer each, no numbers unless quoted from a fact).`

- [ ] **Step 3b: Implement `shorts_engine/stages/shotlist.py`**

```python
"""Stage 4 — SHOTLIST: deterministic beat→shots expansion + linter. No LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.errors import GateFailure
from shorts_engine.stages.script import extract_numeric_tokens

logger = logging.getLogger(__name__)

_PHRASE_SPLIT = re.compile(r"[,.;:]+")


def split_phrases(text: str) -> list[str]:
    return [p.strip() for p in _PHRASE_SPLIT.split(text) if p.strip()]


def estimate_s(text: str) -> float:
    return len(text.split()) / config.WORDS_PER_SECOND


def pack_phrases(phrases: list[str]) -> list[str]:
    """Greedy-pack phrases into spans targeting SHOT_TARGET_MIN..MAX seconds."""
    spans, cur = [], ""
    for ph in phrases:
        trial = (cur + ", " + ph).strip(", ") if cur else ph
        if estimate_s(trial) <= config.SHOT_TARGET_MAX_S or not cur:
            cur = trial
        else:
            spans.append(cur)
            cur = ph
    if cur:
        if spans and estimate_s(cur) < config.SHOT_TARGET_MIN_S / 2:
            spans[-1] = spans[-1] + ", " + cur
        else:
            spans.append(cur)
    return spans


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _chip(marker: int | None, cites: dict) -> str:
    if marker is None or marker not in cites:
        return "Source — HRSU blog"
    return f"Source [{marker}] — {_domain(cites[marker]['url'])}"


def _first_numeric_fact(beat: dict, facts: dict) -> dict | None:
    for fid in beat.get("fact_ids", []):
        f = facts.get(fid)
        if f and extract_numeric_tokens(str(f.get("value", ""))):
            return f
    return None


def _stat_payload(fact: dict, label: str, cites: dict) -> dict:
    return {"value": str(fact["value"]), "unit": str(fact.get("unit") or ""),
            "label": label, "citation": _chip(fact.get("citation_marker"), cites),
            "fact_id": fact["id"]}


def _fallback_labels(narration: str) -> list[str]:
    phrases = split_phrases(narration)[:3]
    labels = [" ".join(p.split()[:4]) for p in phrases if p]
    return labels if len(labels) >= 2 else (labels + ["Result"])[:2]


def plan_beat_shots(beat: dict, facts: dict, cites: dict, brand) -> list[dict]:
    name = beat["beat"]
    narration = beat["narration"]
    spans = pack_phrases(split_phrases(narration)) or [narration]
    est_total = max(estimate_s(narration), config.SHOT_MIN_S)
    shots: list[dict] = []

    def add(type_, payload, span, fallback=None):
        shots.append({"id": "", "beat": name, "type": type_, "duration_s": 0.0,
                      "narration_span": span, "payload": payload,
                      "fallback": fallback})

    if name == "hook":
        add("HEADLINE_CARD", {"text": beat["card_text"],
                              "wish": beat.get("broll_wish", "")}, narration)
    elif name == "stakes":
        fact = _first_numeric_fact(beat, facts)
        if fact:
            add("STAT_CARD", _stat_payload(fact, beat["card_text"], cites), spans[0])
        else:
            add("HEADLINE_CARD", {"text": beat["card_text"]}, spans[0])
        if len(spans) > 1:
            rest = ", ".join(spans[1:])
            add("HEADLINE_CARD", {"text": beat["card_text"]}, rest)
    elif name == "mechanism":
        labels = beat.get("diagram_labels") or _fallback_labels(narration)
        n_shots = max(1, min(3, len(spans)))
        span_groups = spans[:n_shots - 1] + [", ".join(spans[n_shots - 1:])] \
            if n_shots > 1 else [narration]
        for k, span in enumerate(span_groups, start=1):
            add("DIAGRAM", {"template": "flow", "labels": labels,
                            "reveal_stage": k, "reveal_total": n_shots}, span)
    elif name == "proof":
        fact = _first_numeric_fact(beat, facts) or next(
            (facts[f] for f in beat.get("fact_ids", []) if f in facts), None)
        paper_fact = None
        for fid in beat.get("fact_ids", []):
            f = facts.get(fid)
            m = f.get("citation_marker") if f else None
            if m in cites and cites[m]["kind"] == "paper":
                paper_fact = f
                break
        if paper_fact is not None:
            m = paper_fact["citation_marker"]
            quote_fb = {"type": "QUOTE_CARD",
                        "payload": {"quote": paper_fact["verbatim_quote"],
                                    "source": _chip(m, cites)}}
            add("PAPER_CARD", {"marker": m, "url": cites[m]["url"],
                               "highlight": beat["card_text"],
                               "wish": beat.get("broll_wish", "")},
                spans[0], fallback=quote_fb)
            stat = paper_fact if extract_numeric_tokens(str(paper_fact.get("value", ""))) \
                else (fact or paper_fact)
            add("STAT_CARD", _stat_payload(stat, beat["card_text"], cites),
                ", ".join(spans[1:]) or narration)
        elif fact is not None:
            add("STAT_CARD", _stat_payload(fact, beat["card_text"], cites), spans[0])
            add("QUOTE_CARD", {"quote": fact["verbatim_quote"],
                               "source": _chip(fact.get("citation_marker"), cites)},
                ", ".join(spans[1:]) or narration)
        else:
            add("HEADLINE_CARD", {"text": beat["card_text"]}, narration)
    elif name == "cta":
        diff_text = ""
        for fid in beat.get("fact_ids", []):
            for dd in brand.differentiators:
                if dd["id"] == fid:
                    diff_text = dd["text"]
        add("LOGO_CTA", {"differentiator": diff_text,
                         "cta_line": brand.cta_lines[0] if brand.cta_lines else "",
                         "domain": brand.domain}, narration)
    else:
        add("HEADLINE_CARD", {"text": beat.get("card_text", "")}, narration)

    # distribute the beat's estimated duration across its shots, clamped
    per = est_total / len(shots)
    cap = config.LOGO_CTA_MAX_S if name == "cta" else config.SHOT_MAX_S
    for s in shots:
        s["duration_s"] = round(min(max(per, config.SHOT_MIN_S), cap), 2)
    return shots


def lint_shotlist(shots: list[dict], factsheet: dict) -> list[str]:
    errors: list[str] = []
    facts = {f["id"]: f for f in factsheet.get("facts", [])}
    known = {"HEADLINE_CARD", "STAT_CARD", "DIAGRAM", "QUOTE_CARD",
             "PAPER_CARD", "LOGO_CTA"}
    total = 0.0
    for s in shots:
        total += s["duration_s"]
        if s["type"] not in known:
            errors.append(f"{s['id']}: unknown shot type {s['type']}")
        cap = config.LOGO_CTA_MAX_S if s["type"] == "LOGO_CTA" else config.SHOT_MAX_S
        if not (config.SHOT_MIN_S <= s["duration_s"] <= cap):
            errors.append(f"{s['id']}: duration {s['duration_s']} outside "
                          f"[{config.SHOT_MIN_S}, {cap}]")
        if s["type"] == "PAPER_CARD" and not s.get("fallback"):
            errors.append(f"{s['id']}: PAPER_CARD requires a declared fallback")
        if s["type"] == "STAT_CARD":
            fact = facts.get(s["payload"].get("fact_id", ""))
            quote_digits = set(extract_numeric_tokens(
                fact["verbatim_quote"])) if fact else set()
            for tok in extract_numeric_tokens(str(s["payload"].get("value", ""))):
                if tok not in quote_digits:
                    errors.append(f"{s['id']}: STAT value token '{tok}' not in "
                                  f"referenced fact quote")
        if s["type"] == "DIAGRAM" and s["payload"].get("template") == "flow":
            n = len(s["payload"].get("labels") or [])
            if not 2 <= n <= 4:
                errors.append(f"{s['id']}: flow diagram needs 2-4 labels, has {n}")
    if not (config.TOTAL_MIN_S <= total <= config.TOTAL_MAX_S):
        errors.append(f"total duration {total:.1f}s outside "
                      f"[{config.TOTAL_MIN_S}, {config.TOTAL_MAX_S}]")
    return errors


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    script_doc = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    from shorts_engine.brand import load_brand_facts
    brand = load_brand_facts()
    facts = {f["id"]: f for f in factsheet.get("facts", [])}
    cites = {c["marker"]: c for c in post.get("citations", [])}

    shots: list[dict] = []
    for beat in script_doc["beats"]:
        shots.extend(plan_beat_shots(beat, facts, cites, brand))
    for i, s in enumerate(shots):
        s["id"] = f"s{i:02d}"
    errors = lint_shotlist(shots, factsheet)
    if errors:
        raise GateFailure(errors)
    total = round(sum(s["duration_s"] for s in shots), 2)
    out = {"shots": shots, "total_s": total}
    (ws / "shotlist.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    logger.info("shotlist: %d shots, %.1fs", len(shots), total)
    return {"shotlist": "shotlist.json"}
```

Note for the implementer: `RunManifest.create` puts the workspace at `workspace_root/run-<hex8>/`; in tests use `Path(m.workspace)` directly (see the `test_run_writes_shotlist` juggling — simplify it to `ws = Path(m.workspace)` if that is absolute).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_shotlist.py tests/shorts_engine/test_script_gates.py -v`
If `test_run_writes_shotlist` total falls below 35s, adjust BEATS narration lengths in the test (word counts drive duration) rather than weakening the assert — the five test beats above are sized to land ≈39s.

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 9: AUDIO stage — per-beat TTS wrap + `transcribe_words` extension

**Files:**
- Modify: `E:\Projects\HRSU Blog\video_agent\subtitles.py` (add `transcribe_words`, refactor `generate_srt` to use it — light extension per spec §3.2)
- Create: `shorts_engine/stages/audio.py`
- Test: `tests/shorts_engine/test_audio.py`

**Interfaces:**
- Consumes: `script.json` beats; `post.json` region; `video_agent.voiceover.synthesize_segments(segments, output_path, region) -> {"audio_path", "duration_s", ...}` and `VoiceSegment(text, prosody)`; `config.PROSODY_BY_BEAT/MIN_SEGMENT_BYTES/AUDIO_DURATION_TOLERANCE/AUDIO_BEAT_GAP_MS`.
- Produces:
  - `video_agent.subtitles.transcribe_words(audio_path: Path, narration_hint: str | None = None, multilingual: bool = False) -> list[dict]` — `[{"word": str, "start": float, "end": float}, ...]`.
  - `audio.run(ctx) -> {"voice": "voiceover.mp3", "word_timings": "word_timings.json", "beats_audio": "beats_audio.json"}`. `beats_audio.json` = `[{"beat": str, "start_s": float, "duration_s": float}]` (start offsets include the 300ms inter-beat gaps). Per-beat files `voice_beat_00.mp3 … voice_beat_04.mp3` remain on disk (guard evidence).
  - Module-level late-binding test seams: `_synthesize = None` (resolves to `voiceover.synthesize_segments` at call time), `_transcribe = None` (resolves to `subtitles.transcribe_words`).
  - Guards (each raises `EngineError` with the beat name): missing/`< MIN_SEGMENT_BYTES` beat file (F10); total actual voice duration vs script estimate (`Σ words / 2.6`) off by more than ±15%.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_audio.py
from __future__ import annotations
import json
from pathlib import Path
import pytest

BEATS = [{"beat": b, "narration": ("word " * n).strip(), "fact_ids": [],
          "card_text": "c", "broll_wish": ""}
         for b, n in (("hook", 8), ("stakes", 13), ("mechanism", 26),
                      ("proof", 21), ("cta", 18))]  # 86 words ≈ 33.1s est


def _fake_synth_factory(seconds_per_word=1 / 2.6, garbage=()):
    """Writes a real silent mp3 sized to the narration and returns metadata."""
    def fake(segments, output_path, region, voice_override=None):
        from pydub import AudioSegment
        text = segments[0].text
        dur_ms = int(len(text.split()) * seconds_per_word * 1000)
        AudioSegment.silent(duration=max(dur_ms, 120)).export(
            str(output_path), format="mp3", bitrate="128k")
        if Path(output_path).name in garbage:
            Path(output_path).write_bytes(b"")  # simulate F10: 0-byte file
        return {"audio_path": Path(output_path), "duration_s": dur_ms / 1000,
                "voice_used": "test", "engine_used": "fake", "fell_back": False}
    return fake


def _fake_transcribe(audio_path, narration_hint=None, multilingual=False):
    words = (narration_hint or "a b c").split()
    return [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
            for i, w in enumerate(words)]


def _ctx(tmp_path):
    from shorts_engine.manifest import RunManifest
    from shorts_engine.runner import StageContext
    m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
    ws = Path(m.workspace)
    (ws / "script.json").write_text(json.dumps({"beats": BEATS}), encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestAudioStage:
    def test_artifacts_written(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        monkeypatch.setattr(audio, "_synthesize", _fake_synth_factory())
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        ctx = _ctx(tmp_path)
        arts = audio.run(ctx)
        ws = Path(ctx.workspace)
        assert (ws / arts["voice"]).stat().st_size > 1024
        timings = json.loads((ws / arts["word_timings"]).read_text(encoding="utf-8"))
        assert timings and {"word", "start", "end"} <= set(timings[0])
        beats = json.loads((ws / arts["beats_audio"]).read_text(encoding="utf-8"))
        assert [b["beat"] for b in beats] == ["hook", "stakes", "mechanism",
                                              "proof", "cta"]
        assert beats[1]["start_s"] > beats[0]["duration_s"] - 1e-6  # gap included
        for i in range(5):
            assert (ws / f"voice_beat_{i:02d}.mp3").exists()

    def test_zero_byte_segment_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        from shorts_engine.errors import EngineError
        monkeypatch.setattr(audio, "_synthesize",
                            _fake_synth_factory(garbage=("voice_beat_02.mp3",)))
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        with pytest.raises(EngineError, match="mechanism"):
            audio.run(_ctx(tmp_path))

    def test_duration_drift_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        from shorts_engine.errors import EngineError
        monkeypatch.setattr(audio, "_synthesize",
                            _fake_synth_factory(seconds_per_word=0.9))  # 2.3x too slow
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        with pytest.raises(EngineError, match="duration"):
            audio.run(_ctx(tmp_path))

    def test_prosody_mapping_used(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        seen = []
        base = _fake_synth_factory()
        def spy(segments, output_path, region, voice_override=None):
            seen.append(segments[0].prosody)
            return base(segments, output_path, region, voice_override)
        monkeypatch.setattr(audio, "_synthesize", spy)
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        audio.run(_ctx(tmp_path))
        assert seen == ["hook_emphasis", "urgent_problem", "conversational",
                        "matter_of_fact", "warm_cta"]


class TestTranscribeWordsExtension:
    def test_transcribe_words_exists_with_signature(self):
        import inspect
        from video_agent import subtitles
        sig = inspect.signature(subtitles.transcribe_words)
        assert list(sig.parameters) == ["audio_path", "narration_hint", "multilingual"]
```

- [ ] **Step 2: Run tests to verify they fail** — `audio` module missing; `transcribe_words` missing.

- [ ] **Step 3a: Extend `video_agent/subtitles.py`** (real project root)

Add above `generate_srt`:

```python
def transcribe_words(audio_path: Path, narration_hint: str | None = None,
                     multilingual: bool = False) -> list[dict]:
    """Whisper word timings as a flat list of {word, start, end} dicts."""
    model_name = WHISPER_MODEL_MULTILINGUAL if multilingual else WHISPER_MODEL
    model = WhisperModel(model_name, device=WHISPER_DEVICE,
                         compute_type=WHISPER_COMPUTE_TYPE)
    segments, _info = model.transcribe(
        str(audio_path), word_timestamps=True, initial_prompt=narration_hint,
    )
    flat_words = []
    for seg in segments:
        for w in (seg.words or []):
            flat_words.append({"word": w.word.strip(),
                               "start": float(w.start), "end": float(w.end)})
    return flat_words
```

Refactor `generate_srt` to call it (replace its transcription block):

```python
    flat_words = transcribe_words(audio_path, narration_hint, multilingual)
    cues = _chunk_words(flat_words, SUBTITLE_MAX_WORDS_PER_LINE,
                        SUBTITLE_MAX_LINE_DURATION_S)
```
(`_chunk_words`/`_flush` already `.strip()` words — stripping twice is harmless.)

- [ ] **Step 3b: Implement `shorts_engine/stages/audio.py`**

```python
"""Stage 5 — AUDIO: per-beat TTS (existing voiceover engine), stitch, word
timings. Guards: no tiny/zero segment files (F10); duration within ±15%."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

# Late-binding test seams (resolved at call time so monkeypatching works).
_synthesize = None
_transcribe = None


def _resolve():
    global _synthesize, _transcribe
    synth, trans = _synthesize, _transcribe
    if synth is None:
        from video_agent.voiceover import synthesize_segments
        synth = synthesize_segments
    if trans is None:
        from video_agent.subtitles import transcribe_words
        trans = transcribe_words
    return synth, trans


def run(ctx) -> dict[str, str]:
    from video_agent.voiceover import VoiceSegment
    from pydub import AudioSegment

    ws = Path(ctx.workspace)
    beats = json.loads((ws / "script.json").read_text(encoding="utf-8"))["beats"]
    region = json.loads((ws / "post.json").read_text(encoding="utf-8")).get(
        "region") or "default"
    synth, trans = _resolve()

    beat_files: list[Path] = []
    beat_durs: list[float] = []
    for i, beat in enumerate(beats):
        prosody = config.PROSODY_BY_BEAT.get(beat["beat"], "conversational")
        out = ws / f"voice_beat_{i:02d}.mp3"
        res = synth([VoiceSegment(beat["narration"], prosody)], out, region)
        if not out.exists() or out.stat().st_size < config.MIN_SEGMENT_BYTES:
            size = out.stat().st_size if out.exists() else -1
            raise EngineError(
                f"AUDIO: beat '{beat['beat']}' voice file invalid "
                f"({size} bytes < {config.MIN_SEGMENT_BYTES}) — {out}")
        beat_files.append(out)
        beat_durs.append(float(res["duration_s"]))

    est = sum(len(b["narration"].split()) for b in beats) / config.WORDS_PER_SECOND
    actual = sum(beat_durs) + (len(beats) - 1) * config.AUDIO_BEAT_GAP_MS / 1000
    if est > 0 and abs(actual - est) / est > config.AUDIO_DURATION_TOLERANCE:
        raise EngineError(
            f"AUDIO: total voice duration {actual:.1f}s deviates from script "
            f"estimate {est:.1f}s by more than "
            f"{config.AUDIO_DURATION_TOLERANCE:.0%}")

    gap = AudioSegment.silent(duration=config.AUDIO_BEAT_GAP_MS)
    combined = AudioSegment.from_mp3(str(beat_files[0]))
    starts = [0.0]
    for f, d in zip(beat_files[1:], beat_durs[:-1]):
        starts.append(starts[-1] + d + config.AUDIO_BEAT_GAP_MS / 1000)
        combined = combined + gap + AudioSegment.from_mp3(str(f))
    voice_path = ws / "voiceover.mp3"
    combined.export(str(voice_path), format="mp3", bitrate="128k")
    if voice_path.stat().st_size < config.MIN_SEGMENT_BYTES:
        raise EngineError("AUDIO: stitched voiceover.mp3 is undersized")

    hint = " ".join(b["narration"] for b in beats)
    words = trans(voice_path, narration_hint=hint)
    (ws / "word_timings.json").write_text(json.dumps(words, indent=2),
                                          encoding="utf-8")
    beats_audio = [{"beat": b["beat"], "start_s": round(s, 3),
                    "duration_s": round(d, 3)}
                   for b, s, d in zip(beats, starts, beat_durs)]
    (ws / "beats_audio.json").write_text(json.dumps(beats_audio, indent=2),
                                         encoding="utf-8")
    logger.info("audio: %.1fs voice across %d beats (est %.1fs)",
                actual, len(beats), est)
    return {"voice": "voiceover.mp3", "word_timings": "word_timings.json",
            "beats_audio": "beats_audio.json"}
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_audio.py -v`

- [ ] **Step 5: Run BOTH suites**

Run: `python -m pytest tests/shorts_engine -q` (workspace) — green.
Run from the real project root: `cd "E:\Projects\HRSU Blog"; python -m pytest tests -q` — no regressions vs. its pre-task pass count (record both numbers in the task report).

---

### Task 10: VISUALS stage — designed-only dispatch + never-blank enforcement

**Files:**
- Create: `shorts_engine/stages/visuals.py`
- Test: `tests/shorts_engine/test_visuals.py`

**Interfaces:**
- Consumes: `shotlist.json`; all card renderers; `config.TRANSITION_FADE_S/MIN_CONTENT_PIXELS/LUMA_CONTENT_THRESHOLD`; `encoder.probe_duration`.
- Produces:
  - `RENDERERS: dict[str, callable]` mapping `HEADLINE_CARD/STAT_CARD/DIAGRAM/QUOTE_CARD/LOGO_CTA` → each module's `render` (this registry is also reused by ASSEMBLE's re-render step).
  - `resolve_shot(shot: dict) -> tuple[str, dict, dict]` → `(render_type, render_payload, provenance)`. `PAPER_CARD` (and any future `BROLL`) resolves to its declared `fallback` with provenance `{"resolved": "fallback", "reason": "acquisition_deferred_to_phase5", "planned_type": "PAPER_CARD"}`; missing fallback ⇒ `EngineError`. Designed types resolve to themselves with `{"resolved": "designed"}`.
  - `content_pixels(frame_png: Path) -> int` — bright-pixel count (luma > `LUMA_CONTENT_THRESHOLD`).
  - `sample_frame(mp4: Path, t: float, out_png: Path) -> Path` (ffmpeg single-frame extract).
  - `run(ctx) -> {"shots_dir": "shots", "visuals_report": "visuals_report.json"}` — renders `shots/shot_s00.mp4` etc.; first shot of every beat except the first gets `fade_in_s=TRANSITION_FADE_S`; after each render, samples the mid frame and raises `EngineError` if `content_pixels < MIN_CONTENT_PIXELS` (never-blank, F1); report records provenance + duration per shot.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_visuals.py
from __future__ import annotations
import json
from pathlib import Path
import pytest

SHOTS = {"shots": [
    {"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 2.0,
     "narration_span": "x", "payload": {"text": "Nitrate limits tighten"},
     "fallback": None},
    {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 2.0,
     "narration_span": "x", "payload": {"marker": 2, "url": "https://mdpi.com/x"},
     "fallback": {"type": "QUOTE_CARD",
                  "payload": {"quote": "dosage range of 1.5 to 3 kg",
                              "source": "Source [2] — mdpi.com"}}},
    {"id": "s02", "beat": "cta", "type": "LOGO_CTA", "duration_s": 2.0,
     "narration_span": "x", "payload": {"differentiator": "high-purity",
                                        "cta_line": "guide", "domain": "hrsuindore.com"},
     "fallback": None},
], "total_s": 6.0}


class TestResolveShot:
    def test_designed_resolves_to_itself(self):
        from shorts_engine.stages import visuals
        t, p, prov = visuals.resolve_shot(SHOTS["shots"][0])
        assert t == "HEADLINE_CARD" and prov["resolved"] == "designed"

    def test_paper_card_resolves_to_fallback(self):
        from shorts_engine.stages import visuals
        t, p, prov = visuals.resolve_shot(SHOTS["shots"][1])
        assert t == "QUOTE_CARD"
        assert prov["resolved"] == "fallback"
        assert prov["planned_type"] == "PAPER_CARD"

    def test_paper_card_without_fallback_raises(self):
        from shorts_engine.stages import visuals
        from shorts_engine.errors import EngineError
        bad = dict(SHOTS["shots"][1], fallback=None)
        with pytest.raises(EngineError):
            visuals.resolve_shot(bad)


class TestRun:
    def _ctx(self, tmp_path):
        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = Path(m.workspace)
        (ws / "shotlist.json").write_text(json.dumps(SHOTS), encoding="utf-8")
        return StageContext(manifest=m, workspace=ws, flags={})

    def test_all_shots_render_with_content(self, tmp_path):
        from shorts_engine.stages import visuals
        from shorts_engine.cards import encoder
        ctx = self._ctx(tmp_path)
        arts = visuals.run(ctx)
        ws = Path(ctx.workspace)
        report = json.loads((ws / arts["visuals_report"]).read_text(encoding="utf-8"))
        assert len(report["shots"]) == 3
        for entry in report["shots"]:
            clip = ws / "shots" / f"shot_{entry['id']}.mp4"
            assert clip.exists()
            assert entry["content_pixels"] >= 500
        assert report["shots"][1]["provenance"]["resolved"] == "fallback"
        # beat boundary fade: s01 (proof, not first beat) got fade_in
        assert report["shots"][1]["fade_in_s"] == 0.25
        assert report["shots"][0]["fade_in_s"] == 0.0

    def test_blank_render_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from shorts_engine.errors import EngineError
        monkeypatch.setitem(visuals.RENDERERS, "HEADLINE_CARD",
                            _blank_renderer())
        with pytest.raises(EngineError, match="content"):
            visuals.run(self._ctx(tmp_path))


def _blank_renderer():
    from shorts_engine.cards import theme
    def render(payload, duration, out_path, fade_in_s=0.0):
        return theme.render_card(lambda p, t, d: theme.background(t), payload,
                                 duration, out_path, fade_in_s)
    return render
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/stages/visuals.py`**

```python
"""Stage 6 — VISUALS: render every shot to mp4. Designed cards render
directly; PAPER_CARD/BROLL resolve to their declared fallback until the
Plan-3 acquisition ladder lands. Never-blank is enforced with a bright-pixel
check on a sampled mid frame."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from shorts_engine import config
from shorts_engine.cards import (encoder, headline_card, stat_card, diagram_card,
                                 quote_card, logo_cta_card)
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

RENDERERS = {
    "HEADLINE_CARD": headline_card.render,
    "STAT_CARD": stat_card.render,
    "DIAGRAM": diagram_card.render,
    "QUOTE_CARD": quote_card.render,
    "LOGO_CTA": logo_cta_card.render,
}

_DEFERRED = {"PAPER_CARD", "BROLL"}  # acquisition lands in Plan 3


def resolve_shot(shot: dict) -> tuple[str, dict, dict]:
    stype = shot["type"]
    if stype in RENDERERS:
        return stype, shot["payload"], {"resolved": "designed"}
    if stype in _DEFERRED:
        fb = shot.get("fallback")
        if not fb or fb.get("type") not in RENDERERS:
            raise EngineError(f"{shot['id']}: {stype} has no renderable fallback")
        return fb["type"], fb["payload"], {
            "resolved": "fallback",
            "reason": "acquisition_deferred_to_phase5",
            "planned_type": stype,
        }
    raise EngineError(f"{shot['id']}: unknown shot type {stype}")


def sample_frame(mp4: Path, t: float, out_png: Path) -> Path:
    res = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}",
         "-i", str(mp4), "-frames:v", "1", str(out_png)],
        capture_output=True, text=True)
    if res.returncode != 0 or not out_png.exists():
        raise EngineError(f"frame sample failed for {mp4}: {res.stderr}")
    return out_png


def content_pixels(frame_png: Path) -> int:
    arr = np.asarray(Image.open(frame_png).convert("L"))
    return int((arr > config.LUMA_CONTENT_THRESHOLD).sum())


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    shots = json.loads((ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]
    shots_dir = ws / "shots"
    shots_dir.mkdir(exist_ok=True)
    report = {"shots": []}
    prev_beat = None
    first_beat = shots[0]["beat"] if shots else None
    for shot in shots:
        fade = config.TRANSITION_FADE_S if (
            shot["beat"] != prev_beat and shot["beat"] != first_beat) else 0.0
        prev_beat = shot["beat"]
        rtype, payload, prov = resolve_shot(shot)
        out = shots_dir / f"shot_{shot['id']}.mp4"
        RENDERERS[rtype](payload, shot["duration_s"], out, fade_in_s=fade)
        png = shots_dir / f"shot_{shot['id']}_mid.png"
        sample_frame(out, shot["duration_s"] / 2, png)
        pixels = content_pixels(png)
        if pixels < config.MIN_CONTENT_PIXELS:
            raise EngineError(
                f"VISUALS: shot {shot['id']} ({rtype}) rendered without visible "
                f"content ({pixels} bright px < {config.MIN_CONTENT_PIXELS}) — "
                f"never-blank violated")
        report["shots"].append({
            "id": shot["id"], "beat": shot["beat"], "rendered_type": rtype,
            "duration_s": shot["duration_s"], "fade_in_s": fade,
            "content_pixels": pixels, "provenance": prov,
        })
        logger.info("visuals: %s -> %s (%d px)", shot["id"], rtype, pixels)
    (ws / "visuals_report.json").write_text(json.dumps(report, indent=2),
                                            encoding="utf-8")
    return {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_visuals.py -v`
(Note: this renders ~6s of video; expect ~20–40s test time.)

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 11: ASSEMBLE part 1 — re-flow math + ASS caption builder (pure functions)

**Files:**
- Create: `shorts_engine/stages/assemble.py` (pure functions only in this task; `run()` comes in Task 12)
- Test: `tests/shorts_engine/test_assemble_pure.py`

**Interfaces:**
- Consumes: `config` bounds; shot dicts; `beats_audio.json` entries; word-timing dicts.
- Produces (used by Task 12 and its tests):
  - `beat_spans(beats_audio: list[dict], voice_total_s: float) -> list[dict]` — `[{"beat", "start_s", "span_s"}]` where span runs to the next beat's start (so inter-beat gaps belong to the beat before them) and the LAST beat's span = `voice_total_s - start_s + END_CARD_HOLD_S`. Σ spans = `voice_total_s + END_CARD_HOLD_S` exactly.
  - `reflow(shots: list[dict], beats_audio: list[dict], voice_total_s: float) -> list[dict]` — copies shots with `duration_s` scaled so each beat's shots sum exactly to that beat's span: proportional scale, clamp to `[SHOT_MIN_S, SHOT_MAX_S]` (`LOGO_CTA_MAX_S` for LOGO_CTA), then the beat's **last** shot absorbs the residual (and may exceed its cap by up to the residual — the end card legitimately holds). Adds `"reflow_delta_s"` per shot.
  - `group_words_into_cues(words: list[dict], max_words: int = 3, max_dur_s: float = 1.5) -> list[dict]` — `[{"start", "end", "text"}]`, text uppercased.
  - `build_ass(words: list[dict], out_path: Path) -> Path` — ASS file: `PlayResX: 1080 / PlayResY: 1920`, one `Cap` style using the body font name, `Fontsize 60`, `Alignment 2` (bottom-center), `MarginV 440` (≥ SAFE_BOTTOM_PX), `BorderStyle 3` boxed, primary colour `&H00F6D6CC` (BGR of #ccd6f6), box colour `&H90000000`.
  - `ass_time(s: float) -> str` — `H:MM:SS.cc`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_assemble_pure.py
from __future__ import annotations
from pathlib import Path
from shorts_engine import config

BEATS_AUDIO = [
    {"beat": "hook", "start_s": 0.0, "duration_s": 3.0},
    {"beat": "stakes", "start_s": 3.3, "duration_s": 5.0},
    {"beat": "mechanism", "start_s": 8.6, "duration_s": 10.0},
    {"beat": "proof", "start_s": 18.9, "duration_s": 8.0},
    {"beat": "cta", "start_s": 27.2, "duration_s": 7.0},
]
VOICE_TOTAL = 34.2

SHOTS = (
    [{"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 3.0,
      "narration_span": "", "payload": {}, "fallback": None}] +
    [{"id": "s01", "beat": "stakes", "type": "STAT_CARD", "duration_s": 4.6,
      "narration_span": "", "payload": {}, "fallback": None}] +
    [{"id": f"s0{i}", "beat": "mechanism", "type": "DIAGRAM", "duration_s": 3.4,
      "narration_span": "", "payload": {}, "fallback": None} for i in (2, 3, 4)] +
    [{"id": "s05", "beat": "proof", "type": "STAT_CARD", "duration_s": 4.0,
      "narration_span": "", "payload": {}, "fallback": None},
     {"id": "s06", "beat": "proof", "type": "QUOTE_CARD", "duration_s": 4.0,
      "narration_span": "", "payload": {}, "fallback": None},
     {"id": "s07", "beat": "cta", "type": "LOGO_CTA", "duration_s": 7.0,
      "narration_span": "", "payload": {}, "fallback": None}]
)


class TestBeatSpans:
    def test_spans_cover_voice_plus_hold(self):
        from shorts_engine.stages import assemble
        spans = assemble.beat_spans(BEATS_AUDIO, VOICE_TOTAL)
        assert abs(sum(s["span_s"] for s in spans)
                   - (VOICE_TOTAL + config.END_CARD_HOLD_S)) < 1e-6
        assert spans[0]["span_s"] == 3.3   # runs to next beat start (gap included)
        assert abs(spans[-1]["span_s"] - (34.2 - 27.2 + 1.5)) < 1e-6


class TestReflow:
    def test_beat_sums_match_spans_exactly(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        spans = {s["beat"]: s["span_s"]
                 for s in assemble.beat_spans(BEATS_AUDIO, VOICE_TOTAL)}
        for beat, span in spans.items():
            got = sum(s["duration_s"] for s in out if s["beat"] == beat)
            assert abs(got - span) < 1e-6, beat

    def test_total_equals_voice_plus_hold(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        assert abs(sum(s["duration_s"] for s in out)
                   - (VOICE_TOTAL + config.END_CARD_HOLD_S)) < 1e-6

    def test_non_last_shots_respect_bounds(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        by_beat: dict[str, list] = {}
        for s in out:
            by_beat.setdefault(s["beat"], []).append(s)
        for beat, group in by_beat.items():
            for s in group[:-1]:
                assert config.SHOT_MIN_S - 1e-6 <= s["duration_s"] \
                       <= config.SHOT_MAX_S + 1e-6

    def test_delta_recorded(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        assert all("reflow_delta_s" in s for s in out)


class TestCaptions:
    WORDS = [{"word": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.35}
             for i in range(10)]

    def test_cues_grouped_and_uppercase(self):
        from shorts_engine.stages import assemble
        cues = assemble.group_words_into_cues(self.WORDS)
        assert all(len(c["text"].split()) <= 3 for c in cues)
        assert all(c["end"] - c["start"] <= 1.5 + 1e-6 for c in cues)
        assert cues[0]["text"] == cues[0]["text"].upper()

    def test_ass_time_format(self):
        from shorts_engine.stages import assemble
        assert assemble.ass_time(0.0) == "0:00:00.00"
        assert assemble.ass_time(65.37) == "0:01:05.37"

    def test_build_ass_margins_and_style(self, tmp_path):
        from shorts_engine.stages import assemble
        p = assemble.build_ass(self.WORDS, tmp_path / "c.ass")
        text = Path(p).read_text(encoding="utf-8")
        assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
        style = next(l for l in text.splitlines() if l.startswith("Style: Cap"))
        fields = style.split(",")
        assert int(fields[-3]) >= config.SAFE_BOTTOM_PX  # MarginV
        assert "Dialogue:" in text
        assert "&H00F6D6CC" in style.upper()
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement the pure half of `shorts_engine/stages/assemble.py`**

```python
"""Stage 7 — ASSEMBLE. Pure half: re-flow shot durations onto real audio and
build burned-caption ASS. run() (Task 12) concats, muxes, and asserts the
duration law: video = voice + END_CARD_HOLD_S, never `-shortest`."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def beat_spans(beats_audio: list[dict], voice_total_s: float) -> list[dict]:
    spans = []
    for i, b in enumerate(beats_audio):
        if i + 1 < len(beats_audio):
            span = beats_audio[i + 1]["start_s"] - b["start_s"]
        else:
            span = voice_total_s - b["start_s"] + config.END_CARD_HOLD_S
        spans.append({"beat": b["beat"], "start_s": b["start_s"],
                      "span_s": span})
    return spans


def _cap(shot: dict) -> float:
    return config.LOGO_CTA_MAX_S if shot["type"] == "LOGO_CTA" else config.SHOT_MAX_S


def reflow(shots: list[dict], beats_audio: list[dict],
           voice_total_s: float) -> list[dict]:
    spans = {s["beat"]: s["span_s"] for s in beat_spans(beats_audio, voice_total_s)}
    out: list[dict] = []
    for beat, span in spans.items():
        group = [dict(s) for s in shots if s["beat"] == beat]
        if not group:
            raise EngineError(f"reflow: no shots for beat '{beat}'")
        planned = sum(s["duration_s"] for s in group) or 1.0
        scale = span / planned
        for s in group[:-1]:
            new = min(max(s["duration_s"] * scale, config.SHOT_MIN_S), _cap(s))
            s["reflow_delta_s"] = round(new - s["duration_s"], 3)
            s["duration_s"] = new
        last = group[-1]
        allotted = sum(s["duration_s"] for s in group[:-1])
        new_last = span - allotted
        if new_last < config.SHOT_MIN_S / 2:
            raise EngineError(f"reflow: beat '{beat}' leaves last shot "
                              f"{new_last:.2f}s — audio/shot mismatch")
        last["reflow_delta_s"] = round(new_last - last["duration_s"], 3)
        last["duration_s"] = new_last
        out.extend(group)
    return out


def group_words_into_cues(words: list[dict], max_words: int = 3,
                          max_dur_s: float = 1.5) -> list[dict]:
    cues, buf = [], []

    def flush():
        if buf:
            cues.append({"start": buf[0]["start"], "end": buf[-1]["end"],
                         "text": " ".join(w["word"].strip().upper()
                                          for w in buf)})
            buf.clear()

    for w in words:
        if buf and (len(buf) >= max_words
                    or w["end"] - buf[0]["start"] > max_dur_s):
            flush()
        buf.append(w)
    flush()
    return cues


def ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins,60,&H00F6D6CC,&H000000FF,&H00101826,&H90000000,-1,0,0,0,100,100,0,0,3,6,0,2,72,72,440,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[dict], out_path: Path) -> Path:
    out_path = Path(out_path)
    lines = [_ASS_HEADER]
    for cue in group_words_into_cues(words):
        lines.append(f"Dialogue: 0,{ass_time(cue['start'])},"
                     f"{ass_time(cue['end'])},Cap,,0,0,0,,{cue['text']}\n")
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_assemble_pure.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 12: ASSEMBLE part 2 — `run()`: re-render, concat, captions, music, logo bug, progress bar, duration law

**Files:**
- Modify: `shorts_engine/stages/assemble.py` (add `run()` + ffmpeg helpers)
- Test: `tests/shorts_engine/test_assemble_run.py`

**Interfaces:**
- Consumes: Task 11 pure functions; `visuals.RENDERERS` + `visuals.resolve_shot` (re-render at final durations); `encoder.probe_duration`; `video_agent.music.mix_music_under_voice`; `config` (END_CARD_HOLD_S, AUDIO_COMPLETENESS_MARGIN_S, CARD_RERENDER_EPSILON_S, TRANSITION_FADE_S, BRAND_LOGO_FILE).
- Produces: `run(ctx) -> {"video": "video_short.mp4", "captions": "captions.ass", "assemble_report": "assemble_report.json"}`.
- `run()` algorithm:
  1. Load `shotlist.json`, `beats_audio.json`, `word_timings.json`; `voice_total_s = probe_duration(voiceover.mp3)`.
  2. `final_shots = reflow(...)`. Re-render every shot whose `|reflow_delta_s| > CARD_RERENDER_EPSILON_S` into `shots_final/` at its new duration (same fade-in rule as VISUALS: first shot of beats 2–5); shots within epsilon are copied from `shots/`.
  3. Demuxer concat (`ffmpeg -f concat -safe 0 -i list.txt -c copy silent.mp4`) — all clips share codec settings so `-c copy` is lossless and exact.
  4. `build_ass(words, captions.ass)`.
  5. Music: `mixed = mix_music_under_voice(voiceover.mp3, music_mix.mp3, region)` — if it returns the input path (no track for region), use plain voiceover. (Its internal `-shortest` is an audio-only mux — allowed; the ban applies to the final video+audio mux.)
  6. Final mux (single ffmpeg call, **no `-shortest` anywhere**): burn ASS; overlay logo bug (96px wide, 85% opacity, top-right at (W−w−40, 40)) when `BRAND_LOGO_FILE` exists; gold progress bar 1080×6 at y=1914 sliding `x = -1080 + 1080·t/T`; map video + audio; audio is ~1.5s shorter than video — the end card holds in silence by design.
  7. Assert: `probe_duration(video_short.mp4) ≥ voice_total_s + AUDIO_COMPLETENESS_MARGIN_S` AND within ±0.35s of `voice_total_s + END_CARD_HOLD_S`; else `EngineError`.
  8. Write `assemble_report.json`: per-shot final durations + deltas + rerendered flag, voice_total_s, video duration, music_used.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_assemble_run.py
from __future__ import annotations
import json
from pathlib import Path
import pytest
from pydub import AudioSegment

SHOTS = {"shots": [
    {"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 2.5,
     "narration_span": "", "payload": {"text": "Nitrate limits tighten"},
     "fallback": None},
    {"id": "s01", "beat": "cta", "type": "LOGO_CTA", "duration_s": 3.0,
     "narration_span": "", "payload": {"differentiator": "high-purity",
                                       "cta_line": "guide",
                                       "domain": "hrsuindore.com"},
     "fallback": None},
], "total_s": 5.5}
BEATS_AUDIO = [{"beat": "hook", "start_s": 0.0, "duration_s": 2.4},
               {"beat": "cta", "start_s": 2.7, "duration_s": 2.8}]
WORDS = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
         for i, w in enumerate("nitrate limits are tightening act now".split())]


def _ctx(tmp_path):
    from shorts_engine.manifest import RunManifest
    from shorts_engine.runner import StageContext
    m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
    ws = Path(m.workspace)
    (ws / "shotlist.json").write_text(json.dumps(SHOTS), encoding="utf-8")
    (ws / "beats_audio.json").write_text(json.dumps(BEATS_AUDIO), encoding="utf-8")
    (ws / "word_timings.json").write_text(json.dumps(WORDS), encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
    AudioSegment.silent(duration=5500).export(str(ws / "voiceover.mp3"),
                                              format="mp3", bitrate="128k")
    # pre-rendered shots dir as VISUALS would leave it
    from shorts_engine.stages import visuals
    (ws / "shots").mkdir()
    for s in SHOTS["shots"]:
        visuals.RENDERERS[s["type"]](s["payload"], s["duration_s"],
                                     ws / "shots" / f"shot_{s['id']}.mp4")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestAssembleRun:
    def test_duration_law_and_artifacts(self, tmp_path):
        from shorts_engine.stages import assemble
        from shorts_engine.cards import encoder
        from shorts_engine import config
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        video = ws / arts["video"]
        assert video.exists()
        voice = encoder.probe_duration(ws / "voiceover.mp3")
        vd = encoder.probe_duration(video)
        assert vd >= voice + config.AUDIO_COMPLETENESS_MARGIN_S
        assert abs(vd - (voice + config.END_CARD_HOLD_S)) <= 0.35
        assert (ws / arts["captions"]).exists()
        report = json.loads((ws / arts["assemble_report"]).read_text(encoding="utf-8"))
        assert report["video_duration_s"] >= report["voice_total_s"] + 1.4
        assert len(report["shots"]) == 2

    def test_video_has_audio_stream(self, tmp_path):
        import subprocess
        from shorts_engine.stages import assemble
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0",
             str(ws / arts["video"])], capture_output=True, text=True)
        assert "audio" in res.stdout

    def test_no_shortest_in_final_mux(self):
        import inspect
        from shorts_engine.stages import assemble
        src = inspect.getsource(assemble.run) + inspect.getsource(assemble._final_mux)
        assert "-shortest" not in src

    def test_rerender_only_beyond_epsilon(self, tmp_path):
        from shorts_engine.stages import assemble
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        report = json.loads((ws / arts["assemble_report"]).read_text(encoding="utf-8"))
        for s in report["shots"]:
            assert s["rerendered"] == (abs(s["reflow_delta_s"]) > 0.05)
```

- [ ] **Step 2: Run tests to verify they fail** — `assemble.run` missing.

- [ ] **Step 3: Add `run()` and helpers to `shorts_engine/stages/assemble.py`**

```python
def _ffmpeg(args: list[str], what: str) -> None:
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise EngineError(f"ASSEMBLE {what} failed: {res.stderr[:800]}")


def _ass_filter_path(p: Path) -> str:
    # ffmpeg filter-string escaping for Windows paths
    return str(p).replace("\\", "/").replace(":", "\\:")


def _final_mux(silent: Path, audio: Path, ass_path: Path, out: Path,
               video_len_s: float) -> None:
    logo = config.BRAND_LOGO_FILE
    use_logo = logo.exists()
    inputs = ["-i", str(silent), "-i", str(audio)]
    if use_logo:
        inputs += ["-i", str(logo)]
    fc = [f"[0:v]ass='{_ass_filter_path(ass_path)}'[v1]"]
    last = "v1"
    if use_logo:
        fc.append("[2:v]scale=96:-1,format=rgba,colorchannelmixer=aa=0.85[lg]")
        fc.append(f"[{last}][lg]overlay=W-w-40:40[v2]")
        last = "v2"
    fc.append(f"color=c=0xd4af37:s=1080x6:d={video_len_s:.3f}[bar]")
    fc.append(f"[{last}][bar]overlay=x='-1080+1080*t/{video_len_s:.3f}'"
              f":y=1914:eof_action=pass[vout]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(fc),
             "-map", "[vout]", "-map", "1:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
             str(out)], "final mux")


def run(ctx) -> dict[str, str]:
    from shorts_engine.cards import encoder
    from shorts_engine.stages import visuals

    ws = Path(ctx.workspace)
    shots = json.loads((ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]
    beats_audio = json.loads((ws / "beats_audio.json").read_text(encoding="utf-8"))
    words = json.loads((ws / "word_timings.json").read_text(encoding="utf-8"))
    region = json.loads((ws / "post.json").read_text(encoding="utf-8")).get(
        "region") or "default"
    voice = ws / "voiceover.mp3"
    voice_total = encoder.probe_duration(voice)

    final_shots = reflow(shots, beats_audio, voice_total)
    final_dir = ws / "shots_final"
    final_dir.mkdir(exist_ok=True)
    first_beat = final_shots[0]["beat"] if final_shots else None
    prev_beat = None
    report_shots = []
    concat_lines = []
    for shot in final_shots:
        fade = config.TRANSITION_FADE_S if (
            shot["beat"] != prev_beat and shot["beat"] != first_beat) else 0.0
        prev_beat = shot["beat"]
        src = ws / "shots" / f"shot_{shot['id']}.mp4"
        dst = final_dir / f"shot_{shot['id']}.mp4"
        rerender = abs(shot.get("reflow_delta_s", 0.0)) > config.CARD_RERENDER_EPSILON_S
        if rerender or not src.exists():
            rtype, payload, _prov = visuals.resolve_shot(shot)
            visuals.RENDERERS[rtype](payload, shot["duration_s"], dst,
                                     fade_in_s=fade)
            rerender = True
        else:
            dst.write_bytes(src.read_bytes())
        concat_lines.append(f"file '{dst.resolve().as_posix()}'\n")
        report_shots.append({"id": shot["id"], "beat": shot["beat"],
                             "final_duration_s": round(shot["duration_s"], 3),
                             "reflow_delta_s": shot.get("reflow_delta_s", 0.0),
                             "rerendered": rerender})

    concat_file = ws / "concat.txt"
    concat_file.write_text("".join(concat_lines), encoding="utf-8")
    silent = ws / "silent.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c", "copy", str(silent)], "concat")

    ass_path = build_ass(words, ws / "captions.ass")

    from video_agent.music import mix_music_under_voice
    mixed = mix_music_under_voice(voice, ws / "music_mix.mp3", region)
    music_used = Path(mixed) != voice

    video_len = sum(s["duration_s"] for s in final_shots)
    out = ws / "video_short.mp4"
    _final_mux(silent, Path(mixed), ass_path, out, video_len)

    vd = encoder.probe_duration(out)
    if vd < voice_total + config.AUDIO_COMPLETENESS_MARGIN_S:
        raise EngineError(f"ASSEMBLE: video {vd:.2f}s < voice {voice_total:.2f}s "
                          f"+ {config.AUDIO_COMPLETENESS_MARGIN_S}s — CTA would clip")
    if abs(vd - (voice_total + config.END_CARD_HOLD_S)) > 0.35:
        raise EngineError(f"ASSEMBLE: video {vd:.2f}s violates duration law "
                          f"(voice {voice_total:.2f}s + hold "
                          f"{config.END_CARD_HOLD_S}s)")

    report = {"voice_total_s": round(voice_total, 3),
              "video_duration_s": round(vd, 3), "music_used": music_used,
              "shots": report_shots}
    (ws / "assemble_report.json").write_text(json.dumps(report, indent=2),
                                             encoding="utf-8")
    logger.info("assemble: %.1fs video over %.1fs voice (music=%s)",
                vd, voice_total, music_used)
    return {"video": "video_short.mp4", "captions": "captions.ass",
            "assemble_report": "assemble_report.json"}
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_assemble_run.py -v`
(Renders ~7s of video twice; expect ~1–2 min.)

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 13: CLI wiring — four new stages + `--torture`

**Files:**
- Modify: `shorts_engine/cli.py`
- Test: extend `tests/shorts_engine/test_cli.py`

**Interfaces:**
- `build_stages()` returns, in order: `[("ingest","ingested",ingest.run), ("facts","facts",facts.run), ("script","scripted",script.run), ("shotlist","shotlisted",shotlist.run), ("audio","audio",audio.run), ("visuals","visuals",visuals.run), ("assemble","assembled",assemble.run)]`.
- `--until` choices: `{ingest,facts,script,shotlist,audio,visuals,assemble}` mapping to manifest statuses `{...,"shotlist":"shotlisted","audio":"audio","visuals":"visuals","assemble":"assembled"}`.
- New flag `--torture`: sets `flags["torture"] = True`. Phase-4 semantics: identical full run to `assembled` (no web tiers exist yet); the flag is recorded so Plan-3 sourcing can hard-disable web tiers when it sees it.

- [ ] **Step 1: Write the failing tests** (append to `tests/shorts_engine/test_cli.py`)

```python
class TestPhase2Stages:
    def test_build_stages_has_seven_in_order(self):
        from shorts_engine.cli import build_stages
        names = [s[0] for s in build_stages()]
        assert names == ["ingest", "facts", "script", "shotlist", "audio",
                         "visuals", "assemble"]
        statuses = [s[1] for s in build_stages()]
        assert statuses == ["ingested", "facts", "scripted", "shotlisted",
                            "audio", "visuals", "assembled"]

    def test_until_accepts_new_stages(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False,
                     flags=None):
            captured.update(until=until, flags=flags)
            class M:  # minimal manifest stand-in
                status, artifacts, run_id = "assembled", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        rc = cli.main(["https://x.html", "--until", "assemble", "--torture"])
        assert rc == 0
        assert captured["until"] == "assembled"
        assert captured["flags"]["torture"] is True
```

- [ ] **Step 2: Run to verify failure** → new tests fail (missing stages/flag).

- [ ] **Step 3: Implement** — extend `build_stages()` with the four imports (`from shorts_engine.stages import shotlist, audio, visuals, assemble`), extend the `--until` choices list and its stage→status mapping dict, add `parser.add_argument("--torture", action="store_true", ...)` and pass `flags["torture"] = args.torture`.

- [ ] **Step 4-5: Run tests, then full suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 14: Golden integration test — fixture → finished mp4, both invariants + duration law

**Files:**
- Test: `tests/shorts_engine/test_integration_phase2.py`

**Interfaces:** consumes everything; produces no new modules. Mocks ONLY: `text_llm.generate_schema_json` (canned facts/script/critique), `audio._synthesize`, `audio._transcribe`. Everything else (ingest isolation, gates, shotlist, card rendering, ffmpeg assemble) runs real.

- [ ] **Step 1: Write the test**

```python
# tests/shorts_engine/test_integration_phase2.py
"""Golden integration: poisoned fixture → assembled mp4 with mocked LLM/TTS.
Asserts: pipeline completes; duration law holds; every shot has visible
content; captions sit inside the safe zone; no unverified numeric survives."""
from __future__ import annotations
import json
import re
from pathlib import Path
import pytest
from pydub import AudioSegment

FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"
URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

REAL_QUOTE = "dosage range of 1.5 to 3 kg per cubic meter"

FACTS_RESPONSE = {"facts": [
    {"id": "f1", "verbatim_quote": REAL_QUOTE, "value": "1.5 to 3",
     "unit": "kg/m3", "claim_summary": "dosing window", "tags": ["spec"],
     "procurement_significance": 5, "citation_marker": None},
]}

BEATS = [
    {"beat": "hook", "narration": "Your effluent nitrate is creeping toward "
     "the discharge limit again.", "fact_ids": [],
     "card_text": "Nitrate limits are tightening", "broll_wish": ""},
    {"beat": "stakes", "narration": "European plants hold the line with a "
     "dosage range of 1.5 to 3 kg per cubic meter, applied continuously.",
     "fact_ids": ["f1"], "card_text": "The dosing window that works",
     "broll_wish": ""},
    {"beat": "mechanism", "narration": "Calcium nitrate feeds the denitrifying "
     "bacteria already in your basin, so they strip oxygen from nitrate and "
     "release harmless nitrogen gas, with no retrofit and no new hardware.",
     "fact_ids": ["f1"], "card_text": "Bacteria do the removal",
     "broll_wish": "", "diagram_labels": ["Effluent in", "Dosing",
                                          "Denitrifying bacteria", "Nitrogen out"]},
    {"beat": "proof", "narration": "The published dosing window of 1.5 to 3 "
     "kilograms per cubic meter comes straight from the technical guide "
     "underpinning this post.", "fact_ids": ["f1"],
     "card_text": "A proven dosing window", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies consistent high purity powder "
     "with batch level quality control. Read the full dosing guide on the "
     "HRSU blog at hrsuindore dot com.", "fact_ids": ["b_purity"],
     "card_text": "Get the dosing guide", "broll_wish": ""},
]
CRITIQUE = {"actionable_score": 9, "coherence_score": 9, "hrsu_reason_score": 9,
            "revise_notes": ""}


def _llm_router(prompt, system, schema, **kw):
    props = schema.get("properties", {})
    if "facts" in props:
        return FACTS_RESPONSE
    if "beats" in props:
        return {"beats": BEATS}
    return CRITIQUE


def _fake_synth(segments, output_path, region, voice_override=None):
    n_words = len(segments[0].text.split())
    ms = int(n_words / 2.6 * 1000)
    AudioSegment.silent(duration=max(ms, 300)).export(str(output_path),
                                                      format="mp3",
                                                      bitrate="128k")
    return {"audio_path": Path(output_path), "duration_s": ms / 1000,
            "voice_used": "test", "engine_used": "fake", "fell_back": False}


def _fake_transcribe(audio_path, narration_hint=None, multilingual=False):
    words = (narration_hint or "").split()
    if not words:
        return [{"word": "x", "start": 0.0, "end": 0.3}]
    step = 1 / 2.6
    return [{"word": w, "start": round(i * step, 3),
             "end": round(i * step + step * 0.85, 3)}
            for i, w in enumerate(words)]


@pytest.mark.slow
class TestGoldenPipeline:
    def test_fixture_to_assembled_video(self, tmp_path, monkeypatch):
        import shorts_engine.stages.facts as facts_stage
        import shorts_engine.stages.script as script_stage
        from shorts_engine.stages import audio as audio_stage
        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json",
                            _llm_router)
        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json",
                            _llm_router)
        monkeypatch.setattr(audio_stage, "_synthesize", _fake_synth)
        monkeypatch.setattr(audio_stage, "_transcribe", _fake_transcribe)

        from shorts_engine import runner, config
        from shorts_engine.cli import build_stages
        from shorts_engine.cards import encoder

        html = FIXTURE.read_text(encoding="utf-8")
        manifest = runner.run(URL, build_stages(), workspace_root=tmp_path,
                              until="assembled",
                              flags={"html_override": html})
        assert manifest.status == "assembled"
        ws = Path(manifest.workspace)

        # 1) isolation invariant survived the whole pipeline
        canonical = (ws / "canonical.txt").read_text(encoding="utf-8")
        assert "150,000 metric tons" not in canonical
        assert "1.5 to 3 kg" in canonical

        # 2) never-unverified: every numeric in narration traces to fact/brand
        script_doc = json.loads((ws / "script.json").read_text(encoding="utf-8"))
        for b in script_doc["beats"]:
            for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", b["narration"]):
                assert tok in {"1.5", "3"}, f"untraced numeric {tok}"

        # 3) duration law
        voice = encoder.probe_duration(ws / "voiceover.mp3")
        video = encoder.probe_duration(ws / "video_short.mp4")
        assert video >= voice + config.AUDIO_COMPLETENESS_MARGIN_S
        assert abs(video - (voice + config.END_CARD_HOLD_S)) <= 0.35

        # 4) never-blank: visuals report says every shot had content
        report = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        assert all(s["content_pixels"] >= config.MIN_CONTENT_PIXELS
                   for s in report["shots"])

        # 5) captions inside safe zone
        style = next(l for l in (ws / "captions.ass").read_text(
            encoding="utf-8").splitlines() if l.startswith("Style: Cap"))
        assert int(style.split(",")[-3]) >= config.SAFE_BOTTOM_PX
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/shorts_engine/test_integration_phase2.py -v`
Expected: PASS (~2–4 min: renders ~40s of 1080×1920 video). If the script-stage word-budget gate rejects the canned BEATS, tune the beat narration word counts in the test to the budget (hook 4–12 words, stakes 8–19, mechanism 17–37, proof 12–31, cta 12–25 at 2.6 w/s ±20%) — do not weaken the gates.

- [ ] **Step 3: Register the `slow` marker** — if pytest warns about an unknown marker, add to `_shorts_engine_impl/pytest.ini` (create if absent):

```ini
[pytest]
markers =
    slow: renders real video; multi-minute
```

- [ ] **Step 4: Run the full suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 15: Live torture run (sound on) + progress report — the Phase-4 ship gate

**Files:**
- Create: `docs/superpowers/progress/2026-07-05-shorts-engine-plan2-torture.md` (at the real project root's docs tree)

**Interfaces:** consumes the CLI end-to-end with the real LLM, real edge-tts, real Whisper, real ffmpeg.

- [ ] **Step 1: Preflight**

From `_shorts_engine_impl`: `ollama list` — confirm whether `gemma4:31b-cloud` responds. If it is unreachable, run with `--local-only` and note `model_tier: local` in the report (per spec §8.2 the flag is the ONLY sanctioned local path; never patch a fallback in).

- [ ] **Step 2: Run the torture pipeline**

```
python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --until assemble --torture --workspace-root output_torture
```
Expected: exit 0; workspace `output_torture/run-<hex>/` contains `post.json, canonical.txt, factsheet.json, script.json, shotlist.json, voiceover.mp3, word_timings.json, beats_audio.json, shots/, shots_final/, captions.ass, video_short.mp4, assemble_report.json, visuals_report.json, run_manifest.json` with `status: assembled`.

First-run realities to handle (not code changes): edge-tts needs network; Whisper downloads `base.en` on first use (cached under `.cache/`); the run takes several minutes. If the SCRIPT gates legitimately reject the model's draft 3×, the run fails loudly — that is correct behavior; re-run once, and if it persists paste the gate report into the progress report as a writer-prompt tuning item for Plan 3.

- [ ] **Step 3: Human verification (the actual ship criterion)**

Watch `video_short.mp4` end to end and check, in order: (1) no blank/near-blank moment anywhere; (2) every number you hear is one you can find in the blog post; (3) captions never touch the bottom 420px chrome zone; (4) the end card holds ~1.5s after the voice stops — the CTA is never clipped; (5) landscape assets — there are none in this all-designed run, so instead confirm every card looks intentional (torture acceptance from spec §5: a reviewer cannot tell a fallback card from a planned one); (6) read the script aloud once — would a procurement manager learn one actionable thing?

- [ ] **Step 4: Write the progress report**

`docs/superpowers/progress/2026-07-05-shorts-engine-plan2-torture.md` containing: test totals (workspace suite + root suite), the torture command used, model tier, final artifact list with sizes, `assemble_report.json` numbers (voice vs video duration), the six human-verification outcomes from Step 3, and any deviations/tuning items carried to Plan 3 (BROLL ladder, PAPER_CARD acquisition, VERIFY loop, PACKAGE/PUBLISH).

---

## Self-Review (performed while writing)

- **Spec coverage (Phases 3–4):** theme ✓ (T2), 6 renderers ✓ (T3–T7: headline, stat, diagram, quote, logo_cta, broll_frame; paper_card renderer is Plan 3 with its acquisition — until then PAPER_CARD renders its QUOTE_CARD fallback exactly as spec §10 Phase 5 note prescribes), SHOTLIST expansion + linter ✓ (T8), AUDIO wrap + F10 guards ✓ (T9), VISUALS designed-first + never-blank ✓ (T10), ASSEMBLE re-flow/captions/music/end-card hold/duration law ✓ (T11–12), torture criterion ✓ (T14 automated + T15 live).
- **Placeholder scan:** every code step carries complete code; no TBDs.
- **Type consistency:** shot dict fields (`id, beat, type, duration_s, narration_span, payload, fallback`) used identically in T8/T10/T11/T12/T14; `beats_audio` `{beat, start_s, duration_s}` in T9/T11/T12; renderer contract `frame_at/render(payload, duration, out_path, fade_in_s)` uniform across T3–T7 and consumed by T10/T12 registry.
- **Known risks flagged to implementers:** `RunManifest.create` workspace naming (`run-<hex8>`) — tests use `Path(m.workspace)`; integration beat word budgets must satisfy the live gates; ASS `Style` field ordering (MarginV is the 3rd-from-last field — tests index `fields[-3]`).

