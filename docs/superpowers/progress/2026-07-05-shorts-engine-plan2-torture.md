# Shorts Engine — Plan 2 Torture Run Report (Task 15)

**Date:** 2026-07-08 (torture debugging spanned 2026-07-05 → 07-08)
**Ship gate:** All-designed 35–50s portrait video with sound, zero web assets.
**Result: PASSED** — clean end-to-end video produced on run #17, exit code 0.

## Final artifact

- **Video:** `_shorts_engine_impl/output_torture/run-29d20692/video_short.mp4` (11.4 MB)
- An earlier complete video (`run-c93e8f4b`) exists but carries a mojibake em-dash on its LOGO_CTA card (encoding bug, since fixed) — superseded by run-29d20692.

## Command used

```
python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --until assemble --torture --workspace-root output_torture
```

**Model tier:** cloud (`gemma4:31b-cloud`) · edge-tts `en-GB-RyanNeural` · faster-whisper `base.en` · real ffmpeg.

## Test totals

- Workspace suite: **418 passed, 0 failed** (`python -m pytest tests/shorts_engine -q`)
- Root suite: verified no regressions after `video_agent/subtitles.py` extension (Task 9).

## Assemble numbers (run-29d20692)

| Metric | Value | Check |
|---|---|---|
| Voice total | 38.64 s | — |
| Video duration | 40.23 s | ✅ ≥ voice+1.4 · within ±0.35 of voice+1.5 · inside [35, 50] |
| Script words | 60 | inside [60, 85] at 1.7 w/s |
| Shots | 9 (all `designed`, zero fallbacks/web) | ✅ torture criterion |
| Never-blank | min 9,820 content px (floor 500) | ✅ |
| Audio stream | present (AAC) | ✅ |
| Music | none (no `asset_library/music/eu.mp3`) — graceful voiceover-only | expected |
| Reflow | deltas −0.41…+2.57 s; within-epsilon shots copied, rest re-rendered; CTA absorbed residual (9.04 s < 10 s cap) | ✅ as designed |

## Frame spot-checks (sampled mid-frames, verified by inspection)

- HEADLINE hook: serif headline, gold accent word, underline sweep — intentional. ✅
- DIAGRAM flow: rounded nodes, gold arrow, progressive reveal staging. ✅
- LOGO_CTA: real logo, differentiator, CTA line **with correct em dash**, gold domain + underline. ✅

## Human verification (Task 15 Step 3) — PENDING watch-through

Watch `video_short.mp4` end-to-end and confirm: (1) no blank moment; (2) every spoken number findable in the blog post; (3) captions never enter the bottom 420 px; (4) end card holds ~1.5 s after voice stops; (5) every card looks intentional; (6) one actionable takeaway for a procurement manager.

## Root-cause fixes made during torture (17 runs, 12 fixes)

1. `text_llm.generate_schema_json` never communicated **or enforced** its schema → bare-list crash in FACTS. Now folded into system msg + `jsonschema.validate` + retry.
2. Schema-validation retries resent identical prompts → now echo the specific `ValidationError` into the next attempt.
3. `SCRIPT_SCHEMA.diagram_labels` `minItems:2` rejected the writer's benign empty list on non-mechanism beats → relaxed (shotlist enforces 2–4 where it matters).
4. Writer prompt never said the differentiator id goes **in `fact_ids`** → model invented fields 3 different ways. Now explicit with an example.
5. No aggregate duration gate: per-beat-legal scripts could sum far under the video floor, failing in SHOTLIST with no retry path → new `gate_total_duration` in SCRIPT with exact word-deficit + per-beat-headroom messages.
6. `pack_phrases` silently lost duration on long unpunctuated sentences → now subdivides by word count.
7. Critique-triggered rewrite was single-shot → now gate-retried like the initial write.
8. `LLM_MAX_RETRIES` 3 → 5.
9. Shotlist total check failed on per-shot rounding (34.99 vs 35.0, displayed as the impossible "35.0s outside [35.0, 50.0]") → epsilon + honest 2-decimal formatting.
10. `{:.0f}` gate bounds produced paradoxes ("19 words outside [8, 19]") → ceil/floor integer-feasible bounds in errors **and** prompt.
11. **`WORDS_PER_SECOND` 2.6 → 1.7** (the architectural fix): measured 95 words → 53.7 s real voice (1.77 w/s on technical vocabulary). The old constant demanded ≥91-word scripts that would render ~57 s videos; the model's "failing" 82–89-word drafts had been right all along. `AUDIO_DURATION_TOLERANCE` 0.15 → 0.65 (reflow reconciles against real audio; the estimate is a sanity band, not a contract).
12. Windows cp1252: CLI `print("✓ …")` crashed **after** successful assembly (exit 1 on success!) → ASCII markers; `brand.py` missing `encoding="utf-8"` burned mojibake into the end card → fixed + regression tests.

## Carried to Plan 3

- BROLL acquisition ladder + PAPER_CARD renderer (fallbacks currently render as designed cards — indistinguishable, per spec).
- VERIFY loop: the missing Generator-Evaluator layer (frame-sampling 31B judge) — highest-value next build now that videos exist.
- PACKAGE/PUBLISH.
- Music bed: add `asset_library/music/eu.mp3` (mix path is implemented and tested; asset absent).
- **Debt:** Plan 2 Task 14's golden integration test (`test_integration_phase2.py`) was marked complete but never created — still owed.
- Writer-model telemetry: at 2.6 w/s calibration the model needed the full retry budget; at 1.7 it passed SCRIPT quickly. Watch whether other regions/voices need per-voice rate calibration.
