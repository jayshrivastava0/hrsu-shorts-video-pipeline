# Video Harness Phase 3 — Complete

**Date:** 2026-06-18  
**Status:** DONE — 334/334 tests passing, production grading verified

---

## What Phase 3 Delivered

A Vision-LLM artifact gate between VERIFY and PACKAGE:

1. Extracts one mid-frame JPEG per rendered scene clip
2. Grades each frame against narration + rubric via `gemma4:31b-cloud` (0–10 per criterion)
3. Decision bands:
   - All scenes >= 7.0 → **vision_verified** (pipeline continues to PACKAGE)
   - Any scene < 5.0 → **actionable** (revise loop, max 2 cycles)
   - Otherwise → **hold_for_review** (written to `review_queue.json`)
4. Ungradeable scene (LLM failure) → always **hold_for_review**

---

## Real-World Results (optimizing-early-age-strength)

All 5 scenes graded and passed:
| Scene | Overall | Notes |
|-------|---------|-------|
| 0 | 10.0 | Perfect |
| 1 | 9.4 | Minor visual_match (7/10) |
| 2 | 8.6 | visual_mismatch defect (generic stock) |
| 3 | 9.6 | — |
| 4 | 9.2 | — |

Final status: `vision_verified`, `passed: true, hold: false`

---

## All Bugs Fixed

| Bug | Fix |
|-----|-----|
| Cloud model 404 on `/api/generate images` | `_grade_scene_cli()` uses `subprocess ollama run` |
| `UnicodeDecodeError` on Windows | `capture_output=True` (bytes) + `decode('utf-8', errors='replace')` |
| Resume from `hold_for_review` restarted pipeline | Added explicit hold→verified reset in `runner.py` |
| Terminal cursor-rewrite ANSI sequences corrupted JSON | `_TERM_WRAP_RE` regex removes before ANSI strip |
| Embedded `\n` in JSON string values | `.replace("\n", " ")` before `json.loads()` |
| Various Unicode in logs (cp1252 Windows) | Replaced `★→—` with ASCII equivalents in 6 files |
| `runner.generate_srt` not patchable by tests | Added module-level lazy wrapper in runner.py |

---

## What's Now Wired

```
HarnessRunner.run(publish=False)
  PLAN -> GENERATE -> RENDER -> VERIFY -> VISION -> (stop, status=vision_verified)

HarnessRunner.run(publish=True)
  PLAN -> GENERATE -> RENDER -> VERIFY -> VISION -> PACKAGE -> PUBLISH

Resume from hold_for_review:
  HarnessRunner.run(resume=True) -> resets to "verified" -> re-runs VISION only
```

`scripts/make_video.py` updated:
- Added `--resume` flag
- Fixed success check to `vision_verified` (was `verified`, which is never terminal now)
- Shows `hold_for_review` message with pointer to `review_queue.json`

---

## Test Counts

| Suite | Tests |
|-------|-------|
| harness/ | 48 |
| full video_agent/ | 334 |

All passing.

---

## What's Next (Phase 4 candidates)

- **YouTube publish**: `_phase_publish` is wired but untested end-to-end (needs OAuth token)
- **Batch runner**: `scripts/batch_videos.py` to process multiple blog URLs
- **Analytics**: Post-publish view tracking per video
- **A/B thumbnails**: Multiple thumbnail variants per video
