"""
video_agent configuration.
Imports shared brand/region values from the root config.py.
All video-specific knobs live here.
"""
import os
from pathlib import Path as _Path

# ── Load .env from project root (no-op if file absent) ────────────────────
_PROJECT_ROOT = _Path(__file__).parent.parent
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass
_CACHE = _PROJECT_ROOT / ".cache"
_TMP   = _CACHE / "tmp"
for _d in (_CACHE, _TMP):
    _d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("HF_HOME",        str(_CACHE / "huggingface"))   # faster-whisper, kokoro
os.environ.setdefault("TORCH_HOME",     str(_CACHE / "torch"))         # torch hub models
os.environ.setdefault("MPLCONFIGDIR",   str(_CACHE / "matplotlib"))    # matplotlib font/style cache
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE))                   # generic XDG fallback
os.environ["TEMP"]   = str(_TMP)   # moviepy / ffmpeg temp files
os.environ["TMP"]    = str(_TMP)
os.environ["TMPDIR"] = str(_TMP)

from config import (
    BLOG_STYLE_TEMPLATE, REGION_POSTING_SCHEDULE, MAIN_WEBSITE,
    COMPANY_NAME, CALCIUM_NITRATE_APPLICATIONS,
)

# ─── Format ────────────────────────────────────────────────────────────────
SHORT_FORMAT = {
    "resolution": (1080, 1920),
    "fps": 30,
    "min_duration_s": 30,
    "max_duration_s": 65,
    "max_filesize_mb": 100,
    "bitrate": "10M",
}

# ─── TTS ───────────────────────────────────────────────────────────────────
TTS_VOICES = {
    "australia": "en-AU-WilliamNeural",
    "usa":       "en-US-GuyNeural",
    "eu":        "en-GB-RyanNeural",
    # Narration is generated in English for every region, so the voice must
    # match the narration language, not the region (a de-DE voice reading
    # English speaks numbers/units in German -> mixed-language audio).
    "germany":   "en-GB-RyanNeural",
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
# Full-colour logo from assets/brand/ — used by the final CTA scene and the
# silent 2s logo stinger. Set to absolute or repo-relative path.
BRAND_LOGO_PATH = "asset_library/brand/Logo.png"
INTRO_VIDEO_PATH = "asset_library/brand/intro_3s.mp4"
OUTRO_VERSION = 2
# DEPRECATED (v2.2): replaced by BRAND_LOGO_PATH + brand_outro_card.py.
# Kept for one release cycle so external tooling doesn't break.
OUTRO_VIDEO_PATH = f"asset_library/brand/outro_5s_v{OUTRO_VERSION}.mp4"

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
# All infographic chart types are rendered for a fixed viewport — axes and labels
# break under any pan, so every chart type is static.
STATIC_INFOGRAPHIC_TYPES: set[str] = {"callout_stat", "flow", "bar", "comparison", "line"}
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
OLLAMA_MODEL = "gemma3:4b"          # cheap local model (kept for low-value calls)
OLLAMA_HOST = "http://localhost:11434"

# ─── Model routing ─────────────────────────────────────────────────────────
# The capable model for high-value reasoning (script writing, semantic
# judgment). Cloud multimodal model. Text-only calls to it are routed by
# OllamaClient; vision calls go through video_agent/vision/ollama_vision.py.
SMART_TEXT_MODEL = "gemma4:31b-cloud"
# Master switch: when True, agents listed in Workstream C use SMART_TEXT_MODEL;
# when False they fall back to OLLAMA_MODEL. Lets you A/B without code edits.
USE_SMART_TEXT_MODEL = True
# How the OllamaClient should reach a *-cloud text model. Set by Task C-1
# after empirical verification. One of: "api" (POST /api/generate) or "cli"
# (ollama run). Until C-1 runs, "api" is assumed.
SMART_TEXT_TRANSPORT = "sdk"   # gemma4:31b-cloud: use ollama Python SDK (avoids Windows encoding issues with CLI)
SCRIPT_BANNED_PHRASES = [
    "as an ai", "in this video", "thanks for watching",
    "hope you enjoyed", "i don't have", "as of my last update",
    "in conclusion", "let's dive in", "unlock", "game-changer", "in today's",
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

# ─── Search context ────────────────────────────────────────────────────────
# Appended to every visual-search query as a positive relevance bias.
# Pushes results toward B2B industrial imagery without hardcoded blacklists —
# search engines rank by all terms, so "Saudi Arabia oil refinery industrial"
# naturally outranks war/conflict footage for "Saudi Arabia oil refinery".
# Keep this short; long qualifiers narrow results too aggressively.
BRAND_SEARCH_CONTEXT = "industrial"

# ─── Stock fallback ────────────────────────────────────────────────────────
PEXELS_API_BASE = "https://api.pexels.com/v1"
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
STOCK_CACHE_DIR = "asset_library/stock_cache"

# ─── Source quality gates ──────────────────────────────────────────────────
MIN_IMAGE_LONG_EDGE = 1280     # hard floor — reject below this in scoring
IDEAL_IMAGE_LONG_EDGE = 1920   # bonus threshold

# ─── Vision-judge gates (Workstream B) ─────────────────────────────────────
# Minimum vision score (0-10) for a web image to be usable. Below this, no
# image is better than a wrong image (we fall back to a designed card).
VISION_SELECT_MIN = 6
# How many downloaded candidates per scene to send to the vision judge.
# "Lean hard" budget: judge a generous shortlist.
VISION_JUDGE_SHORTLIST = 12
# Parallel vision subprocess calls. Cloud handles concurrency; keep modest to
# avoid local process thrash.
VISION_JUDGE_WORKERS = 6
# Safety flag: when False, fall back to caption-rerank (pre-B3 behavior).
# Lets you disable vision-first selection without a code change.
VISION_FIRST_ENABLED = True

# ─── Footage preference (Workstream B-5) ───────────────────────────────────
# Minimum vision score for OWN footage to be preferred over any web image.
# Lower than VISION_SELECT_MIN: we accept slightly weaker matches from our own
# footage because real HRSU footage carries more B2B trust than stock.
FOOTAGE_PREFER_MIN = 5

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

# ─── Source API keys (read from env; None disables that source) ──────────
import os as _os
UNSPLASH_ACCESS_KEY = _os.environ.get("UNSPLASH_ACCESS_KEY")
BING_API_KEY        = _os.environ.get("BING_API_KEY")
PIXABAY_API_KEY     = _os.environ.get("PIXABAY_API_KEY")

# ─── Harness: artifact verification ────────────────────────────────────────
# Reuse SHORT_FORMAT for duration/size bounds; these are the gate-specific knobs.
VERIFY_AUDIO_RMS_FLOOR = 250.0      # pydub RMS below this == effectively silent
VERIFY_AUDIO_PEAK_CEIL = 32500      # 16-bit peak above this == clipping risk (max 32768)
VERIFY_FRAME_SAMPLES = 5            # frames sampled across the video for visual checks
VERIFY_DARK_RIBBON_STRIP_PX = 120   # bottom strip height inspected for a dark band
VERIFY_DARK_RIBBON_LUMA_MAX = 24    # mean luma below this over the strip == dark ribbon
VERIFY_SAFEZONE_MARGIN_FRAC = 0.06  # caption text must sit inside this margin (6%)

# ─── Harness: YouTube publishing ───────────────────────────────────────────
YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"   # reuse Blogger app secrets
YOUTUBE_TOKEN_PATH = "youtube_token.json"        # SEPARATE from blogger token
YOUTUBE_CATEGORY_ID = "28"                        # Science & Technology
YOUTUBE_DEFAULT_PRIVACY = "unlisted"              # Phase 1: never public
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 4900                            # API hard limit is 5000

# ─── Harness: vision verification (Phase 3) ────────────────────────────────
VISION_MODEL = "gemma4:31b-cloud"   # multimodal via Ollama images field
VISION_TIMEOUT_S = 300              # cloud round-trip per scene can be slow
VISION_PASS_MIN = 7.0               # every scene's overall >= this -> pass (float for grading 0–10)
VISION_FAIL_BELOW = 5.0             # any scene overall < this -> actionable defect (float for grading 0–10)
VISION_MAX_REVISE_CYCLES = 2        # bounded revise loop (spec: <=2)
REVIEW_QUEUE_PATH = _Path("review_queue.json")   # operator hold-for-review queue
