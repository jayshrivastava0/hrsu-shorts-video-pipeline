# Shorts Engine — Plan 3 of 3: Acquisition + Verify + Package/Publish (spec Phases 5–7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the shipping Plan-2 pipeline (INGEST→…→ASSEMBLE, torture-verified 2026-07-08) with real-asset acquisition (BROLL ladder + PAPER_CARD "receipts" shot — the user's top request), a VERIFY stage (heuristic + vision judge + deterministic revise loop — the Generator-Evaluator layer), and PACKAGE/PUBLISH (YouTube unlisted + `linkedin_caption.txt`), so `python -m shorts_engine <url> --publish` takes a blog post to an uploaded short.

**Architecture:** Acquisition is a gated ladder (own library → blog images → free APIs → scrape) where every candidate passes deterministic gates (blacklist/resolution/watermark/dedupe) before a two-call describe-then-match vision judge; no acceptance anywhere ⇒ the shot's declared fallback card renders (never-blank holds by construction). PAPER_CARD fetches the cited paper's page 1 (pypdfium2 for OA PDFs, Playwright header screenshot otherwise, cached by URL hash). VERIFY samples one frame per shot from the FINAL video and judges it against that shot's narration span; failures route to deterministic fixes (fallback swap / text shrink / caption margin bump / audio re-run), max 2 cycles, always terminating publishable. PACKAGE/PUBLISH reuses `youtube_packager`/`youtube_publisher` with one light extension (lazy `Storyboard` import).

**Tech Stack:** Python 3.11+, Pillow, numpy, requests, pypdfium2 (installed), Playwright (installed), ffmpeg/ffprobe, `gemma4:31b-cloud` via ollama for text + vision, pytest.

## Global Constraints

- **No git.** Task steps end with test runs, never commits.
- **Workspace:** engine code in `E:\Projects\HRSU Blog\_shorts_engine_impl` (`shorts_engine/` + `tests/shorts_engine/`); run commands from there; `python`, not `python3`. Real project root `E:\Projects\HRSU Blog` holds `video_agent/`, `asset_library/`, `brand_facts.yaml`.
- **Test baseline:** 418 passed (`python -m pytest tests/shorts_engine -q`). Every task ends green. Tasks touching `video_agent/` (Tasks 2, 12) must also run the ROOT suite from `E:\Projects\HRSU Blog` (`python -m pytest tests -q`) with no regressions — record both counts.
- **Calibration (do NOT revert):** `WORDS_PER_SECOND = 1.7` (measured on en-GB-RyanNeural, technical vocabulary), `AUDIO_DURATION_TOLERANCE = 0.65`, `LLM_MAX_RETRIES = 5`. All gate messages must show integer-feasible (ceil/floor) bounds — never `{:.0f}`.
- **Console output:** ASCII only in `print()`/log messages (`[OK]`/`[FAIL]`, `->`). Windows cp1252 killed a successful run once. Any file read that may contain non-ASCII uses `encoding="utf-8"`.
- **Vision model:** `video_agent.config.VISION_MODEL` (`gemma4:31b-cloud`), `VISION_TIMEOUT_S` (300). Vision calls retry 3x with exponential backoff (2s/4s/8s); an ungradeable judgment after retries is a REJECT (sourcing) or run-`failed` (verify) — never a silent pass (F3/F8).
- **Never-blank / never-unverified:** unchanged from Plan 2. BROLL/PAPER_CARD failures resolve to declared fallbacks; there is no degraded state.
- **`--torture` semantics (now enforced):** `flags["torture"]` ⇒ ladder and paper_page are DISABLED — every BROLL/PAPER_CARD renders its declared fallback (zero fetched assets, the all-designed path). Plan-2's flag recording anticipated exactly this.
- **Forbidden imports (spec §3.2):** `shorts_engine` must never import (directly or transitively at module level) `video_agent/agents/*`, `orchestrator.py`, `storyboard.py`, `script_builder.py`, `composer.py`, `harness/{runner,rubric,revise_router,verify_vision}.py`, `run_stage.py`, `visual_engine/{footage_library,factory_broll,dispatcher}.py`, `motion/*`, `sources/scoring.py`. Task 13 adds the transitive-import CI guard.
- **Judge thresholds:** own library ≥5, blog images ≥6, free APIs ≥6, scrape ≥7; ≤8 candidates judged per tier; first acceptance wins; a hard gate reject is final (F2).
- All new modules start `from __future__ import annotations`, use `logging.getLogger(__name__)`; tests use class-based `TestXxx`.

## Plan-2 debts folded in

1. **Task 14 golden integration test** was marked complete in Plan 2 but never created — built here as Task 14 (with vision/sourcing mocks added).
2. Music bed still absent (`asset_library/music/eu.mp3`) — mix path is implemented+tested; dropping a file in enables it. Not a code task; noted in Task 15's report.
3. Script "interesting-ness" headroom (user watch-through feedback): Task 11's vision gate includes a hook-strength note in the contact sheet, and Task 15's report carries a critique-prompt tuning item. No gate change in this plan.

---

### Task 1: Sourcing constants + `sourcing/gates.py` (blacklist, resolution, watermark, dedupe)

**Files:**
- Modify: `shorts_engine/config.py` (append sourcing/verify constants)
- Create: `shorts_engine/sourcing/__init__.py`
- Create: `shorts_engine/sourcing/gates.py`
- Test: `tests/shorts_engine/test_sourcing_gates.py`

**Interfaces:**
- Consumes: `config.PROJECT_ROOT`, `video_agent/sources/watermark.py::is_watermarked(img_path: Path, cache_root: Path) -> tuple[bool, str]`.
- Produces (used by Tasks 4–6, 10):
  - config: `DOMAIN_BLACKLIST: list[str]`, `MIN_LONG_EDGE_PX = 1280`, `PER_TIER_CANDIDATES = 8`, `JUDGE_MIN_OWN = 5`, `JUDGE_MIN_BLOG = 6`, `JUDGE_MIN_API = 6`, `JUDGE_MIN_SCRAPE = 7`, `SOURCING_CACHE_DIR (Path)`, `PAPER_CACHE_DIR (Path)`, `VISION_DESCRIBE_MIN_CHARS = 120`, `VISION_REFUSAL_PHRASES: list[str]`, `WATERMARK_TERMS: list[str]`, `VERIFY_MAX_REVISE_CYCLES = 2`, `LEGIBILITY_SHRINK_FACTOR = 0.7`.
  - `gates.blacklisted(url: str) -> bool` (matches host or any parent domain against DOMAIN_BLACKLIST)
  - `gates.resolution_ok(width: int, height: int) -> bool` (long edge ≥ MIN_LONG_EDGE_PX; 0-values ⇒ False)
  - `gates.watermarked(img_path: Path) -> bool` (wraps `is_watermarked` with `SOURCING_CACHE_DIR`; OCR unavailable ⇒ False i.e. pass-through, matching existing behavior)
  - `gates.seen_before(url: str, seen: set[str]) -> bool` (sha256[:16] of normalized URL; mutates `seen` on first sight)
  - `gates.run_pre_gates(cand, seen: set[str]) -> str | None` — returns rejection reason string or None (order: blacklist → dedupe → resolution). `cand` is any object with `.url/.width/.height` (duck-typed to `RawCandidate`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_sourcing_gates.py
"""Deterministic pre-judge gates: blacklist, resolution, watermark, dedupe."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace


def _cand(url="https://example.com/a.jpg", w=1600, h=900):
    return SimpleNamespace(url=url, width=w, height=h)


class TestSourcingConstants:
    def test_constants_exist(self):
        from shorts_engine import config
        assert config.MIN_LONG_EDGE_PX == 1280
        assert config.PER_TIER_CANDIDATES == 8
        assert (config.JUDGE_MIN_OWN, config.JUDGE_MIN_BLOG,
                config.JUDGE_MIN_API, config.JUDGE_MIN_SCRAPE) == (5, 6, 6, 7)
        assert config.VERIFY_MAX_REVISE_CYCLES == 2
        for d in ("shutterstock.com", "gettyimages.com", "istockphoto.com",
                  "alamy.com", "dreamstime.com", "123rf.com",
                  "depositphotos.com", "ftcdn.net"):
            assert d in config.DOMAIN_BLACKLIST, d

    def test_refusal_and_watermark_terms(self):
        from shorts_engine import config
        assert "cannot see" in config.VISION_REFUSAL_PHRASES
        assert "unable to" in config.VISION_REFUSAL_PHRASES
        assert "shutterstock" in config.WATERMARK_TERMS


class TestBlacklist:
    def test_blacklisted_domain_and_subdomain(self):
        from shorts_engine.sourcing import gates
        assert gates.blacklisted("https://www.shutterstock.com/image/x.jpg")
        assert gates.blacklisted("https://cdn.shutterstock.com/x.jpg")
        assert not gates.blacklisted("https://commons.wikimedia.org/x.jpg")

    def test_lookalike_domain_not_blacklisted(self):
        from shorts_engine.sourcing import gates
        # substring match would wrongly flag this; suffix-label match must not
        assert not gates.blacklisted("https://notshutterstock.com/x.jpg")


class TestResolutionAndDedupe:
    def test_resolution_long_edge(self):
        from shorts_engine.sourcing import gates
        assert gates.resolution_ok(1280, 720)
        assert gates.resolution_ok(720, 1280)
        assert not gates.resolution_ok(1279, 720)
        assert not gates.resolution_ok(0, 0)

    def test_seen_before_mutates_and_detects(self):
        from shorts_engine.sourcing import gates
        seen: set[str] = set()
        assert not gates.seen_before("https://a.com/x.jpg", seen)
        assert gates.seen_before("https://a.com/x.jpg", seen)
        assert not gates.seen_before("https://a.com/y.jpg", seen)


class TestRunPreGates:
    def test_order_and_reasons(self):
        from shorts_engine.sourcing import gates
        seen: set[str] = set()
        assert gates.run_pre_gates(
            _cand("https://alamy.com/x.jpg"), seen) == "blacklisted"
        c = _cand()
        assert gates.run_pre_gates(c, seen) is None
        assert gates.run_pre_gates(c, seen) == "duplicate"
        assert gates.run_pre_gates(
            _cand("https://b.com/lo.jpg", 640, 480), seen) == "low_resolution"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_sourcing_gates.py -v`
Expected: ERROR (no `shorts_engine.sourcing`, missing constants).

- [ ] **Step 3: Implement**

Append to `shorts_engine/config.py` (before `init_directories` if present, else at end):

```python
# ── Sourcing: acquisition ladder (spec §6) ──────────────────────────────────
DOMAIN_BLACKLIST = [
    "ftcdn.net", "shutterstock.com", "alamy.com", "istockphoto.com",
    "gettyimages.com", "dreamstime.com", "123rf.com", "depositphotos.com",
    "stock.adobe.com", "adobestock.com", "fotolia.com", "bigstockphoto.com",
    "canstockphoto.com", "vectorstock.com",
]
MIN_LONG_EDGE_PX = 1280
PER_TIER_CANDIDATES = 8       # max candidates judged per ladder tier
JUDGE_MIN_OWN = 5             # own asset_library footage (trust bonus)
JUDGE_MIN_BLOG = 6            # blog's own images
JUDGE_MIN_API = 6             # free license-aware APIs
JUDGE_MIN_SCRAPE = 7          # scrape tier: must be CLEARLY right
SOURCING_CACHE_DIR = OUTPUT_BASE / "_sourcing_cache"
PAPER_CACHE_DIR = OUTPUT_BASE / "_paper_cache"

# ── Vision judge attach-verification (spec §6.2, fixes F3) ─────────────────
VISION_DESCRIBE_MIN_CHARS = 120
VISION_REFUSAL_PHRASES = [
    "cannot see", "no image", "as an ai", "unable to", "i'm sorry",
    "can't view", "cannot view", "not able to see",
]
WATERMARK_TERMS = [
    "shutterstock", "getty", "alamy", "istock", "dreamstime", "123rf",
    "depositphotos", "adobe stock", "watermark",
]

# ── Verify stage (spec §4 Stage 8) ──────────────────────────────────────────
VERIFY_MAX_REVISE_CYCLES = 2
LEGIBILITY_SHRINK_FACTOR = 0.7   # deterministic text-shorten on legibility fail
```

Create `shorts_engine/sourcing/__init__.py`:

```python
"""Acquisition: gated ladder + paper front-page fetch (spec §6, Phase 5)."""
```

Create `shorts_engine/sourcing/gates.py`:

```python
"""Deterministic pre-judge gates. A hard reject here is FINAL (F2) — no
downstream signal (judge score, tier, authority) can override it."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

from shorts_engine import config

logger = logging.getLogger(__name__)


def blacklisted(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    for dom in config.DOMAIN_BLACKLIST:
        if host == dom or host.endswith("." + dom):
            return True
    return False


def resolution_ok(width: int, height: int) -> bool:
    return max(width or 0, height or 0) >= config.MIN_LONG_EDGE_PX


def _url_key(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def seen_before(url: str, seen: set[str]) -> bool:
    key = _url_key(url)
    if key in seen:
        return True
    seen.add(key)
    return False


def watermarked(img_path: Path) -> bool:
    from video_agent.sources.watermark import is_watermarked
    config.SOURCING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    flagged, reason = is_watermarked(Path(img_path), config.SOURCING_CACHE_DIR)
    if flagged:
        logger.info("watermark gate rejected %s: %s", img_path, reason)
    return flagged


def run_pre_gates(cand, seen: set[str]) -> str | None:
    """Pre-download gates in order. Returns rejection reason or None."""
    if blacklisted(cand.url):
        return "blacklisted"
    if seen_before(cand.url, seen):
        return "duplicate"
    if not resolution_ok(cand.width, cand.height):
        return "low_resolution"
    return None
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_sourcing_gates.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` — all green (418 baseline + new).

---

### Task 2: Vision transport — parser fix + SDK-first (`video_agent/vision/ollama_vision.py`, lightly extended)

**Files:**
- Modify: `E:\Projects\HRSU Blog\video_agent\vision\ollama_vision.py`
- Test: `tests/shorts_engine/test_vision_transport.py` (workspace suite; imports video_agent directly)

**Interfaces:**
- Consumes: existing `call_vision_json(prompt, image_path, model, timeout_s) -> dict | list | None` (CLI path, kept).
- Produces (used by Task 3):
  - `call_vision_json_sdk(prompt: str, image_path: Path, model: str, timeout_s: float) -> dict | list | None` — ollama Python SDK `chat(messages=[{role,content,images:[path]}])`, JSON parsed from the reply (reusing fence-strip + outermost-JSON isolation); never raises, returns None on any failure.
  - `call_vision_auto(prompt, image_path, model, timeout_s) -> dict | list | None` — SDK first, CLI fallback; remembers which worked in module global `_TRANSPORT` ("sdk"/"cli") so later calls skip the failed transport (spec §6.3a).
  - Fixed `_strip_cli_noise(raw: str) -> str` used by `_parse_json_from_cli`: conservative ANSI-only regex (`\x1b\[[0-9;?]*[A-Za-z]` + `\x1b\][^\x07]*\x07` OSC), **no terminal-wrap heuristic** — the old `_TERM_WRAP_RE` duplicated characters ("brabranded").

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_vision_transport.py
"""Spec §6.3: SDK-first vision transport + the ANSI parser fix (no char
duplication). These test video_agent/vision/ollama_vision.py directly — the
root suite must also stay green (run it in Step 5)."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch


class TestParserFix:
    def test_no_character_duplication_on_wrapped_cli_output(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        # Captured-style CLI noise: cursor-move ANSI in the middle of a word.
        raw = 'noise \x1b[25l\x1b[2K{"description": "bra\x1b[1Gnded factory floor", "visible_text": ""}\x1b[25h'
        out = _parse_json_from_cli(raw)
        assert out is not None
        assert out["description"] == "branded factory floor"
        assert "brabra" not in out["description"]

    def test_plain_json_still_parses(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        assert _parse_json_from_cli('{"a": 1}') == {"a": 1}

    def test_json_after_thinking_marker(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        raw = 'blah blah ...done thinking. {"score": 7}'
        assert _parse_json_from_cli(raw) == {"score": 7}


class TestSdkTransport:
    def test_sdk_call_parses_reply(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")

        class FakeMsg:
            content = '```json\n{"description": "a factory"}\n```'
        class FakeResp:
            message = FakeMsg()

        with patch.object(ov, "_sdk_chat", return_value=FakeResp()):
            out = ov.call_vision_json_sdk("describe", img, "m", 30)
        assert out == {"description": "a factory"}

    def test_sdk_failure_returns_none(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        with patch.object(ov, "_sdk_chat", side_effect=RuntimeError("boom")):
            assert ov.call_vision_json_sdk("d", img, "m", 30) is None


class TestAutoTransport:
    def test_auto_prefers_sdk_then_remembers(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        ov._TRANSPORT = None  # reset
        calls = []
        with patch.object(ov, "call_vision_json_sdk",
                          side_effect=lambda *a, **k: (calls.append("sdk"), {"ok": 1})[1]), \
             patch.object(ov, "call_vision_json",
                          side_effect=lambda *a, **k: (calls.append("cli"), {"ok": 2})[1]):
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 1}
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 1}
        assert calls == ["sdk", "sdk"] and ov._TRANSPORT == "sdk"
        ov._TRANSPORT = None

    def test_auto_falls_back_to_cli_and_remembers(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        ov._TRANSPORT = None
        with patch.object(ov, "call_vision_json_sdk", return_value=None), \
             patch.object(ov, "call_vision_json", return_value={"ok": 2}):
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 2}
        assert ov._TRANSPORT == "cli"
        ov._TRANSPORT = None
```

- [ ] **Step 2: Run tests to verify they fail** → missing `call_vision_json_sdk`/`call_vision_auto`; duplication test fails on current parser.

- [ ] **Step 3: Implement in `video_agent/vision/ollama_vision.py`**

Replace the ANSI/terminal-wrap stripping and add the SDK/auto paths:

```python
# — replace the module's _ANSI_RE/_TERM_WRAP_RE usage with: —
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")


def _strip_cli_noise(raw: str) -> str:
    """Conservative ANSI-only strip. The old terminal-wrap heuristic
    re-assembled wrapped lines and DUPLICATED characters ('brabranded');
    plain escape-sequence removal never does."""
    return _ANSI_OSC_RE.sub("", _ANSI_CSI_RE.sub("", raw))
```

In `_parse_json_from_cli`, replace the first two lines (`clean = _TERM_WRAP_RE.sub(...)`, `clean = _ANSI_RE.sub(...)`) with `clean = _strip_cli_noise(raw)`. Keep the `...done thinking.` marker logic and last-balanced-object scan unchanged.

Append:

```python
def _sdk_chat(model: str, messages: list) -> "object":
    """Seam for tests. Import inside so the SDK stays an optional dependency."""
    from ollama import chat
    return chat(model=model, messages=messages)


def call_vision_json_sdk(prompt: str, image_path: Path, model: str,
                         timeout_s: float) -> dict | list | None:
    """SDK-transport vision call. Never raises; None on any failure."""
    try:
        resp = _sdk_chat(model, [{
            "role": "user", "content": prompt, "images": [str(image_path)],
        }])
        raw = resp.message.content or ""
    except Exception as e:  # noqa: BLE001 — any failure == no judgment
        log.warning("vision SDK call failed: %s", e)
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                     flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return _parse_json_from_cli(cleaned)


_TRANSPORT: str | None = None  # remembered per process after first success


def call_vision_auto(prompt: str, image_path: Path, model: str,
                     timeout_s: float) -> dict | list | None:
    """Empirical transport selection (spec §6.3a): SDK first, CLI fallback;
    the first transport that succeeds is remembered for the process."""
    global _TRANSPORT
    order = {"sdk": ["sdk"], "cli": ["cli"]}.get(_TRANSPORT, ["sdk", "cli"])
    for transport in order:
        fn = call_vision_json_sdk if transport == "sdk" else call_vision_json
        out = fn(prompt, image_path, model, timeout_s)
        if out is not None:
            _TRANSPORT = transport
            return out
    return None
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_vision_transport.py -v`

- [ ] **Step 5: Run BOTH suites**

`python -m pytest tests/shorts_engine -q` (workspace) AND, from `E:\Projects HRSU Blog` root: `python -m pytest tests -q` — no regressions vs. pre-task counts (the old parser has root-suite consumers; if a root test pinned the wrap-heuristic behavior, update that test's expectation to the non-duplicating output and note it in the task report).

---

### Task 3: `llm/vision_judge.py` — describe-then-match with attach-verification

**Files:**
- Create: `shorts_engine/llm/vision_judge.py`
- Test: `tests/shorts_engine/test_vision_judge.py`

**Interfaces:**
- Consumes: `video_agent.vision.ollama_vision.call_vision_auto` (Task 2); `shorts_engine.llm.text_llm.generate_schema_json`; config from Task 1 (`VISION_DESCRIBE_MIN_CHARS`, `VISION_REFUSAL_PHRASES`, `WATERMARK_TERMS`); `video_agent.config.VISION_MODEL/VISION_TIMEOUT_S`.
- Produces (used by Tasks 5, 6, 10, 11):
  - `describe(image_path: Path) -> dict | None` — DESCRIBE call → `{"description": str, "visible_text": str, "quality_notes": str}`; retries 3× (2s/4s/8s backoff); returns None if still unparseable.
  - `verify_description(desc: dict | None, prompt: str) -> str | None` — attach-verification: rejection reason or None. Checks: desc is a dict with `description`; ≥ `VISION_DESCRIBE_MIN_CHARS` chars; contains no `VISION_REFUSAL_PHRASES` (case-insensitive); does not contain a ≥40-char run of the prompt text; `visible_text` contains no `WATERMARK_TERMS`.
  - `match(description: str, wish: str, narration_span: str) -> dict` — TEXT-ONLY call via `generate_schema_json` with `MATCH_SCHEMA = {"score": int 0-10, "reason": str, "focal_hint": enum[center,left,right,top,bottom]}`; raises `EngineLLMError` only after text_llm's own retries.
  - `judge(image_path: Path, wish: str, narration_span: str) -> dict` — full protocol → `{"accepted_score": int, "description": str, "focal_hint": str, "reject_reason": str | None}`; any describe/verify failure ⇒ `accepted_score=0`, `reject_reason` set (**failure can never pass**, F3).
  - Module seams: `_describe_call = None` (resolves to `call_vision_auto`), `_match_call = None` (resolves to `text_llm.generate_schema_json`) — monkeypatch targets, same pattern as `audio.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_vision_judge.py
from __future__ import annotations
from pathlib import Path
import pytest

GOOD_DESC = {
    "description": ("A wide industrial photograph showing rows of circular "
                    "clarifier tanks at a municipal wastewater treatment "
                    "plant, with walkways, railings and aeration equipment "
                    "visible under an overcast sky."),
    "visible_text": "",
    "quality_notes": "sharp, well lit",
}


class TestVerifyDescription:
    def test_good_description_passes(self):
        from shorts_engine.llm import vision_judge as vj
        assert vj.verify_description(GOOD_DESC, "Describe exactly") is None

    def test_none_and_short_and_refusal_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        assert vj.verify_description(None, "p") == "describe_failed"
        short = dict(GOOD_DESC, description="a tank")
        assert vj.verify_description(short, "p") == "description_too_short"
        refusal = dict(GOOD_DESC, description="I am unable to see the image " + "x" * 120)
        assert vj.verify_description(refusal, "p") == "refusal_phrase"

    def test_prompt_echo_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        prompt = "Describe exactly what this image shows: subjects, setting, any visible text"
        echo = dict(GOOD_DESC, description=prompt + " " + "y" * 80)
        assert vj.verify_description(echo, prompt) == "prompt_echo"

    def test_watermark_term_in_visible_text_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        wm = dict(GOOD_DESC, visible_text="shutterstock 12345")
        assert vj.verify_description(wm, "p") == "watermark_text"


class TestJudge:
    def test_describe_failure_can_never_pass(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        monkeypatch.setattr(vj, "_describe_call", lambda *a, **k: None)
        out = vj.judge(img, "clarifier tanks", "narration")
        assert out["accepted_score"] == 0
        assert out["reject_reason"] == "describe_failed"

    def test_full_protocol_happy_path(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        seen = {}
        monkeypatch.setattr(vj, "_describe_call", lambda *a, **k: GOOD_DESC)
        def fake_match(prompt, system, schema, **kw):
            seen["prompt"] = prompt
            return {"score": 8, "reason": "matches", "focal_hint": "center"}
        monkeypatch.setattr(vj, "_match_call", fake_match)
        out = vj.judge(img, "clarifier tanks at plant", "dosing narration")
        assert out["accepted_score"] == 8
        assert out["focal_hint"] == "center"
        assert out["reject_reason"] is None
        # the MATCH call sees the DESCRIPTION, never the raw image
        assert "clarifier tanks" in seen["prompt"]
        assert GOOD_DESC["description"][:40] in seen["prompt"]

    def test_describe_retries_then_succeeds(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        attempts = []
        def flaky(*a, **k):
            attempts.append(1)
            return None if len(attempts) < 3 else GOOD_DESC
        monkeypatch.setattr(vj, "_describe_call", flaky)
        monkeypatch.setattr(vj, "_sleep", lambda s: None)  # no real backoff in tests
        monkeypatch.setattr(vj, "_match_call",
                            lambda *a, **k: {"score": 6, "reason": "r", "focal_hint": "top"})
        out = vj.judge(img, "w", "n")
        assert len(attempts) == 3 and out["accepted_score"] == 6
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/llm/vision_judge.py`**

```python
"""Describe-then-match vision judge (spec §6.2). The model NEVER sees the
desired subject while looking at pixels: call 1 describes the image blind;
call 2 (text-only) scores that description against the wish + narration.
Attach-verification makes a failed/refused describe a hard reject — a
failure can never pass (F3)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from shorts_engine import config
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)

DESCRIBE_PROMPT = (
    "Describe exactly what this image shows: subjects, setting, any visible "
    "text or watermarks, image quality. Respond with raw JSON only: "
    '{"description": str, "visible_text": str, "quality_notes": str}'
)

MATCH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reason": {"type": "string"},
        "focal_hint": {"enum": ["center", "left", "right", "top", "bottom"]},
    },
    "required": ["score", "reason", "focal_hint"],
    "additionalProperties": False,
}

_MATCH_SYSTEM = (
    "You score how well an image DESCRIPTION matches a desired b-roll "
    "subject for a technical B2B video. 0 = unrelated, 10 = exactly the "
    "subject, correctly framed. Penalize stocky/staged imagery. focal_hint "
    "= where the main subject sits in frame."
)

# Late-binding test seams (resolved at call time; audio.py pattern).
_describe_call = None
_match_call = None
_sleep = time.sleep


def _resolve():
    describe_fn, match_fn = _describe_call, _match_call
    if describe_fn is None:
        from video_agent.vision.ollama_vision import call_vision_auto
        describe_fn = call_vision_auto
    if match_fn is None:
        match_fn = text_llm.generate_schema_json
    return describe_fn, match_fn


def describe(image_path: Path) -> dict | None:
    from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
    describe_fn, _ = _resolve()
    for attempt in range(1, 4):
        out = describe_fn(DESCRIBE_PROMPT, Path(image_path), VISION_MODEL,
                          VISION_TIMEOUT_S)
        if isinstance(out, dict) and "description" in out:
            return out
        if attempt < 3:
            _sleep(2 ** attempt)
    return None


def verify_description(desc: dict | None, prompt: str) -> str | None:
    if not isinstance(desc, dict) or not desc.get("description"):
        return "describe_failed"
    text = str(desc["description"])
    if len(text) < config.VISION_DESCRIBE_MIN_CHARS:
        return "description_too_short"
    lowered = text.lower()
    for phrase in config.VISION_REFUSAL_PHRASES:
        if phrase in lowered:
            return "refusal_phrase"
    for i in range(0, max(1, len(prompt) - 40), 20):
        if prompt[i:i + 40].lower() in lowered:
            return "prompt_echo"
    visible = str(desc.get("visible_text") or "").lower()
    for term in config.WATERMARK_TERMS:
        if term in visible:
            return "watermark_text"
    return None


def match(description: str, wish: str, narration_span: str) -> dict:
    _, match_fn = _resolve()
    prompt = (
        f"DESIRED SUBJECT (broll wish): {wish}\n"
        f"NARRATION THIS SHOT COVERS: {narration_span}\n\n"
        f"IMAGE DESCRIPTION (from a separate blind viewing):\n{description}\n\n"
        f"Score the match now as JSON."
    )
    return match_fn(prompt, _MATCH_SYSTEM, MATCH_SCHEMA)


def judge(image_path: Path, wish: str, narration_span: str) -> dict:
    desc = describe(image_path)
    reason = verify_description(desc, DESCRIBE_PROMPT)
    if reason is not None:
        logger.info("judge reject (%s): %s", reason, image_path)
        return {"accepted_score": 0, "description": "", "focal_hint": "center",
                "reject_reason": reason}
    m = match(desc["description"], wish, narration_span)
    return {"accepted_score": int(m["score"]), "description": desc["description"],
            "focal_hint": m["focal_hint"], "reject_reason": None}
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_vision_judge.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 4: `sourcing/adapters.py` + `sourcing/openverse.py` + download helper

**Files:**
- Create: `shorts_engine/sourcing/openverse.py`
- Create: `shorts_engine/sourcing/adapters.py`
- Test: `tests/shorts_engine/test_sourcing_adapters.py`

**Interfaces:**
- Consumes: `video_agent.sources.base.RawCandidate` (dataclass: source, url, caption, width, height, file_size, is_clip, duration_s, extra) and `BaseSource.search(query, limit) -> list[RawCandidate]`; existing adapters `video_agent/sources/{pexels,pixabay,unsplash,wikimedia,duckduckgo,google_images_browser}.py`. **Bing is dropped** (spec §6.1). `requests` for downloads.
- Produces (used by Tasks 5, 6):
  - `openverse.OpenverseSource(BaseSource)` — `name="openverse"`, keyless GET `https://api.openverse.org/v1/images/?q=<query>&license_type=commercial&page_size=<limit>` → RawCandidates (url from `result["url"]`, width/height, caption from `title`, extra={"license": ..., "foreign_landing_url": ...}). Network errors ⇒ `[]` (a tier that can't search contributes nothing; the ladder moves on).
  - `adapters.tier_sources(tier: str) -> list` — `"api"` → [Pexels, Pixabay, Unsplash, Openverse, Wikimedia] instances; `"scrape"` → [DuckDuckGo, GoogleImagesBrowser]. Instantiation failures (missing key/env) are skipped with a warning, never raised.
  - `adapters.search_tier(tier: str, query: str, limit_per_source: int = 4) -> list[RawCandidate]` — round-robin across the tier's sources until `config.PER_TIER_CANDIDATES` collected; each source's `.search` wrapped in try/except (a failing source contributes nothing).
  - `adapters.download(cand, dest_dir: Path) -> Path | None` — streams `cand.url` to `dest_dir/<sha16>.<ext>` (ext from URL path, default `.jpg`); verifies the file opens with PIL and records real width/height back onto `cand`; any failure ⇒ None. 20s timeout, browser User-Agent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_sourcing_adapters.py
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image


class TestOpenverse:
    def test_search_maps_results(self):
        from shorts_engine.sourcing.openverse import OpenverseSource
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"results": [
            {"url": "https://img.example/a.jpg", "width": 1920, "height": 1080,
             "title": "clarifier", "license": "cc0",
             "foreign_landing_url": "https://page.example/a"},
        ]}
        with patch("shorts_engine.sourcing.openverse.requests.get",
                   return_value=fake) as g:
            out = OpenverseSource().search("wastewater clarifier", limit=5)
        assert g.call_args.kwargs["params"]["license_type"] == "commercial"
        assert len(out) == 1
        c = out[0]
        assert (c.source, c.url, c.width) == ("openverse", "https://img.example/a.jpg", 1920)
        assert c.extra["license"] == "cc0"

    def test_network_error_returns_empty(self):
        from shorts_engine.sourcing.openverse import OpenverseSource
        with patch("shorts_engine.sourcing.openverse.requests.get",
                   side_effect=OSError("net down")):
            assert OpenverseSource().search("x") == []


class TestSearchTier:
    def test_round_robin_caps_at_per_tier_candidates(self, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate

        class Fake:
            def __init__(self, name, n):
                self.name, self.n = name, n
            def search(self, q, limit=4):
                return [RawCandidate(source=self.name, url=f"https://{self.name}/{i}",
                                     width=1600, height=900) for i in range(self.n)]

        monkeypatch.setattr(adapters, "tier_sources",
                            lambda tier: [Fake("s1", 6), Fake("s2", 6)])
        out = adapters.search_tier("api", "query")
        from shorts_engine import config
        assert len(out) == config.PER_TIER_CANDIDATES
        assert {c.source for c in out} == {"s1", "s2"}  # interleaved, not s1-only

    def test_failing_source_is_skipped(self, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate

        class Boom:
            name = "boom"
            def search(self, q, limit=4):
                raise RuntimeError("api down")
        class Ok:
            name = "ok"
            def search(self, q, limit=4):
                return [RawCandidate(source="ok", url="https://ok/1",
                                     width=1600, height=900)]

        monkeypatch.setattr(adapters, "tier_sources", lambda tier: [Boom(), Ok()])
        out = adapters.search_tier("api", "q")
        assert [c.source for c in out] == ["ok"]


class TestDownload:
    def test_download_writes_and_backfills_dimensions(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate
        img_bytes = tmp_path / "src.png"
        Image.new("RGB", (1400, 900), (10, 20, 30)).save(img_bytes)
        payload = img_bytes.read_bytes()

        fake = MagicMock()
        fake.status_code = 200
        fake.iter_content = lambda chunk_size: [payload]
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: False
        monkeypatch.setattr(adapters.requests, "get", lambda *a, **k: fake)

        cand = RawCandidate(source="t", url="https://x.example/img.png", width=0, height=0)
        out = adapters.download(cand, tmp_path / "dl")
        assert out is not None and out.exists() and out.suffix == ".png"
        assert (cand.width, cand.height) == (1400, 900)

    def test_download_failure_returns_none(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate
        monkeypatch.setattr(adapters.requests, "get",
                            MagicMock(side_effect=OSError("refused")))
        cand = RawCandidate(source="t", url="https://x/y.jpg")
        assert adapters.download(cand, tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail** — modules missing.

- [ ] **Step 3: Implement both modules**

```python
# shorts_engine/sourcing/openverse.py
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
```

```python
# shorts_engine/sourcing/adapters.py
"""Tier construction over existing video_agent sources + download helper.
Bing is intentionally absent (spec §6.1: keyed/retired, no keyless path)."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from shorts_engine import config

logger = logging.getLogger(__name__)

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 hrsu-shorts-engine")}


def tier_sources(tier: str) -> list:
    """Instantiate a tier's sources; anything unconstructable is skipped."""
    specs: list = []
    if tier == "api":
        from shorts_engine.sourcing.openverse import OpenverseSource
        specs = [
            ("video_agent.sources.pexels", "PexelsSource"),
            ("video_agent.sources.pixabay", "PixabaySource"),
            ("video_agent.sources.unsplash", "UnsplashSource"),
            (OpenverseSource, None),
            ("video_agent.sources.wikimedia", "WikimediaSource"),
        ]
    elif tier == "scrape":
        specs = [
            ("video_agent.sources.duckduckgo", "DuckDuckGoSource"),
            ("video_agent.sources.google_images_browser", "GoogleImagesBrowserSource"),
        ]
    out = []
    for spec in specs:
        try:
            if isinstance(spec, tuple) and spec[1] is None:
                out.append(spec[0]())
                continue
            mod_name, attr_hint = spec
            mod = __import__(mod_name, fromlist=["*"])
            cls = getattr(mod, attr_hint, None)
            if cls is None:  # fall back: first BaseSource subclass in the module
                from video_agent.sources.base import BaseSource
                cls = next(v for v in vars(mod).values()
                           if isinstance(v, type) and issubclass(v, BaseSource)
                           and v is not BaseSource)
            out.append(cls())
        except Exception as e:  # noqa: BLE001 — missing key/env: skip source
            logger.warning("tier %s: source %s unavailable: %s", tier, spec, e)
    return out


def search_tier(tier: str, query: str, limit_per_source: int = 4) -> list:
    """Round-robin the tier's sources until PER_TIER_CANDIDATES collected."""
    per_source: list[list] = []
    for src in tier_sources(tier):
        try:
            per_source.append(src.search(query, limit=limit_per_source))
        except Exception as e:  # noqa: BLE001
            logger.warning("source %s search failed: %s",
                           getattr(src, "name", src), e)
    out, i = [], 0
    while len(out) < config.PER_TIER_CANDIDATES and any(per_source):
        for results in per_source:
            if i < len(results) and len(out) < config.PER_TIER_CANDIDATES:
                out.append(results[i])
        if all(i >= len(r) for r in per_source):
            break
        i += 1
    return out


def download(cand, dest_dir: Path) -> Path | None:
    """Stream a candidate to disk, verify it opens, backfill real dims."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(urlparse(cand.url).path).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    dest = dest_dir / (hashlib.sha256(cand.url.encode()).hexdigest()[:16] + ext)
    try:
        with requests.get(cand.url, stream=True, timeout=20, headers=_UA) as r:
            if r.status_code != 200:
                return None
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        with Image.open(dest) as img:
            cand.width, cand.height = img.size
        return dest
    except Exception as e:  # noqa: BLE001 — bad candidate, ladder moves on
        logger.info("download failed for %s: %s", cand.url, e)
        dest.unlink(missing_ok=True)
        return None
```

Implementer note: check the actual class names inside `video_agent/sources/{pexels,pixabay,unsplash,wikimedia,duckduckgo,google_images_browser}.py` at implementation time and put the real names in `tier_sources` (the `attr_hint` fallback covers drift, but explicit names are clearer). Do not import these at module level — `tier_sources` imports lazily so a missing API key never breaks module import.

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_sourcing_adapters.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 5: `sourcing/library_index.py` — own-footage vision index + query

**Files:**
- Create: `shorts_engine/sourcing/library_index.py`
- Test: `tests/shorts_engine/test_library_index.py`

**Interfaces:**
- Consumes: `vision_judge.describe`/`verify_description` (Task 3); `asset_library/{factory,footage,brand}/` under `config.PROJECT_ROOT`.
- Produces (used by Task 6):
  - `index_path() -> Path` — `PROJECT_ROOT / "asset_library" / "index.json"`.
  - `build_index(force: bool = False) -> dict` — walks `asset_library/{factory,footage}` for `.jpg/.jpeg/.png/.webp` (images only in this plan; video sampling is future work), DESCRIBEs each file not already indexed (mtime-keyed), stores `{rel_path: {"description", "visible_text", "mtime"}}`; a describe failure stores `{"description": "", "failed": true}` so it's retried on the next `force=True` build only. Writes/returns the index.
  - `query(wish: str, limit: int = 8) -> list[dict]` — token-overlap match (lowercased word intersection between wish and description, len ≥ 2 tokens OR any exact bigram hit) → `[{"path": abs Path str, "description": str, "score_hint": int overlap_count}]` sorted by overlap desc.
  - Module seam: `_describe = None` → resolves to `vision_judge.describe`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_library_index.py
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image


def _setup_lib(tmp_path, monkeypatch):
    from shorts_engine.sourcing import library_index as li
    lib = tmp_path / "asset_library"
    (lib / "factory").mkdir(parents=True)
    (lib / "footage").mkdir(parents=True)
    Image.new("RGB", (1600, 900), (50, 50, 50)).save(lib / "factory" / "plant.jpg")
    Image.new("RGB", (1600, 900), (90, 90, 90)).save(lib / "footage" / "tanks.png")
    monkeypatch.setattr(li, "_library_root", lambda: lib)
    return lib


class TestBuildIndex:
    def test_indexes_new_files_and_persists(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        lib = _setup_lib(tmp_path, monkeypatch)
        descs = {
            "plant.jpg": {"description": "wide shot of the HRSU calcium nitrate "
                          "production floor with bagging line and granulation "
                          "equipment, workers in safety gear visible throughout",
                          "visible_text": "", "quality_notes": "sharp"},
            "tanks.png": {"description": "circular clarifier tanks at a municipal "
                          "wastewater treatment plant seen from a walkway with "
                          "railings and aeration equipment in operation",
                          "visible_text": "", "quality_notes": "sharp"},
        }
        monkeypatch.setattr(li, "_describe", lambda p: descs[Path(p).name])
        idx = li.build_index()
        assert len(idx) == 2
        assert json.loads(li.index_path().read_text(encoding="utf-8")) == idx

    def test_incremental_skips_already_indexed(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        calls = []
        monkeypatch.setattr(li, "_describe", lambda p: (calls.append(p) or {
            "description": "x" * 130, "visible_text": "", "quality_notes": ""}))
        li.build_index()
        assert len(calls) == 2
        li.build_index()          # second run: nothing new
        assert len(calls) == 2

    def test_describe_failure_recorded_not_fatal(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        monkeypatch.setattr(li, "_describe", lambda p: None)
        idx = li.build_index()
        assert all(v.get("failed") for v in idx.values())


class TestQuery:
    def test_query_ranks_by_token_overlap(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        idx = {
            "factory/plant.jpg": {"description": "calcium nitrate production floor bagging line"},
            "footage/tanks.png": {"description": "clarifier tanks wastewater treatment plant walkway"},
        }
        monkeypatch.setattr(li, "_load_index", lambda: idx)
        out = li.query("wastewater treatment clarifier tanks")
        assert out and Path(out[0]["path"]).name == "tanks.png"
        assert out[0]["score_hint"] >= 3

    def test_query_no_overlap_returns_empty(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        monkeypatch.setattr(li, "_load_index", lambda: {
            "factory/plant.jpg": {"description": "bagging line equipment"}})
        assert li.query("ocean sunset beach") == []
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/sourcing/library_index.py`**

```python
"""Own-footage index: vision-describe each asset once, query by token match.
Own HRSU footage beats stock — it enters the ladder as tier 1 with a lower
judge threshold (JUDGE_MIN_OWN)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shorts_engine import config

logger = logging.getLogger(__name__)

_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_STOPWORDS = {"the", "a", "an", "of", "at", "in", "on", "with", "and", "or",
              "to", "for", "from", "by"}

_describe = None  # seam → vision_judge.describe


def _library_root() -> Path:
    return config.PROJECT_ROOT / "asset_library"


def index_path() -> Path:
    return _library_root() / "index.json"


def _load_index() -> dict:
    p = index_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("index.json corrupt — rebuilding")
    return {}


def _resolve_describe():
    global _describe
    if _describe is not None:
        return _describe
    from shorts_engine.llm.vision_judge import describe
    return describe


def build_index(force: bool = False) -> dict:
    root = _library_root()
    idx = {} if force else _load_index()
    describe_fn = _resolve_describe()
    for sub in ("factory", "footage"):
        d = root / sub
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in _EXTS:
                continue
            rel = f.relative_to(root).as_posix()
            mtime = f.stat().st_mtime
            entry = idx.get(rel)
            if entry and entry.get("mtime") == mtime and not (
                    force and entry.get("failed")):
                continue
            desc = describe_fn(f)
            if isinstance(desc, dict) and desc.get("description"):
                idx[rel] = {"description": desc["description"],
                            "visible_text": desc.get("visible_text", ""),
                            "mtime": mtime}
            else:
                idx[rel] = {"description": "", "failed": True, "mtime": mtime}
                logger.warning("library index: describe failed for %s", rel)
    index_path().parent.mkdir(parents=True, exist_ok=True)
    index_path().write_text(json.dumps(idx, indent=2), encoding="utf-8")
    return idx


def _tokens(text: str) -> set[str]:
    return {w for w in text.lower().split() if w not in _STOPWORDS and len(w) > 2}


def query(wish: str, limit: int = 8) -> list[dict]:
    idx = _load_index()
    want = _tokens(wish)
    scored = []
    for rel, entry in idx.items():
        have = _tokens(entry.get("description", ""))
        overlap = len(want & have)
        if overlap >= 2:
            scored.append({"path": str(_library_root() / rel),
                           "description": entry.get("description", ""),
                           "score_hint": overlap})
    scored.sort(key=lambda e: -e["score_hint"])
    return scored[:limit]
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_library_index.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 6: `sourcing/ladder.py` — the acquisition ladder

**Files:**
- Create: `shorts_engine/sourcing/ladder.py`
- Test: `tests/shorts_engine/test_ladder.py`

**Interfaces:**
- Consumes: `gates.run_pre_gates/watermarked` (Task 1), `adapters.search_tier/download` (Task 4), `library_index.query` (Task 5), `vision_judge.judge` (Task 3), `post.json`'s `images: list[{url, ...}]` (may be `[]`).
- Produces (used by Task 10):
  - `acquire(wish: str, narration_span: str, workspace: Path, post_images: list[dict], torture: bool = False) -> dict` — returns
    `{"image_path": str | None, "focal_hint": str, "provenance": {"tiers": [ {"tier", "candidates_seen", "rejections": [{"url","reason"}], "accepted": {...}|None} ], "reason": str | None}}`.
    Tier order: `own` (library_index.query → judge ≥ JUDGE_MIN_OWN) → `blog` (post_images → download → gates → judge ≥ JUDGE_MIN_BLOG) → `api` (search_tier("api") → gates → download → watermark → judge ≥ JUDGE_MIN_API) → `scrape` (same, ≥ JUDGE_MIN_SCRAPE). First acceptance wins; downloads land in `workspace/broll/`. `torture=True` ⇒ immediately `{"image_path": None, ..., "reason": "torture_mode"}` with empty tiers. No acceptance anywhere ⇒ `image_path=None, reason="no_acceptance"` (caller renders the declared fallback — never-blank).
  - Judge-call budget: at most `PER_TIER_CANDIDATES` judged per tier (pre-gate rejects don't count against the search pull but each tier judges ≤8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_ladder.py
from __future__ import annotations
from pathlib import Path
from PIL import Image
import pytest


def _img(tmp_path, name="a.png", size=(1600, 900)):
    p = tmp_path / name
    Image.new("RGB", size, (40, 40, 40)).save(p)
    return p


class TestTortureMode:
    def test_torture_short_circuits_everything(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        called = []
        monkeypatch.setattr(ladder, "_query_library",
                            lambda *a: called.append("lib") or [])
        out = ladder.acquire("tanks", "narration", tmp_path, [], torture=True)
        assert out["image_path"] is None
        assert out["provenance"]["reason"] == "torture_mode"
        assert called == []  # nothing searched at all


class TestOwnLibraryTier:
    def test_own_footage_accepted_at_lower_threshold(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        own = _img(tmp_path, "own.png")
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [
            {"path": str(own), "description": "clarifier tanks", "score_hint": 3}])
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: {
            "accepted_score": 5, "description": "d", "focal_hint": "left",
            "reject_reason": None})  # 5 passes OWN (>=5) but would fail API (>=6)
        out = ladder.acquire("clarifier tanks", "narr", tmp_path, [])
        assert out["image_path"] == str(own)
        assert out["focal_hint"] == "left"
        assert out["provenance"]["tiers"][0]["tier"] == "own"

    def test_judge_reject_falls_through_to_next_tier(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        own = _img(tmp_path, "own.png")
        blog_img = _img(tmp_path, "blog.png")
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [
            {"path": str(own), "description": "d", "score_hint": 2}])
        monkeypatch.setattr(ladder, "_download",
                            lambda cand, d: blog_img)
        scores = {str(own): 3, str(blog_img): 8}
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: {
            "accepted_score": scores[str(p)], "description": "d",
            "focal_hint": "center", "reject_reason": None})
        out = ladder.acquire("tanks", "narr", tmp_path,
                             [{"url": "https://blog/img1.png", "width": 1600, "height": 900}])
        assert out["image_path"] == str(blog_img)
        tiers = {t["tier"]: t for t in out["provenance"]["tiers"]}
        assert tiers["own"]["accepted"] is None
        assert tiers["blog"]["accepted"] is not None


class TestGatesAndBudget:
    def test_pre_gate_reject_recorded_and_final(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        judged = []
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: judged.append(p))
        out = ladder.acquire("tanks", "narr", tmp_path, [
            {"url": "https://www.shutterstock.com/x.jpg", "width": 1600, "height": 900}])
        blog_tier = next(t for t in out["provenance"]["tiers"] if t["tier"] == "blog")
        assert blog_tier["rejections"][0]["reason"] == "blacklisted"
        assert judged == []  # a hard gate reject is FINAL — never judged

    def test_no_acceptance_returns_none_reason(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        monkeypatch.setattr(ladder, "_search_tier", lambda tier, q: [])
        out = ladder.acquire("tanks", "narr", tmp_path, [])
        assert out["image_path"] is None
        assert out["provenance"]["reason"] == "no_acceptance"

    def test_judge_budget_capped_per_tier(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import ladder
        from shorts_engine import config
        from video_agent.sources.base import RawCandidate
        monkeypatch.setattr(ladder, "_query_library", lambda wish: [])
        img = _img(tmp_path, "c.png")
        cands = [RawCandidate(source="s", url=f"https://ok{i}.example/i.jpg",
                              width=1600, height=900) for i in range(20)]
        monkeypatch.setattr(ladder, "_search_tier",
                            lambda tier, q: cands if tier == "api" else [])
        monkeypatch.setattr(ladder, "_download", lambda c, d: img)
        monkeypatch.setattr(ladder, "_watermarked", lambda p: False)
        judged = []
        monkeypatch.setattr(ladder, "_judge", lambda p, w, n: (judged.append(1) or {
            "accepted_score": 0, "description": "", "focal_hint": "center",
            "reject_reason": "low"}))
        ladder.acquire("tanks", "narr", tmp_path, [])
        # api tier judges at most PER_TIER_CANDIDATES (scrape tier found nothing)
        assert len(judged) == config.PER_TIER_CANDIDATES
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/sourcing/ladder.py`**

```python
"""Acquisition ladder (spec §6.1): own library → blog images → free APIs →
scrape. Gates first (hard rejects are final, F2), then the describe-then-
match judge. First acceptance wins; nothing accepted ⇒ caller renders the
declared fallback (never-blank)."""
from __future__ import annotations

import logging
from pathlib import Path

from shorts_engine import config

logger = logging.getLogger(__name__)

_THRESHOLDS = {"own": None, "blog": None, "api": None, "scrape": None}


def _thresholds() -> dict:
    return {"own": config.JUDGE_MIN_OWN, "blog": config.JUDGE_MIN_BLOG,
            "api": config.JUDGE_MIN_API, "scrape": config.JUDGE_MIN_SCRAPE}


# ── Late-binding seams (audio.py pattern) ────────────────────────────────────
def _query_library(wish: str) -> list[dict]:
    from shorts_engine.sourcing.library_index import query
    return query(wish)


def _search_tier(tier: str, wish: str) -> list:
    from shorts_engine.sourcing.adapters import search_tier
    return search_tier(tier, wish)


def _download(cand, dest_dir: Path) -> Path | None:
    from shorts_engine.sourcing.adapters import download
    return download(cand, dest_dir)


def _watermarked(img_path: Path) -> bool:
    from shorts_engine.sourcing.gates import watermarked
    return watermarked(img_path)


def _judge(image_path: Path, wish: str, narration_span: str) -> dict:
    from shorts_engine.llm.vision_judge import judge
    return judge(image_path, wish, narration_span)


def _accept(tier_rec: dict, path: Path, verdict: dict, url: str) -> dict:
    tier_rec["accepted"] = {"path": str(path), "url": url,
                            "score": verdict["accepted_score"],
                            "focal_hint": verdict["focal_hint"]}
    return {"image_path": str(path), "focal_hint": verdict["focal_hint"]}


def acquire(wish: str, narration_span: str, workspace: Path,
            post_images: list[dict], torture: bool = False) -> dict:
    provenance: dict = {"tiers": [], "reason": None}
    result = {"image_path": None, "focal_hint": "center",
              "provenance": provenance}
    if torture:
        provenance["reason"] = "torture_mode"
        return result
    if not (wish or "").strip():
        provenance["reason"] = "no_wish"
        return result

    thresholds = _thresholds()
    dl_dir = Path(workspace) / "broll"

    for tier in ("own", "blog", "api", "scrape"):
        rec = {"tier": tier, "candidates_seen": 0, "rejections": [],
               "accepted": None}
        provenance["tiers"].append(rec)
        judged = 0
        seen: set[str] = set()

        if tier == "own":
            for hit in _query_library(wish):
                if judged >= config.PER_TIER_CANDIDATES:
                    break
                rec["candidates_seen"] += 1
                judged += 1
                v = _judge(Path(hit["path"]), wish, narration_span)
                if v["reject_reason"] is None and \
                        v["accepted_score"] >= thresholds[tier]:
                    result.update(_accept(rec, Path(hit["path"]), v, hit["path"]))
                    return result
                rec["rejections"].append(
                    {"url": hit["path"],
                     "reason": v["reject_reason"] or f"score_{v['accepted_score']}"})
            continue

        # candidate-producing tiers
        if tier == "blog":
            from types import SimpleNamespace
            cands = [SimpleNamespace(url=i.get("url", ""),
                                     width=int(i.get("width") or 0),
                                     height=int(i.get("height") or 0))
                     for i in (post_images or []) if i.get("url")]
        else:
            cands = _search_tier(tier, wish)

        for cand in cands:
            if judged >= config.PER_TIER_CANDIDATES:
                break
            rec["candidates_seen"] += 1
            from shorts_engine.sourcing.gates import run_pre_gates
            reason = run_pre_gates(cand, seen)
            if reason:
                rec["rejections"].append({"url": cand.url, "reason": reason})
                continue                     # hard reject — FINAL (F2)
            local = _download(cand, dl_dir)
            if local is None:
                rec["rejections"].append({"url": cand.url, "reason": "download_failed"})
                continue
            from shorts_engine.sourcing.gates import resolution_ok
            if not resolution_ok(cand.width, cand.height):
                rec["rejections"].append({"url": cand.url, "reason": "low_resolution_actual"})
                continue
            if _watermarked(local):
                rec["rejections"].append({"url": cand.url, "reason": "watermarked"})
                continue
            judged += 1
            v = _judge(local, wish, narration_span)
            if v["reject_reason"] is None and \
                    v["accepted_score"] >= thresholds[tier]:
                result.update(_accept(rec, local, v, cand.url))
                return result
            rec["rejections"].append(
                {"url": cand.url,
                 "reason": v["reject_reason"] or f"score_{v['accepted_score']}"})

    provenance["reason"] = "no_acceptance"
    return result
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_ladder.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 7: `sourcing/paper_page.py` — cited paper's front page (PDF render / Playwright screenshot)

**Files:**
- Create: `shorts_engine/sourcing/paper_page.py`
- Test: `tests/shorts_engine/test_paper_page.py`

**Interfaces:**
- Consumes: `pypdfium2` (installed), Playwright sync API (installed), `requests`, `config.PAPER_CACHE_DIR`.
- Produces (used by Task 10):
  - `cache_key(url: str) -> str` — sha256[:16] of the URL.
  - `is_pdf_url(url: str) -> bool` — True for paths ending `.pdf` OR arxiv.org/pdf/ OR /pdf/ segments on mdpi.com / ncbi.nlm.nih.gov (pmc).
  - `render_pdf_page1(pdf_bytes: bytes, out_png: Path) -> Path | None` — pypdfium2 page-1 render at scale for ~1600px width; None on any failure.
  - `screenshot_header(url: str, out_png: Path) -> Path | None` — Playwright chromium, viewport 1200×1500, `page.goto(url, timeout=30s)`, best-effort cookie-banner dismissal (click first visible button matching `accept|agree|got it|ok` case-insensitive, wrapped in try/except), screenshot `clip={"x":0,"y":0,"width":1200,"height":900}` (the header region: title/authors/journal); None on any failure. Import playwright lazily inside the function.
  - `fetch_front_page(url: str, torture: bool = False) -> Path | None` — cache hit (`PAPER_CACHE_DIR/<key>.png`) → return; `torture=True` and no cache → None; PDF path: `requests.get` (20s, UA header) → `render_pdf_page1`; else `screenshot_header`; both fail → None (caller renders QUOTE_CARD fallback). Successful renders are cached.
  - Module seams: `_fetch_bytes = None` (→ requests download), `_screenshot = None` (→ screenshot_header internals) for tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_paper_page.py
from __future__ import annotations
from pathlib import Path
import pytest
from PIL import Image


def _tiny_pdf_bytes() -> bytes:
    """One-page PDF built with pypdfium2's raw API is overkill — write a
    minimal hand-rolled valid PDF (blank A4 page)."""
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000052 00000 n \n0000000101 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF")


class TestUrlClassification:
    def test_is_pdf_url(self):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.is_pdf_url("https://arxiv.org/pdf/2602.21290")
        assert pp.is_pdf_url("https://www.mdpi.com/2073-4441/12/5/1234/pdf")
        assert pp.is_pdf_url("https://example.com/paper.pdf")
        assert not pp.is_pdf_url("https://pubmed.ncbi.nlm.nih.gov/18462937/")

    def test_cache_key_stable(self):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.cache_key("https://a.com/x") == pp.cache_key("https://a.com/x")
        assert len(pp.cache_key("https://a.com/x")) == 16


class TestPdfRender:
    def test_renders_page1_to_png(self, tmp_path):
        from shorts_engine.sourcing import paper_page as pp
        out = pp.render_pdf_page1(_tiny_pdf_bytes(), tmp_path / "p.png")
        assert out is not None and out.exists()
        with Image.open(out) as img:
            assert img.width >= 1200  # rendered at readable scale

    def test_garbage_bytes_return_none(self, tmp_path):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.render_pdf_page1(b"not a pdf", tmp_path / "p.png") is None


class TestFetchFrontPage:
    def test_cache_hit_short_circuits(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        url = "https://arxiv.org/pdf/2602.21290"
        cached = tmp_path / (pp.cache_key(url) + ".png")
        Image.new("RGB", (1600, 2000), (255, 255, 255)).save(cached)
        called = []
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: called.append(u))
        assert pp.fetch_front_page(url) == cached
        assert called == []

    def test_torture_mode_never_fetches(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        called = []
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: called.append(u))
        assert pp.fetch_front_page("https://arxiv.org/pdf/1", torture=True) is None
        assert called == []

    def test_pdf_path_fetches_renders_and_caches(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: _tiny_pdf_bytes())
        url = "https://arxiv.org/pdf/2602.21290"
        out = pp.fetch_front_page(url)
        assert out is not None and out.exists()
        assert out.name == pp.cache_key(url) + ".png"

    def test_landing_page_uses_screenshot_seam(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        def fake_shot(url, out_png):
            Image.new("RGB", (1200, 900), (250, 250, 250)).save(out_png)
            return out_png
        monkeypatch.setattr(pp, "_screenshot", fake_shot)
        out = pp.fetch_front_page("https://pubmed.ncbi.nlm.nih.gov/18462937/")
        assert out is not None and out.exists()

    def test_both_paths_failing_returns_none(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: None)
        monkeypatch.setattr(pp, "_screenshot", lambda u, o: None)
        assert pp.fetch_front_page("https://x.example/paper.pdf") is None
        assert pp.fetch_front_page("https://x.example/landing") is None
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/sourcing/paper_page.py`**

```python
"""PAPER_CARD acquisition (spec §4 Stage 4): the cited paper's page 1.
(a) open-access PDF → pypdfium2 page-1 render; (b) else Playwright header
screenshot of the landing page; (c) both fail → None and the shot's declared
QUOTE_CARD fallback renders. Results cached by URL hash — papers don't
change, and the user wants this shot on every proof beat."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from shorts_engine import config

logger = logging.getLogger(__name__)

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 hrsu-shorts-engine")}

_screenshot = None  # test seam → screenshot_header
_fetch_bytes = None  # test seam → _default_fetch_bytes


def cache_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def is_pdf_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if path.endswith(".pdf"):
        return True
    if "/pdf/" in path or path.endswith("/pdf"):
        return host.endswith(("arxiv.org", "mdpi.com", "ncbi.nlm.nih.gov"))
    return False


def _default_fetch_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20, headers=_UA)
        return r.content if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        logger.info("paper fetch failed %s: %s", url, e)
        return None


def render_pdf_page1(pdf_bytes: bytes, out_png: Path) -> Path | None:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        page = pdf[0]
        scale = 1600 / page.get_size()[0]
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil()
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out_png)
        return out_png
    except Exception as e:  # noqa: BLE001 — corrupt/protected PDF: fall through
        logger.info("pdf page-1 render failed: %s", e)
        return None


def screenshot_header(url: str, out_png: Path) -> Path | None:
    """Playwright header screenshot: title/authors/journal visible."""
    try:
        from playwright.sync_api import sync_playwright
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1500})
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            try:  # best-effort cookie banner dismissal
                btn = page.locator(
                    "button:visible",
                    has_text=__import__("re").compile(
                        r"accept|agree|got it|^ok$", __import__("re").I))
                if btn.count():
                    btn.first.click(timeout=3_000)
            except Exception:  # noqa: BLE001
                pass
            page.screenshot(path=str(out_png),
                            clip={"x": 0, "y": 0, "width": 1200, "height": 900})
            browser.close()
        return out_png if out_png.exists() else None
    except Exception as e:  # noqa: BLE001 — any failure ⇒ fallback card renders
        logger.info("landing screenshot failed %s: %s", url, e)
        return None


def fetch_front_page(url: str, torture: bool = False) -> Path | None:
    config.PAPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = config.PAPER_CACHE_DIR / (cache_key(url) + ".png")
    if cached.exists():
        return cached
    if torture:
        return None
    fetch = _fetch_bytes or _default_fetch_bytes
    shot = _screenshot or screenshot_header
    if is_pdf_url(url):
        data = fetch(url)
        if data and render_pdf_page1(data, cached):
            return cached
        return None
    return shot(url, cached)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_paper_page.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 8: `cards/paper_card.py` — the "receipts" shot

**Files:**
- Create: `shorts_engine/cards/paper_card.py`
- Test: `tests/shorts_engine/test_paper_card.py`

**Interfaces:**
- Consumes: `cards/theme.py` (Plan 2: `background/fit_text/paste_text_block/draw_citation_chip/render_card/ease_out_cubic/GOLD/TEXT/NAVY`), `config` canvas/safe-margin constants.
- Produces (used by Task 10): `frame_at(payload, t, duration) -> Image` and `render(payload, duration, out_path, fade_in_s=0.0) -> Path` — the Plan-2 renderer contract. Payload: `{"image_path": str (front-page PNG), "highlight": str, "citation": str (chip text)}`.
- Look (spec §5): front page inset at **78% canvas width** on the brand background, **−2° tilt**, soft shadow, **gold underline sweep across the paper's title region** (a bar at ~12% down the inset, sweeping over the first 0.8s), slow **1.00→1.05 push-in** over the shot (portrait document — safe), citation chip bottom-left, highlight phrase (≤ 8 words shown) under the inset in brand text. Missing/unreadable `image_path` raises `EngineError` — VISUALS resolves the fallback BEFORE calling this renderer, so reaching it with a bad path is a pipeline bug, not a degraded state.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_paper_card.py
from __future__ import annotations
import numpy as np
import pytest
from PIL import Image
from shorts_engine import config


@pytest.fixture()
def front_page(tmp_path):
    p = tmp_path / "page1.png"
    img = Image.new("RGB", (1600, 2100), (250, 250, 248))  # white paper
    img.save(p)
    return p


def _payload(front_page):
    return {"image_path": str(front_page),
            "highlight": "92 percent nitrate removal",
            "citation": "Source [12] — arxiv.org"}


class TestPaperFrames:
    def test_paper_inset_visible_and_inside_safe_zone(self, front_page):
        from shorts_engine.cards import paper_card, theme
        img = paper_card.frame_at(_payload(front_page), 1.5, 4.0)
        arr = np.asarray(img).astype(int)
        # the white paper dominates the mid region
        white = (arr.min(axis=2) > 200).sum()
        assert white > 200_000
        ys, xs = np.where(arr.min(axis=2) > 200)
        assert xs.min() >= config.SAFE_SIDE_PX - 40   # tilt tolerance
        assert xs.max() <= config.CANVAS_W - config.SAFE_SIDE_PX + 40

    def test_gold_sweep_grows_over_first_second(self, front_page):
        from shorts_engine.cards import paper_card, theme
        def gold(t):
            arr = np.asarray(paper_card.frame_at(_payload(front_page), t, 4.0)).astype(int)
            return (np.abs(arr - np.array(theme.GOLD)).sum(axis=2) < 90).sum()
        assert gold(0.9) > gold(0.1)

    def test_push_in_changes_frame_over_time(self, front_page):
        from shorts_engine.cards import paper_card
        a = np.asarray(paper_card.frame_at(_payload(front_page), 0.0, 4.0))
        b = np.asarray(paper_card.frame_at(_payload(front_page), 3.9, 4.0))
        assert np.abs(a.astype(int) - b.astype(int)).sum() > 0

    def test_missing_image_raises(self):
        from shorts_engine.cards import paper_card
        from shorts_engine.errors import EngineError
        with pytest.raises(EngineError):
            paper_card.frame_at({"image_path": "Z:/nope.png",
                                 "highlight": "h", "citation": "c"}, 0.5, 4.0)

    def test_render_mp4(self, front_page, tmp_path):
        from shorts_engine.cards import paper_card, encoder
        out = paper_card.render(_payload(front_page), 0.6, tmp_path / "p.mp4")
        assert abs(encoder.probe_duration(out) - 0.6) < 0.15
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/cards/paper_card.py`**

```python
"""PAPER_CARD: the cited paper's front page as a 'receipts' shot — inset at
78% width on the brand background, -2° tilt with soft shadow, gold underline
sweep over the title region, slow 1.05 push-in. The user's top request from
the first watch-through."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from shorts_engine import config
from shorts_engine.cards import theme
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)

_INSET_FRAC = 0.78
_TILT_DEG = -2.0
_PUSH_MAX = 1.05
_CENTER_Y = 820


@functools.lru_cache(maxsize=8)
def _load_page(path_str: str) -> Image.Image:
    p = Path(path_str)
    if not p.exists():
        raise EngineError(f"paper front page missing: {p}")
    try:
        return Image.open(p).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise EngineError(f"paper front page unreadable: {p}: {e}") from e


def _tilted_paper(src: Image.Image, width: int) -> Image.Image:
    """Paper resized to `width`, tilted, with a soft drop shadow. RGBA."""
    h = int(src.height * width / src.width)
    h = min(h, int(width * 1.5))  # clamp very tall pages to 3:2 of width
    paper = src.resize((width, h)).convert("RGBA")
    d = ImageDraw.Draw(paper)
    d.rectangle([0, 0, width - 1, h - 1], outline=(200, 200, 200, 255), width=2)
    pad = 60
    canvas = Image.new("RGBA", (width + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        [pad + 10, pad + 14, pad + width + 10, pad + h + 14],
        fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(paper, (pad, pad))
    return canvas.rotate(_TILT_DEG, expand=True, resample=Image.BICUBIC)


def frame_at(payload: dict, t: float, duration: float) -> Image.Image:
    src = _load_page(str(payload["image_path"]))
    img = theme.background(t)
    d = ImageDraw.Draw(img)

    push = 1.0 + (_PUSH_MAX - 1.0) * min(1.0, t / max(duration, 0.01))
    inset_w = int(config.CANVAS_W * _INSET_FRAC * push)
    composite = _tilted_paper(src, inset_w)
    x = (config.CANVAS_W - composite.width) // 2
    y = _CENTER_Y - composite.height // 2
    img.paste(composite, (x, y), composite)

    # gold underline sweep across the title region (~12% down the inset)
    p = theme.ease_out_cubic(t / 0.8)
    if p > 0:
        sweep_w = int(inset_w * 0.72 * p)
        sx = (config.CANVAS_W - int(inset_w * 0.72)) // 2
        sy = y + int(composite.height * 0.16)
        d.rectangle([sx, sy, sx + sweep_w, sy + 8], fill=theme.GOLD)

    highlight = " ".join(str(payload.get("highlight", "")).split()[:8])
    if highlight:
        max_w = config.CANVAS_W - 2 * config.SAFE_SIDE_PX
        f, lines, _ = theme.fit_text(d, highlight, "heading", max_w, max_size=56)
        ty = min(y + composite.height + 24,
                 config.CANVAS_H - config.SAFE_BOTTOM_PX - 180)
        theme.paste_text_block(img, lines, f, ty, theme.TEXT)

    chip = payload.get("citation")
    if chip:
        theme.draw_citation_chip(img, str(chip))
    return img


def render(payload: dict, duration: float, out_path: Path,
           fade_in_s: float = 0.0) -> Path:
    return theme.render_card(frame_at, payload, duration, out_path, fade_in_s)
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_paper_card.py -v`
(If the safe-zone assertion fails from tilt overhang, reduce `_INSET_FRAC` to 0.75 — do not widen the test tolerance.)

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 9: SHOTLIST extension — emit BROLL shots (with required fallback)

**Files:**
- Modify: `shorts_engine/stages/shotlist.py`
- Test: extend `tests/shorts_engine/test_shotlist.py` (new class `TestBrollEmission`)

**Interfaces:**
- Consumes: Plan-2 `plan_beat_shots` structure; beats' `broll_wish`.
- Produces (consumed by Task 10):
  - Beat mapping change (spec §4 Stage 4 defaults): on **hook** and **proof** beats, when `broll_wish` is a non-empty string AND the beat produced ≥2 narration spans, the FIRST shot becomes `BROLL` with payload `{"wish": <broll_wish>, "layout": "auto"}` and **required** `fallback` = what that slot would otherwise have been (hook → HEADLINE_CARD payload; proof → its existing first-shot type/payload). Beats with one span keep their designed card (a 1-shot beat can't afford an acquisition miss disrupting rhythm — deterministic reading).
  - Linter additions: `BROLL` is a known type; every BROLL must declare a renderable fallback (same rule as PAPER_CARD); BROLL duration bounds same as designed shots.

- [ ] **Step 1: Write the failing tests** (append to `tests/shorts_engine/test_shotlist.py`)

```python
class TestBrollEmission:
    def _fixtures(self):
        facts = {f["id"]: f for f in FACTS["facts"]}
        cites = {c["marker"]: c for c in CITES}
        from shorts_engine.brand import BrandFacts
        brand = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                           differentiators=[{"id": "b_purity", "text": "high-purity powder"}],
                           cta_lines=["Full guide on the HRSU blog"], banned_claims=[])
        return facts, cites, brand

    def test_hook_with_wish_and_two_spans_emits_broll_first(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook",
                "narration": "Your effluent nitrate is creeping toward the limit, "
                             "and the discharge clock is already running.",
                "fact_ids": [], "card_text": "Nitrate limits are tightening",
                "broll_wish": "wastewater aeration basin"}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert shots[0]["type"] == "BROLL"
        assert shots[0]["payload"]["wish"] == "wastewater aeration basin"
        fb = shots[0]["fallback"]
        assert fb["type"] == "HEADLINE_CARD"
        assert fb["payload"]["text"] == "Nitrate limits are tightening"

    def test_hook_without_wish_stays_headline(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook", "narration": "Your effluent nitrate is rising fast.",
                "fact_ids": [], "card_text": "Limits tightening", "broll_wish": ""}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert all(s["type"] != "BROLL" for s in shots)

    def test_single_span_hook_keeps_designed_card_despite_wish(self):
        from shorts_engine.stages import shotlist
        beat = {"beat": "hook", "narration": "Nitrate limits are rising.",
                "fact_ids": [], "card_text": "Limits tightening",
                "broll_wish": "aeration basin"}
        shots = shotlist.plan_beat_shots(beat, *self._fixtures())
        assert shots[0]["type"] == "HEADLINE_CARD"

    def test_linter_flags_broll_without_fallback(self):
        from shorts_engine.stages import shotlist
        shots = [{"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 3.0,
                  "narration_span": "x", "payload": {"wish": "w"}, "fallback": None}]
        errs = shotlist.lint_shotlist(shots, FACTS)
        assert any("fallback" in e for e in errs)
```

- [ ] **Step 2: Run tests to verify they fail** — BROLL never emitted; linter treats BROLL as unknown type.

- [ ] **Step 3: Implement in `shorts_engine/stages/shotlist.py`**

(a) In `plan_beat_shots`, hook branch — replace the single `add("HEADLINE_CARD", ...)` with:

```python
    if name == "hook":
        headline_payload = {"text": beat["card_text"],
                            "wish": beat.get("broll_wish", "")}
        wish = (beat.get("broll_wish") or "").strip()
        if wish and len(spans) >= 2:
            add("BROLL", {"wish": wish, "layout": "auto"}, spans[0],
                fallback={"type": "HEADLINE_CARD", "payload": headline_payload})
            add("HEADLINE_CARD", headline_payload, ", ".join(spans[1:]))
        else:
            add("HEADLINE_CARD", headline_payload, narration)
```

(b) In the proof branch, before the `paper_fact` check, insert the same pattern: when `wish` non-empty and `len(spans) >= 2` **and no paper_fact** (PAPER_CARD outranks BROLL on proof), shot 1 becomes BROLL with fallback = the STAT_CARD (or HEADLINE_CARD) payload it replaces; remaining spans keep their existing mapping. Keep the existing code as the else path.

(c) In `lint_shotlist`: add `"BROLL"` to `known`; extend the fallback rule to `if s["type"] in ("PAPER_CARD", "BROLL") and not s.get("fallback")`.

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_shotlist.py -v`
(Existing `TestRun::test_run_writes_shotlist` uses a hook beat WITH a wish — if its narration now yields 2 spans and emits BROLL, that's correct new behavior; update that test's expectations to accept `BROLL` as shots[0] with a HEADLINE fallback rather than weakening the new rule.)

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 10: VISUALS integration — BROLL ladder + PAPER_CARD resolution + provenance

**Files:**
- Modify: `shorts_engine/stages/visuals.py`
- Test: extend `tests/shorts_engine/test_visuals.py` (new class `TestAcquisitionResolution`)

**Interfaces:**
- Consumes: `ladder.acquire` (Task 6), `paper_page.fetch_front_page` (Task 7), `cards/paper_card.py` + `cards/broll_frame.py` renderers (Task 8 / Plan 2), `post.json` (`images`, citations), `ctx.flags["torture"]`.
- Produces:
  - `RENDERERS` gains `"PAPER_CARD": paper_card.render` and `"BROLL": broll_frame.render`.
  - `resolve_shot(shot, ctx=None, post=None) -> (rtype, payload, provenance)` — extended signature (old 1-arg calls still work: `ctx=None ⇒ torture-style fallback resolution`, keeping Plan-2 tests green):
    - `BROLL`: `ladder.acquire(wish, narration_span, workspace, post_images, torture)`. Acceptance ⇒ `("BROLL", {"image_path", "layout": "auto", "focal_hint"}, {"resolved": "acquired", "tier": ..., "score": ..., "provenance": full ladder record})`; miss ⇒ declared fallback with `{"resolved": "fallback", "reason": <ladder reason>}`.
    - `PAPER_CARD`: `fetch_front_page(url, torture)`. Success ⇒ `("PAPER_CARD", {"image_path", "highlight", "citation": "Source [m] — <domain>"}, {"resolved": "acquired"})`; None ⇒ QUOTE_CARD fallback (Plan-2 behavior, now with `reason: "paper_fetch_failed"` or `"torture_mode"`).
    - `focal_hint` maps to `broll_frame` layout: `center→auto`, `left/right/top/bottom→inset` (deterministic: off-center subjects get the matte, not blur-fill).
  - `run(ctx)` passes `ctx`/`post` through; `visuals_report.json` entries carry the full acquisition provenance (spec Stage 6 output contract).

- [ ] **Step 1: Write the failing tests** (append to `tests/shorts_engine/test_visuals.py`)

```python
class TestAcquisitionResolution:
    def _broll_shot(self):
        return {"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 2.0,
                "narration_span": "nitrate is rising",
                "payload": {"wish": "aeration basin", "layout": "auto"},
                "fallback": {"type": "HEADLINE_CARD",
                             "payload": {"text": "Nitrate limits tighten"}}}

    def test_broll_acquired_resolves_with_image(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from PIL import Image
        img = tmp_path / "b.png"
        Image.new("RGB", (1600, 900), (60, 60, 60)).save(img)
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: {
            "image_path": str(img), "focal_hint": "left",
            "provenance": {"tiers": [{"tier": "own"}], "reason": None}})
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert rtype == "BROLL"
        assert payload["image_path"] == str(img)
        assert payload["layout"] == "inset"      # off-center focal hint
        assert prov["resolved"] == "acquired"

    def test_broll_miss_resolves_to_fallback(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: {
            "image_path": None, "focal_hint": "center",
            "provenance": {"tiers": [], "reason": "no_acceptance"}})
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert rtype == "HEADLINE_CARD"
        assert prov["resolved"] == "fallback"
        assert prov["reason"] == "no_acceptance"

    def test_torture_flag_reaches_ladder(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        seen = {}
        monkeypatch.setattr(visuals, "_acquire", lambda **kw: (seen.update(kw) or {
            "image_path": None, "focal_hint": "center",
            "provenance": {"tiers": [], "reason": "torture_mode"}}))
        class Ctx: workspace = tmp_path; flags = {"torture": True}
        visuals.resolve_shot(self._broll_shot(), Ctx(), {"images": []})
        assert seen["torture"] is True

    def test_paper_card_acquired_renders_paper(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        from PIL import Image
        page = tmp_path / "page.png"
        Image.new("RGB", (1600, 2100), (250, 250, 250)).save(page)
        monkeypatch.setattr(visuals, "_fetch_front_page", lambda url, torture: page)
        shot = {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                "narration_span": "x",
                "payload": {"marker": 12, "url": "https://arxiv.org/pdf/2602.21290",
                            "highlight": "92 percent removal"},
                "fallback": {"type": "QUOTE_CARD",
                             "payload": {"quote": "q", "source": "s"}}}
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(shot, Ctx(), {"images": []})
        assert rtype == "PAPER_CARD"
        assert payload["image_path"] == str(page)
        assert "arxiv.org" in payload["citation"]
        assert prov["resolved"] == "acquired"

    def test_paper_fetch_failure_keeps_quote_fallback(self, tmp_path, monkeypatch):
        from shorts_engine.stages import visuals
        monkeypatch.setattr(visuals, "_fetch_front_page", lambda url, torture: None)
        shot = {"id": "s01", "beat": "proof", "type": "PAPER_CARD", "duration_s": 3.0,
                "narration_span": "x",
                "payload": {"marker": 12, "url": "https://arxiv.org/pdf/x",
                            "highlight": "h"},
                "fallback": {"type": "QUOTE_CARD",
                             "payload": {"quote": "q", "source": "s"}}}
        class Ctx: workspace = tmp_path; flags = {}
        rtype, payload, prov = visuals.resolve_shot(shot, Ctx(), {"images": []})
        assert rtype == "QUOTE_CARD" and prov["resolved"] == "fallback"

    def test_legacy_one_arg_call_still_resolves_fallback(self):
        # Plan-2 compatibility: resolve_shot(shot) with no ctx behaves like torture
        from shorts_engine.stages import visuals
        rtype, payload, prov = visuals.resolve_shot(self._broll_shot())
        assert rtype == "HEADLINE_CARD" and prov["resolved"] == "fallback"
```

- [ ] **Step 2: Run tests to verify they fail** — `_acquire`/`_fetch_front_page` seams missing, BROLL unknown.

- [ ] **Step 3: Implement in `shorts_engine/stages/visuals.py`**

(a) Imports + registry: add `paper_card`, `broll_frame` to the cards import; `RENDERERS["PAPER_CARD"] = paper_card.render`, `RENDERERS["BROLL"] = broll_frame.render`. Remove `_DEFERRED` (both types now resolve for real).

(b) Seams + helpers:

```python
def _acquire(**kwargs):
    from shorts_engine.sourcing.ladder import acquire
    return acquire(**kwargs)


def _fetch_front_page(url: str, torture: bool):
    from shorts_engine.sourcing.paper_page import fetch_front_page
    return fetch_front_page(url, torture=torture)


_FOCAL_TO_LAYOUT = {"center": "auto", "left": "inset", "right": "inset",
                    "top": "inset", "bottom": "inset"}


def _fallback_of(shot: dict, reason: str):
    fb = shot.get("fallback")
    if not fb or fb.get("type") not in RENDERERS:
        raise EngineError(f"{shot['id']}: {shot['type']} has no renderable fallback")
    return fb["type"], fb["payload"], {"resolved": "fallback", "reason": reason,
                                       "planned_type": shot["type"]}
```

(c) `resolve_shot(shot, ctx=None, post=None)`:

```python
def resolve_shot(shot: dict, ctx=None, post=None) -> tuple[str, dict, dict]:
    stype = shot["type"]
    if stype in RENDERERS and stype not in ("BROLL", "PAPER_CARD"):
        return stype, shot["payload"], {"resolved": "designed"}
    torture = bool(getattr(ctx, "flags", {}).get("torture", False)) if ctx else True
    if stype == "BROLL":
        if ctx is None:
            return _fallback_of(shot, "no_context")
        res = _acquire(wish=shot["payload"].get("wish", ""),
                       narration_span=shot.get("narration_span", ""),
                       workspace=Path(ctx.workspace),
                       post_images=(post or {}).get("images", []),
                       torture=torture)
        if res["image_path"]:
            layout = _FOCAL_TO_LAYOUT.get(res["focal_hint"], "auto")
            return "BROLL", {"image_path": res["image_path"], "layout": layout,
                             "caption": shot["payload"].get("wish", "")}, \
                   {"resolved": "acquired", "acquisition": res["provenance"]}
        return _fallback_of(shot, res["provenance"].get("reason") or "no_acceptance")
    if stype == "PAPER_CARD":
        url = shot["payload"].get("url", "")
        page = _fetch_front_page(url, torture) if url else None
        if page is not None:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.removeprefix("www.")
            marker = shot["payload"].get("marker")
            return "PAPER_CARD", {
                "image_path": str(page),
                "highlight": shot["payload"].get("highlight", ""),
                "citation": f"Source [{marker}] — {domain}",
            }, {"resolved": "acquired"}
        return _fallback_of(shot, "torture_mode" if torture else "paper_fetch_failed")
    raise EngineError(f"{shot['id']}: unknown shot type {stype}")
```

(d) In `run(ctx)`: load `post = json.loads((ws / "post.json").read_text(encoding="utf-8"))` once; call `resolve_shot(shot, ctx, post)`; keep everything else (fade rule, content-pixel check, report) — report entries now include the acquisition provenance when present.

(e) `stages/assemble.py` calls `visuals.resolve_shot(shot)` in its re-render path — the ctx-less call now resolves fallbacks (correct: re-render must not re-run acquisition; the shots dir already has the acquired render, and only within-epsilon copies skip re-render). **Change assemble's re-render to re-use the resolved type recorded in `visuals_report.json`** — read the report, build `{shot_id: (rendered_type, payload)}` from it, and re-render from THAT instead of re-resolving. Add to the report entries in visuals.run: `"payload": payload` (the resolved payload) to make this possible.

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_visuals.py tests/shorts_engine/test_assemble_run.py -v`
(assemble_run's `_ctx` writes no visuals_report.json — assemble's report-reading path must fall back to `resolve_shot(shot)` when the report file is absent, preserving those tests.)

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 11: VERIFY stage part 1 — heuristic + vision gates (`stages/verify.py`)

**Files:**
- Create: `shorts_engine/stages/verify.py`
- Test: `tests/shorts_engine/test_verify_gates.py`

**Interfaces:**
- Consumes: `video_agent.harness.verify_heuristic.verify_heuristic(video_path: str, workspace: str) -> VerifyReport` (fields used: `.passed: bool`, `.checks: dict` — confirm exact attribute names from `video_agent/harness/manifest.py::VerifyReport` at implementation time and pin them in a test); `vision_judge.describe/verify_description` (Task 3); `text_llm.generate_schema_json`; `assemble_report.json` (per-shot `final_duration_s`), `shotlist.json` (narration spans), `visuals_report.json` (rendered types/payloads), `video_short.mp4`.
- Produces (used by Task 12):
  - `shot_timeline(assemble_report: dict) -> list[dict]` — cumulative `[{"id", "start_s", "mid_s", "duration_s"}]` from the report's shot order.
  - `sample_shot_frames(video: Path, timeline: list[dict], out_dir: Path) -> dict[str, Path]` — one ffmpeg frame per shot at `mid_s` → `verify/frame_<id>.png`.
  - `SHOT_VERDICT_SCHEMA = {"match_score": int 0-10, "legible": bool, "issues": [str]}`.
  - `judge_shot_frame(frame: Path, narration_span: str, rendered_type: str, payload: dict) -> dict` — DESCRIBE frame (attach-verified) → text-only verdict against the span + expected on-screen text (payload text/value/quote/labels joined). Describe/verify failure after retries ⇒ `{"ungradeable": True}` — **caller must fail the run, not skip** (F8).
  - `run_gates(ctx) -> dict` — `{"heuristic": {...}, "shots": [{"id", "match_score", "legible", "issues", "frame": str} ...], "failures": [{"id", "kind": "broll_mismatch"|"legibility"|"heuristic_audio"|"heuristic_safezone", ...}]}`. Shot fail thresholds: `match_score < 5` on an `acquired` BROLL ⇒ `broll_mismatch`; `legible == False` ⇒ `legibility`. Heuristic report failures map: audio-RMS/peak → `heuristic_audio`; safe-zone/dark-ribbon → `heuristic_safezone`.
  - Seams: `_heuristic = None`, `_describe = None`, `_verdict_call = None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_verify_gates.py
from __future__ import annotations
import json
from pathlib import Path
import pytest


class TestShotTimeline:
    def test_cumulative_midpoints(self):
        from shorts_engine.stages import verify
        rep = {"shots": [
            {"id": "s00", "final_duration_s": 4.0},
            {"id": "s01", "final_duration_s": 3.0},
            {"id": "s02", "final_duration_s": 5.0},
        ]}
        tl = verify.shot_timeline(rep)
        assert [t["start_s"] for t in tl] == [0.0, 4.0, 7.0]
        assert tl[1]["mid_s"] == 5.5


class TestJudgeShotFrame:
    GOOD = {"description": "a navy slide with a large gold number 92 percent and "
                           "the label nitrate removal in a white serif typeface, "
                           "sharp and clearly legible on screen",
            "visible_text": "92% nitrate removal", "quality_notes": "sharp"}

    def test_happy_path_scores(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        monkeypatch.setattr(verify, "_describe", lambda p: self.GOOD)
        monkeypatch.setattr(verify, "_verdict_call", lambda *a, **k: {
            "match_score": 8, "legible": True, "issues": []})
        out = verify.judge_shot_frame(f, "ninety two percent removal",
                                      "STAT_CARD", {"value": "92", "unit": "%"})
        assert out["match_score"] == 8 and out["legible"] is True

    def test_describe_failure_is_ungradeable_not_a_pass(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        f = tmp_path / "f.png"; f.write_bytes(b"x")
        monkeypatch.setattr(verify, "_describe", lambda p: None)
        out = verify.judge_shot_frame(f, "span", "STAT_CARD", {})
        assert out.get("ungradeable") is True


class TestRunGates:
    def _ws(self, tmp_path):
        ws = tmp_path
        (ws / "assemble_report.json").write_text(json.dumps({"shots": [
            {"id": "s00", "final_duration_s": 4.0}]}), encoding="utf-8")
        (ws / "shotlist.json").write_text(json.dumps({"shots": [
            {"id": "s00", "narration_span": "nitrate is rising",
             "type": "HEADLINE_CARD", "payload": {"text": "t"}}]}), encoding="utf-8")
        (ws / "visuals_report.json").write_text(json.dumps({"shots": [
            {"id": "s00", "rendered_type": "HEADLINE_CARD",
             "payload": {"text": "t"},
             "provenance": {"resolved": "designed"}}]}), encoding="utf-8")
        (ws / "video_short.mp4").write_bytes(b"fake")
        return ws

    def test_all_pass_no_failures(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 9, "legible": True, "issues": []})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        assert out["failures"] == []

    def test_illegible_designed_shot_flagged(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame", lambda *a, **k: {
            "match_score": 7, "legible": False, "issues": ["caption too small"]})
        class Ctx: workspace = ws; flags = {}
        out = verify.run_gates(Ctx())
        assert out["failures"][0]["kind"] == "legibility"

    def test_ungradeable_raises_engine_error(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        from shorts_engine.errors import EngineError
        ws = self._ws(tmp_path)
        class HR: passed = True; checks = {}
        monkeypatch.setattr(verify, "_heuristic", lambda v, w: HR())
        monkeypatch.setattr(verify, "sample_shot_frames",
                            lambda v, tl, d: {"s00": ws / "f.png"})
        monkeypatch.setattr(verify, "judge_shot_frame",
                            lambda *a, **k: {"ungradeable": True})
        class Ctx: workspace = ws; flags = {}
        with pytest.raises(EngineError, match="ungradeable"):
            verify.run_gates(Ctx())
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `shorts_engine/stages/verify.py`** (gates half)

```python
"""Stage 8 — VERIFY: heuristic gate (reused verify_heuristic) + a vision
gate that judges ONE frame per shot from the FINAL video against that shot's
narration span and expected on-screen text. Ungradeable after retries ⇒ the
run FAILS — never skipped (F8). The revise loop (Task 12) fixes what it can
deterministically; everything converges to designed cards."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)

SHOT_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "match_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "legible": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["match_score", "legible", "issues"],
    "additionalProperties": False,
}

_VERDICT_SYSTEM = (
    "You verify a single frame of a technical B2B short against what should "
    "be on screen. match_score: does the frame's content match the narration "
    "and expected text (10 = clearly yes)? legible: is every piece of "
    "on-screen text comfortably readable at phone size (contrast + size)?"
)

# Seams
_heuristic = None
_describe = None
_verdict_call = None


def _resolve():
    heuristic, describe_fn, verdict = _heuristic, _describe, _verdict_call
    if heuristic is None:
        from video_agent.harness.verify_heuristic import verify_heuristic
        heuristic = verify_heuristic
    if describe_fn is None:
        from shorts_engine.llm.vision_judge import describe
        describe_fn = describe
    if verdict is None:
        verdict = text_llm.generate_schema_json
    return heuristic, describe_fn, verdict


def shot_timeline(assemble_report: dict) -> list[dict]:
    out, cursor = [], 0.0
    for s in assemble_report["shots"]:
        d = float(s["final_duration_s"])
        out.append({"id": s["id"], "start_s": round(cursor, 3),
                    "mid_s": round(cursor + d / 2, 3), "duration_s": d})
        cursor += d
    return out


def sample_shot_frames(video: Path, timeline: list[dict],
                       out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, Path] = {}
    for t in timeline:
        png = out_dir / f"frame_{t['id']}.png"
        res = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t['mid_s']:.3f}",
             "-i", str(video), "-frames:v", "1", str(png)],
            capture_output=True, text=True)
        if res.returncode != 0 or not png.exists():
            raise EngineError(f"VERIFY: frame sample failed for {t['id']}")
        frames[t["id"]] = png
    return frames


def _expected_text(payload: dict) -> str:
    parts = [str(payload.get(k, "")) for k in
             ("text", "value", "unit", "label", "quote", "highlight",
              "differentiator", "cta_line", "domain")]
    parts += [str(x) for x in (payload.get("labels") or [])]
    return " | ".join(p for p in parts if p)


def judge_shot_frame(frame: Path, narration_span: str, rendered_type: str,
                     payload: dict) -> dict:
    from shorts_engine.llm.vision_judge import verify_description, DESCRIBE_PROMPT
    _, describe_fn, verdict = _resolve()
    desc = describe_fn(frame)
    if verify_description(desc, DESCRIBE_PROMPT) is not None:
        return {"ungradeable": True}
    prompt = (
        f"SHOT TYPE: {rendered_type}\n"
        f"NARRATION FOR THIS SHOT: {narration_span}\n"
        f"EXPECTED ON-SCREEN TEXT: {_expected_text(payload)}\n\n"
        f"FRAME DESCRIPTION (from a blind viewing):\n{desc['description']}\n"
        f"VISIBLE TEXT SEEN: {desc.get('visible_text', '')}\n\n"
        f"Verdict as JSON."
    )
    return verdict(prompt, _VERDICT_SYSTEM, SHOT_VERDICT_SCHEMA)


def run_gates(ctx) -> dict:
    heuristic, _, _ = _resolve()
    ws = Path(ctx.workspace)
    video = ws / "video_short.mp4"
    hrep = heuristic(str(video), str(ws))
    assemble_report = json.loads((ws / "assemble_report.json").read_text(encoding="utf-8"))
    shotlist = {s["id"]: s for s in json.loads(
        (ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]}
    vis = {s["id"]: s for s in json.loads(
        (ws / "visuals_report.json").read_text(encoding="utf-8"))["shots"]}

    timeline = shot_timeline(assemble_report)
    frames = sample_shot_frames(video, timeline, ws / "verify")

    failures: list[dict] = []
    if not getattr(hrep, "passed", True):
        checks = getattr(hrep, "checks", {}) or {}
        kind = "heuristic_audio" if any(
            "audio" in str(k).lower() or "rms" in str(k).lower()
            for k, v in checks.items() if v is False) else "heuristic_safezone"
        failures.append({"id": "_global", "kind": kind, "checks": str(checks)})

    shots_out = []
    for t in timeline:
        sid = t["id"]
        verdict = judge_shot_frame(
            frames[sid], shotlist[sid].get("narration_span", ""),
            vis[sid]["rendered_type"], vis[sid].get("payload", {}))
        if verdict.get("ungradeable"):
            raise EngineError(
                f"VERIFY: shot {sid} ungradeable after retries — failing the "
                f"run, not skipping (F8)")
        entry = {"id": sid, "frame": str(frames[sid]), **verdict}
        shots_out.append(entry)
        acquired_broll = (vis[sid]["rendered_type"] == "BROLL"
                          and vis[sid]["provenance"].get("resolved") == "acquired")
        if acquired_broll and verdict["match_score"] < 5:
            failures.append({"id": sid, "kind": "broll_mismatch",
                             "score": verdict["match_score"]})
        if not verdict["legible"]:
            failures.append({"id": sid, "kind": "legibility",
                             "issues": verdict["issues"]})
    return {"heuristic": {"passed": getattr(hrep, "passed", True)},
            "shots": shots_out, "failures": failures}
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_verify_gates.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 12: VERIFY part 2 — revise loop + `run()` + contact sheet

**Files:**
- Modify: `shorts_engine/stages/verify.py` (add revise + `run()`)
- Create: `shorts_engine/review/__init__.py`, `shorts_engine/review/contact_sheet.py`
- Test: `tests/shorts_engine/test_verify_revise.py`

**Interfaces:**
- Consumes: Task 11's `run_gates`; `visuals.RENDERERS`; `stages/assemble.py` (re-concat path); `stages/audio.py::run`; `config.VERIFY_MAX_REVISE_CYCLES`, `LEGIBILITY_SHRINK_FACTOR`.
- Produces:
  - `apply_fixes(ctx, failures: list[dict]) -> list[str]` — deterministic fixes, returns descriptions of fixes applied:
    - `broll_mismatch` → swap that shot to its declared fallback in `visuals_report.json` (rendered_type/payload := fallback's), re-render the clip in `shots/`, mark `provenance.resolved = "fallback"`, `reason = "verify_rejected"`.
    - `legibility` → shorten the offending shot's dominant text field (first non-empty of text/label/quote/highlight) to `int(len * LEGIBILITY_SHRINK_FACTOR)` words (min 3), re-render.
    - `heuristic_safezone` → re-burn captions with `MarginV += 40` (assemble re-run with `caption_margin_bump` flag; add that optional flag to assemble's `build_ass` call path: `build_ass(words, out, margin_v=440+bump)` — add the `margin_v: int = 440` parameter to `build_ass`, threading it into the Style line).
    - `heuristic_audio` → re-run `audio.run(ctx)` then assemble.
    - After ANY fix: re-run `assemble.run(ctx)` (cheap; cards re-render fast) so the next gate pass sees the fixed video.
  - `run(ctx) -> {"verify_report": "verify_report.json", "contact_sheet": "contact_sheet.html"}` — gate → if failures: fix, re-gate, up to `VERIFY_MAX_REVISE_CYCLES`; failures remaining after the loop that are ALL `broll_mismatch`-class are impossible (fallback swap removes them deterministically) so any residue ⇒ `EngineError`; writes `verify_report.json` `{cycles, fixes_applied, final: {...gates output}}` and the contact sheet.
  - `contact_sheet.build(ctx, verify_report: dict) -> Path` — self-contained HTML (`contact_sheet.html`): script beats + narration, facts with verbatim quotes, one `<img>` per shot (relative `verify/frame_*.png` paths), per-shot provenance + verdicts, assemble numbers, a "hook strength" note line (user feedback item), link to `video_short.mp4`. No external assets (offline-viewable).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_verify_revise.py
from __future__ import annotations
import json
from pathlib import Path
import pytest


def _ws(tmp_path):
    ws = tmp_path
    (ws / "shots").mkdir()
    (ws / "verify").mkdir()
    (ws / "assemble_report.json").write_text(json.dumps({"shots": [
        {"id": "s00", "final_duration_s": 4.0}]}), encoding="utf-8")
    (ws / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": "s00", "beat": "hook", "type": "BROLL", "duration_s": 4.0,
         "narration_span": "n", "payload": {"wish": "w"},
         "fallback": {"type": "HEADLINE_CARD", "payload": {"text": "Fallback headline"}}}
    ]}), encoding="utf-8")
    (ws / "visuals_report.json").write_text(json.dumps({"shots": [
        {"id": "s00", "beat": "hook", "rendered_type": "BROLL",
         "payload": {"image_path": "x.png", "layout": "auto"},
         "duration_s": 4.0, "fade_in_s": 0.0, "content_pixels": 9000,
         "provenance": {"resolved": "acquired"}}]}), encoding="utf-8")
    (ws / "video_short.mp4").write_bytes(b"fake")
    return ws


class TestApplyFixes:
    def test_broll_mismatch_swaps_to_fallback_and_rerenders(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        rendered = []
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: rendered.append((sid, rtype)))
        class Ctx: workspace = ws; flags = {}
        fixes = verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "broll_mismatch", "score": 3}])
        assert rendered == [("s00", "HEADLINE_CARD")]
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        assert vis["shots"][0]["rendered_type"] == "HEADLINE_CARD"
        assert vis["shots"][0]["provenance"]["reason"] == "verify_rejected"
        assert any("fallback" in f for f in fixes)

    def test_legibility_shortens_dominant_text(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        vis["shots"][0]["rendered_type"] = "HEADLINE_CARD"
        vis["shots"][0]["payload"] = {"text": "one two three four five six seven eight nine ten"}
        (ws / "visuals_report.json").write_text(json.dumps(vis), encoding="utf-8")
        captured = {}
        monkeypatch.setattr(verify, "_render_shot",
                            lambda ctx, sid, rtype, payload, duration: captured.update(payload))
        class Ctx: workspace = ws; flags = {}
        verify.apply_fixes(Ctx(), [{"id": "s00", "kind": "legibility", "issues": []}])
        assert len(captured["text"].split()) == 7  # 10 * 0.7


class TestRunLoop:
    def test_clean_gates_write_report_and_sheet(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        monkeypatch.setattr(verify, "run_gates", lambda ctx: {
            "heuristic": {"passed": True}, "shots": [
                {"id": "s00", "frame": str(ws / "verify" / "f.png"),
                 "match_score": 9, "legible": True, "issues": []}],
            "failures": []})
        class Ctx: workspace = ws; flags = {}
        arts = verify.run(Ctx())
        rep = json.loads((ws / arts["verify_report"]).read_text(encoding="utf-8"))
        assert rep["cycles"] == 1 and rep["fixes_applied"] == []
        assert (ws / arts["contact_sheet"]).exists()
        html = (ws / arts["contact_sheet"]).read_text(encoding="utf-8")
        assert "video_short.mp4" in html and "s00" in html

    def test_failures_fixed_then_pass_within_cycle_budget(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        ws = _ws(tmp_path)
        gates = [
            {"heuristic": {"passed": True}, "shots": [],
             "failures": [{"id": "s00", "kind": "broll_mismatch", "score": 3}]},
            {"heuristic": {"passed": True}, "shots": [], "failures": []},
        ]
        monkeypatch.setattr(verify, "run_gates", lambda ctx: gates.pop(0))
        fixed = []
        monkeypatch.setattr(verify, "apply_fixes",
                            lambda ctx, failures: fixed.append(1) or ["swap"])
        monkeypatch.setattr(verify, "_reassemble", lambda ctx: None)
        class Ctx: workspace = ws; flags = {}
        arts = verify.run(Ctx())
        rep = json.loads((ws / arts["verify_report"]).read_text(encoding="utf-8"))
        assert rep["cycles"] == 2 and fixed == [1]

    def test_residual_failures_after_budget_raise(self, tmp_path, monkeypatch):
        from shorts_engine.stages import verify
        from shorts_engine.errors import EngineError
        ws = _ws(tmp_path)
        bad = {"heuristic": {"passed": True}, "shots": [],
               "failures": [{"id": "s00", "kind": "legibility", "issues": []}]}
        monkeypatch.setattr(verify, "run_gates", lambda ctx: bad)
        monkeypatch.setattr(verify, "apply_fixes", lambda ctx, f: ["shrink"])
        monkeypatch.setattr(verify, "_reassemble", lambda ctx: None)
        class Ctx: workspace = ws; flags = {}
        with pytest.raises(EngineError, match="revise"):
            verify.run(Ctx())
```

- [ ] **Step 2: Run tests to verify they fail** — `apply_fixes`/`run`/seams missing.

- [ ] **Step 3: Implement** — append to `stages/verify.py`:

```python
def _render_shot(ctx, shot_id: str, rtype: str, payload: dict,
                 duration: float) -> None:
    from shorts_engine.stages.visuals import RENDERERS
    out = Path(ctx.workspace) / "shots" / f"shot_{shot_id}.mp4"
    RENDERERS[rtype](payload, duration, out)


def _reassemble(ctx) -> None:
    from shorts_engine.stages import assemble
    assemble.run(ctx)


_TEXT_FIELDS = ("text", "label", "quote", "highlight")


def apply_fixes(ctx, failures: list[dict]) -> list[str]:
    ws = Path(ctx.workspace)
    vis_path = ws / "visuals_report.json"
    vis = json.loads(vis_path.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in vis["shots"]}
    shotlist = {s["id"]: s for s in json.loads(
        (ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]}
    applied: list[str] = []

    for f in failures:
        kind, sid = f["kind"], f.get("id")
        if kind == "broll_mismatch":
            fb = shotlist[sid].get("fallback") or {}
            entry = by_id[sid]
            entry["rendered_type"] = fb["type"]
            entry["payload"] = fb["payload"]
            entry["provenance"] = {"resolved": "fallback",
                                   "reason": "verify_rejected"}
            _render_shot(ctx, sid, fb["type"], fb["payload"], entry["duration_s"])
            applied.append(f"{sid}: swapped to fallback {fb['type']}")
        elif kind == "legibility":
            entry = by_id[sid]
            payload = dict(entry["payload"])
            for field in _TEXT_FIELDS:
                words = str(payload.get(field, "")).split()
                if words:
                    keep = max(3, int(len(words) * config.LEGIBILITY_SHRINK_FACTOR))
                    payload[field] = " ".join(words[:keep])
                    break
            entry["payload"] = payload
            _render_shot(ctx, sid, entry["rendered_type"], payload,
                         entry["duration_s"])
            applied.append(f"{sid}: shortened text for legibility")
        elif kind == "heuristic_safezone":
            ctx.flags["caption_margin_bump"] = \
                int(ctx.flags.get("caption_margin_bump", 0)) + 40
            applied.append("caption margin bumped +40px")
        elif kind == "heuristic_audio":
            from shorts_engine.stages import audio
            audio.run(ctx)
            applied.append("audio stage re-run")
    vis_path.write_text(json.dumps(vis, indent=2), encoding="utf-8")
    return applied


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    all_fixes: list[str] = []
    cycles = 0
    result = None
    for cycle in range(1, config.VERIFY_MAX_REVISE_CYCLES + 2):
        cycles = cycle
        result = run_gates(ctx)
        if not result["failures"]:
            break
        if cycle > config.VERIFY_MAX_REVISE_CYCLES:
            raise EngineError(
                f"VERIFY: {len(result['failures'])} failure(s) remain after "
                f"{config.VERIFY_MAX_REVISE_CYCLES} revise cycles: "
                f"{[f['kind'] for f in result['failures']]}")
        logger.info("verify cycle %d: %d failure(s) — applying deterministic "
                    "fixes", cycle, len(result["failures"]))
        all_fixes += apply_fixes(ctx, result["failures"])
        _reassemble(ctx)

    report = {"cycles": cycles, "fixes_applied": all_fixes, "final": result}
    (ws / "verify_report.json").write_text(json.dumps(report, indent=2),
                                           encoding="utf-8")
    from shorts_engine.review.contact_sheet import build
    sheet = build(ctx, report)
    logger.info("verify: passed in %d cycle(s), %d fix(es)", cycles,
                len(all_fixes))
    return {"verify_report": "verify_report.json",
            "contact_sheet": sheet.name}
```

Also thread `caption_margin_bump` through assemble: `build_ass(words, out_path, margin_v: int = 440)` (parameterize the `440` in the Style line and the header constant), and in `assemble.run`: `margin_v = 440 + int(ctx.flags.get("caption_margin_bump", 0))`, passed to `build_ass`. Update `test_assemble_pure.py::test_build_ass_margins_and_style` only if its MarginV parse needs the parameter's default — the default must remain 440 so the existing assertion holds unchanged.

Create `shorts_engine/review/__init__.py` (docstring only) and `shorts_engine/review/contact_sheet.py`:

```python
"""Human-review contact sheet: everything needed to approve a video on one
offline HTML page (spec Stage 8 hold_for_review)."""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build(ctx, verify_report: dict) -> Path:
    ws = Path(ctx.workspace)
    script = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    facts = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
    assemble = json.loads((ws / "assemble_report.json").read_text(encoding="utf-8"))
    verdicts = {s["id"]: s for s in verify_report["final"]["shots"]}

    rows = []
    for s in vis["shots"]:
        sid = s["id"]
        v = verdicts.get(sid, {})
        frame_rel = f"verify/frame_{sid}.png"
        prov = s.get("provenance", {})
        rows.append(f"""
<tr><td><img src="{frame_rel}" width="180"></td>
<td><b>{sid}</b> · {html.escape(s['rendered_type'])}<br>
resolved: {html.escape(str(prov.get('resolved')))}
{('· ' + html.escape(str(prov.get('reason')))) if prov.get('reason') else ''}<br>
match {v.get('match_score', '—')}/10 · legible: {v.get('legible', '—')}<br>
{html.escape('; '.join(v.get('issues', [])))}</td></tr>""")

    beats_html = "".join(
        f"<li><b>{html.escape(b['beat'])}</b>: {html.escape(b['narration'])}"
        f"<br><i>card: {html.escape(b.get('card_text', ''))}</i></li>"
        for b in script["beats"])
    facts_html = "".join(
        f"<li>[{html.escape(str(f['id']))}] “{html.escape(f['verbatim_quote'])}”"
        f" (= {html.escape(str(f['value']))} {html.escape(str(f.get('unit', '')))})</li>"
        for f in facts.get("facts", []))

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Contact sheet — {html.escape(ctx.manifest.slug if hasattr(ctx, 'manifest') else str(ws.name))}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#0a192f;color:#ccd6f6;
margin:24px}} a{{color:#d4af37}} td{{padding:8px;border-bottom:1px solid #233}}
img{{border:1px solid #345}}</style></head><body>
<h1>Review: <a href="video_short.mp4">video_short.mp4</a></h1>
<p>voice {assemble['voice_total_s']}s · video {assemble['video_duration_s']}s ·
music: {assemble['music_used']} · verify cycles: {verify_report['cycles']} ·
fixes: {html.escape('; '.join(verify_report['fixes_applied']) or 'none')}</p>
<p><b>Hook check (human):</b> would a procurement manager stop scrolling for
beat 1? If not, note it — hook strength is a tracked tuning item.</p>
<h2>Shots</h2><table>{''.join(rows)}</table>
<h2>Script</h2><ol>{beats_html}</ol>
<h2>Facts (verbatim)</h2><ul>{facts_html}</ul>
</body></html>"""
    out = ws / "contact_sheet.html"
    out.write_text(doc, encoding="utf-8")
    return out
```

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_verify_revise.py tests/shorts_engine/test_assemble_pure.py -v`

- [ ] **Step 5: Run the suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 13: PACKAGE + PUBLISH stages + CLI wiring + forbidden-import guard

**Files:**
- Modify: `E:\Projects\HRSU Blog\video_agent\publishers\youtube_packager.py` (light extension: lazy `Storyboard` import)
- Create: `shorts_engine/stages/package.py`, `shorts_engine/stages/publish.py`
- Modify: `shorts_engine/cli.py`
- Test: `tests/shorts_engine/test_package_publish.py`, extend `tests/shorts_engine/test_cli.py`, extend `tests/shorts_engine/test_boundaries.py`

**Interfaces:**
- Consumes: `package_for_youtube(storyboard, blog_record, workspace) -> PublishPackage` (only reads `storyboard.hero_claim`); `publish_to_youtube(package, video_path, workspace, dry_run=False) -> PublishResult`; `post.json` (title/region/category/url), `script.json` (hook card_text → hero_claim), `factsheet.json` (top facts → takeaways), `captions.ass` exists but packager expects SRT — generate `subtitles.srt` from `word_timings.json` via `video_agent.subtitles._chunk_words`-equivalent (reuse `generate_srt`? No — that re-transcribes; instead write a small `_words_to_srt(words, out_path)` in package.py using the existing cue-grouping logic from `assemble.group_words_into_cues`).
- Produces:
  - `youtube_packager.py` change: `from video_agent.storyboard import Storyboard` → guarded `if TYPE_CHECKING:` import; the runtime annotation becomes a string (`storyboard: "Storyboard"`). Root-suite packager tests must stay green.
  - `package.run(ctx) -> {"publish_package": "publish_package.json", "linkedin_caption": "linkedin_caption.txt", "captions_srt": "subtitles.srt"}` — builds hero_claim from the hook beat's `card_text`, `blog_record = {"region", "category", "subcategory": None, "title", "url": ctx.manifest.blog_url}` from post.json; calls `package_for_youtube(SimpleNamespace(hero_claim=...), blog_record, str(ws))`; serializes the PublishPackage fields to `publish_package.json`; writes `linkedin_caption.txt` = hook line + top-3 facts' `claim_summary` bullets (by `procurement_significance` desc) + blog URL.
  - `publish.run(ctx)` — loads `publish_package.json` back into a `PublishPackage` (import from `video_agent.harness.manifest` — allowed; only harness/{runner,rubric,revise_router,verify_vision} are forbidden), calls `publish_to_youtube(..., dry_run=not ctx.flags.get("publish", False))` — **without `--publish` the publish stage runs as dry-run** (validation only, unlisted metadata, no upload). Returns `{"publish_result": "publish_result.json"}`.
  - CLI: `build_stages()` → 10 stages ending `("verify","verified",verify.run), ("package","packaged",package.run), ("publish","published",publish.run)`; `--until` choices + map gain verify/package/publish; new flag `--publish` → `flags["publish"]=True`; **default `until` when neither `--until` nor `--publish` given: `"verified"`** (hold_for_review: the run stops after the contact sheet; the printout names the contact sheet path).
  - Boundary guard: new test walks `sys.modules` after importing every `shorts_engine.stages.*` module and asserts none of the forbidden module names were imported transitively.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_package_publish.py
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


def _ws(tmp_path):
    ws = tmp_path
    (ws / "post.json").write_text(json.dumps({
        "title": "Optimizing Nitrate Removal", "region": "eu",
        "category": "wastewater_treatment"}), encoding="utf-8")
    (ws / "script.json").write_text(json.dumps({"beats": [
        {"beat": "hook", "narration": "n", "card_text": "Nitrate limits tightening",
         "fact_ids": [], "broll_wish": ""}]}), encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({"facts": [
        {"id": "f1", "claim_summary": "dosing window 1.5-3 kg/m3",
         "procurement_significance": 5, "verbatim_quote": "q", "value": "1.5",
         "unit": "kg"},
        {"id": "f2", "claim_summary": "92 percent removal",
         "procurement_significance": 4, "verbatim_quote": "q", "value": "92",
         "unit": "%"},
    ]}), encoding="utf-8")
    (ws / "word_timings.json").write_text(json.dumps([
        {"word": "nitrate", "start": 0.0, "end": 0.4},
        {"word": "limits", "start": 0.4, "end": 0.8}]), encoding="utf-8")
    (ws / "video_short.mp4").write_bytes(b"fake")
    return ws


class Ctx:
    def __init__(self, ws, flags=None):
        self.workspace = ws
        self.flags = flags or {}
        self.manifest = MagicMock(blog_url="https://blog.hrsuindore.com/x.html",
                                  slug="x")


class TestPackage:
    def test_package_writes_all_artifacts(self, tmp_path, monkeypatch):
        from shorts_engine.stages import package
        ws = _ws(tmp_path)
        fake_pkg = MagicMock(title="T", description="D", tags=["a"],
                             category_id="28", privacy_status="unlisted",
                             thumbnail_path=str(ws / "th.jpg"),
                             caption_srt_path=str(ws / "subtitles.srt"))
        monkeypatch.setattr(package, "_package_for_youtube",
                            lambda sb, br, w: fake_pkg)
        arts = package.run(Ctx(ws))
        pkg = json.loads((ws / arts["publish_package"]).read_text(encoding="utf-8"))
        assert pkg["title"] == "T" and pkg["privacy_status"] == "unlisted"
        cap = (ws / arts["linkedin_caption"]).read_text(encoding="utf-8")
        assert "Nitrate limits tightening" in cap
        assert "dosing window" in cap and "hrsuindore.com/x.html" in cap
        srt = (ws / arts["captions_srt"]).read_text(encoding="utf-8")
        assert "-->" in srt and "NITRATE" in srt

    def test_hero_claim_is_hook_card_text(self, tmp_path, monkeypatch):
        from shorts_engine.stages import package
        ws = _ws(tmp_path)
        seen = {}
        monkeypatch.setattr(package, "_package_for_youtube",
                            lambda sb, br, w: (seen.update(h=sb.hero_claim, br=br)
                                               or MagicMock(title="T", description="", tags=[],
                                                            category_id="28", privacy_status="unlisted",
                                                            thumbnail_path=None, caption_srt_path=None)))
        package.run(Ctx(ws))
        assert seen["h"] == "Nitrate limits tightening"
        assert seen["br"]["region"] == "eu"


class TestPublish:
    def _pkg(self, ws):
        (ws / "publish_package.json").write_text(json.dumps({
            "title": "T", "description": "D", "tags": ["a"], "category_id": "28",
            "privacy_status": "unlisted", "thumbnail_path": None,
            "caption_srt_path": None}), encoding="utf-8")

    def test_default_is_dry_run(self, tmp_path, monkeypatch):
        from shorts_engine.stages import publish
        ws = _ws(tmp_path); self._pkg(ws)
        seen = {}
        def fake_pub(package, video_path, workspace, dry_run=False):
            seen["dry_run"] = dry_run
            return MagicMock(video_id="DRY_RUN_1", url="", platform="youtube")
        monkeypatch.setattr(publish, "_publish_to_youtube", fake_pub)
        publish.run(Ctx(ws))
        assert seen["dry_run"] is True

    def test_publish_flag_uploads_for_real(self, tmp_path, monkeypatch):
        from shorts_engine.stages import publish
        ws = _ws(tmp_path); self._pkg(ws)
        seen = {}
        def fake_pub(package, video_path, workspace, dry_run=False):
            seen["dry_run"] = dry_run
            return MagicMock(video_id="abc123", url="https://youtu.be/abc123",
                             platform="youtube")
        monkeypatch.setattr(publish, "_publish_to_youtube", fake_pub)
        arts = publish.run(Ctx(ws, flags={"publish": True}))
        assert seen["dry_run"] is False
        res = json.loads((ws / arts["publish_result"]).read_text(encoding="utf-8"))
        assert res["video_id"] == "abc123"
```

Append to `tests/shorts_engine/test_cli.py`:

```python
class TestPhase3Stages:
    def test_build_stages_has_ten_in_order(self):
        from shorts_engine.cli import build_stages
        names = [s[0] for s in build_stages()]
        assert names == ["ingest", "facts", "script", "shotlist", "audio",
                         "visuals", "assemble", "verify", "package", "publish"]
        statuses = [s[1] for s in build_stages()]
        assert statuses[-3:] == ["verified", "packaged", "published"]

    def test_default_until_is_verified_hold_for_review(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False, flags=None):
            captured.update(until=until, flags=flags)
            class M: status, artifacts, run_id = "verified", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        assert cli.main(["https://x.html"]) == 0
        assert captured["until"] == "verified"

    def test_publish_flag_runs_to_published(self, monkeypatch):
        import shorts_engine.cli as cli
        captured = {}
        def fake_run(blog_url, stages, workspace_root, until=None, resume=False, flags=None):
            captured.update(until=until, flags=flags)
            class M: status, artifacts, run_id = "published", {}, "t"
            return M()
        monkeypatch.setattr(cli.runner, "run", fake_run)
        assert cli.main(["https://x.html", "--publish"]) == 0
        assert captured["until"] == "published"
        assert captured["flags"]["publish"] is True
```

Append to `tests/shorts_engine/test_boundaries.py`:

```python
class TestForbiddenTransitiveImports:
    FORBIDDEN = [
        "video_agent.orchestrator", "video_agent.storyboard",
        "video_agent.script_builder", "video_agent.composer",
        "video_agent.run_stage", "video_agent.harness.runner",
        "video_agent.harness.rubric", "video_agent.harness.revise_router",
        "video_agent.harness.verify_vision", "video_agent.sources.scoring",
    ]

    def test_no_stage_module_imports_forbidden_modules(self):
        """Spec §3.2 CI guard: importing every shorts_engine stage must not
        pull any superseded video_agent module into sys.modules."""
        import importlib, sys
        for mod in ("ingest", "facts", "script", "shotlist", "audio",
                    "visuals", "assemble", "verify", "package", "publish"):
            importlib.import_module(f"shorts_engine.stages.{mod}")
        loaded = set(sys.modules)
        hits = [f for f in self.FORBIDDEN if f in loaded]
        assert hits == [], f"forbidden modules imported: {hits}"
```

- [ ] **Step 2: Run tests to verify they fail** — stages missing; boundary test fails via packager's module-level `storyboard` import once package.py exists (and `test_build_stages_has_ten_in_order` fails now).

- [ ] **Step 3: Implement**

(a) `video_agent/publishers/youtube_packager.py` — light extension:

```python
# replace:  from video_agent.storyboard import Storyboard
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from video_agent.storyboard import Storyboard
```
and change the signature line to `def package_for_youtube(storyboard: "Storyboard", ...)`. (Behavior identical; the runtime import — and its transitive pull of the superseded module tree — disappears. Root packager tests must pass unchanged.)

(b) `shorts_engine/stages/package.py`:

```python
"""Stage 9 — PACKAGE: YouTube metadata via the reused packager + the
linkedin caption file + an SRT for caption upload."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger(__name__)


def _package_for_youtube(sb, blog_record: dict, workspace: str):
    from video_agent.publishers.youtube_packager import package_for_youtube
    return package_for_youtube(sb, blog_record, workspace)


def _words_to_srt(words: list[dict], out_path: Path) -> Path:
    from shorts_engine.stages.assemble import group_words_into_cues
    def ts(s: float) -> str:
        ms = int(round(s * 1000))
        hh, rem = divmod(ms, 3_600_000)
        mm, rem = divmod(rem, 60_000)
        ss, mss = divmod(rem, 1000)
        return f"{hh:02d}:{mm:02d}:{ss:02d},{mss:03d}"
    lines = []
    for i, cue in enumerate(group_words_into_cues(words), start=1):
        lines.append(f"{i}\n{ts(cue['start'])} --> {ts(cue['end'])}\n{cue['text']}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    script = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    words = json.loads((ws / "word_timings.json").read_text(encoding="utf-8"))

    hook = next(b for b in script["beats"] if b["beat"] == "hook")
    hero_claim = hook.get("card_text") or hook["narration"]
    blog_url = getattr(ctx.manifest, "blog_url", "")
    blog_record = {"region": post.get("region"), "category": post.get("category"),
                   "subcategory": post.get("subcategory"),
                   "title": post.get("title"), "url": blog_url}

    _words_to_srt(words, ws / "subtitles.srt")
    pkg = _package_for_youtube(SimpleNamespace(hero_claim=hero_claim),
                               blog_record, str(ws))
    (ws / "publish_package.json").write_text(json.dumps({
        "title": pkg.title, "description": pkg.description, "tags": pkg.tags,
        "category_id": pkg.category_id, "privacy_status": pkg.privacy_status,
        "thumbnail_path": str(pkg.thumbnail_path) if pkg.thumbnail_path else None,
        "caption_srt_path": str(pkg.caption_srt_path) if pkg.caption_srt_path else None,
    }, indent=2), encoding="utf-8")

    top = sorted(factsheet.get("facts", []),
                 key=lambda f: -int(f.get("procurement_significance", 0)))[:3]
    caption = "\n".join(
        [hero_claim, ""]
        + [f"- {f['claim_summary']}" for f in top]
        + ["", f"Full technical guide: {blog_url}"])
    (ws / "linkedin_caption.txt").write_text(caption, encoding="utf-8")
    logger.info("package: metadata + linkedin caption written")
    return {"publish_package": "publish_package.json",
            "linkedin_caption": "linkedin_caption.txt",
            "captions_srt": "subtitles.srt"}
```

(c) `shorts_engine/stages/publish.py`:

```python
"""Stage 10 — PUBLISH: resumable YouTube upload via the reused publisher.
WITHOUT --publish this runs as dry_run (metadata validation only) — the
default flow holds at the contact sheet for human review."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _publish_to_youtube(package, video_path: str, workspace: str,
                        dry_run: bool = False):
    from video_agent.publishers.youtube_publisher import publish_to_youtube
    return publish_to_youtube(package, video_path, workspace, dry_run=dry_run)


def run(ctx) -> dict[str, str]:
    from video_agent.harness.manifest import PublishPackage
    ws = Path(ctx.workspace)
    raw = json.loads((ws / "publish_package.json").read_text(encoding="utf-8"))
    pkg = PublishPackage(**raw)
    dry_run = not bool(ctx.flags.get("publish", False))
    result = _publish_to_youtube(pkg, str(ws / "video_short.mp4"), str(ws),
                                 dry_run=dry_run)
    (ws / "publish_result.json").write_text(json.dumps({
        "platform": getattr(result, "platform", "youtube"),
        "video_id": getattr(result, "video_id", ""),
        "url": getattr(result, "url", ""),
        "dry_run": dry_run,
    }, indent=2), encoding="utf-8")
    logger.info("publish: %s (dry_run=%s)",
                getattr(result, "video_id", "?"), dry_run)
    return {"publish_result": "publish_result.json"}
```

Implementer note: confirm `PublishPackage`'s exact dataclass fields in `video_agent/harness/manifest.py` — if it has extra required fields, serialize those too in package.py rather than passing partial kwargs.

(d) `shorts_engine/cli.py`: import the three new stages; extend `build_stages()`; `--until` choices += `["verify", "package", "publish"]` with map `{"verify": "verified", "package": "packaged", "publish": "published"}`; add `--publish` flag (`flags["publish"] = True`); default resolution:

```python
    if args.until:
        until_status = until_map[args.until]
    elif args.publish:
        until_status = "published"
    else:
        until_status = "verified"   # hold_for_review: stop at the contact sheet
```
and after a successful default run print `  Review: <workspace>\contact_sheet.html` (ASCII only).

- [ ] **Step 4: Run tests to verify they pass** → `python -m pytest tests/shorts_engine/test_package_publish.py tests/shorts_engine/test_cli.py tests/shorts_engine/test_boundaries.py -v`

- [ ] **Step 5: Run BOTH suites** — workspace suite green; ROOT suite from `E:\Projects HRSU Blog` green (packager change is root-visible).

---

### Task 14: Golden integration test — fixture → verified video (Plan-2 debt + Phase 5–6 mocks)

**Files:**
- Test: `tests/shorts_engine/test_integration_phase3.py`
- Create if absent: `_shorts_engine_impl/pytest.ini` (register `slow` marker)

**Interfaces:** consumes everything. Mocks ONLY the nondeterministic boundaries: `text_llm.generate_schema_json` (canned facts/script/critique/match/verdicts routed by schema), `audio._synthesize`, `audio._transcribe`, `vision_judge._describe_call`, `verify._describe`, `visuals._acquire` (returns a real temp image → exercises the REAL broll_frame render path), `paper_page._fetch_bytes`/`_screenshot`. Everything else — ingest isolation, gates, shotlist BROLL emission, card rendering, ffmpeg assembly, verify frame sampling, revise loop wiring, package SRT/caption — runs REAL.

- [ ] **Step 1: Write the test**

```python
# tests/shorts_engine/test_integration_phase3.py
"""Golden pipeline: fixture HTML -> verified video with acquisition, verify
and package running against mocked model boundaries only. Also settles the
Plan-2 Task-14 debt (that golden test was never created)."""
from __future__ import annotations
import json
import re
from pathlib import Path
import pytest
from PIL import Image
from pydub import AudioSegment

FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"
URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

FACTS_RESPONSE = {"facts": [
    {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
     "value": "1.5 to 3", "unit": "kg/m3", "claim_summary": "dosing window",
     "tags": ["spec"], "procurement_significance": 5, "citation_marker": None},
]}
# 62 words total at 1.7 w/s ≈ 36.5s — inside [60, 85] words / [35, 50] s.
BEATS = [
    {"beat": "hook", "narration": "Your effluent nitrate is creeping toward "
     "the discharge limit again.", "fact_ids": [],
     "card_text": "Nitrate limits are tightening",
     "broll_wish": "wastewater aeration basin"},
    {"beat": "stakes", "narration": "European plants hold the line at 1.5 to "
     "3 kg per cubic meter.", "fact_ids": ["f1"],
     "card_text": "The dosing window that works", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Calcium nitrate feeds denitrifying "
     "bacteria, converting nitrate into harmless nitrogen gas inside the "
     "treatment train without a retrofit.", "fact_ids": ["f1"],
     "card_text": "Bacteria do the removal", "broll_wish": "",
     "diagram_labels": ["Effluent in", "Dosing", "Denitrifying bacteria",
                        "Nitrogen out"]},
    {"beat": "proof", "narration": "The published dosing window of 1.5 to 3 "
     "kilograms per cubic meter comes from the cited guide.",
     "fact_ids": ["f1"], "card_text": "A proven dosing window",
     "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies high purity powder with batch "
     "level QC. Read the guide at hrsuindore dot com.",
     "fact_ids": ["b_purity"], "card_text": "Get the dosing guide",
     "broll_wish": ""},
]
CRITIQUE = {"actionable_score": 9, "coherence_score": 9, "hrsu_reason_score": 9,
            "revise_notes": ""}
GOOD_DESC = {"description": "A branded navy slide with clearly legible serif "
             "text describing calcium nitrate dosing for wastewater treatment, "
             "sharp typography with a gold accent underline.",
             "visible_text": "", "quality_notes": "sharp"}


def _llm_router(prompt, system, schema, **kw):
    props = schema.get("properties", {})
    if "facts" in props:
        return FACTS_RESPONSE
    if "beats" in props:
        return {"beats": BEATS}
    if "match_score" in props:   # verify shot verdict
        return {"match_score": 9, "legible": True, "issues": []}
    if "score" in props:         # sourcing judge match
        return {"score": 8, "reason": "matches", "focal_hint": "center"}
    return CRITIQUE


def _fake_synth(segments, output_path, region, voice_override=None):
    ms = int(len(segments[0].text.split()) / 1.7 * 1000)
    AudioSegment.silent(duration=max(ms, 300)).export(
        str(output_path), format="mp3", bitrate="128k")
    return {"audio_path": Path(output_path), "duration_s": ms / 1000,
            "voice_used": "test", "engine_used": "fake", "fell_back": False}


def _fake_transcribe(audio_path, narration_hint=None, multilingual=False):
    words = (narration_hint or "x").split()
    step = 1 / 1.7
    return [{"word": w, "start": round(i * step, 3),
             "end": round(i * step + step * 0.85, 3)}
            for i, w in enumerate(words)]


@pytest.mark.slow
class TestGoldenPipelinePhase3:
    def test_fixture_to_verified_video(self, tmp_path, monkeypatch):
        import shorts_engine.stages.facts as facts_stage
        import shorts_engine.stages.script as script_stage
        from shorts_engine.stages import audio as audio_stage
        from shorts_engine.stages import visuals as visuals_stage
        from shorts_engine.stages import verify as verify_stage
        from shorts_engine.llm import vision_judge

        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(verify_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(audio_stage, "_synthesize", _fake_synth)
        monkeypatch.setattr(audio_stage, "_transcribe", _fake_transcribe)
        monkeypatch.setattr(vision_judge, "_describe_call", lambda *a, **k: GOOD_DESC)
        monkeypatch.setattr(verify_stage, "_describe", lambda p: GOOD_DESC)

        broll_img = tmp_path / "acq.png"
        Image.new("RGB", (1600, 900), (70, 70, 70)).save(broll_img)
        monkeypatch.setattr(visuals_stage, "_acquire", lambda **kw: {
            "image_path": str(broll_img), "focal_hint": "center",
            "provenance": {"tiers": [{"tier": "own"}], "reason": None}})

        from shorts_engine import runner, config
        from shorts_engine.cli import build_stages

        html = FIXTURE.read_text(encoding="utf-8")
        manifest = runner.run(URL, build_stages(), workspace_root=tmp_path,
                              until="verified",
                              flags={"html_override": html})
        assert manifest.status == "verified"
        ws = Path(manifest.workspace)

        # never-unverified survived
        script_doc = json.loads((ws / "script.json").read_text(encoding="utf-8"))
        for b in script_doc["beats"]:
            for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", b["narration"]):
                assert tok in {"1.5", "3"}, f"untraced numeric {tok}"

        # duration law on the FINAL (post-verify) video
        from shorts_engine.cards import encoder
        voice = encoder.probe_duration(ws / "voiceover.mp3")
        video = encoder.probe_duration(ws / "video_short.mp4")
        assert video >= voice + config.AUDIO_COMPLETENESS_MARGIN_S
        assert abs(video - (voice + config.END_CARD_HOLD_S)) <= 0.35

        # acquisition actually happened: hook shot 1 rendered as real BROLL
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        rendered = {s["id"]: s for s in vis["shots"]}
        assert any(s["rendered_type"] == "BROLL"
                   and s["provenance"]["resolved"] == "acquired"
                   for s in vis["shots"])
        assert all(s["content_pixels"] >= config.MIN_CONTENT_PIXELS
                   for s in vis["shots"])

        # verify artifacts
        vrep = json.loads((ws / "verify_report.json").read_text(encoding="utf-8"))
        assert vrep["final"]["failures"] == []
        assert (ws / "contact_sheet.html").exists()
```

- [ ] **Step 2: Register the slow marker** — `_shorts_engine_impl/pytest.ini` (create if absent):

```ini
[pytest]
markers =
    slow: renders real video; multi-minute
```

- [ ] **Step 3: Run it** → `python -m pytest tests/shorts_engine/test_integration_phase3.py -v`
Expected: PASS in ~2–4 min (renders ~40s of 1080×1920 video + verify frame sampling). If the canned BEATS trip a gate, retune the narration word counts to the [60, 85]-word aggregate and per-beat integer bounds (hook [3,8], stakes [6,12], mechanism [11,24], proof [9,20], cta [9,16]) — do NOT weaken any gate.

- [ ] **Step 4: Run the full suite** → `python -m pytest tests/shorts_engine -q` all green.

---

### Task 15: Live smoke run + progress report — the Plan-3 ship gate

**Files:**
- Create: `docs/superpowers/progress/2026-07-08-shorts-engine-plan3-live.md` (real project root)

**Interfaces:** consumes the CLI end-to-end with real LLM + vision (`gemma4:31b-cloud`), real edge-tts, real Whisper, real ffmpeg, real network acquisition (Playwright/pypdfium2/APIs).

- [ ] **Step 1: Preflight** — from `_shorts_engine_impl`: `ollama list` shows `gemma4:31b-cloud`; `python -c "import pypdfium2, playwright"` OK; note which API keys exist (Pexels/Pixabay/Unsplash — absent keys just thin the api tier).

- [ ] **Step 2: Library index (one-time)** — if `asset_library/{factory,footage}` contains images: `python -c "from shorts_engine.sourcing.library_index import build_index; print(len(build_index()))"`. Empty/missing dirs are fine (tier 1 contributes nothing).

- [ ] **Step 3: The live run (hold-for-review default — no upload)**

```
python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --workspace-root output_live
```
Expected: exit 0, status `verified`, workspace contains `video_short.mp4`, `verify_report.json`, `contact_sheet.html`, `publish_package`-less (package/publish not reached by default), acquisition provenance inside `visuals_report.json`. This post has 10 `paper` citations (arXiv PDFs → pypdfium2 path; PubMed → Playwright path) — expect a real PAPER_CARD on the proof beat.

- [ ] **Step 4: Human review via the contact sheet** — open `contact_sheet.html`; check every acquired asset looks right (subject, no watermark), the PAPER_CARD page-1 is readable with the gold sweep on its title, captions clear of the bottom 420px, and answer the hook-strength prompt. If an acquired asset is wrong, that's a judge-calibration data point for the report — the deterministic fix is manual: delete it from `output/_sourcing_cache`/workspace and re-run from scratch (see Step 5's note — `--resume` is not usable yet).

- [ ] **Step 5: Dry-run package+publish** — **`--resume` is a stub in `shorts_engine/runner.py` (the `if resume:` branch never binds `manifest`, guaranteeing `UnboundLocalError` on any invocation) — this is pre-existing Plan-2 code, out of scope for Plan 3 to fix as a side-quest here.** Run to `--until publish` fresh instead of resuming: `python -m shorts_engine <same url> --until publish --workspace-root output_live` (a new run, not a resume of Step 3's workspace; no `--publish` ⇒ dry-run upload). This re-does the LLM/audio/acquisition work from Step 3, but `PAPER_CACHE_DIR`/`_sourcing_cache` keep the paper-fetch and previously-accepted-image costs low. Verify `publish_package.json` title/description/tags read sensibly and `linkedin_caption.txt` has hook + 3 takeaways + blog link. (Implementing real resume — workspace discovery by slug/run-id + manifest reload — is a follow-up task, not part of this plan.)

- [ ] **Step 6: Write the progress report** — test totals (workspace + root), the live command(s), model tier, per-tier acquisition stats from provenance (candidates seen / gate rejections / judge scores), PAPER_CARD path used (pdf vs screenshot), verify cycles + fixes applied, human contact-sheet verdicts, and carried items (music bed `asset_library/music/eu.mp3` still absent; hook-strength/critique tuning; per-voice `WORDS_PER_SECOND` calibration if a non-`eu` region is used; real `--publish` upload deferred until 3 consecutive human-approved videos per spec §10).

---

## Self-Review (performed while writing)

- **Spec coverage (Phases 5–7):** ladder tiers+gates+budgets ✓ (T1/T4/T5/T6), describe-then-match judge + attach-verification (F3) ✓ (T3), vision transport check + parser duplication fix ✓ (T2), Openverse adapter ✓ (T4), library index ✓ (T5), `paper_page.py` PDF/Playwright + cache ✓ (T7), PAPER_CARD "receipts" look ✓ (T8), BROLL emission with required fallback ✓ (T9), VISUALS dispatch + provenance + focal_hint→layout ✓ (T10), VERIFY heuristic+vision gates + ungradeable⇒failed (F8) ✓ (T11), revise loop (max 2, deterministic, converges to designed) + contact sheet ✓ (T12), PACKAGE/PUBLISH + linkedin_caption + hold_for_review default + CI import guard ✓ (T13), golden test (Plan-2 debt) ✓ (T14), live smoke + report ✓ (T15). Deliberately deferred, named in T15: real `--publish` upload (spec §10 wants 3 human-approved videos first), §3.2 module deletion pass (spec: separate session), video-file library indexing (images only here).
- **Placeholder scan:** every code step carries complete code; the two "confirm at implementation time" notes (source class names in T4, `PublishPackage`/`VerifyReport` field names in T11/T13) name exactly what to look up and where — they are lookups, not designs.
- **Type consistency:** `judge()` verdict dict (`accepted_score/description/focal_hint/reject_reason`) consumed identically in T6/T10; `acquire()` provenance shape consumed by T10's report and T15's stats; renderer contract `frame_at/render(payload, duration, out_path, fade_in_s)` reused from Plan 2 for T8; `resolve_shot(shot, ctx=None, post=None)` keeps 1-arg Plan-2 calls valid (T10 + assemble note); `group_words_into_cues` reused by T13's SRT writer; seams all follow the Plan-2 `audio.py` late-binding pattern.
- **Known risks flagged:** packager's `Storyboard` runtime import (T13 removes it — root suite must stay green); assemble's ctx-less `resolve_shot` re-render path (T10e switches it to the visuals report with a fallback when absent); tilt overhang vs. safe-zone test (T8 note: shrink `_INSET_FRAC`, don't widen tolerance); scrape-tier judge threshold 7 keeps unlicensed sources rare by design.
