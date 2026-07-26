# Video Harness Phase 3 — Progress Report

**Date:** 2026-06-17  
**Status:** 🟡 In Progress — one last bug to fix before all 5 scenes grade cleanly

---

## What Phase 3 Is

A Vision-LLM artifact gate that:
1. Extracts one mid-frame JPEG per rendered scene clip
2. Sends each frame + narration + rubric to `gemma4:31b-cloud` for grading (0–10 per criterion)
3. Decision bands:
   - All scenes ≥ 7.0 → **pass** (pipeline continues to PACKAGE)
   - Any scene < 5.0 → **actionable** (revise loop, max 2 cycles)
   - Otherwise → **hold** (written to `review_queue.json` for operator)
4. Ungradeable scene (LLM failure) → always **hold**

---

## What Was Already Done (before this session)

All Phase 3 code was pre-implemented and all 486 tests passed:
- `video_agent/harness/verify_vision.py` — vision grader
- `video_agent/harness/rubric.py` — rubric schema and write/load
- `video_agent/harness/revise_router.py` — route defects, apply re-source/re-render actions
- `video_agent/harness/manifest.py` — `SceneGrade`, `VisionReport`, `hold_for_review` status
- `video_agent/config.py` — vision knobs (model, timeout, thresholds)
- `video_agent/harness/runner.py` — VISION phase wired in between VERIFY and PACKAGE

---

## What We Did This Session

### Problem 1: `/api/generate` with images field returns 404 for cloud model

`gemma4:31b-cloud` is cloud-routed by Ollama and the image bytes path on the HTTP API doesn't work. `ollama run` CLI does work for multimodal.

**Fix:** Added `_grade_scene_cli()` that calls `ollama run gemma4:31b-cloud PROMPT image.jpg` via `subprocess.run`. Tests still use the mock `OllamaClient` path via `client=` injection.

### Problem 2: `text=True` caused `UnicodeDecodeError` on Windows

**Fix:** Use `capture_output=True` (bytes) and decode with `utf-8, errors=replace`.

### Problem 3: Resume from `hold_for_review` restarted entire pipeline

`hold_for_review` is not in `_STATUS_ORDER`, so `_status_index()` returns -1, making all phase conditions true and re-running from PLAN.

**Fix:** Added explicit resume handling in `runner.py`:
```python
if manifest.status == "hold_for_review" and resume and manifest.video_path:
    manifest.status = "verified"
    manifest.vision = None
```

### Problem 4: Terminal cursor-rewrite sequences corrupted JSON output

`ollama run` streams output with cursor-back (`\x1b[ND`) + erase-to-EOL (`\x1b[K`) + newline sequences. When captured as raw bytes, these aren't rendered — they stay in the text, corrupting the JSON:

**Pattern A:** `\x1b[ND\x1b[K\n` (cursor-back + erase + newline) → embedded literal `\n` inside JSON string values → `json.loads` fails.

**Pattern B:** `\x1b[K\n` (erase only + newline) → same result.

`TERM=dumb` and `stdin=DEVNULL` were tried — neither eliminated the sequences.

**Fix applied (partially working):**
```python
# In verify_vision.py
_TERM_WRAP_RE = re.compile(r"\x1b\[(?:\d+D\x1b\[)?K\r?\n")
```
Applied BEFORE ANSI stripping to remove the wrap sequences. This fixed scenes 1–4.

**Fix applied (pending test):** Also replaced literal `\n` in the extracted JSON block with space before `json.loads`:
```python
candidate = clean[j:i + 1].replace("\n", " ")
last_obj = json.loads(candidate)
```
This handles any remaining embedded newlines in string values. **This change is written to the file but not yet tested.**

### Problem 5: Various Unicode encoding errors (cp1252 on Windows)

Several files had `★`, `→`, `—` in log messages. Fixed to ASCII equivalents in:
- `video_agent/sources/blog_references.py`
- `video_agent/agents/sourcer.py`
- `video_agent/agents/narration_polisher.py`
- `video_agent/visual_engine/footage_library.py`
- `video_agent/visual_engine/factory_broll.py`
- `video_agent/orchestrator.py`

---

## Current State

Last dry-run (before pausing):
- Scene 0: ✅ 9.4 (with debug logging)
- Scene 1: ✅ 9.2
- Scene 2: ✅ 9.4
- Scene 3: ✅ 8.4 (visual_mismatch defect found)
- Scene 4: ❌ malformed payload — literal `\n` in detail string

The `\n` normalization fix has been written to `verify_vision.py` but **not yet run**.

Final pipeline status: `hold_for_review` (because ungradeable scene 4, and some scores in 5–7 range).

---

## What To Do Next

### 1. Run the dry-run to confirm scene 4 now grades
```bash
python -c "
import sys, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
from video_agent.harness.runner import HarnessRunner
m = HarnessRunner.run(
    blog_url='https://blog.hrsuindore.com/2026/02/optimizing-early-age-strength.html',
    workspace='output/videos/optimizing-early-age-strength-html',
    publish=False,
    resume=True,
)
print('Final status:', m.status)
"
```

Expected: all 5 scenes grade, final status is `hold_for_review` (some scenes have `visual_mismatch` defects with overall scores 3–9, landing in the uncertain band). This is correct behaviour — the vision gate is working, it's just identifying real quality issues (generic stock images not matching narration).

### 2. Verify tests still pass
```bash
python -m pytest tests/video_agent/harness/ -v
```

### 3. Consider rubric tuning (optional)
Many scenes get `visual_mismatch` because stock imagery doesn't perfectly illustrate the narration (e.g., "generic concrete imagery for Bavaria trial content"). This is intentional strict grading. If the overall scores are consistently 7–8, you may want to:
- Lower `VISION_PASS_MIN` from 7.0 to 6.5
- Or accept `hold_for_review` as the normal outcome and review manually

### 4. Add `--resume` flag to `scripts/make_video.py` (optional)
Currently `make_video.py` hard-codes `resume=False`. Add a `--resume` flag so the CLI can resume from `hold_for_review` without writing a custom inline script each time.

---

## Key Files Modified This Session

| File | Change |
|------|--------|
| `video_agent/harness/verify_vision.py` | Added `_grade_scene_cli()`, `_build_prompt()`, updated `_parse_grade()` with ANSI stripping + terminal wrap fix + `\n` normalization |
| `video_agent/harness/runner.py` | Resume from `hold_for_review` logic |
| `video_agent/sources/blog_references.py` | Unicode log fix |
| `video_agent/agents/sourcer.py` | Unicode log fix |
| `video_agent/agents/narration_polisher.py` | Unicode log fix |
| `video_agent/visual_engine/footage_library.py` | Unicode log fix |
| `video_agent/visual_engine/factory_broll.py` | Unicode log fix |
| `video_agent/orchestrator.py` | Unicode log fix |

---

## Architecture Note

Production grading flow:
```
HarnessRunner.run() [resume=True]
  → status: hold_for_review → resets to "verified" 
  → VISION phase
    → grade_video(storyboard, workspace, client=None)
      → _grade_scene_cli() per frame
        → subprocess: ollama run gemma4:31b-cloud PROMPT scene_XX.jpg
        → stdout captured as bytes → decoded utf-8
        → _TERM_WRAP_RE removes cursor-wrap sequences
        → _ANSI_RE strips remaining ANSI
        → "...done thinking." splits thinking vs answer
        → last balanced {...} extracted + \n normalized
        → json.loads → SceneGrade
      → VisionReport(passed, hold, scenes)
    → if hold → status = "hold_for_review", write review_queue.json
    → if passed → status = "vision_verified", continue to PACKAGE
```
