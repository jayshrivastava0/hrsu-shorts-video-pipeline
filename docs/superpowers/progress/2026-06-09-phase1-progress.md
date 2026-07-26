# Video Harness Phase 1 — Implementation Progress Report

**Date:** 2026-06-09  
**Execution Method:** Subagent-Driven Development  
**Goal:** Implement publish path + harness skeleton for YouTube Shorts  

---

## Summary

| Component | Status | Details |
|-----------|--------|---------|
| **Task 1: Config knobs** | ✅ COMPLETE | 14 config values appended to `video_agent/config.py` |
| **Task 2: RunManifest** | 🔄 IN REVIEW | Code done, spec ✅, code quality review flagged 3 minor issues |
| **Task 3: Verify gate** | ⏳ PENDING | Not started |
| **Task 4: Packager** | ⏳ PENDING | Not started |
| **Task 5: Publisher** | ⏳ PENDING | Not started |
| **Task 6: HarnessRunner** | ⏳ PENDING | Not started |
| **Task 7: Entry points** | ⏳ PENDING | Not started |
| **Task 8: Full suite** | ⏳ PENDING | Not started |

**Overall:** 1 of 8 complete, 1 in review loop, 6 queued for execution.

---

## Task 1: Config knobs ✅ COMPLETE

**Files Modified:**
- `video_agent/config.py` (appended lines 234–253)

**What was added:**
- **Verification thresholds (6 knobs):**
  - `VERIFY_AUDIO_RMS_FLOOR = 250.0` — silence detection floor
  - `VERIFY_AUDIO_PEAK_CEIL = 32500` — clipping detection ceiling
  - `VERIFY_FRAME_SAMPLES = 5` — frames sampled for visual checks
  - `VERIFY_DARK_RIBBON_STRIP_PX = 120` — dark-ribbon strip height (pixels)
  - `VERIFY_DARK_RIBBON_LUMA_MAX = 24` — dark-ribbon luma threshold
  - `VERIFY_SAFEZONE_MARGIN_FRAC = 0.06` — caption safe-zone margin (6%)

- **YouTube publishing config (8 knobs):**
  - `YOUTUBE_UPLOAD_SCOPES` — OAuth scopes for `youtube.upload` + `youtube.force-ssl`
  - `YOUTUBE_CLIENT_SECRETS` — path to `client_secrets.json` (reuse Blogger app)
  - `YOUTUBE_TOKEN_PATH` — separate `youtube_token.json` (don't clobber Blogger token)
  - `YOUTUBE_CATEGORY_ID = "28"` — Science & Technology category
  - `YOUTUBE_DEFAULT_PRIVACY = "unlisted"` — Phase 1 default (never public)
  - `YOUTUBE_TITLE_MAX = 100` — title character limit
  - `YOUTUBE_DESC_MAX = 4900` — description limit (API hard limit 5000)

**Verification:**
- ✅ Imports work: `python -c "import video_agent.config as c; print(c.YOUTUBE_DEFAULT_PRIVACY, c.VERIFY_AUDIO_RMS_FLOOR)"` → `unlisted 250.0`
- ✅ Only appended (no file rewrites)

**Reviews:**
- ✅ **Spec compliance:** All 14 config values match the plan exactly. No gaps, no extras.

---

## Task 2: RunManifest (State subsystem) 🔄 IN REVIEW

**Files Created:**
- `video_agent/harness/manifest.py` — 148 lines, full implementation
- `tests/video_agent/harness/test_manifest.py` — 187 lines, 3 tests
- `video_agent/harness/__init__.py` — empty package marker
- `tests/video_agent/harness/__init__.py` — empty package marker

**What was implemented:**

### Dataclasses (5 total)
1. **`RunStatus`** — Literal enum with 8 states: `init`, `planned`, `generated`, `rendered`, `verified`, `packaged`, `published`, `failed`
2. **`VerifyReport`** — verification results: `passed` (bool), `checks` (dict), `defects` (list)
3. **`PublishPackage`** — YouTube metadata: title, description, tags, category_id, thumbnail_path, caption_srt_path, privacy_status
4. **`PublishResult`** — upload result: platform, video_id, url, visibility, uploaded_at
5. **`RunManifest`** — durable state container:
   - Required: version, run_id, blog_url, slug, status, workspace
   - Optional: storyboard_path, video_path, srt_path, voice_path, verify, package, publish, attempts, last_error, created_at, updated_at

### Functions (3 total)
1. **`new_manifest(blog_url, slug, workspace)`** — creates fresh manifest
   - Status: `"init"` (critical for runner idempotency)
   - Run ID: 12-char hex UUID
   - Timestamps: ISO format with Z suffix
2. **`save_manifest(m, path)`** — JSON serialization via `dataclasses.asdict()`
   - Auto-updates `updated_at` before save
3. **`load_manifest(path)`** — JSON deserialization
   - Reconstructs nested dataclasses conditionally (handles None fields)

### Tests (3 total, all passing ✅)
- **`test_new_manifest_defaults()`** — fresh manifest has `status="init"`, no optional fields set
- **`test_roundtrip_with_nested(tmp_path)`** — full roundtrip (save/load) with all nested objects populated
- **`test_roundtrip_all_none(tmp_path)`** — roundtrip with all optional fields None

**Test results:**
```
tests/video_agent/harness/test_manifest.py::test_new_manifest_defaults PASSED
tests/video_agent/harness/test_manifest.py::test_roundtrip_with_nested PASSED
tests/video_agent/harness/test_manifest.py::test_roundtrip_all_none PASSED
====== 3 passed in 0.37s ======
```

**Reviews:**

### ✅ Spec Compliance: APPROVED
All requirements met, nothing extra:
- ✅ All 5 dataclasses present with correct fields
- ✅ All 3 functions implemented correctly
- ✅ `new_manifest()` returns `status="init"` (essential for runner resumability)
- ✅ Timestamps in ISO format with Z suffix
- ✅ Nested dataclass reconstruction handles None fields
- ✅ All 3 tests pass and cover the main paths

### ⚠️ Code Quality: 3 MINOR ISSUES FOUND

**Issue 1: Redundant code in `save_manifest()` (lines 117–118)**
- **Problem:** `save_manifest()` has a comment "Convert enum/Literal status to string (already is string, but be explicit)" followed by `data["status"] = m.status`. This is redundant — `asdict(m)` already converts the status correctly.
- **Severity:** Low (redundant but harmless)
- **Fix:** Delete lines 117–118 entirely

**Issue 2: Missing error handling in `load_manifest()` (lines 124–147)**
- **Problem:** No try/except around JSON parsing or dataclass reconstruction. If the JSON is malformed or missing required fields, the error message is unclear (raw json.JSONDecodeError or stack trace).
- **Severity:** Low–Medium (optional but recommended for production code)
- **Fix:** Wrap JSON load and dataclass construction in try/except with clear error messages:
  ```python
  try:
      data = json.loads(path.read_text(encoding="utf-8"))
  except json.JSONDecodeError as e:
      raise ValueError(f"Invalid JSON in manifest at {path}: {e}") from e
  
  try:
      # ... nested reconstruction ...
      return RunManifest(...)
  except (KeyError, TypeError) as e:
      raise ValueError(f"Invalid/missing fields in manifest: {e}") from e
  ```

**Issue 3: Hardcoded version string (line 93)**
- **Problem:** `version="1.0"` is hardcoded in `new_manifest()`. For maintainability, should be a module-level constant.
- **Severity:** Low (nice-to-have, not critical)
- **Fix:** Add at the top of the file: `MANIFEST_VERSION = "1.0"`, then use in `new_manifest()`

**Strengths:**
- ✅ Excellent dataclass patterns (proper use of `field(default_factory=...)` for mutable defaults)
- ✅ Clean separation of concerns
- ✅ Type hints consistent and correct
- ✅ Timestamp handling is solid
- ✅ Tests are clear and well-structured

**Next step:** Implementer will fix these 3 issues, re-run tests (should still pass), and report completion.

---

## Pending Tasks (Not yet started)

### Task 3: Heuristic verification gate
- **Deliverables:** `video_agent/harness/verify_heuristic.py`, `tests/video_agent/harness/test_verify_heuristic.py`
- **What:** Deterministic artifact checks (no LLM) on rendered MP4
  - ffprobe: duration, streams, resolution, filesize
  - Audio: RMS level (silence detection) + peak (clipping detection)
  - Visual: dark-ribbon scan (bottom strip luma check), caption safe-zone OCR
- **Test fixtures:** ffmpeg-generated MP4s (good, too-short, silent, dark-ribbon)

### Task 4: YouTube packager
- **Deliverables:** `video_agent/publishers/youtube_packager.py`, `tests/video_agent/publishers/test_youtube_packager.py`
- **What:** Build `PublishPackage` from Storyboard
  - Title ≤ 100 chars, keyword-front-loaded
  - Description with CTA link + hashtags
  - Tags, category_id, thumbnail, captions
  - LLM-assisted copy (optional), wrapped in deterministic validation

### Task 5: YouTube publisher
- **Deliverables:** `video_agent/publishers/youtube_publisher.py`, `tests/video_agent/publishers/test_youtube_publisher.py`
- **What:** Upload to YouTube Data API v3
  - Lazy OAuth (`_build_service()`)
  - `videos.insert` resumable upload
  - `privacyStatus="unlisted"` in Phase 1
  - `thumbnails.set`, `captions.insert`
  - Separate `youtube_token.json` (don't clobber Blogger token)
  - `--dry-run` support

### Task 6: HarnessRunner
- **Deliverables:** `video_agent/harness/runner.py`, `tests/video_agent/harness/test_runner.py`
- **What:** Deterministic phase state-machine
  - Phases: PLAN → GENERATE → RENDER → VERIFY → PACKAGE → PUBLISH
  - Manifest checkpointed after each phase
  - Idempotent (skip already-done phases on resume)
  - Reuses existing generator (`orchestrator.build_storyboard`, `compose_short_v2`) unchanged
  - `publish=False` stops after VERIFY (for `make_video.py` dev mode)

### Task 7: Entry points
- **Deliverables:** New `scripts/publish_video.py`, refactored `scripts/make_video.py`
- **What:**
  - `publish_video.py` — full harness (PLAN → PUBLISH), `--dry-run`, `--resume` flags
  - `make_video.py` — now calls `HarnessRunner.run(url, publish=False)` (render+verify only)

### Task 8: Full suite + e2e
- **Deliverables:** All tests passing, smoke-test on real blog
- **What:**
  - `pytest tests/ -x -q` — full suite passes
  - Dry-run on real blog URL: `python scripts/publish_video.py <url> --dry-run`
  - Verify `run_manifest.json` structure, `verify.passed`, `package` fields

---

## Folder Structure Created ✅

```
E:\Projects\HRSU Blog\
├── video_agent/
│   ├── harness/
│   │   ├── __init__.py               ✅ Created
│   │   ├── manifest.py               ✅ Created
│   │   ├── verify_heuristic.py       ⏳ Pending
│   │   └── runner.py                 ⏳ Pending
│   └── publishers/
│       ├── __init__.py               ✅ Existing
│       ├── youtube_packager.py       ⏳ Pending
│       └── youtube_publisher.py      ⏳ Pending
├── tests/
│   └── video_agent/
│       ├── harness/
│       │   ├── __init__.py           ✅ Created
│       │   ├── test_manifest.py      ✅ Created (3/3 tests pass)
│       │   ├── test_verify_heuristic.py  ⏳ Pending
│       │   └── test_runner.py        ⏳ Pending
│       └── publishers/
│           ├── __init__.py           ✅ Created
│           ├── test_youtube_packager.py ⏳ Pending
│           └── test_youtube_publisher.py ⏳ Pending
└── scripts/
    ├── make_video.py                 🔄 To be refactored
    └── publish_video.py              ⏳ To be created
```

---

## Key Decisions Locked In

1. **Status enum includes "init" sentinel** — allows fresh manifests to skip PLAN on resume
2. **Separate youtube_token.json** — doesn't clobber Blogger OAuth token from `client_secrets.json`
3. **Lazy OAuth in publishers** — google libs imported inside functions so tests can mock without triggering auth
4. **Manifest checkpointing after every phase** — enables resumable runs with `--resume` flag
5. **No-model heuristic gate** — Phase 1 verify is deterministic (ffprobe, audio RMS, ribbon scan, OCR), no LLM calls
6. **Reuse existing generator as-is** — `orchestrator.build_storyboard()` and `compose_short_v2()` unchanged
7. **Idempotent phase execution** — phases are skipped on resume if already completed
8. **publish=False stops after VERIFY** — allows `make_video.py` to render+verify without uploading

---

## How to Continue

**Option A: Resume subagent-driven execution**
- Implementer fixes Task 2's 3 minor issues
- I continue with Tasks 3–8 using fresh subagents per task
- Automatic two-stage review (spec compliance, then code quality) after each
- Fully autonomous until Task 8 complete

**Option B: Pause for manual review**
- You review `video_agent/harness/manifest.py` in the IDE
- Decide whether to fix the 3 minor issues or skip them
- I continue with remaining tasks

**Option C: Make manual edits**
- You edit manifest.py directly in the IDE (fix the 3 issues)
- I continue with Tasks 3–8

---

**Next Action:** Which option would you prefer?

**Estimated time to completion** (full automation, Option A): ~4–6 hours  
**Current time invested:** ~1.5 hours (including brainstorming, design, planning, Task 1, Task 2 implementation + reviews)
