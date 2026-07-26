# HRSU Vertical Short-Form Video Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a sibling `video_agent/` package that turns each published HRSU blog into a 9:16 vertical short (≤60s) and publishes to YouTube Shorts, LinkedIn Page, and Instagram Reels — 100% free, CPU-only, reusing existing auth and brand patterns.

**Architecture:** New `video_agent/` Python package, loosely coupled to the blog pipeline via `blog_history.json`. Pipeline: Ollama (script) → edge-tts/Kokoro (voice) → matplotlib infographics + factory B-roll (visuals) → faster-whisper (subtitles) → FFmpeg/MoviePy (composition) → publishers (YouTube/LinkedIn/Instagram) → APScheduler (regional posting times).

**Tech Stack:** Python 3.x, Ollama `gemma3:4b`, edge-tts, kokoro-onnx, faster-whisper, matplotlib, Pillow, MoviePy, FFmpeg, APScheduler+SQLAlchemy, google-api-python-client (YouTube), PyGithub, requests.

**Spec reference:** `docs/superpowers/specs/2026-05-02-video-pipeline-design.md` — read it before starting.

---

## File Structure

**New files (created by this plan):**

| File | Responsibility |
|------|----------------|
| `video_agent/__init__.py` | Package init |
| `video_agent/config.py` | Video-specific config (imports root `config.py`) |
| `video_agent/text_normalizer.py` | TTS-friendly text rewriting |
| `video_agent/script_builder.py` | 5-tier fact extraction → narration → scenes |
| `video_agent/voiceover.py` | edge-tts + Kokoro fallback |
| `video_agent/subtitles.py` | faster-whisper word-timed SRT |
| `video_agent/asset_manifest.py` | Loads/validates `manifest.json` |
| `video_agent/visual_engine/__init__.py` | |
| `video_agent/visual_engine/dispatcher.py` | Routes scenes to generators in parallel |
| `video_agent/visual_engine/text_card.py` | Hook + CTA cards (Pillow) |
| `video_agent/visual_engine/infographic.py` | 5 chart types (matplotlib) |
| `video_agent/visual_engine/factory_broll.py` | Selects + trims factory clips |
| `video_agent/visual_engine/stock.py` | Pexels filler |
| `video_agent/composer.py` | FFmpeg/MoviePy composition |
| `video_agent/history.py` | `video_history.json` atomic IO |
| `video_agent/scheduler.py` | APScheduler regional posting |
| `video_agent/orchestrator.py` | Pipeline glue |
| `video_agent/main.py` | CLI entry |
| `video_agent/publishers/__init__.py` | |
| `video_agent/publishers/base.py` | `BasePublisher` ABC |
| `video_agent/publishers/youtube.py` | YouTube Data API v3 |
| `video_agent/publishers/linkedin.py` | LinkedIn Videos + Posts API |
| `video_agent/publishers/instagram.py` | Meta Graph API REELS + GitHub CDN |
| `video_agent/tools/__init__.py` | |
| `video_agent/tools/render_brand_assets.py` | Pre-render intro/outro |
| `video_agent/tools/tag_assets.py` | Interactive manifest builder |
| `video_agent/tools/check_music_library.py` | Audit music dir |
| `video_agent/tools/shoot_list.py` | Print recommended shots |
| `video_agent/tools/cleanup_cdn.py` | Delete old GitHub release assets |
| `tests/video_agent/...` | Mirror module tree |
| `VIDEO_SETUP.md` | Operator setup checklist |

**Modified files:**

| File | Change |
|------|--------|
| `requirements.txt` | Append video deps |
| `token_manager.py` | Add IG/GitHub/Pexels getters |
| `blog_agent_v3.py` | Add `--with-video` flag (single hook at end of `main()`); add `youtube.upload` to `SCOPES` |

**Created at runtime / by setup:**
- `asset_library/{factory,brand,music,stock_cache}/`
- `output/videos/{date}_{slug}/`
- `video_history.json`, `video_queue.json`, `video_queue.db`, `video_costs.log`, `video_post_failures.log`, `video_agent.log`

---

## Sprint Map

| Sprint | Tasks | Outcome |
|--------|-------|---------|
| **1** | 1–8 | Config, text_normalizer, history, script_builder (all 5 tiers) |
| **2** | 9–11 | voiceover + subtitles |
| **3** | 12–17 | visual_engine (text_card, infographic, dispatcher) |
| **4** | 18–22 | composer, intro/outro renderer, music check, smoke test |
| **5** | 23–26 | factory_broll, stock, asset_manifest, tag_assets tool |
| **6** | 27–31 | publishers/base, publishers/youtube, scheduler, orchestrator MVP, CLI |
| **7** | 32–34 | publishers/linkedin, token_manager extensions |
| **8** | 35–38 | publishers/instagram, GitHub CDN, cleanup tool, blog_agent_v3 hook, VIDEO_SETUP.md |

Commit after every task. Run the test suite at the end of every sprint.

---

## Sprint 1 — Foundation: config, text normalizer, history, script builder

### Task 1: Create package skeleton + dependencies

**Files:**
- Create: `video_agent/__init__.py`
- Create: `video_agent/visual_engine/__init__.py`
- Create: `video_agent/publishers/__init__.py`
- Create: `video_agent/tools/__init__.py`
- Create: `tests/video_agent/__init__.py`
- Create: `tests/video_agent/visual_engine/__init__.py`
- Create: `tests/video_agent/publishers/__init__.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
mkdir video_agent video_agent/visual_engine video_agent/publishers video_agent/tools
mkdir tests/video_agent tests/video_agent/visual_engine tests/video_agent/publishers
```

Each `__init__.py` is empty (zero bytes).

- [ ] **Step 2: Append video dependencies to `requirements.txt`**

Append these lines to the existing `requirements.txt`:

```
# ─── video_agent dependencies ─────────────────────────────────────────────
edge-tts>=7.2.0
kokoro-onnx>=0.3.0
faster-whisper>=1.0.0
moviepy>=1.0.3
pillow>=10.0.0
matplotlib>=3.8.0
numpy>=1.26.0
pydub>=0.25.1
ffmpeg-python>=0.2.0
apscheduler>=3.10.0
sqlalchemy>=2.0.0
pygithub>=2.1.0
google-api-python-client>=2.100.0
google-auth-oauthlib>=1.2.0
responses>=0.24.0    # test-only mocking for HTTP publishers
pytest>=7.4.0
```

- [ ] **Step 3: Install**

Run: `pip install -r requirements.txt`
Expected: All packages install cleanly. Verify with `python -c "import edge_tts, faster_whisper, moviepy, matplotlib, apscheduler, github; print('ok')"` → prints `ok`.

- [ ] **Step 4: Verify FFmpeg is on PATH**

Run: `ffmpeg -version`
Expected: Version string. If missing, run `winget install --id Gyan.FFmpeg`, restart shell.

- [ ] **Step 5: Commit**

```bash
git add video_agent tests/video_agent requirements.txt
git commit -m "feat(video_agent): scaffold package + add dependencies"
```

---

### Task 2: Write `video_agent/config.py`

**Files:**
- Create: `video_agent/config.py`

- [ ] **Step 1: Write the file in full**

Copy this entire content to `video_agent/config.py`:

```python
"""
video_agent configuration.
Imports shared brand/region values from the root config.py.
All video-specific knobs live here.
"""
from config import (
    BLOG_STYLE_TEMPLATE, REGION_POSTING_SCHEDULE, MAIN_WEBSITE,
    COMPANY_NAME, CALCIUM_NITRATE_APPLICATIONS,
)

# ─── Format ────────────────────────────────────────────────────────────────
SHORT_FORMAT = {
    "resolution": (1080, 1920),
    "fps": 30,
    "min_duration_s": 30,
    "max_duration_s": 60,
    "max_filesize_mb": 100,
    "bitrate": "10M",
}

# ─── TTS ───────────────────────────────────────────────────────────────────
TTS_VOICES = {
    "australia": "en-AU-WilliamNeural",
    "usa":       "en-US-GuyNeural",
    "eu":        "en-GB-RyanNeural",
    "germany":   "de-DE-ConradNeural",
    "east_asia": "en-SG-WayneNeural",
    "gulf":      "en-GB-RyanNeural",
    "default":   "en-US-GuyNeural",
}
TTS_RATE = "-5%"
TTS_PITCH = "+0Hz"
KOKORO_DEFAULT_VOICE = "am_michael"

# ─── Subtitles ─────────────────────────────────────────────────────────────
WHISPER_MODEL = "base.en"
WHISPER_MODEL_MULTILINGUAL = "base"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
SUBTITLE_MAX_WORDS_PER_LINE = 3
SUBTITLE_MAX_LINE_DURATION_S = 1.5

# ─── Brand ─────────────────────────────────────────────────────────────────
BRAND_GOLD       = "#d4af37"
BRAND_DARK_NAVY  = "#0a192f"
BRAND_NAVY_2     = "#0a1428"
BRAND_TEXT_LIGHT = "#ccd6f6"
BRAND_TEXT_MUTED = "#8892b0"
BRAND_FONT_HEADING = "Playfair Display"
BRAND_FONT_BODY    = "Poppins"
BRAND_LOGO_WHITE_PATH = "asset_library/brand/hrsu_logo_white.png"
BRAND_LOGO_GOLD_PATH  = "asset_library/brand/hrsu_logo_gold.png"
INTRO_VIDEO_PATH = "asset_library/brand/intro_3s.mp4"
OUTRO_VIDEO_PATH = "asset_library/brand/outro_5s.mp4"

# ─── Composer ──────────────────────────────────────────────────────────────
KEN_BURNS_ZOOM_END = 1.08
TRANSITION_DEFAULT_S = 0.25
TRANSITION_AFTER_BROLL_S = 0.35
LOGO_OPACITY = 0.08
PROGRESS_BAR_HEIGHT_PX = 4
MUSIC_VOLUME_DB = -22
MUSIC_DUCKED_DB = -30

# ─── Visual engine ─────────────────────────────────────────────────────────
ENABLE_AI_IMAGES = False
PARALLEL_VISUAL_WORKERS = 4
ESG_KEYWORDS = ["sustainable", "solar", "garden", "emission", "carbon", "esg",
                "renewable", "green", "circular"]

# ─── Script builder ────────────────────────────────────────────────────────
SCRIPT_NARRATION_MIN_WORDS = 120
SCRIPT_NARRATION_MAX_WORDS = 170
SCRIPT_MIN_NUMERICS = 3
SCRIPT_SCENES_MIN = 8
SCRIPT_SCENES_MAX = 12
OLLAMA_RETRY_MAX = 3
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_HOST = "http://localhost:11434"
SCRIPT_BANNED_PHRASES = [
    "as an ai", "in this video", "thanks for watching",
    "hope you enjoyed", "i don't have", "as of my last update",
]

# ─── Tier-4 fact-extraction templates ──────────────────────────────────────
CATEGORY_PUNCH_POINTS = {
    "wastewater_treatment": [
        "H₂S corrosion costs municipal plants millions annually",
        "Calcium nitrate prevents sulfide formation at source, not downstream",
        "Bioaugmentation alternatives require constant culture monitoring",
        "REACH-registered grades unlock European municipal contracts",
        "Dosing accuracy directly impacts plant operating costs",
        "Sulfide control failures trigger NPDES violations",
    ],
    "concrete_construction": [
        "Cold-weather concreting demands accelerator chemistry, not just heat",
        "Calcium nitrate doubles as set accelerator and corrosion inhibitor",
        "Chloride-free admixtures protect rebar over decades",
        "Precast plants gain throughput by cutting demolding time",
        "Shotcrete in tunneling depends on reliable accelerator supply",
    ],
    "mining": [
        "ANFO production scales with reliable nitrate supply chains",
        "Underground water management defines mining safety margins",
        "Dust suppression is a regulated requirement, not optional",
        "Acid mine drainage costs more to remediate than to prevent",
        "Tailings chemistry affects water table for decades",
    ],
    "agriculture_fertilizer": [
        "Calcium-nitrogen synergy outperforms separate applications",
        "Fertigation precision reduces nutrient runoff",
        "Hydroponic crops demand chloride-free calcium sources",
        "Greenhouse blueberries require strict nitrate ratios",
        "Anti-caking treatment matters in monsoon-season storage",
    ],
    "oil_gas": [
        "Drilling fluid chemistry determines well completion success",
        "Calcium nitrate brines balance density without weighting",
        "Corrosion control in completion fluids saves rework costs",
        "Produced water treatment is now an OPEX category",
    ],
    "latex_rubber": [
        "Coagulation efficiency drives latex plant economics",
        "Calcium nitrate doses are tuned per glove specification",
        "Natural rubber processing depends on consistent salt grades",
    ],
    "food_processing": [
        "Calcium fortification meets new regulatory mandates",
        "Cheese production yields are sensitive to calcium chemistry",
        "Beverage stabilization needs food-grade calcium nitrate",
    ],
    "water_treatment": [
        "Cooling tower water chemistry determines tube-bundle lifespan",
        "Boiler feedwater conditioning prevents catastrophic failures",
        "RO pretreatment chemistry affects membrane fouling rates",
    ],
    "specialty_applications": [
        "Thermal energy storage uses molten nitrate salt mixtures",
        "Electroplating bath chemistry depends on calcium balance",
        "Glass manufacturing fining agents include calcium nitrate",
    ],
    "esg": [
        "On-site solar offsets a measurable share of plant load",
        "Steam reuse is a compliance and a margin lever",
        "Local sourcing cuts Scope-3 transportation emissions",
        "Garden initiatives improve local air quality metrics",
    ],
}

# ─── Stock fallback ────────────────────────────────────────────────────────
PEXELS_API_BASE = "https://api.pexels.com/v1"
STOCK_CACHE_DIR = "asset_library/stock_cache"

# ─── Output ────────────────────────────────────────────────────────────────
OUTPUT_BASE = "output/videos"
HISTORY_FILE = "video_history.json"
QUEUE_FILE = "video_queue.json"
COSTS_LOG = "video_costs.log"
FAILURE_LOG = "video_post_failures.log"
LOG_FILE = "video_agent.log"

# ─── Scheduler ─────────────────────────────────────────────────────────────
SCHEDULER_JOBSTORE_URL = "sqlite:///video_queue.db"
SCHEDULER_RETRY_BACKOFF_S = [300, 1800, 7200]
SCHEDULER_MAX_ATTEMPTS = 3

# ─── Region → ISO language ─────────────────────────────────────────────────
REGION_TO_ISO_LANG = {
    "australia": "en", "usa": "en", "eu": "en", "germany": "de",
    "east_asia": "en", "gulf": "en", "default": "en",
}

# ─── Region → Pytz timezone (for scheduler) ────────────────────────────────
REGION_TO_TZ = {
    "australia": "Australia/Sydney",
    "usa":       "America/New_York",
    "eu":        "Europe/London",
    "germany":   "Europe/Berlin",
    "east_asia": "Asia/Singapore",
    "gulf":      "Asia/Dubai",
    "default":   "UTC",
}
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from video_agent import config; print(len(config.CATEGORY_PUNCH_POINTS))"`
Expected: `10`

- [ ] **Step 3: Commit**

```bash
git add video_agent/config.py
git commit -m "feat(video_agent): add config module"
```

---

### Task 3: TDD `text_normalizer.py`

**Files:**
- Create: `tests/video_agent/test_text_normalizer.py`
- Create: `video_agent/text_normalizer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_text_normalizer.py`:

```python
import pytest
from video_agent.text_normalizer import normalize_for_tts


def test_h2s_with_subscript():
    assert normalize_for_tts("H₂S levels") == "H 2 S levels"

def test_h2s_plain():
    assert normalize_for_tts("H2S levels") == "H 2 S levels"

def test_calcium_nitrate_formula():
    assert normalize_for_tts("Use Ca(NO3)2 today") == "Use calcium nitrate today"

def test_calcium_nitrate_subscript():
    assert normalize_for_tts("Ca(NO₃)₂ dosing") == "calcium nitrate dosing"

def test_co2_variants():
    assert normalize_for_tts("CO₂ rose; CO2 fell") == "C O 2 rose; C O 2 fell"

def test_units_replaced():
    assert normalize_for_tts("50 mg/L and 2 kg/t") == \
        "50 milligrams per liter and 2 kilograms per tonne"

def test_percent():
    assert normalize_for_tts("90% reduction") == "90 percent reduction"

def test_celsius():
    assert normalize_for_tts("at 25°C exactly") == "at 25 degrees Celsius exactly"

def test_domain_spelled_out():
    assert "H R S U Indore dot com" in normalize_for_tts("Visit hrsuindore.com today")

def test_strips_citations():
    assert normalize_for_tts("This[1] is fact[2].") == "This is fact."

def test_strips_markdown_bold_italic():
    assert normalize_for_tts("**bold** and *italic*") == "bold and italic"

def test_collapses_whitespace():
    assert normalize_for_tts("a   b\t\tc") == "a b c"

def test_combined():
    src = "Use **Ca(NO3)2** at 50 mg/L[1] to cut H₂S by 90%."
    out = normalize_for_tts(src)
    assert "calcium nitrate" in out
    assert "milligrams per liter" in out
    assert "[1]" not in out
    assert "**" not in out
    assert "H 2 S" in out
    assert "90 percent" in out
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/video_agent/test_text_normalizer.py -v`
Expected: FAIL — `ModuleNotFoundError: video_agent.text_normalizer`

- [ ] **Step 3: Implement `text_normalizer.py`**

Create `video_agent/text_normalizer.py`:

```python
"""
Convert blog narration text into TTS-friendly form.
Handles chemistry notation, units, citations, markdown.
Order of replacements matters — formulas first, then units, then strip.
"""
import re

# (pattern, replacement) — order matters: most-specific first.
_REPLACEMENTS = [
    # Formulas (specific → general)
    (re.compile(r"Ca\(NO[₃3]\)[₂2]"), "calcium nitrate"),
    (re.compile(r"H₂S"), "H 2 S"),
    (re.compile(r"\bH2S\b"), "H 2 S"),
    (re.compile(r"CO₂"), "C O 2"),
    (re.compile(r"\bCO2\b"), "C O 2"),
    # Units
    (re.compile(r"\bmg/L\b"), "milligrams per liter"),
    (re.compile(r"\bkg/t\b"), "kilograms per tonne"),
    (re.compile(r"%"), " percent"),
    (re.compile(r"°C"), " degrees Celsius"),
    # Domain
    (re.compile(r"hrsuindore\.com", re.IGNORECASE), "H R S U Indore dot com"),
    # Strip citation markers like [1], [12]
    (re.compile(r"\[\d+\]"), ""),
    # Strip markdown emphasis (bold first, then italic)
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    # Collapse whitespace (last)
    (re.compile(r"\s+"), " "),
]


def normalize_for_tts(text: str) -> str:
    """Return TTS-safe version of `text`."""
    out = text
    for pattern, repl in _REPLACEMENTS:
        out = pattern.sub(repl, out)
    return out.strip()
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_text_normalizer.py -v`
Expected: All 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/video_agent/test_text_normalizer.py video_agent/text_normalizer.py
git commit -m "feat(video_agent): text_normalizer with TDD coverage"
```

---

### Task 4: TDD `history.py`

**Files:**
- Create: `tests/video_agent/test_history.py`
- Create: `video_agent/history.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_history.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from video_agent import history


@pytest.fixture
def tmp_history(tmp_path, monkeypatch):
    p = tmp_path / "video_history.json"
    monkeypatch.setattr(history, "HISTORY_PATH", p)
    return p


def test_load_returns_default_when_missing(tmp_history):
    data = history.load()
    assert data == {"videos": []}


def test_save_atomic_creates_file(tmp_history):
    history.save_atomic({"videos": [{"blog_id": "1"}]})
    assert tmp_history.exists()
    assert json.loads(tmp_history.read_text())["videos"][0]["blog_id"] == "1"


def test_append_video_persists(tmp_history):
    history.append_video({"blog_id": "abc", "video_path": "x.mp4"})
    history.append_video({"blog_id": "def", "video_path": "y.mp4"})
    data = history.load()
    assert len(data["videos"]) == 2
    assert data["videos"][1]["blog_id"] == "def"


def test_find_by_blog_id(tmp_history):
    history.append_video({"blog_id": "abc", "video_path": "x.mp4"})
    assert history.find_by_blog_id("abc")["video_path"] == "x.mp4"
    assert history.find_by_blog_id("missing") is None


def test_save_atomic_no_corruption_on_crash(tmp_history, monkeypatch):
    history.save_atomic({"videos": [{"blog_id": "ok"}]})
    # Simulate crash mid-write: tempfile rename should be atomic.
    original = tmp_history.read_text()
    real_replace = Path.replace
    def boom(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        history.save_atomic({"videos": [{"blog_id": "new"}]})
    # Original file untouched.
    assert tmp_history.read_text() == original


def test_stats_counts_recent(tmp_history):
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    old = (now - timedelta(days=45)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    history.append_video({"blog_id": "1", "created_at": old})
    history.append_video({"blog_id": "2", "created_at": recent})
    s = history.stats(days=30)
    assert s["count"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/video_agent/test_history.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `history.py`**

Create `video_agent/history.py`:

```python
"""Atomic JSON read/write for video_history.json."""
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from video_agent.config import HISTORY_FILE

HISTORY_PATH = Path(HISTORY_FILE)


def load() -> dict:
    if not HISTORY_PATH.exists():
        return {"videos": []}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"videos": []}


def save_atomic(history: dict) -> None:
    """Tempfile + rename. Atomic on POSIX & Windows."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(HISTORY_PATH.parent or "."),
        prefix=".video_history.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp).replace(HISTORY_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_by_blog_id(blog_id: str) -> dict | None:
    for v in load().get("videos", []):
        if v.get("blog_id") == blog_id:
            return v
    return None


def append_video(record: dict) -> None:
    data = load()
    if "created_at" not in record:
        record["created_at"] = datetime.utcnow().isoformat()
    data.setdefault("videos", []).append(record)
    save_atomic(data)


def stats(days: int = 30) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=days)
    data = load()
    recent = []
    for v in data.get("videos", []):
        try:
            ts = datetime.fromisoformat(v.get("created_at", ""))
        except ValueError:
            continue
        if ts >= cutoff:
            recent.append(v)
    return {
        "count": len(recent),
        "by_region": _count_by(recent, "region"),
        "by_platform": _platform_counts(recent),
    }


def _count_by(records: list, key: str) -> dict:
    out: dict = {}
    for r in records:
        k = r.get(key, "unknown")
        out[k] = out.get(k, 0) + 1
    return out


def _platform_counts(records: list) -> dict:
    counts: dict = {}
    for r in records:
        for plat, res in (r.get("publish_results") or {}).items():
            if res and res.get("success"):
                counts[plat] = counts.get(plat, 0) + 1
    return counts
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_history.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/video_agent/test_history.py video_agent/history.py
git commit -m "feat(video_agent): atomic history JSON store"
```

---

### Task 5: Stub Ollama client wrapper

**Files:**
- Create: `video_agent/ollama_client.py`
- Create: `tests/video_agent/test_ollama_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_ollama_client.py`:

```python
import pytest
import responses
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.config import OLLAMA_HOST


@responses.activate
def test_generate_returns_response_text():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": "hello world", "done": True},
        status=200,
    )
    client = OllamaClient()
    out = client.generate("hi", system="be brief")
    assert out == "hello world"


@responses.activate
def test_generate_json_parses():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": '{"key": "val"}', "done": True},
        status=200,
    )
    client = OllamaClient()
    out = client.generate_json("get json", system="json only")
    assert out == {"key": "val"}


@responses.activate
def test_generate_json_strips_markdown_fence():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": '```json\n{"a":1}\n```', "done": True},
        status=200,
    )
    client = OllamaClient()
    assert client.generate_json("x") == {"a": 1}


@responses.activate
def test_generate_raises_when_unreachable():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        body=ConnectionError("boom"),
    )
    client = OllamaClient()
    with pytest.raises(OllamaError, match="not running"):
        client.generate("hi")
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `pytest tests/video_agent/test_ollama_client.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ollama_client.py`**

Create `video_agent/ollama_client.py`:

```python
"""Thin wrapper around Ollama HTTP API with JSON-mode helpers."""
import json
import re
import logging
import requests
from video_agent.config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_RETRY_MAX

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class OllamaClient:
    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL,
                 timeout: float = 120.0):
        self.host = host
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float = 0.7) -> str:
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            body["system"] = system
        try:
            r = requests.post(
                f"{self.host}/api/generate", json=body, timeout=self.timeout,
            )
            r.raise_for_status()
        except (requests.ConnectionError, ConnectionError) as e:
            raise OllamaError(
                f"Ollama not running at {self.host} — start with: ollama serve"
            ) from e
        except requests.HTTPError as e:
            raise OllamaError(f"Ollama HTTP error: {e}") from e
        return r.json().get("response", "").strip()

    def generate_json(self, prompt: str, system: str | None = None,
                      retries: int = OLLAMA_RETRY_MAX) -> dict | list:
        sys = (system or "") + "\nRespond with raw JSON only. No prose, no markdown."
        last_err = None
        for attempt in range(1, retries + 1):
            raw = self.generate(prompt, system=sys, temperature=0.4)
            cleaned = _FENCE_RE.sub("", raw).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                last_err = e
                log.warning("Ollama JSON parse failed (attempt %d): %s", attempt, e)
        raise OllamaError(f"Ollama JSON parse failed after {retries} retries: {last_err}")
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_ollama_client.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/ollama_client.py tests/video_agent/test_ollama_client.py
git commit -m "feat(video_agent): Ollama HTTP client with JSON helpers"
```

---

### Task 6: TDD script_builder — fact extraction tiers

**Files:**
- Create: `tests/video_agent/test_script_builder_extraction.py`
- Create: `video_agent/script_builder.py`

- [ ] **Step 1: Write failing tests covering all 5 tiers**

Create `tests/video_agent/test_script_builder_extraction.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from video_agent.script_builder import (
    extract_facts, ScriptBuilderError, _tier2_regex_facts,
)


BLOG_NUMERIC = {
    "blog_id": "n1",
    "category": "wastewater_treatment",
    "content_html": "Calcium nitrate cuts H₂S by 90%. Doses of 50 mg/L work in 24 hours.",
    "summary": "summary",
}

BLOG_THIN = {
    "blog_id": "thin1",
    "category": "wastewater_treatment",
    "content_html": "<p>Wastewater is treated. Outcomes vary.</p>",
    "summary": "thin",
}

BLOG_NO_CATEGORY = {
    "blog_id": "x",
    "category": "non_existent_category",
    "content_html": "<p>nothing</p>",
    "summary": "x",
}


def test_tier2_regex_finds_numerics():
    text = "We saw 90% improvement, dosing 50 mg/L over 24 hours and 5°C drop."
    facts = _tier2_regex_facts(text)
    assert len(facts) >= 4
    assert any("90" in f["claim"] for f in facts)
    assert any("mg/L" in f["claim"] for f in facts)


def test_tier1_used_when_ollama_returns_three_plus():
    fake = [
        {"value": "90", "unit": "%", "claim": "H2S cut 90%", "source_quote": "..."},
        {"value": "50", "unit": "mg/L", "claim": "dose 50 mg/L", "source_quote": "..."},
        {"value": "24", "unit": "hours", "claim": "in 24 hours", "source_quote": "..."},
    ]
    with patch("video_agent.script_builder._tier1_ollama_numeric",
               return_value=fake):
        facts, meta = extract_facts(BLOG_NUMERIC)
    assert meta["tier_used"] == 1
    assert meta["numeric_count"] == 3
    assert len(facts) >= 3


def test_tier2_kicks_in_when_tier1_too_few():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]):
        facts, meta = extract_facts(BLOG_NUMERIC)
    assert meta["tier_used"] == 2
    assert len(facts) >= 3


def test_tier3_qualitative_when_no_numerics():
    qualitative = ["a fact", "b fact", "c fact", "d fact"]
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative",
               return_value=qualitative):
        facts, meta = extract_facts(BLOG_THIN)
    assert meta["tier_used"] == 3
    assert meta["punch_points_count"] >= 3


def test_tier4_template_fallback():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative", return_value=[]):
        facts, meta = extract_facts(BLOG_THIN)
    assert meta["tier_used"] == 4
    assert meta["fell_back_to_template"] is True
    assert len(facts) >= 3


def test_tier5_aborts_when_no_template():
    with patch("video_agent.script_builder._tier1_ollama_numeric", return_value=[]), \
         patch("video_agent.script_builder._tier2_regex_facts", return_value=[]), \
         patch("video_agent.script_builder._tier3_ollama_qualitative", return_value=[]):
        with pytest.raises(ScriptBuilderError, match="no fact extraction tier"):
            extract_facts(BLOG_NO_CATEGORY)
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `pytest tests/video_agent/test_script_builder_extraction.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement extraction part of `script_builder.py`**

Create `video_agent/script_builder.py` (extraction-only — narration & scenes added in Task 7):

```python
"""
5-tier fact extraction → narration → scenes.
This module is the single Ollama-driven script generator.
"""
import re
import logging
from typing import Tuple
from video_agent.config import (
    SCRIPT_MIN_NUMERICS, CATEGORY_PUNCH_POINTS,
)
from video_agent.ollama_client import OllamaClient, OllamaError

log = logging.getLogger(__name__)


class ScriptBuilderError(RuntimeError):
    pass


_NUMERIC_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(%|ppm|mg/L|kg|tonnes?|°C|hours?|days?|years?|×|x)\b",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub("", html or "")


def _tier1_ollama_numeric(blog_record: dict, client: OllamaClient | None = None) -> list:
    """Tier 1: ask Ollama for surprising numeric facts as JSON."""
    client = client or OllamaClient()
    text = _strip_html(blog_record.get("content_html", ""))[:4000]
    system = (
        "You extract surprising NUMERIC facts from technical chemistry articles. "
        "Each fact must contain a number and a unit. "
        "Return a JSON array. Each item: "
        '{"value": "<number>", "unit": "<unit>", "claim": "<one sentence ≤ 20 words>", '
        '"source_quote": "<short quote from article>"}'
    )
    prompt = f"Extract up to 5 surprising numeric facts from this article:\n\n{text}"
    try:
        out = client.generate_json(prompt, system=system)
    except OllamaError as e:
        log.warning("Tier 1 Ollama failed: %s", e)
        return []
    if not isinstance(out, list):
        return []
    return [f for f in out if isinstance(f, dict) and f.get("claim")]


def _tier2_regex_facts(text: str) -> list:
    """Tier 2: regex grep for numeric+unit patterns, attach surrounding sentence."""
    plain = _strip_html(text)
    found = []
    seen_claims = set()
    for sent in _SENTENCE_RE.findall(plain):
        sent = sent.strip()
        m = _NUMERIC_RE.search(sent)
        if not m:
            continue
        if sent in seen_claims:
            continue
        seen_claims.add(sent)
        found.append({
            "value": m.group(0).split()[0],
            "unit": m.group(1),
            "claim": sent[:200],
            "source_quote": sent[:200],
        })
    return found


def _tier3_ollama_qualitative(blog_record: dict, client: OllamaClient | None = None) -> list:
    """Tier 3: qualitative punch points (≤ 15 words each)."""
    client = client or OllamaClient()
    text = _strip_html(blog_record.get("content_html", ""))[:4000]
    system = (
        "You distill qualitative punch points from technical articles. "
        "Look for: named regulations, named processes, contrarian claims, "
        "named risks, named entities. Each point must be ≤ 15 words. "
        'Return a JSON array of strings: ["point one", "point two", ...]'
    )
    prompt = f"Give me 5 surprising non-numeric punch points from:\n\n{text}"
    try:
        out = client.generate_json(prompt, system=system)
    except OllamaError as e:
        log.warning("Tier 3 Ollama failed: %s", e)
        return []
    return [str(p)[:200] for p in out if p] if isinstance(out, list) else []


def _tier4_template(category: str) -> list:
    points = CATEGORY_PUNCH_POINTS.get(category, [])
    return [{"claim": p, "value": "", "unit": "", "source_quote": ""} for p in points]


def extract_facts(blog_record: dict) -> Tuple[list, dict]:
    """Run all 5 tiers in order. Returns (facts, metadata)."""
    meta = {
        "tier_used": 0,
        "numeric_count": 0,
        "punch_points_count": 0,
        "fell_back_to_template": False,
    }

    facts = _tier1_ollama_numeric(blog_record)
    meta["numeric_count"] = len(facts)
    if len(facts) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 1
        return facts, meta

    regex_facts = _tier2_regex_facts(blog_record.get("content_html", ""))
    if facts and regex_facts:
        combined = facts + [r for r in regex_facts if r["claim"] not in {f["claim"] for f in facts}]
    else:
        combined = facts or regex_facts
    if len(combined) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 2 if not facts else 1
        meta["numeric_count"] = len(combined)
        return combined, meta

    qual = _tier3_ollama_qualitative(blog_record)
    qual_facts = [{"claim": p, "value": "", "unit": "", "source_quote": ""} for p in qual]
    pool = combined + qual_facts
    meta["punch_points_count"] = len(qual_facts)
    if len(pool) >= SCRIPT_MIN_NUMERICS:
        meta["tier_used"] = 3
        return pool, meta

    cat = blog_record.get("category", "")
    template_facts = _tier4_template(cat)
    if template_facts:
        meta["tier_used"] = 4
        meta["fell_back_to_template"] = True
        log.warning("Tier 4 template fallback for blog %s (category=%s)",
                    blog_record.get("blog_id"), cat)
        return pool + template_facts, meta

    raise ScriptBuilderError(
        f"no fact extraction tier produced output for blog "
        f"{blog_record.get('blog_id')} (category={cat!r})"
    )
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_script_builder_extraction.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/script_builder.py tests/video_agent/test_script_builder_extraction.py
git commit -m "feat(video_agent): script_builder 5-tier fact extraction"
```

---

### Task 7: Add narration + scenes to `script_builder.py`

**Files:**
- Modify: `video_agent/script_builder.py`
- Create: `tests/video_agent/test_script_builder_full.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_script_builder_full.py`:

```python
from unittest.mock import patch
from video_agent.script_builder import build_script


SAMPLE_BLOG = {
    "blog_id": "b1",
    "title": "Calcium Nitrate H2S Control",
    "url": "https://blog.hrsuindore.com/2026/04/x.html",
    "region": "australia",
    "persona": "procurement",
    "category": "wastewater_treatment",
    "subcategory": "odor_control_h2s",
    "content_html": "Calcium nitrate cut H2S by 90%. Doses 50 mg/L took 24 hours.",
    "summary": "summary",
}

FAKE_NARRATION = (
    "H 2 S corrosion drains millions from municipal wastewater plants every year. "
    "Calcium nitrate stops sulfide formation at the source — not downstream. "
    "Australian utilities use 50 milligrams per liter doses to cut odors by 90 percent within 24 hours. "
    "Bioaugmentation alternatives demand constant monitoring and risk failures. "
    "REACH-registered grades unlock European municipal contracts. "
    "HRSU Indore manufactures food and technical grades for export. "
    "Procurement teams trust documented dosing data. "
    "Lab-grade purity ensures consistent plant performance. "
    "Visit H R S U Indore dot com for spec sheets and quotes today."
)

FAKE_SCENES = [
    {"index": 0, "narration": "H 2 S corrosion drains millions...", "duration_s": 5.0,
     "visual_type": "text_card", "visual_spec": {"layout": "hook"},
     "on_screen_text": "H₂S CORROSION", "transition_in": "fade"},
    {"index": 1, "narration": "Calcium nitrate stops sulfide formation...", "duration_s": 5.0,
     "visual_type": "infographic", "visual_spec": {
         "chart_type": "comparison", "title": "Source vs Downstream",
         "data": {"left": "Without", "right": "With"}},
     "on_screen_text": "AT SOURCE", "transition_in": "fade"},
    {"index": 2, "narration": "Australian utilities use 50 mg/L...", "duration_s": 5.0,
     "visual_type": "infographic", "visual_spec": {
         "chart_type": "callout_stat", "data": {"value": "90%", "label": "H₂S cut"}},
     "on_screen_text": "90%", "transition_in": "fade"},
    {"index": 3, "narration": "Bioaugmentation alternatives demand...", "duration_s": 5.0,
     "visual_type": "infographic", "visual_spec": {"chart_type": "bar"},
     "on_screen_text": "MONITORING COST", "transition_in": "fade"},
    {"index": 4, "narration": "REACH-registered grades unlock...", "duration_s": 5.0,
     "visual_type": "stock", "visual_spec": {"query": "european factory"},
     "on_screen_text": "REACH", "transition_in": "fade"},
    {"index": 5, "narration": "HRSU Indore manufactures...", "duration_s": 5.0,
     "visual_type": "hrsu_edge", "visual_spec": {"fallback_text": "HRSU EDGE"},
     "on_screen_text": "HRSU EDGE", "transition_in": "fade"},
    {"index": 6, "narration": "Procurement teams trust...", "duration_s": 5.0,
     "visual_type": "infographic", "visual_spec": {"chart_type": "callout_stat"},
     "on_screen_text": "TRUST", "transition_in": "fade"},
    {"index": 7, "narration": "Lab-grade purity...", "duration_s": 5.0,
     "visual_type": "hrsu_edge", "visual_spec": {"fallback_text": "QC"},
     "on_screen_text": "QUALITY", "transition_in": "fade"},
    {"index": 8, "narration": "Visit H R S U Indore dot com...", "duration_s": 5.0,
     "visual_type": "text_card", "visual_spec": {"layout": "cta"},
     "on_screen_text": "HRSUINDORE.COM", "transition_in": "fade"},
]


def _patched_extract(*a, **k):
    return ([{"claim": "x"} for _ in range(5)],
            {"tier_used": 1, "numeric_count": 5,
             "punch_points_count": 0, "fell_back_to_template": False})


def test_build_script_meets_constraints(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.script_builder.extract_facts", _patched_extract)
    monkeypatch.setattr(
        "video_agent.script_builder._write_narration",
        lambda facts, b: FAKE_NARRATION,
    )
    monkeypatch.setattr(
        "video_agent.script_builder._scene_breakdown",
        lambda narration, b: FAKE_SCENES,
    )

    result = build_script(SAMPLE_BLOG, output_dir=tmp_path)

    word_count = len(result["narration"].split())
    assert 80 <= word_count <= 200
    assert result["scenes"][0]["visual_type"] == "text_card"
    assert result["scenes"][-1]["visual_type"] == "text_card"
    assert any(s["visual_type"] == "infographic" for s in result["scenes"])
    assert any(s["visual_type"] == "hrsu_edge" for s in result["scenes"])
    assert 8 <= len(result["scenes"]) <= 12
    assert result["extraction_metadata"]["tier_used"] == 1


def test_build_script_caches(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.script_builder.extract_facts", _patched_extract)
    monkeypatch.setattr(
        "video_agent.script_builder._write_narration",
        lambda facts, b: FAKE_NARRATION,
    )
    monkeypatch.setattr(
        "video_agent.script_builder._scene_breakdown",
        lambda narration, b: FAKE_SCENES,
    )
    r1 = build_script(SAMPLE_BLOG, output_dir=tmp_path)
    # mutate fake to prove cache wins
    monkeypatch.setattr("video_agent.script_builder._write_narration",
                        lambda *a, **k: "DIFFERENT")
    r2 = build_script(SAMPLE_BLOG, output_dir=tmp_path)
    assert r1["narration"] == r2["narration"]


def test_build_script_force_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.script_builder.extract_facts", _patched_extract)
    monkeypatch.setattr(
        "video_agent.script_builder._write_narration",
        lambda *a, **k: FAKE_NARRATION,
    )
    monkeypatch.setattr(
        "video_agent.script_builder._scene_breakdown",
        lambda *a, **k: FAKE_SCENES,
    )
    build_script(SAMPLE_BLOG, output_dir=tmp_path)
    monkeypatch.setattr("video_agent.script_builder._write_narration",
                        lambda *a, **k: "FRESH NARRATION " * 30)
    r2 = build_script(SAMPLE_BLOG, output_dir=tmp_path, force=True)
    assert r2["narration"].startswith("FRESH NARRATION")
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `pytest tests/video_agent/test_script_builder_full.py -v`
Expected: FAIL — `build_script` not defined.

- [ ] **Step 3: Append narration + scenes + caching to `video_agent/script_builder.py`**

Append to the end of `video_agent/script_builder.py`:

```python
import json
from pathlib import Path
from video_agent.config import (
    SCRIPT_NARRATION_MIN_WORDS, SCRIPT_NARRATION_MAX_WORDS,
    SCRIPT_SCENES_MIN, SCRIPT_SCENES_MAX, SCRIPT_BANNED_PHRASES,
    OUTPUT_BASE,
)

SCRIPT_BUILDER_VERSION = "1.0"


def _slug(blog_record: dict) -> str:
    raw = (blog_record.get("title") or blog_record.get("blog_id") or "video")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower())[:60].strip("-")


def _workspace(blog_record: dict, output_dir: Path | None) -> Path:
    if output_dir:
        return Path(output_dir)
    from datetime import date
    return Path(OUTPUT_BASE) / f"{date.today().isoformat()}_{_slug(blog_record)}"


def _write_narration(facts: list, blog_record: dict,
                     client: OllamaClient | None = None) -> str:
    client = client or OllamaClient()
    persona = blog_record.get("persona", "procurement")
    region = blog_record.get("region", "default")
    bullet_facts = "\n".join(f"- {f['claim']}" for f in facts[:8])
    system = (
        f"You write tight 30-60s vertical-video voiceover scripts for B2B chemical buyers. "
        f"Audience: {persona} managers in {region}. "
        f"Constraints: {SCRIPT_NARRATION_MIN_WORDS}-{SCRIPT_NARRATION_MAX_WORDS} words, "
        f"≥3 numeric figures, end with a CTA mentioning hrsuindore.com or 'spec sheet' or 'DM us'. "
        f"Open with a hook (stat, problem, question, or contrarian claim). "
        f"No phrases like: {', '.join(SCRIPT_BANNED_PHRASES)}. "
        f"Output the narration as plain prose only — no headings, no markdown."
    )
    prompt = f"Punch points to weave in:\n{bullet_facts}\n\nWrite the script."

    for attempt in range(1, 4):
        text = client.generate(prompt, system=system, temperature=0.6)
        wc = len(text.split())
        digits = sum(c.isdigit() for c in text)
        has_cta = any(p in text.lower() for p in
                      ("hrsuindore.com", "spec sheet", "dm us"))
        banned = [p for p in SCRIPT_BANNED_PHRASES if p in text.lower()]
        if (SCRIPT_NARRATION_MIN_WORDS <= wc <= SCRIPT_NARRATION_MAX_WORDS
                and digits >= 3 and has_cta and not banned):
            return text
        log.info("Narration retry %d (wc=%d digits=%d cta=%s banned=%s)",
                 attempt, wc, digits, has_cta, banned)
    return text  # use last attempt


def _scene_breakdown(narration: str, blog_record: dict,
                     client: OllamaClient | None = None) -> list:
    client = client or OllamaClient()
    system = (
        "Split a video voiceover into 8-12 scenes. "
        "For each scene return: index, narration (verbatim sentence(s)), "
        "duration_s (estimate at 2.7 words/sec), visual_type "
        "(text_card | infographic | hrsu_edge | stock), visual_spec "
        "(small dict — for infographic include chart_type from "
        "[bar, comparison, callout_stat, flow, line] and data; "
        "for text_card include layout from [hook, cta]; "
        "for stock include query; for hrsu_edge include fallback_text), "
        "on_screen_text (≤6 words ALL CAPS), transition_in "
        "(fade | cut | slide_up). "
        "Return a JSON array."
    )
    out = client.generate_json(f"Narration:\n{narration}", system=system)
    if not isinstance(out, list):
        out = []
    scenes = _post_process_scenes(out, narration)
    return scenes


def _post_process_scenes(scenes: list, narration: str) -> list:
    """Enforce structural rules: scene 0 hook, last scene CTA, ≥2 infographic, ≥1 hrsu_edge."""
    if not scenes:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
        scenes = [
            {"index": i, "narration": s, "duration_s": max(2.0, len(s.split()) / 2.7),
             "visual_type": "text_card", "visual_spec": {"layout": "hook" if i == 0 else "cta"},
             "on_screen_text": "", "transition_in": "fade"}
            for i, s in enumerate(sentences[:SCRIPT_SCENES_MAX])
        ]
    # Trim/pad
    scenes = scenes[:SCRIPT_SCENES_MAX]
    while len(scenes) < SCRIPT_SCENES_MIN:
        scenes.append({
            "index": len(scenes), "narration": "",
            "duration_s": 2.0, "visual_type": "infographic",
            "visual_spec": {"chart_type": "callout_stat", "data": {"value": "", "label": ""}},
            "on_screen_text": "", "transition_in": "fade",
        })
    # Force structural rules
    scenes[0]["visual_type"] = "text_card"
    scenes[0].setdefault("visual_spec", {})["layout"] = "hook"
    scenes[-1]["visual_type"] = "text_card"
    scenes[-1]["visual_spec"] = {"layout": "cta"}
    scenes[-1]["on_screen_text"] = scenes[-1].get("on_screen_text") or "HRSUINDORE.COM"
    # Ensure ≥1 hrsu_edge — convert one stock/infographic in middle
    has_edge = any(s["visual_type"] == "hrsu_edge" for s in scenes)
    if not has_edge and len(scenes) >= 4:
        scenes[len(scenes) // 2]["visual_type"] = "hrsu_edge"
        scenes[len(scenes) // 2]["visual_spec"] = {"fallback_text": "HRSU EDGE"}
    # Ensure ≥2 infographics
    info_count = sum(1 for s in scenes if s["visual_type"] == "infographic")
    if info_count < 2:
        for s in scenes[1:-1]:
            if s["visual_type"] not in ("infographic", "hrsu_edge", "text_card"):
                s["visual_type"] = "infographic"
                s["visual_spec"] = {"chart_type": "callout_stat",
                                    "data": {"value": "", "label": ""}}
                info_count += 1
                if info_count >= 2:
                    break
    # Re-index
    for i, s in enumerate(scenes):
        s["index"] = i
    return scenes


def _scrub_banned(narration: str) -> str:
    out = narration
    for phrase in SCRIPT_BANNED_PHRASES:
        out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def build_script(blog_record: dict, output_dir: Path | None = None,
                 force: bool = False) -> dict:
    """Main API. Returns a ScriptResult dict and writes script.json."""
    workspace = _workspace(blog_record, output_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    cache_path = workspace / "script.json"

    if cache_path.exists() and not force:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("_builder_version") == SCRIPT_BUILDER_VERSION:
            return cached

    facts, meta = extract_facts(blog_record)
    narration = _write_narration(facts, blog_record)
    narration = _scrub_banned(narration)
    scenes = _scene_breakdown(narration, blog_record)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
    hook = sentences[0] if sentences else ""
    cta = sentences[-1] if sentences else ""

    duration = sum(s.get("duration_s", 0) for s in scenes)

    title = (blog_record.get("title") or "")[:100]
    description = (
        f"{hook}\n\nFull breakdown: {blog_record.get('url', '')}\n"
        f"More: https://hrsuindore.com"
    )
    hashtags = ["#calciumnitrate", "#chemistry", "#procurement",
                f"#{blog_record.get('category', 'industry').replace('_', '')}",
                f"#{blog_record.get('region', 'global')}"]

    result = {
        "narration": narration,
        "scenes": scenes,
        "hook": hook,
        "cta": cta,
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "estimated_duration_s": duration,
        "extraction_metadata": meta,
        "_builder_version": SCRIPT_BUILDER_VERSION,
    }

    cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    return result
```

- [ ] **Step 4: Run all script_builder tests**

Run: `pytest tests/video_agent/test_script_builder_full.py tests/video_agent/test_script_builder_extraction.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/script_builder.py tests/video_agent/test_script_builder_full.py
git commit -m "feat(video_agent): script_builder narration + scene breakdown + caching"
```

---

### Task 8: Sprint 1 verification

- [ ] **Step 1: Run all Sprint 1 tests**

Run: `pytest tests/video_agent/ -v`
Expected: All tests in `test_text_normalizer.py`, `test_history.py`, `test_ollama_client.py`, `test_script_builder_extraction.py`, `test_script_builder_full.py` PASS.

- [ ] **Step 2: Live smoke test (requires Ollama running)**

Run: `ollama serve` in another terminal first. Then:

```bash
python -c "
from video_agent.script_builder import build_script
blog = {
    'blog_id': 'smoke1',
    'title': 'Calcium Nitrate Cuts H2S 90% in Australian Wastewater',
    'url': 'https://blog.hrsuindore.com/test',
    'region': 'australia', 'persona': 'procurement',
    'category': 'wastewater_treatment', 'subcategory': 'h2s',
    'content_html': '<p>Calcium nitrate cut H2S by 90% at 50 mg/L within 24 hours. Australian utilities saved 15% on chemical costs.</p>',
    'summary': 'smoke',
}
r = build_script(blog)
print('Narration:', r['narration'][:200])
print('Scenes:', len(r['scenes']))
print('Tier:', r['extraction_metadata']['tier_used'])
"
```

Expected: Prints narration, scene count between 8 and 12, tier 1 or 2.

- [ ] **Step 3: Tag sprint completion**

```bash
git tag video-agent-sprint-1
```

---

## Sprint 2 — Voice & subtitles

### Task 9: TDD `voiceover.py`

**Files:**
- Create: `tests/video_agent/test_voiceover.py`
- Create: `video_agent/voiceover.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_voiceover.py`:

```python
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.voiceover import synthesize, VoiceoverError


def _fake_pydub_segment(duration_s: float):
    seg = MagicMock()
    seg.__len__ = lambda self: int(duration_s * 1000)  # ms
    return seg


def test_voice_picked_by_region(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda txt, voice, path: path.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        result = synthesize("hello world " * 30, out, region="australia")
    assert result["voice_used"] == "en-AU-WilliamNeural"
    assert result["engine_used"] == "edge-tts"
    assert result["fell_back"] is False


def test_voice_override_wins(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda txt, voice, path: path.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        result = synthesize("hello " * 30, out, region="usa",
                           voice_override="en-GB-RyanNeural")
    assert result["voice_used"] == "en-GB-RyanNeural"


def test_falls_back_to_kokoro_when_edge_fails(tmp_path):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize",
               side_effect=ConnectionError("net")), \
         patch("video_agent.voiceover._kokoro_synthesize") as mock_k, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_k.side_effect = lambda txt, path: path.write_bytes(b"y" * 80_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        result = synthesize("hi " * 30, out, region="usa")
    assert result["fell_back"] is True
    assert result["engine_used"] == "kokoro"


def test_text_normalized_before_tts(tmp_path):
    captured = {}
    def capture(txt, voice, path):
        captured["text"] = txt
        path.write_bytes(b"x" * 60_000)
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize", side_effect=capture), \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_audio.from_mp3.return_value = _fake_pydub_segment(45.0)
        synthesize("H2S at 50 mg/L cut by 90%. " * 5, out, region="usa")
    assert "H 2 S" in captured["text"]
    assert "milligrams per liter" in captured["text"]
    assert "percent" in captured["text"]


def test_rejects_oversized_narration(tmp_path):
    long_text = "word " * 250
    with pytest.raises(VoiceoverError, match="200 words"):
        synthesize(long_text, tmp_path / "v.mp3", region="usa")


def test_warns_on_duration_outside_range(tmp_path, caplog):
    out = tmp_path / "v.mp3"
    with patch("video_agent.voiceover._edge_synthesize") as mock_edge, \
         patch("video_agent.voiceover.AudioSegment") as mock_audio:
        mock_edge.side_effect = lambda t, v, p: p.write_bytes(b"x" * 60_000)
        mock_audio.from_mp3.return_value = _fake_pydub_segment(15.0)
        synthesize("hi " * 30, out, region="usa")
    assert any("duration" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `pytest tests/video_agent/test_voiceover.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `voiceover.py`**

Create `video_agent/voiceover.py`:

```python
"""Voiceover generation via edge-tts (primary) with Kokoro-82M fallback."""
import asyncio
import logging
from pathlib import Path
from pydub import AudioSegment

import edge_tts

from video_agent.config import (
    TTS_VOICES, TTS_RATE, TTS_PITCH, KOKORO_DEFAULT_VOICE,
)
from video_agent.text_normalizer import normalize_for_tts

log = logging.getLogger(__name__)

MAX_WORDS = 200
MIN_DURATION_S = 30
MAX_DURATION_S = 65
MIN_FILE_BYTES = 1024


class VoiceoverError(RuntimeError):
    pass


def _build_ssml_text(text: str) -> str:
    """edge-tts accepts plain text; insert sentence pauses naturally via punctuation."""
    return text


def _edge_synthesize(text: str, voice: str, output_path: Path) -> None:
    async def _run():
        comm = edge_tts.Communicate(
            text, voice=voice, rate=TTS_RATE, pitch=TTS_PITCH,
        )
        await comm.save(str(output_path))
    asyncio.run(_run())


def _kokoro_synthesize(text: str, output_path: Path) -> None:
    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        raise VoiceoverError(
            "Kokoro fallback requires kokoro-onnx — pip install kokoro-onnx"
        ) from e
    kokoro = Kokoro.from_pretrained()
    samples, sr = kokoro.create(text, voice=KOKORO_DEFAULT_VOICE)
    seg = AudioSegment(
        samples.tobytes(), frame_rate=sr, sample_width=samples.dtype.itemsize, channels=1,
    )
    seg.export(output_path, format="mp3", bitrate="128k")


def synthesize(narration: str, output_path: Path, region: str,
               voice_override: str | None = None) -> dict:
    """Generate voiceover MP3. Returns VoiceoverResult dict."""
    output_path = Path(output_path)
    word_count = len(narration.split())
    if word_count > MAX_WORDS:
        raise VoiceoverError(
            f"narration too long ({word_count} > {MAX_WORDS} words)"
        )

    voice = voice_override or TTS_VOICES.get(region, TTS_VOICES["default"])
    normalized = normalize_for_tts(narration)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fell_back = False
    engine = "edge-tts"
    try:
        _edge_synthesize(normalized, voice, output_path)
        if not output_path.exists() or output_path.stat().st_size < MIN_FILE_BYTES:
            raise RuntimeError("edge-tts output too small")
    except Exception as e:
        log.warning("edge-tts failed (%s) — falling back to Kokoro", e)
        fell_back = True
        engine = "kokoro"
        _kokoro_synthesize(normalized, output_path)

    seg = AudioSegment.from_mp3(str(output_path))
    duration_s = len(seg) / 1000.0
    if not (MIN_DURATION_S <= duration_s <= MAX_DURATION_S):
        log.warning("Voiceover duration %.1fs outside target [%d, %d]",
                    duration_s, MIN_DURATION_S, MAX_DURATION_S)

    return {
        "audio_path": output_path,
        "duration_s": duration_s,
        "voice_used": voice,
        "engine_used": engine,
        "fell_back": fell_back,
    }
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_voiceover.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Live smoke test (network required)**

```bash
python -c "
from pathlib import Path
from video_agent.voiceover import synthesize
r = synthesize('Calcium nitrate cuts H2S by 90 percent.', Path('test_voice.mp3'), 'australia')
print(r)
"
```

Expected: file `test_voice.mp3` created (>10KB), prints VoiceoverResult dict. Delete file: `rm test_voice.mp3`.

- [ ] **Step 6: Commit**

```bash
git add video_agent/voiceover.py tests/video_agent/test_voiceover.py
git commit -m "feat(video_agent): voiceover with edge-tts + Kokoro fallback"
```

---

### Task 10: TDD `subtitles.py`

**Files:**
- Create: `tests/video_agent/test_subtitles.py`
- Create: `video_agent/subtitles.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_subtitles.py`:

```python
import re
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.subtitles import generate_srt, _chunk_words


def test_chunk_words_max_3_per_line():
    words = [
        {"word": "Calcium", "start": 0.0, "end": 0.4},
        {"word": "nitrate", "start": 0.4, "end": 0.7},
        {"word": "cuts", "start": 0.7, "end": 1.0},
        {"word": "H2S", "start": 1.0, "end": 1.3},
        {"word": "fast", "start": 1.3, "end": 1.6},
    ]
    cues = _chunk_words(words, max_words=3, max_dur=1.5)
    assert all(len(c["text"].split()) <= 3 for c in cues)
    assert cues[0]["text"] == "CALCIUM NITRATE CUTS"
    assert cues[1]["text"] == "H2S FAST"


def test_chunk_breaks_on_max_duration():
    words = [
        {"word": "long", "start": 0.0, "end": 1.6},  # single word > max_dur
    ]
    cues = _chunk_words(words, max_words=3, max_dur=1.5)
    assert len(cues) == 1
    assert cues[0]["text"] == "LONG"


def test_generate_srt_writes_valid_file(tmp_path):
    fake_segments = [
        MagicMock(words=[
            MagicMock(word="Hello", start=0.0, end=0.4),
            MagicMock(word="world", start=0.4, end=0.8),
        ]),
    ]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (fake_segments, MagicMock())
    out = tmp_path / "s.srt"
    with patch("video_agent.subtitles.WhisperModel", return_value=fake_model):
        path = generate_srt(tmp_path / "fake.mp3", out, narration_hint="Hello world")
    assert path == out
    text = out.read_text(encoding="utf-8")
    assert text.startswith("1\n")
    assert "HELLO WORLD" in text
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", text)
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `pytest tests/video_agent/test_subtitles.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `subtitles.py`**

Create `video_agent/subtitles.py`:

```python
"""Generate SRT subtitles via faster-whisper, mobile-optimized 3-word chunks."""
import logging
from datetime import timedelta
from pathlib import Path
from faster_whisper import WhisperModel

from video_agent.config import (
    WHISPER_MODEL, WHISPER_MODEL_MULTILINGUAL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
    SUBTITLE_MAX_WORDS_PER_LINE, SUBTITLE_MAX_LINE_DURATION_S,
)

log = logging.getLogger(__name__)


def _format_ts(seconds: float) -> str:
    td = timedelta(seconds=max(0.0, seconds))
    total_ms = int(td.total_seconds() * 1000)
    hh, rem = divmod(total_ms, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _chunk_words(words: list[dict], max_words: int, max_dur: float) -> list[dict]:
    cues = []
    buf = []
    for w in words:
        if not buf:
            buf.append(w)
            continue
        new_words = buf + [w]
        new_dur = w["end"] - buf[0]["start"]
        if len(new_words) > max_words or new_dur > max_dur:
            cues.append(_flush(buf))
            buf = [w]
        else:
            buf.append(w)
    if buf:
        cues.append(_flush(buf))
    return cues


def _flush(buf: list[dict]) -> dict:
    return {
        "start": buf[0]["start"],
        "end": buf[-1]["end"],
        "text": " ".join(w["word"].strip() for w in buf).upper(),
    }


def generate_srt(audio_path: Path, output_srt_path: Path,
                 narration_hint: str | None = None,
                 multilingual: bool = False) -> Path:
    model_name = WHISPER_MODEL_MULTILINGUAL if multilingual else WHISPER_MODEL
    model = WhisperModel(model_name, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
    segments, _info = model.transcribe(
        str(audio_path), word_timestamps=True, initial_prompt=narration_hint,
    )

    flat_words = []
    for seg in segments:
        for w in (seg.words or []):
            flat_words.append({"word": w.word, "start": w.start, "end": w.end})

    cues = _chunk_words(flat_words, SUBTITLE_MAX_WORDS_PER_LINE,
                        SUBTITLE_MAX_LINE_DURATION_S)
    output_srt_path.parent.mkdir(parents=True, exist_ok=True)
    with output_srt_path.open("w", encoding="utf-8") as f:
        for i, cue in enumerate(cues, start=1):
            f.write(f"{i}\n")
            f.write(f"{_format_ts(cue['start'])} --> {_format_ts(cue['end'])}\n")
            f.write(f"{cue['text']}\n\n")
    return output_srt_path
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/test_subtitles.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/subtitles.py tests/video_agent/test_subtitles.py
git commit -m "feat(video_agent): subtitles with faster-whisper word timestamps"
```

---

### Task 11: Sprint 2 verification

- [ ] **Step 1: Run all tests**

Run: `pytest tests/video_agent/ -v`
Expected: All Sprint 1 + Sprint 2 tests PASS.

- [ ] **Step 2: Tag**

```bash
git tag video-agent-sprint-2
```

---

## Sprint 3 — Visual engine (text cards + infographics)

### Task 12: TDD `visual_engine/text_card.py`

**Files:**
- Create: `tests/video_agent/visual_engine/test_text_card.py`
- Create: `video_agent/visual_engine/text_card.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/visual_engine/test_text_card.py`:

```python
from pathlib import Path
from PIL import Image
from video_agent.visual_engine.text_card import render_text_card


def test_hook_card_resolution(tmp_path):
    out = tmp_path / "hook.png"
    render_text_card(out, layout="hook", text="H₂S CORROSION")
    assert Image.open(out).size == (1080, 1920)


def test_cta_card_resolution(tmp_path):
    out = tmp_path / "cta.png"
    render_text_card(out, layout="cta", text="HRSUINDORE.COM")
    assert Image.open(out).size == (1080, 1920)


def test_long_text_does_not_crash(tmp_path):
    out = tmp_path / "long.png"
    render_text_card(out, layout="hook",
                     text="A VERY LONG TITLE THAT EXCEEDS EIGHT WORDS EASILY")
    assert out.exists()


def test_custom_resolution(tmp_path):
    out = tmp_path / "v.png"
    render_text_card(out, layout="hook", text="X", resolution=(720, 1280))
    assert Image.open(out).size == (720, 1280)
```

- [ ] **Step 2: Run — confirm fail**

Run: `pytest tests/video_agent/visual_engine/test_text_card.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `text_card.py`**

Create `video_agent/visual_engine/text_card.py`:

```python
"""Pillow-rendered hook & CTA cards. Brand colors from config."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from video_agent.config import (
    BRAND_GOLD, BRAND_DARK_NAVY, BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    # Try Playfair Display Bold first; fall back gracefully.
    for name in ("PlayfairDisplay-Bold.ttf", "Playfair Display Bold.ttf",
                 "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines, line = [], ""
    for w in words:
        cand = f"{line} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font)
        if bbox[2] - bbox[0] <= max_width:
            line = cand
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def render_text_card(output_path: Path, *, layout: str, text: str,
                     resolution: tuple[int, int] = (1080, 1920)) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    img = Image.new("RGB", (w, h), color=BRAND_DARK_NAVY)
    draw = ImageDraw.Draw(img)

    if layout == "hook":
        size = 120 if len(text.split()) <= 8 else 90
        color = BRAND_GOLD
    else:  # cta
        size = 100
        color = BRAND_TEXT_LIGHT

    font = _load_font(size)
    margin = 80
    while size > 36:
        lines = _wrap(text, font, w - 2 * margin, draw)
        line_h = font.size + 18
        block_h = line_h * len(lines)
        if block_h <= h * 0.7 and len(lines) <= 6:
            break
        size -= 8
        font = _load_font(size)

    line_h = font.size + 18
    block_h = line_h * len(lines)
    y = (h - block_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), line, font=font, fill=color)
        y += line_h

    if layout == "cta":
        sub_font = _load_font(40)
        sub = "Need calcium nitrate?"
        sub_bbox = draw.textbbox((0, 0), sub, font=sub_font)
        draw.text(
            ((w - (sub_bbox[2] - sub_bbox[0])) // 2, h // 2 - block_h // 2 - 100),
            sub, font=sub_font, fill=BRAND_TEXT_MUTED,
        )
        # Gold underline
        underline_y = (h + block_h) // 2 + 40
        draw.line([(margin, underline_y), (w - margin, underline_y)],
                  fill=BRAND_GOLD, width=4)

    img.save(output_path, "PNG")
    return output_path
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/visual_engine/test_text_card.py -v`
Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/visual_engine/text_card.py tests/video_agent/visual_engine/test_text_card.py
git commit -m "feat(video_agent): Pillow text_card renderer (hook + cta)"
```

---

### Task 13: TDD `visual_engine/infographic.py`

**Files:**
- Create: `tests/video_agent/visual_engine/test_infographic.py`
- Create: `video_agent/visual_engine/infographic.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/visual_engine/test_infographic.py`:

```python
from pathlib import Path
from PIL import Image
from video_agent.visual_engine.infographic import render_infographic


def test_bar_chart(tmp_path):
    out = tmp_path / "bar.png"
    render_infographic(out, chart_type="bar",
                       title="Test", data={"labels": ["A", "B"], "values": [10, 90]})
    assert Image.open(out).size == (1080, 1920)


def test_callout_stat(tmp_path):
    out = tmp_path / "stat.png"
    render_infographic(out, chart_type="callout_stat",
                       title="H₂S Reduction", data={"value": "90%", "label": "with calcium nitrate"})
    assert Image.open(out).size == (1080, 1920)


def test_comparison(tmp_path):
    out = tmp_path / "cmp.png"
    render_infographic(out, chart_type="comparison",
                       title="Without vs With",
                       data={"left_label": "Without", "left_value": "20%",
                             "right_label": "With", "right_value": "90%"})
    assert out.exists()


def test_flow_chart(tmp_path):
    out = tmp_path / "flow.png"
    render_infographic(out, chart_type="flow",
                       title="Process", data={"steps": ["Dose", "React", "Settle"]})
    assert out.exists()


def test_line_chart(tmp_path):
    out = tmp_path / "line.png"
    render_infographic(out, chart_type="line",
                       title="Trend", data={"x": [1, 2, 3], "y": [10, 50, 90]})
    assert out.exists()


def test_unknown_chart_falls_back_to_callout(tmp_path):
    out = tmp_path / "u.png"
    render_infographic(out, chart_type="totally_made_up",
                       title="X", data={"value": "1", "label": "x"})
    assert out.exists()


def test_deterministic_output(tmp_path):
    out1 = tmp_path / "1.png"
    out2 = tmp_path / "2.png"
    spec = dict(chart_type="bar", title="T",
                data={"labels": ["A", "B"], "values": [10, 90]}, seed=42)
    render_infographic(out1, **spec)
    render_infographic(out2, **spec)
    assert out1.read_bytes() == out2.read_bytes()
```

- [ ] **Step 2: Run — confirm fail**

Run: `pytest tests/video_agent/visual_engine/test_infographic.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `infographic.py`**

Create `video_agent/visual_engine/infographic.py`:

```python
"""Matplotlib infographic renderer with HRSU brand styling."""
import logging
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from video_agent.config import (
    BRAND_GOLD, BRAND_DARK_NAVY, BRAND_NAVY_2, BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
)

log = logging.getLogger(__name__)

plt.rcParams.update({
    "axes.facecolor": BRAND_DARK_NAVY,
    "figure.facecolor": BRAND_DARK_NAVY,
    "axes.edgecolor": BRAND_TEXT_MUTED,
    "axes.labelcolor": BRAND_TEXT_LIGHT,
    "xtick.color": BRAND_TEXT_LIGHT,
    "ytick.color": BRAND_TEXT_LIGHT,
    "font.size": 22,
})


def _setup_fig(resolution: tuple[int, int]):
    w, h = resolution
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi,
                     facecolor=BRAND_DARK_NAVY)
    return fig


def _add_title(fig, title: str):
    fig.text(0.5, 0.92, title, ha="center", va="top",
             color=BRAND_GOLD, fontsize=42, weight="bold")


def _add_footer(fig):
    fig.text(0.5, 0.04, "hrsuindore.com", ha="center",
             color=BRAND_TEXT_MUTED, fontsize=18)


def _bar(fig, data: dict):
    ax = fig.add_axes([0.12, 0.20, 0.76, 0.58])
    labels = data.get("labels", [])
    values = data.get("values", [])
    colors = [BRAND_GOLD if v == max(values) else BRAND_TEXT_MUTED for v in values]
    ax.bar(labels, values, color=colors, edgecolor="none")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, v in enumerate(values):
        ax.text(i, v, str(v), ha="center", va="bottom",
                color=BRAND_TEXT_LIGHT, fontsize=28, weight="bold")


def _callout_stat(fig, data: dict):
    fig.text(0.5, 0.55, str(data.get("value", "")),
             ha="center", va="center", color=BRAND_GOLD,
             fontsize=200, weight="bold")
    fig.text(0.5, 0.30, str(data.get("label", "")),
             ha="center", va="center", color=BRAND_TEXT_LIGHT,
             fontsize=36)


def _comparison(fig, data: dict):
    left_lbl = data.get("left_label", "Without")
    right_lbl = data.get("right_label", "With")
    left_val = str(data.get("left_value", "—"))
    right_val = str(data.get("right_value", "—"))
    fig.text(0.27, 0.6, left_val, ha="center", color=BRAND_TEXT_MUTED,
             fontsize=120, weight="bold")
    fig.text(0.73, 0.6, right_val, ha="center", color=BRAND_GOLD,
             fontsize=120, weight="bold")
    fig.text(0.27, 0.4, left_lbl, ha="center", color=BRAND_TEXT_MUTED, fontsize=32)
    fig.text(0.73, 0.4, right_lbl, ha="center", color=BRAND_TEXT_LIGHT, fontsize=32)


def _flow(fig, data: dict):
    steps = data.get("steps", [])[:5]
    if not steps:
        return
    ax = fig.add_axes([0.05, 0.35, 0.9, 0.30])
    ax.set_xlim(0, len(steps))
    ax.set_ylim(0, 1)
    ax.axis("off")
    for i, step in enumerate(steps):
        ax.add_patch(plt.Rectangle((i + 0.05, 0.3), 0.9, 0.4,
                                   facecolor=BRAND_NAVY_2, edgecolor=BRAND_GOLD, linewidth=3))
        ax.text(i + 0.5, 0.5, step, ha="center", va="center",
                color=BRAND_TEXT_LIGHT, fontsize=22, weight="bold")
        if i < len(steps) - 1:
            ax.annotate("", xy=(i + 1.05, 0.5), xytext=(i + 0.95, 0.5),
                        arrowprops=dict(arrowstyle="->", color=BRAND_GOLD, lw=3))


def _line(fig, data: dict):
    ax = fig.add_axes([0.12, 0.20, 0.76, 0.58])
    x = data.get("x", [])
    y = data.get("y", [])
    ax.plot(x, y, color=BRAND_GOLD, linewidth=4, marker="o", markersize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


_DISPATCH = {
    "bar": _bar, "callout_stat": _callout_stat, "comparison": _comparison,
    "flow": _flow, "line": _line,
}


def render_infographic(output_path: Path, *, chart_type: str, title: str = "",
                       data: dict | None = None,
                       resolution: tuple[int, int] = (1080, 1920),
                       seed: int | None = None) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        np.random.seed(seed)
    fig = _setup_fig(resolution)
    if title:
        _add_title(fig, title)
    fn = _DISPATCH.get(chart_type, _callout_stat)
    fn(fig, data or {})
    _add_footer(fig)
    fig.savefig(output_path, dpi=100, facecolor=BRAND_DARK_NAVY,
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return output_path
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/visual_engine/test_infographic.py -v`
Expected: All 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/visual_engine/infographic.py tests/video_agent/visual_engine/test_infographic.py
git commit -m "feat(video_agent): matplotlib infographic renderer (5 chart types)"
```

---

### Task 14: TDD `visual_engine/dispatcher.py` (text_card + infographic only)

**Files:**
- Create: `tests/video_agent/visual_engine/test_dispatcher.py`
- Create: `video_agent/visual_engine/dispatcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/visual_engine/test_dispatcher.py`:

```python
from pathlib import Path
from video_agent.visual_engine.dispatcher import generate_visual, generate_all_visuals


def _scene(idx, vt, spec=None, text=""):
    return {"index": idx, "narration": "n", "duration_s": 3.0,
            "visual_type": vt, "visual_spec": spec or {},
            "on_screen_text": text, "transition_in": "fade"}


def test_dispatches_text_card(tmp_path):
    s = _scene(0, "text_card", {"layout": "hook"}, "HOOK")
    out = generate_visual(s, tmp_path / "0.png")
    assert out["asset_path"].exists()
    assert out["generator_used"] == "text_card"
    assert not out["is_video_clip"]


def test_dispatches_infographic(tmp_path):
    s = _scene(1, "infographic",
               {"chart_type": "callout_stat", "data": {"value": "90%", "label": "x"}})
    out = generate_visual(s, tmp_path / "1.png")
    assert out["asset_path"].exists()
    assert out["generator_used"] == "infographic"


def test_unknown_visual_falls_back_to_text_card(tmp_path):
    s = _scene(2, "unknown_type", {}, "FALLBACK")
    out = generate_visual(s, tmp_path / "2.png")
    assert out["generator_used"] == "text_card"


def test_generate_all_preserves_order(tmp_path):
    scenes = [_scene(i, "text_card", {"layout": "hook"}, f"S{i}") for i in range(4)]
    results = generate_all_visuals(scenes, tmp_path)
    assert len(results) == 4
    for i, r in enumerate(results):
        assert r["asset_path"].name.startswith(f"scene_{i:02d}")


def test_per_scene_failure_falls_back(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("matplotlib died")
    monkeypatch.setattr("video_agent.visual_engine.dispatcher.render_infographic", boom)
    s = _scene(0, "infographic", {"chart_type": "bar", "data": {"labels": [], "values": []}}, "FB")
    out = generate_visual(s, tmp_path / "0.png")
    assert out["generator_used"] == "text_card"
```

- [ ] **Step 2: Run — confirm fail**

Run: `pytest tests/video_agent/visual_engine/test_dispatcher.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `dispatcher.py`**

Create `video_agent/visual_engine/dispatcher.py`:

```python
"""Routes scenes to the right visual generator. Falls back to text_card on error."""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from video_agent.config import PARALLEL_VISUAL_WORKERS
from video_agent.visual_engine.text_card import render_text_card
from video_agent.visual_engine.infographic import render_infographic

log = logging.getLogger(__name__)


def _safe_text_card(scene: dict, output_path: Path,
                    resolution: tuple[int, int]) -> dict:
    spec = scene.get("visual_spec") or {}
    layout = spec.get("layout", "hook")
    text = scene.get("on_screen_text") or scene.get("narration", "")[:40] or "HRSU"
    render_text_card(output_path, layout=layout, text=text, resolution=resolution)
    return {
        "asset_path": output_path, "is_video_clip": False, "duration_s": None,
        "generator_used": "text_card",
    }


def _safe_infographic(scene: dict, output_path: Path,
                      resolution: tuple[int, int], seed: int | None) -> dict:
    spec = scene.get("visual_spec") or {}
    render_infographic(
        output_path,
        chart_type=spec.get("chart_type", "callout_stat"),
        title=spec.get("title", ""),
        data=spec.get("data") or {},
        resolution=resolution,
        seed=seed,
    )
    return {
        "asset_path": output_path, "is_video_clip": False, "duration_s": None,
        "generator_used": "infographic",
    }


def generate_visual(scene: dict, output_path: Path,
                    resolution: tuple[int, int] = (1080, 1920),
                    seed: int | None = None) -> dict:
    output_path = Path(output_path)
    vt = scene.get("visual_type", "text_card")
    seed = seed if seed is not None else hash((scene.get("index"), scene.get("narration", ""))) & 0xFFFF
    try:
        if vt == "text_card":
            return _safe_text_card(scene, output_path, resolution)
        if vt == "infographic":
            return _safe_infographic(scene, output_path, resolution, seed)
        # hrsu_edge / stock are added in Sprint 5; fall back to text_card for now
        log.info("Visual type %r not yet implemented; using text_card", vt)
        return _safe_text_card(scene, output_path, resolution)
    except Exception as e:
        log.warning("Visual generation failed for scene %s (%s); falling back to text_card",
                    scene.get("index"), e)
        return _safe_text_card(scene, output_path, resolution)


def generate_all_visuals(scenes: list[dict], output_dir: Path,
                         resolution: tuple[int, int] = (1080, 1920)) -> list[dict]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    indexed = list(enumerate(scenes))

    def _job(args):
        i, s = args
        path = output_dir / f"scene_{i:02d}.png"
        return i, generate_visual(s, path, resolution)

    results: list[dict] = [None] * len(scenes)
    with ThreadPoolExecutor(max_workers=PARALLEL_VISUAL_WORKERS) as ex:
        for i, r in ex.map(_job, indexed):
            results[i] = r
    return results
```

- [ ] **Step 4: Run tests until green**

Run: `pytest tests/video_agent/visual_engine/test_dispatcher.py -v`
Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/visual_engine/dispatcher.py tests/video_agent/visual_engine/test_dispatcher.py
git commit -m "feat(video_agent): visual dispatcher + parallel batch + fallback"
```

---

### Task 15: Sprint 3 verification

- [ ] **Step 1: Run all tests**

Run: `pytest tests/video_agent/ -v`
Expected: All Sprint 1+2+3 tests PASS.

- [ ] **Step 2: Tag**

```bash
git tag video-agent-sprint-3
```

---

(Plan continues — tasks 16+ for composer, publishers, scheduler, orchestrator, CLI, and tools added in subsequent commits to keep this file maintainable. Tasks 16–38 follow the same TDD pattern. See spec sections §4.6–§4.12 and §6 for the remaining module specifications and sprint mapping.)

---

## Continuation marker

This file is intentionally split: Sprints 1–3 are fully detailed above. Sprints 4–8 (composer, publishers, scheduler, orchestrator, CLI, tools) follow the **same task pattern** — each task is `(failing test → implement → green → commit)`. The implementing agent should generate the remaining tasks against the spec sections noted in the Sprint Map at the top.

If you are an agentic worker executing this plan: **stop after Task 15 and request the continuation file** (`2026-05-03-video-pipeline-implementation-part-2.md`) before starting Sprint 4. The author will produce it on demand.
