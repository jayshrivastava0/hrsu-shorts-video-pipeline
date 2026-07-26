# HRSU Vertical Short-Form Video Pipeline — Design Spec

**Date:** 2026-05-02
**Status:** Design approved, awaiting implementation plan
**Owner:** S. Shrivastava
**Target audience for this doc:** Implementing AI agent (Claude) in a fresh session

---

## 0. Context

HRSU Indore manufactures calcium nitrate. The existing `blog_agent_v3.py` pipeline generates SEO-optimized blog posts targeting two personas (procurement managers, executives) across six export regions (Australia, USA, EU, Germany, East Asia, Gulf), publishes to Blogger, and distributes to LinkedIn + Facebook via direct APIs (no Playwright). All content generation runs on **Ollama (`gemma3:4b`)** locally — zero per-call API cost.

This spec describes a **new sibling module `video_agent/`** that turns each published blog into a **vertical short-form video (9:16, ≤60s)** and publishes it to:

- **YouTube Shorts** (Data API v3)
- **LinkedIn Page Video Post** (LinkedIn Videos API + UGC Posts)
- **Instagram Reels** (Meta Graph API, gated by Meta app review)

The goal is reaching procurement managers and decision-makers in target export markets through visual media — building on the SEO-driven blog strategy with social discovery.

**Hard constraints from project owner:**
- 100% free / no recurring costs (one-time GitHub free tier, free TTS, free image generation, free APIs).
- Must run on a Windows laptop with optional GTX 1550Ti (4GB VRAM, often unavailable to user code) — therefore all heavy compute must work CPU-only.
- Up to ~15 min of generation time per video is acceptable.
- Must reuse existing patterns: `token_manager.py`, `secrets.txt`, `BLOG_STYLE_TEMPLATE` brand colors, `REGION_POSTING_SCHEDULE`, Ollama for text generation.

---

## 1. Strategic & Architectural Decisions (Locked)

| Decision | Choice | Rationale |
|---|---|---|
| MVP scope | Vertical shorts (9:16, ≤60s) only | Highest discovery odds for a no-budget B2B brand |
| Architecture | Sibling `video_agent/` package, loosely coupled to blog pipeline via `blog_history.json` | Mirrors existing `social_agent/` mental model; doesn't bloat `blog_agent_v3.py` |
| Script generation LLM | Ollama `gemma3:4b` (same as blogs) | 100% free; consistent stack |
| TTS primary | `edge-tts` (Microsoft Neural Voices) | Free, no key, neural quality, online |
| TTS fallback | Kokoro-82M (Apache 2.0, CPU) | Offline backstop; commercial-safe license (F5-TTS rejected — CC-BY-NC) |
| Image generation | NONE in default pipeline | Style choice (technical infographics) makes AI photos a trust risk |
| Visual style | Matplotlib infographics + real HRSU factory B-roll for "HRSU Edge" segments | No uncanny AI imagery; real footage = trust |
| Stock filler | Pexels API (free, commercial-OK) — used rarely | Last-resort visual when no factory asset matches |
| Subtitles | `faster-whisper base.en` (CPU, int8, word-timed) | Mobile-optimized 3-words-per-line karaoke style |
| Composition | FFmpeg + MoviePy | Industry standard, free, Windows-friendly |
| Video hosting for IG | GitHub Releases as MP4 CDN | Free, zero new accounts, IG requires public URL |
| Cadence | One-time backfill of last 20 blogs + ongoing per-blog generation | Channels with <10 videos get suppressed by algorithms |
| Posting time | Per-region prime time via APScheduler (reuses `REGION_POSTING_SCHEDULE`) | Indian-time posts miss the actual audience |
| Account state | YT channel: needs creating · IG account: needs creating · Meta app: needs IG permission review (5–15 days) | Spec gates IG behind a config flag until approval lands |

---

## 2. System Architecture

### 2.1 Data flow

```
                ┌──────────────────────────────────────────────────────┐
                │  EXISTING (no changes)                               │
                │  blog_agent_v3.py  →  Blogger publish                │
                │                    →  appends to blog_history.json   │
                └──────────────────────────────────────────────────────┘
                                        │
                          ┌─────────────┴─────────────┐
                          │ Trigger A:                │ Trigger B:
                          │ --with-video flag         │ Standalone CLI
                          │ added to blog_agent_v3    │ python -m video_agent.main
                          │ (one-line addition)       │ --from-blog-id <id> | --backfill
                          └─────────────┬─────────────┘
                                        ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │              video_agent/orchestrator.py                        │
        │                                                                 │
        │  1. Load blog record   ─→  blog_history.json                    │
        │  2. Build script       ─→  script_builder.py    (Ollama)        │
        │  3. Generate voiceover ─→  voiceover.py         (edge-tts)      │
        │  4. Generate visuals   ─→  visual_engine/*      (parallel)      │
        │  5. Generate subtitles ─→  subtitles.py         (faster-whisper)│
        │  6. Compose video      ─→  composer.py          (FFmpeg)        │
        │  7. Schedule/publish   ─→  scheduler.py + publishers/*          │
        │  8. Log result         ─→  video_history.json                   │
        └─────────────────────────────────────────────────────────────────┘
                                        │
                ┌───────────────────────┼───────────────────────┐
                ▼                       ▼                       ▼
        publishers/youtube.py   publishers/linkedin.py   publishers/instagram.py
        (Data API v3 upload)    (Videos API +            (Graph API REELS,
                                 UGC Post w/ video URN)   gated by app review)
                ▼                       ▼                       ▼
            YouTube Shorts          LinkedIn Page           Instagram Reels
```

### 2.2 Module dependency graph

```
main.py ─→ orchestrator.py ─┬─→ script_builder.py ──→ text_normalizer.py
                            ├─→ voiceover.py ──────→ text_normalizer.py
                            ├─→ visual_engine/dispatcher.py ─┬→ infographic.py
                            │                                ├→ factory_broll.py ──→ asset_manifest.py
                            │                                ├→ stock.py
                            │                                └→ text_card.py
                            ├─→ subtitles.py
                            ├─→ composer.py
                            ├─→ scheduler.py ──┐
                            └─→ history.py     │
                                               ▼
                                publishers/youtube.py
                                publishers/linkedin.py    (reuses linkedin_api.py + token_manager.py)
                                publishers/instagram.py   (reuses token_manager.py)
```

No circular dependencies. Every module is independently testable. Public APIs take plain dicts/Paths (no shared mutable state).

### 2.3 Directory layout

```
E:\Projects\HRSU Blog\
├── video_agent\                          # NEW — this spec
│   ├── __init__.py
│   ├── main.py                           # CLI entry
│   ├── config.py                         # video-specific config (imports root config.py)
│   ├── orchestrator.py
│   ├── script_builder.py
│   ├── text_normalizer.py
│   ├── voiceover.py
│   ├── subtitles.py
│   ├── composer.py
│   ├── scheduler.py
│   ├── history.py
│   ├── asset_manifest.py
│   ├── visual_engine\
│   │   ├── __init__.py
│   │   ├── dispatcher.py
│   │   ├── infographic.py
│   │   ├── factory_broll.py
│   │   ├── stock.py
│   │   └── text_card.py
│   ├── publishers\
│   │   ├── __init__.py
│   │   ├── base.py                       # BasePublisher ABC
│   │   ├── youtube.py
│   │   ├── linkedin.py
│   │   └── instagram.py
│   └── tools\
│       ├── tag_assets.py                 # interactive manifest builder
│       ├── render_brand_assets.py        # one-time intro/outro renderer
│       ├── check_music_library.py
│       └── shoot_list.py                 # prints recommended phone-shoot list
├── asset_library\                        # NEW — populated by user
│   ├── factory\
│   │   ├── manifest.json
│   │   └── *.mp4 / *.jpg
│   ├── brand\
│   │   ├── hrsu_logo_white.png
│   │   ├── hrsu_logo_gold.png
│   │   ├── intro_3s.mp4
│   │   └── outro_5s.mp4
│   ├── music\
│   │   └── *.mp3
│   └── stock_cache\                      # auto-populated Pexels cache
├── output\
│   └── videos\
│       └── {YYYY-MM-DD}_{slug}\
│           ├── script.json
│           ├── voiceover.mp3
│           ├── subtitles.srt
│           ├── scenes\                   # generated visuals
│           └── video_short.mp4
├── tests\                                # NEW — see Section 9
│   └── video_agent\
│       └── (mirror module structure)
├── docs\
│   └── superpowers\
│       └── specs\
│           └── 2026-05-02-video-pipeline-design.md   # THIS DOC
├── video_history.json                    # NEW
├── video_queue.json                      # NEW (scheduler state)
├── video_queue.db                        # NEW (APScheduler SQLite jobstore)
├── video_costs.log                       # NEW (per-run cost ledger)
├── video_post_failures.log               # NEW (max-attempts failures)
└── (existing files — no changes except blog_agent_v3.py optional flag)
```

### 2.4 Key data contracts

**`blog_record`** (already exists in `blog_history.json`, consumed by orchestrator):
```python
{
  "blog_id": "8123...",
  "title": "...",
  "url": "https://blog.hrsuindore.com/2026/...",
  "region": "australia",                # one of: australia | usa | eu | germany | east_asia | gulf
  "persona": "procurement",             # one of: procurement | executive
  "category": "wastewater_treatment",   # CALCIUM_NITRATE_APPLICATIONS keys
  "subcategory": "odor_control_h2s",
  "content_html": "...",                # full Blogger HTML
  "summary": "...",
  "key_facts": ["...", "..."],
  "citations": [...],
  "published_at": "2026-04-..."
}
```

**`SceneSpec`** (new, internal):
```python
{
  "index": 0,
  "narration": "Calcium nitrate cuts H₂S in wastewater by 90%.",
  "duration_s": 4.2,
  "visual_type": "infographic",         # text_card | infographic | hrsu_edge | stock
  "visual_spec": {
    # for "infographic": chart_type, title, data, highlight_label
    # for "hrsu_edge": tag preferences, fallback_text
    # for "text_card": layout (hook|cta), text content
    # for "stock": query string
  },
  "on_screen_text": "90% H₂S REDUCTION",  # optional overlay
  "transition_in": "fade",               # fade | cut | slide_up
}
```

**`ScriptResult`**:
```python
{
  "narration": str,                      # 120-170 words
  "scenes": list[SceneSpec],             # 8-12
  "hook": str,                           # first 5s text
  "cta": str,                            # final 5s text
  "title": str,
  "description": str,
  "hashtags": list[str],
  "estimated_duration_s": float,
  "extraction_metadata": {
    "tier_used": int,                    # 1-4 (see §4.1)
    "punch_points_count": int,
    "numeric_count": int,
    "fell_back_to_template": bool,
  },
}
```

**`VoiceoverResult`**:
```python
{
  "audio_path": Path,
  "duration_s": float,
  "voice_used": str,                     # e.g. "en-AU-WilliamNeural"
  "engine_used": str,                    # "edge-tts" | "kokoro"
  "fell_back": bool,
}
```

**`VisualResult`**:
```python
{
  "asset_path": Path,                    # PNG (still) or MP4 (B-roll clip)
  "is_video_clip": bool,
  "duration_s": float | None,            # only for video clips
  "generator_used": str,                 # "infographic" | "broll" | "stock" | "text_card"
}
```

**`PublishResult`**:
```python
{
  "success": bool,
  "platform": str,
  "post_url": str | None,
  "post_id": str | None,
  "scheduled": bool,
  "scheduled_for": datetime | None,
  "error": str | None,
}
```

**`OrchestratorResult`**:
```python
{
  "blog_id": str,
  "video_path": Path,
  "duration_s": float,
  "script_metadata": dict,
  "publish_results": dict[str, PublishResult | None],
  "elapsed_s": float,
  "errors": list[str],                   # non-fatal warnings
}
```

---

## 3. `video_agent/config.py`

New file. Imports shared values from root `config.py`. New keys:

```python
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
WHISPER_MODEL = "base.en"               # German blogs swap to "base"
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
ENABLE_AI_IMAGES = False   # future flag for Pollinations
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
SCRIPT_BANNED_PHRASES = [
    "as an ai", "in this video", "thanks for watching",
    "hope you enjoyed", "i don't have", "as of my last update",
]

# ─── Tier 4 fact-extraction templates ──────────────────────────────────────
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

# ─── Scheduler ─────────────────────────────────────────────────────────────
SCHEDULER_JOBSTORE_URL = "sqlite:///video_queue.db"
SCHEDULER_RETRY_BACKOFF_S = [300, 1800, 7200]   # 5min, 30min, 2hr
SCHEDULER_MAX_ATTEMPTS = 3

# ─── Quality gates ─────────────────────────────────────────────────────────
MIN_VIDEO_DURATION_S = 30
MAX_VIDEO_DURATION_S = 60
```

---

## 4. Module Specifications

### 4.1 `script_builder.py`

**Public API:**
```python
def build_script(blog_record: dict) -> ScriptResult: ...
```

**Pipeline:**

1. **Pass 1 — Tier-1 numeric fact extraction (Ollama).**
   System prompt: extract surprising numeric facts from blog HTML. JSON output schema:
   `{"value": str, "unit": str, "claim": str, "source_quote": str}`. Max 3 retries with format-repair on JSON failure.

2. **Pass 1.5 — Tier-2 regex backstop.**
   Fires if Tier 1 returns < 3 items. Pattern:
   `\b\d+(?:[.,]\d+)?\s*(%|ppm|mg/L|kg|tonnes?|°C|hours?|days?|years?|×|x)\b`.
   For each match, grab the surrounding sentence. Dedupe.

3. **Pass 1.6 — Tier-3 qualitative punch points (Ollama).**
   Fires if Tiers 1+2 combined < 3 items. Prompts for 5 surprising non-numeric claims (named entities, regulatory refs, contrarian statements, named processes, named risks). Each ≤ 15 words.

4. **Pass 1.7 — Tier-4 category template fallback.**
   Fires if Tiers 1+2+3 < 3 items. Pulls from `CATEGORY_PUNCH_POINTS[blog_record["category"]]`. Logs WARNING.

5. **Pass 1.8 — Tier-5 abort.**
   Only if no template defined for category. Raises `ScriptBuilderError` with clear message.

6. **Pass 2 — Narration writing (Ollama).**
   Inputs: punch points + persona + region. Constraints (validated post-call, max 3 retries):
   - 120–170 words
   - ≥3 numerals
   - Contains CTA phrase ("hrsuindore.com" OR "spec sheet" OR "DM us")
   - No banned phrases from `SCRIPT_BANNED_PHRASES`
   - Hook in first sentence (one of: stat / problem / question / contrarian template)

7. **Pass 3 — Scene breakdown (Ollama).**
   Splits narration into 8–12 sentence-aligned scenes. LLM picks `visual_type` and `visual_spec`. Then code post-processor enforces:
   - Scene 0: `text_card` (hook layout)
   - ≥2 scenes: `infographic`
   - ≥1 scene: `hrsu_edge`
   - Final scene: `text_card` (CTA layout)
   - All other scenes: `infographic` if data-heavy, else `stock` or `hrsu_edge`

8. **Banned-phrase scrub:** Apply existing `quality_guardrails.ContentGuardrails` to narration.

9. **Caching:** Identical `blog_id` + builder version → return cached `script.json`. Override with `--force`.

10. **Error handling:** Ollama unreachable → raise `ScriptBuilderError("Ollama not running — start with: ollama serve")`. No silent fallback to other LLMs.

**Audit field** added to `ScriptResult["extraction_metadata"]` (see §2.4).

---

### 4.2 `text_normalizer.py`

**Public API:**
```python
def normalize_for_tts(text: str) -> str: ...
```

**Replacements (regex map):**

| Source | Replacement |
|---|---|
| `H₂S` | `"H 2 S"` |
| `H2S` | `"H 2 S"` |
| `Ca(NO3)2`, `Ca(NO₃)₂` | `"calcium nitrate"` |
| `CO₂`, `CO2` | `"C O 2"` |
| `mg/L` | `"milligrams per liter"` |
| `kg/t` | `"kilograms per tonne"` |
| `%` | `"percent"` |
| `°C` | `"degrees Celsius"` |
| `hrsuindore.com` | `"H R S U Indore dot com"` |
| `[1]`, `[2]`, … (citation markers) | removed |
| Markdown `**bold**`, `*italic*` | stripped |
| Multiple spaces | single space |

Unit-test each rule. See §9.

---

### 4.3 `voiceover.py`

**Public API:**
```python
def synthesize(
    narration: str,
    output_path: Path,
    region: str,
    voice_override: str | None = None,
) -> VoiceoverResult: ...
```

**Engine pipeline:**
1. Normalize text via `text_normalizer.normalize_for_tts`.
2. Choose voice from `TTS_VOICES[region]` (or `voice_override`).
3. Build SSML with `<break time="400ms"/>` after each sentence.
4. Try edge-tts (async). On network error or output `< 1KB` → fallback to Kokoro.
5. Read back with `pydub.AudioSegment.from_mp3()` for exact duration.

**Hard limits:**
- Narration > 200 words → raise `VoiceoverError`.
- Duration outside `[30, 65]s` → log WARNING (composer pads/trims).

---

### 4.4 `subtitles.py`

**Public API:**
```python
def generate_srt(
    audio_path: Path,
    output_srt_path: Path,
    narration_hint: str | None = None,
) -> Path: ...
```

**Implementation:**
- `faster_whisper.WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)`.
- For German blogs → swap to `base` (multilingual).
- Pass `initial_prompt=narration_hint` for chemistry-term accuracy.
- Post-process word timings into ≤3-words-per-line, ≤1.5s-per-line, ALL CAPS chunks.
- Output standard SRT, UTF-8.

---

### 4.5 `visual_engine/`

**Dispatcher API:**
```python
def generate_visual(
    scene: SceneSpec,
    output_path: Path,
    resolution: tuple[int, int] = (1080, 1920),
    seed: int | None = None,
) -> VisualResult: ...

def generate_all_visuals(
    scenes: list[SceneSpec],
    output_dir: Path,
    resolution: tuple[int, int] = (1080, 1920),
) -> list[VisualResult]: ...
```

`generate_all_visuals` uses `ThreadPoolExecutor(max_workers=PARALLEL_VISUAL_WORKERS)`. Order preserved.

#### 4.5.1 `infographic.py`

Matplotlib-based. Five `chart_type` variants:

| chart_type | Renders | When LLM picks |
|---|---|---|
| `bar` | Horizontal/vertical bar, gold on dark navy, large white labels | Comparing 2–6 numeric values |
| `comparison` | Side-by-side cards: "Without X" (muted) vs "With X" (gold) | Before/after, vs alternatives |
| `callout_stat` | Single huge number (200pt) + caption | One big stat per narration |
| `flow` | 3–5 step horizontal flow with arrows | Process explanations |
| `line` | Line chart, gold line on dark bg | Time series, dose-response |

Style enforcement via global rcParams set on module import (see Section 4 of brainstorm record). Composition: top 30% title, middle 50% chart, bottom 20% source attribution + small HRSU watermark.

**Determinism:** seed all RNGs with `hash((scene["index"], scene["narration"]))`.

#### 4.5.2 `factory_broll.py`

Reads `asset_library/factory/manifest.json`. Selection scoring:
1. Filter by `blog_record["category"]` ∈ `asset.categories` (or `"all"`).
2. Filter by `blog_record["persona"]` ∈ `asset.personas`.
3. Boost `esg_relevant: true` if narration mentions any `ESG_KEYWORDS`.
4. Score by tag overlap with narration tokens.
5. Anti-repeat: per-blog used-asset set.
6. Top scorer wins; trim/loop video to scene duration.

**Empty-library fallback:** delegate to `text_card.py` with the scene's `on_screen_text`.

#### 4.5.3 `stock.py`

Pexels filler — used rarely. Cached at `asset_library/stock_cache/<sha1(query)>.jpg`. Logs photographer name to `video_history.json`.

#### 4.5.4 `text_card.py`

Pure Pillow. Two layouts:
- **`hook_card`** (Scene 0): Centered Playfair Display Bold 120pt gold, optional small HRSU wordmark top-center. Auto-shrinks to 90pt if text > 8 words.
- **`cta_card`** (Final scene): "Need calcium nitrate?" / "**HRSUINDORE.COM**" / contact line. Subtle gold underline.

Both deterministic.

---

### 4.6 `composer.py`

**Public API:**
```python
def compose_short(
    scenes: list[SceneSpec],
    visual_results: list[VisualResult],
    voiceover: VoiceoverResult,
    subtitle_path: Path,
    output_path: Path,
    music_track: Path | None = None,
    region: str = "default",
) -> Path: ...
```

**Steps:**

1. **Per-scene clips:** Video assets → `VideoFileClip` trimmed/looped to duration, smart center-cropped to 1080×1920. Stills → `ImageClip` with Ken Burns (zoom 1.0→1.08, 5% pan, seeded).
2. **Concatenate** with crossfades: 250ms default, hard cut after `text_card`, 350ms after `hrsu_edge`. `concatenate_videoclips(method="compose")`.
3. **Persistent overlays:**
   - Logo: top-right, 80px height, opacity 0.08, 24px margin.
   - Progress bar: 4px gold bar at bottom edge, fills L→R.
   - On-screen text: Pillow-rendered ImageClip overlay center-positioned, 200ms slide-up + fade-in.
4. **Subtitles:** Burned via FFmpeg `subtitles` filter with `force_style` (Poppins Bold 22, white text, 3px black outline, bottom-third position with `MarginV=200`).
5. **Audio mix:** Voiceover at 0dB. Music at −22dB. Sidechain ducking via FFmpeg `sidechaincompress`. Music selection deterministic by `region+persona`. Loops with `afade` if shorter than video.
6. **Intro + Outro:** Concat pre-rendered `intro_3s.mp4` + main + `outro_5s.mp4`.
7. **Encode:** libx264 + AAC, 30fps, 10M bitrate, `-pix_fmt yuv420p -movflags +faststart -profile:v high -level 4.0`.
8. **Validation (`_validate_output`):** ffprobe checks codec, resolution, duration, file size. Failure → `ComposerError`.

**Hard limits enforced:** duration ∈ [30, 60]s, file size < 100MB, exactly 1080×1920.

---

### 4.7 `publishers/`

#### 4.7.1 `base.py` — `BasePublisher` ABC

```python
class BasePublisher(ABC):
    @abstractmethod
    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        hashtags: list[str],
        blog_url: str,
        region: str,
        scheduled_for: datetime | None = None,
    ) -> PublishResult: ...
```

#### 4.7.2 `youtube.py`

**Auth:** Reuse Google OAuth from `blog_agent_v3.py::_get_blogger_service`. Add scope `https://www.googleapis.com/auth/youtube.upload` to existing scope list. Single `token.pickle` covers both. **One-time:** delete `token.pickle` and re-run any blogger command after scope change to trigger re-consent.

**Upload:** Resumable `MediaFileUpload(chunksize=-1, resumable=True)` to `youtube.videos().insert()`.

**Body:**
```python
{
  "snippet": {
    "title": title[:100],
    "description": _build_description(...),
    "tags": hashtags[:30],
    "categoryId": "27",
    "defaultLanguage": _region_to_iso(region),
  },
  "status": {
    "privacyStatus": "public" if not scheduled_for else "private",
    "publishAt": scheduled_for.isoformat() if scheduled_for else None,
    "selfDeclaredMadeForKids": False,
  }
}
```

**Shorts auto-detection:** ≤60s + 9:16 = auto-classified. Append `#Shorts` to description as belt-and-braces.

**Scheduling:** Use YouTube native `publishAt` — no custom scheduler entry needed.

**Quota guard:** `_quota_check()` reads Google Cloud quota usage; raises `QuotaExceededError` if remaining < 1600 units. Surfaces reset time.

**Description template:**
```
{first_sentence_of_narration}

📖 Full technical breakdown: {blog_url}
🌐 https://hrsuindore.com
📩 Spec sheet & quote — comment "SPEC" or DM us

#Shorts {hashtags}
```

#### 4.7.3 `linkedin.py`

Extends existing `linkedin_api.py::LinkedInAPI` with `post_video_to_company_page()`. Reuses `TokenManager`.

**Three-step LinkedIn Videos API flow:**

1. `POST /rest/videos?action=initializeUpload` — get video URN + chunked upload URLs + uploadToken.
2. For each upload instruction → `PUT` raw bytes for `firstByte..lastByte`. Collect `etag` headers.
3. `POST /rest/videos?action=finalizeUpload` with all etags.
4. Poll `GET /rest/videos/{video_urn}` every 5s until `status.processed == true` (timeout 5 min — post anyway on timeout).
5. `POST /rest/posts` with `content.media.id = video_urn`.

**Headers:** `LinkedIn-Version: 202604`, `X-Restli-Protocol-Version: 2.0.0`.

**Scheduling:** Posts API doesn't support native scheduling — delegate to `scheduler.py`.

**Post text template:**
```
{narration_hook_one_liner}

{2-3 line summary of value}

🔗 Full technical breakdown: {blog_url}
📩 Spec sheet & quote → DM us

{hashtags}
```

#### 4.7.4 `instagram.py`

**Gate flag:** Reads `IG_PUBLISHING_ENABLED` from `secrets.txt`. Default `false` → graceful no-op:
`{"success": False, "error": "IG publishing disabled — pending Meta app review"}`.

**Video hosting (GitHub Releases CDN):**
- `_upload_to_github_release(video_path)` creates a GitHub release in `GITHUB_CDN_REPO`, uploads MP4 as release asset, returns public download URL.
- Uses `PyGithub` (added to requirements).
- Cleanup task deletes assets older than 7 days (separate `python -m video_agent.tools.cleanup_cdn`).

**Two-step Reels upload:**
1. `POST /v19.0/{ig_user_id}/media` with `media_type=REELS`, `video_url=<github_url>`, `caption`, `share_to_feed=true`.
2. Poll `GET /v19.0/{container_id}?fields=status_code` every 5s until `FINISHED` (timeout 5 min).
3. `POST /v19.0/{ig_user_id}/media_publish` with `creation_id`.
4. `GET /{media_id}?fields=permalink` to get post URL.

**TokenManager additions** (in existing `token_manager.py`):
```python
def get_instagram_user_id(self) -> str
def get_instagram_publishing_enabled(self) -> bool
def get_github_token(self) -> str
def get_github_cdn_repo(self) -> str
def get_pexels_api_key(self) -> str
```

**Caption length:** ≤2200 chars, ≤30 hashtags. Both well under our usage.

**Scheduling:** No native API support — delegate to `scheduler.py`.

---

### 4.8 `scheduler.py`

APScheduler-based. New `Scheduler` class wrapping `BackgroundScheduler` with `SQLAlchemyJobStore(SCHEDULER_JOBSTORE_URL)`.

**Public API:**
```python
class Scheduler:
    def enqueue_for_region(
        self,
        video_path: Path,
        blog_record: dict,
        platforms: list[str],
        title: str,
        description: str,
        hashtags: list[str],
    ) -> list[str]: ...   # returns queue_ids

    def list_queue(self) -> list[dict]: ...
    def cancel(self, queue_id: str) -> bool: ...
    def flush_now(self) -> None: ...
```

**Behavior:**
- Computes target datetime from `REGION_POSTING_SCHEDULE` (next valid weekday at region's local hour, in region's timezone, converted to UTC).
- One APScheduler job per `(platform, video)`.
- **YouTube exception:** Calls `youtube.upload(scheduled_for=...)` immediately (YT does the scheduling). No queue entry.
- Retry policy: backoffs `[300, 1800, 7200]`, max 3 attempts. After max → log to `video_post_failures.log`, mark failed.
- Persistence: APScheduler SQLite at `video_queue.db`; mirror state in `video_queue.json` for human inspection.

**CLI:**
```
python -m video_agent.scheduler --start
python -m video_agent.scheduler --list
python -m video_agent.scheduler --cancel <id>
python -m video_agent.scheduler --flush
```

---

### 4.9 `orchestrator.py`

**Public API:**
```python
def generate_video_for_blog(
    blog_record: dict,
    *,
    publish_to: list[str] = ("youtube", "linkedin", "instagram"),
    scheduled: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> OrchestratorResult: ...
```

**Class-based implementation** (`Orchestrator`) for state encapsulation. Methods:

1. `_check_cache()` — skip if blog_id already produced valid video, unless `force`.
2. `_prepare_workspace()` — `mkdir -p output/videos/{date}_{slug}/`.
3. `_build_script()` — `script_builder.build_script()`. Save `script.json`.
4. `_synthesize_voiceover()` — `voiceover.synthesize()`. Save `voiceover.mp3`. Re-distribute scene durations to match audio.
5. `_generate_visuals()` — parallel `visual_engine.generate_all_visuals()`. Per-scene failure → `text_card` fallback.
6. `_generate_subtitles()` — `subtitles.generate_srt()`. Save `subtitles.srt`.
7. `_compose()` — `composer.compose_short()`. Save `video_short.mp4`. Run `_validate_output()`.
8. `_publish_or_schedule()` — per platform: dry_run → log & skip; scheduled → `scheduler.enqueue_for_region`; immediate → publisher direct call. Per-platform isolation (one failure doesn't affect others).
9. `_log_to_history()` — atomic append to `video_history.json`.
10. `_emit_cost_summary()` — append to `video_costs.log`.

---

### 4.10 `history.py`

```python
def load() -> dict
def save_atomic(history: dict) -> None       # tempfile + rename
def find_by_blog_id(blog_id: str) -> dict | None
def append_video(record: dict) -> None
def stats(days: int = 30) -> dict
```

Atomic save prevents corruption from concurrent runs. Backwards-compat default fields when loading old records.

---

### 4.11 `main.py` — CLI

```bash
python -m video_agent.main --from-blog-id <blog_id>
python -m video_agent.main --latest
python -m video_agent.main --backfill --limit 20 [--region australia]
python -m video_agent.main --latest --dry-run
python -m video_agent.main --latest --platforms youtube,linkedin
python -m video_agent.main --from-blog-id <id> --force
python -m video_agent.main --stats
python -m video_agent.main --usage                # cost/quota summary
```

**Argparse-driven.** Logging via root logger to `video_agent.log` AND stdout. UTF-8 stdout reconfiguration (mirror `blog_agent_v3.py` pattern for Windows cp1252).

---

### 4.12 `blog_agent_v3.py` integration (single change)

Near the end of the existing `main()`:
```python
if args.with_video:
    from video_agent.orchestrator import generate_video_for_blog
    generate_video_for_blog(
        blog_record,
        publish_to=("youtube", "linkedin"),  # IG omitted until app review
        scheduled=True,
    )
```
Add `--with-video` flag to its argparse. Default off — opt-in only.

---

## 5. Failure Handling & Cost Controls

| Failure | Behavior |
|---|---|
| Ollama unreachable | Abort with clear "start with: ollama serve" message |
| Edge-tts network error | Auto-fallback to Kokoro, set `fell_back=True` |
| Single-scene visual fails | Replace with `text_card` fallback, continue |
| Composer ffprobe validation fails | Raise `ComposerError`, do NOT publish |
| YouTube quota near limit | Raise `QuotaExceededError` with reset time |
| LinkedIn video processing timeout (5 min) | Post anyway with warning |
| Instagram container error | Retry once, then mark failed |
| Publisher max retries hit | Log to `video_post_failures.log`, mark failed |
| Blog category has no Tier-4 templates | Raise `ScriptBuilderError`, orchestrator skips |

**Per-run cost ledger** (`video_costs.log`, append-only):
```
2026-05-02T14:23:11Z blog_id=8123 ollama_calls=4 ollama_tokens=8523 pollinations=0 pexels=2 youtube_quota=1600 linkedin_calls=5 instagram_calls=0 elapsed_s=312.4 total_$=0.00
```

`python -m video_agent.main --usage` summarizes last 30 days.

---

## 6. Phased Rollout

| Sprint | Modules | Outcome |
|---|---|---|
| **1** (week 1) | config, text_normalizer, script_builder, history | Can produce `script.json` from any blog with all 5 fact-extraction tiers working |
| **2** (week 2) | voiceover, subtitles | Can produce voiceover.mp3 + subtitles.srt for any script |
| **3** (week 3) | visual_engine (text_card + infographic only) | Can produce all visuals using just infographics + text cards (no factory assets needed yet) |
| **4** (week 4) | composer, intro/outro tools, music check | Can produce playable video_short.mp4 (MVP — without B-roll) |
| **5** (week 5) | factory_broll, stock, asset_manifest, tag_assets tool | Real footage integrated — full visual quality unlocked |
| **6** (week 6) | publishers/youtube + scheduler | Can auto-publish to YouTube Shorts on regional schedule |
| **7** (week 7) | publishers/linkedin (extends linkedin_api.py) | Can auto-publish to LinkedIn Page |
| **8** (week 8+) | publishers/instagram + GitHub CDN tool | IG auto-publish ready (gated until Meta approval) |
| **Polish** | cost tracking, --usage, --stats, integration with blog_agent_v3.py | Production-ready |

The MVP is shippable end of Sprint 6 (YouTube only). LinkedIn comes one week later. IG flips on whenever Meta approves.

---

## 7. One-Time Setup Checklist (becomes `VIDEO_SETUP.md`)

1. **System dependencies:**
   - `winget install --id Gyan.FFmpeg` → verify `ffmpeg -version`.
   - Install Poppins + Playfair Display fonts (Google Fonts) via Windows Font Settings.

2. **Python dependencies** (append to `requirements.txt`):
   ```
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
   ```

3. **Account setup (manual):**
   - Create `@HRSUIndore` YouTube channel; link brand assets.
   - Add `youtube.upload` scope to existing OAuth flow; delete `token.pickle`; re-auth.
   - Create HRSU Instagram Business account; link to HRSU FB Page in Meta Business Suite.
   - Submit Meta app for `instagram_content_publish` + `instagram_basic` review.
   - Create `hrsu/video-cdn` GitHub repo (private OK).
   - Add to `secrets.txt`:
     ```
     PEXELS_API_KEY=...
     IG_USER_ID=...
     IG_PUBLISHING_ENABLED=false
     GITHUB_TOKEN=ghp_...
     GITHUB_CDN_REPO=hrsu/video-cdn
     ```

4. **Asset library bootstrap:**
   - Shoot ~30 min of factory footage (shot list at `python -m video_agent.tools.shoot_list`).
   - Save to `asset_library/factory/`.
   - Run `python -m video_agent.tools.tag_assets` (interactive) to build `manifest.json`.
   - Download 5 royalty-free music tracks from YouTube Audio Library (no-attribution filter); save to `asset_library/music/`.
   - Run `python -m video_agent.tools.render_brand_assets` to pre-render `intro_3s.mp4` + `outro_5s.mp4`.
   - Save `hrsu_logo_white.png` + `hrsu_logo_gold.png` to `asset_library/brand/`.

5. **First-run smoke tests:**
   - `python -m video_agent.main --latest --dry-run` — generate without publishing; inspect.
   - `python -m video_agent.main --backfill --limit 3` — publish 3 videos to enabled platforms.

---

## 8. Acceptance Criteria

The pipeline is **production-ready** when:

- [ ] `python -m video_agent.main --latest` produces a valid 1080×1920 MP4 in < 15 min.
- [ ] Generated video passes ffprobe validation (codec, resolution, duration, file size).
- [ ] Script extraction tier metadata is logged in `video_history.json`.
- [ ] At least 1 of last 5 generated videos uses Tier-1 numeric facts (high-quality blog input).
- [ ] All 4 publisher classes implement `BasePublisher` interface.
- [ ] YouTube upload returns a valid `youtu.be/...` URL within 5 min.
- [ ] LinkedIn upload returns a valid `linkedin.com/feed/...` URL within 8 min (incl. processing).
- [ ] Instagram publisher gracefully no-ops when gate flag is `false`.
- [ ] Scheduler successfully fires a queued post at the right regional local time.
- [ ] Per-run entry appended to `video_costs.log`.
- [ ] `python -m video_agent.main --backfill --limit 20` completes without manual intervention.
- [ ] All unit tests in `tests/video_agent/` pass.
- [ ] `python -m video_agent.main --stats` and `--usage` produce non-empty output.

---

## 9. Test Strategy

Tests live in `tests/video_agent/` mirroring module structure. Use `pytest`. Apply TDD per the superpowers `test-driven-development` skill — write the test before the module.

**Critical tests (must exist):**

```python
# tests/video_agent/test_text_normalizer.py
def test_normalize_chemical_formulas():
    assert normalize_for_tts("H₂S levels rose to 50 mg/L") == "H 2 S levels rose to 50 milligrams per liter"

def test_normalize_strips_citation_markers():
    assert normalize_for_tts("This is fact[1] from study[2].") == "This is fact from study."

# tests/video_agent/test_script_builder.py
def test_extraction_tier_fallback():
    r1 = build_script(BLOG_NUMERIC); assert r1["extraction_metadata"]["tier_used"] == 1
    r3 = build_script(BLOG_ESG_QUALITATIVE); assert r3["extraction_metadata"]["tier_used"] in (3, 4)
    r4 = build_script(BLOG_THIN_CONTENT); assert r4["extraction_metadata"]["fell_back_to_template"] is True

def test_script_meets_constraints():
    result = build_script(SAMPLE_BLOG_RECORD)
    assert 120 <= len(result["narration"].split()) <= 170
    assert sum(c.isdigit() for c in result["narration"]) >= 3
    assert any(s["visual_type"] == "hrsu_edge" for s in result["scenes"])
    assert result["scenes"][0]["visual_type"] == "text_card"
    assert "hrsuindore" in result["scenes"][-1].get("on_screen_text", "").lower()
    assert 8 <= len(result["scenes"]) <= 12

# tests/video_agent/test_voiceover.py
def test_voiceover_within_shorts_limits(tmp_path):
    result = synthesize(SAMPLE_NARRATION_150W, tmp_path / "v.mp3", "australia")
    assert 30 <= result["duration_s"] <= 65
    assert result["voice_used"] == "en-AU-WilliamNeural"
    assert os.path.getsize(result["audio_path"]) > 50_000

# tests/video_agent/test_subtitles.py
def test_srt_lines_short_for_mobile(tmp_path):
    srt_path = generate_srt(SAMPLE_VOICEOVER_MP3, tmp_path / "s.srt", narration_hint=SAMPLE_NARRATION)
    for cue in parse_srt(srt_path):
        assert len(cue.text.split()) <= 3
        assert (cue.end - cue.start).total_seconds() <= 1.6
        assert cue.text == cue.text.upper()

# tests/video_agent/test_visual_engine.py
def test_infographic_renders_at_resolution(tmp_path):
    scene = make_scene(visual_type="infographic", chart_type="bar",
                      data={"labels": ["A", "B"], "values": [10, 90]})
    out = generate_visual(scene, tmp_path / "i.png", (1080, 1920))
    assert Image.open(out["asset_path"]).size == (1080, 1920)
    assert not out["is_video_clip"]

def test_factory_broll_falls_back_when_library_empty(tmp_path):
    with empty_asset_library():
        scene = make_scene(visual_type="hrsu_edge", on_screen_text="HRSU EDGE")
        out = generate_visual(scene, tmp_path / "f.png", (1080, 1920))
        assert out["generator_used"] == "text_card"

# tests/video_agent/test_composer.py
def test_compose_produces_valid_short(tmp_path):
    out = compose_short(SAMPLE_SCENES, SAMPLE_VISUALS, SAMPLE_VOICEOVER,
                       SAMPLE_SRT, tmp_path / "v.mp4",
                       music_track=SAMPLE_MUSIC, region="australia")
    probe = ffmpeg.probe(str(out))
    assert 30 <= float(probe["format"]["duration"]) <= 60
    assert probe["streams"][0]["width"] == 1080
    assert probe["streams"][0]["height"] == 1920

# tests/video_agent/publishers/test_youtube.py
@responses.activate
def test_youtube_upload_succeeds():
    responses.add(...)
    pub = YouTubePublisher(token=fake_creds)
    result = pub.upload(SAMPLE_MP4, "title", "desc", ["#calciumnitrate"], BLOG_URL, "australia")
    assert result["success"] and result["post_url"].startswith("https://youtu.be/")

# tests/video_agent/publishers/test_linkedin.py
@responses.activate
def test_linkedin_video_upload_three_step():
    responses.add(POST, ".../initializeUpload", json={"value": {...}})
    responses.add(PUT, "{chunked_url}", status=200, headers={"etag": "abc"})
    responses.add(POST, ".../finalizeUpload", json={"value": {...}})
    responses.add(POST, ".../posts", json={"id": "urn:li:share:123"})
    result = LinkedInVideoPublisher().upload(SAMPLE_MP4, ...)
    assert result["success"]

# tests/video_agent/publishers/test_instagram.py
def test_instagram_publishing_disabled_by_default():
    pub = InstagramPublisher(token_manager=mock_tm(ig_enabled=False))
    result = pub.upload(SAMPLE_MP4, ...)
    assert not result["success"] and "pending Meta app review" in result["error"]

# tests/video_agent/test_scheduler.py
def test_scheduler_uses_regional_time():
    sched = Scheduler(now=datetime(2026, 5, 2, 12, 0, tzinfo=UTC))
    target = sched._compute_target("australia", platform="linkedin")
    assert target.weekday() == 1     # Tuesday
    assert target.hour == 9
    assert target.tzinfo.zone == "Australia/Sydney"
```

---

## 10. Realistic Outcomes (Honesty Section)

| Metric | Month 1 | Month 3 | Month 6 |
|---|---|---|---|
| Videos published | ~25 (incl. backfill) | ~80 | ~170 |
| YouTube subscribers | 10–50 | 100–300 | 500–1500 |
| LinkedIn impressions/post | 200–800 | 1k–5k | 5k–20k |
| Inbound procurement inquiries | 0–1 | 1–4/month | 5–15/month |
| Operator time | ~5 hrs setup + monitoring | ~2 hrs/week | ~1 hr/week |

Compounding kicks in around month 3 once YouTube has watch-time data and LinkedIn indexes HRSU as a "consistently posts about industrial chemicals" account.

---

## 11. Out of Scope (Do Not Build)

- True text-to-video generation (HunyuanVideo, CogVideoX) — needs 16GB+ VRAM, won't run on GTX 1550Ti.
- AI talking-head avatars (HeyGen, D-ID, SadTalker) — destroys B2B trust.
- Pollinations.ai image generation by default — clashes with technical-illustration style choice.
- Long-form YouTube videos (5–10 min) — explicitly deferred per MVP scope (option C).
- LinkedIn personal-profile posting — only Page posting in scope.
- TikTok publishing — wrong audience.
- Multi-language voiceovers per video — single-language per region only.
- Hybrid Ollama + Anthropic API for script generation — explicitly chosen Ollama-only.
- Real-time analytics integration — separate future project.
- Video A/B testing of titles/thumbnails — separate future project.

---

## 12. References

- [edge-tts on PyPI](https://pypi.org/project/edge-tts/)
- [Pollinations.ai API docs](https://enter.pollinations.ai/api/docs) (deferred to optional)
- [Kokoro-82M HuggingFace](https://huggingface.co/hexgrad/Kokoro-82M)
- [faster-whisper GitHub](https://github.com/SYSTRAN/faster-whisper)
- [YouTube Data API v3 — Quotas](https://developers.google.com/youtube/v3/determine_quota_cost)
- [YouTube Data API v3 — videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [LinkedIn Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Meta Instagram Reels publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Pexels API](https://www.pexels.com/api/)
- [APScheduler docs](https://apscheduler.readthedocs.io/)
- [FFmpeg sidechaincompress](https://ffmpeg.org/ffmpeg-filters.html#sidechaincompress)

---

**End of design spec.** Implementation plan to follow via the `superpowers:writing-plans` skill.
