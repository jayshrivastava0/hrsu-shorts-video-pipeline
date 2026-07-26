# Plan 3 — Task 15: Live smoke run + progress report

Plan: `docs/superpowers/plans/2026-07-08-shorts-engine-plan-3-acquisition-verify-publish.md`
Ledger: `_shorts_engine_impl/.superpowers/sdd/progress.md` (Tasks 1-14 complete going in)

## Result: PASS — live run verified, dry-run package+publish succeeded, one real bug found and fixed

## Test totals

- Workspace (`_shorts_engine_impl`): **511 passed, 0 failed** (full suite, both before and after the fix below)
- Root (`HRSU Blog`): `pytest tests/video_agent` — **423 passed, 5 failed**. The 5 failures
  (`test_sourcer_picks_best_candidate`, `test_storyboarder_creates_one_scene_per_beat`,
  `test_strategist_populates_hero_arc_supporting`, `test_composer_v2_produces_valid_mp4`,
  `test_stock_visual_no_manifest_falls_back_to_text_card`) match the pre-existing,
  unrelated baseline already noted in Task 2's review — not touched by this session.
  Root-level `pytest -q` (no path) errors out at collection (97 errors: a stray
  non-UTF8 `test_result.txt` file plus a `tests/blog/` package with no `__init__.py`
  under this rootdir) — pre-existing repo-layout issue, not a regression, not
  investigated further (out of scope for Task 15).

## Live commands run

```
python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --workspace-root output_live
python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --until publish --workspace-root output_live
```

Model tier: `cloud` (`gemma4:31b-cloud` for both text and vision judging), real edge-tts,
real Whisper, real ffmpeg, real network acquisition (Playwright/pypdfium2/Unsplash API).

It took **12 attempts** across the two steps to get one clean run of each (see
"Environmental flakiness hit" below) — none of the failed attempts were silently
retried without understanding why first; each was diagnosed before retrying.

## Step 3 result (`run-16a46d58`) — verified

- **Verify:** 2 revise cycles, 1 fix applied (`s07: shortened quote for legibility`),
  final heuristic passed, 0 unresolved failures.
- **Shot match scores:** s00 10, s01 10, s02 10, s03 4, s04 6, s05 10, s06 7, s07 6, s08 10.
  s03/s04 are the same 3-node flow DIAGRAM sampled at different points of its
  reveal animation — s03's low score is the judge correctly noting the 2nd/3rd
  node aren't on screen yet at that frame, not a rendering defect (s05, the full
  reveal, scores 10).
- **Acquisition (shot s06, BROLL, "proof" beat):** own=0 seen, blog=0 seen,
  api(Unsplash)=4 seen/4 rejected (2× score_2, 1× score_0, 1× score_0), scrape=4
  seen/3 rejected (score_4, score_4, score_3) → **accepted** at scrape tier, score
  7, from `municipal.ovivowater.com` (a real sludge-thickener technical diagram).
  Google Images CAPTCHA blocked that tier every run (see carried items) but the
  ladder's lower tiers covered it as designed.
- **PAPER_CARD:** not used this run. The shotlist planner chose BROLL+QUOTE for
  the proof beat instead; this post's citation list (30 items) included only one
  arXiv PDF among mostly ECHA/EPA/WHO HTML sources, unlike the plan's speculative
  note expecting "10 paper citations." Not a defect — the planner had a real
  choice to make and made one; no PAPER_CARD path (pdf vs screenshot) was
  exercised on this particular live run.

## Step 4: human contact-sheet review — 1 new finding

Reviewed all 9 sampled frames directly (`verify/frame_s00..s08.png`) plus
`contact_sheet.html`. Hook card, stat card, diagrams, acquired b-roll, and CTA
card all render cleanly and match brand (navy/gold, HRSU logo present, legible).

**Finding — caption/card-element overlap (missed by the automated vision gate):**
On 3 of 9 shots, the burned-in bottom caption box overlaps a card-native bottom
element and clips it:
- s01 (STAT_CARD): "MG PER LITER" caption clips the "Source [4] — echa..." pill.
- s07 (QUOTE_CARD): "FIVE-YEAR LIFECYCLE." caption clips the "Source [6]..." pill.
- s08 (LOGO_CTA): the "HRSUINDORE" caption box crowds directly against the
  `hrsuindore.com` domain text above it.

The automated legibility gate scored all three `legible: true` because each
text chunk is independently readable — it has no check for two elements
occupying the same region. This is a real layout gap between the caption
system and the card renderers' own bottom-anchored elements (source pills,
CTA domain text). **Not fixed in this session** — it's a renderer/caption
layout coordination issue, not a quick one-line fix, and Task 15's charter is
smoke-run + report, not a redesign. Flagged as a follow-up task.

**Hook check (human):** "European Wastewater Treatment" / "NITRATE REMOVAL" —
functional but generic; matches the tracked hook-strength critique item from
Task 14's review, not a new problem.

## Step 5 result — dry-run package+publish (`run-85aa407a`)

`publish_package.json` title/description/tags read sensibly; `linkedin_caption.txt`
has the hook line + 3 top facts + blog link, matches spec. `publish_result.json`
confirms `dry_run: true`, `video_id: DRY_RUN_...` — **no real upload occurred**,
hold-for-review default is intact.

### Real bug found and fixed: PACKAGE stage crash — `'str' object has no attribute 'stat'`

First Step-5 attempt (after Step 3 succeeded) crashed in `package.run()`:
```
File "shorts_engine/stages/package.py", line 48, in run
  pkg = _package_for_youtube(SimpleNamespace(hero_claim=hero_claim), ...)
File "video_agent/publishers/youtube_packager.py", line 475, in package_for_youtube
  hero_stat = hero_claim.stat if hero_claim else None
AttributeError: 'str' object has no attribute 'stat'
```
Root cause: `video_agent.publishers.youtube_packager.package_for_youtube` expects
`storyboard.hero_claim` to be a `video_agent.storyboard.HeroClaim` dataclass
(`stat`, `claim_text`, `source_quote`), but Task 13's `shorts_engine/stages/package.py`
passed the hook's raw narration/card-text **string** into that slot. This is
the first time PACKAGE ever ran against the real `package_for_youtube` function —
the existing unit tests (`test_hero_claim_is_hook_card_text`) monkeypatch
`_package_for_youtube` entirely, so the interface mismatch was invisible to
511 passing tests until this live run.

**Fix** (`shorts_engine/stages/package.py`): wrap the hook text and the
top-ranked fact's `claim_summary` into a real `HeroClaim(stat=..., claim_text=...)`
before calling `_package_for_youtube`, importing `HeroClaim` lazily inside
`run()` — `video_agent.storyboard` is on the CI forbidden-transitive-import
list (`tests/shorts_engine/test_boundaries.py`), so a module-level import
would have broken that guard; the existing `_package_for_youtube`'s own lazy
import of `youtube_packager` already established this pattern.

TDD: added `test_hero_claim_is_a_real_heroclaim_object` (asserts `isinstance(...,
HeroClaim)`, `.claim_text`, `.stat`), watched it fail for the right reason,
applied the minimal fix, confirmed green. Folded the old
`test_hero_claim_is_hook_card_text` into the new test (same scenario, corrected
assertion — the old one encoded the buggy contract). Full workspace suite
re-run afterward: still 511 passed, 0 failed.

## Environmental flakiness hit (documented, not code bugs)

Across the ~12 attempts: one blog-fetch SSL blip (`SSLEOFError`, resolved on
retry, confirmed via direct `requests.get` loop — 5/5 clean), one accidental
process kill (my own tooling mistake, mid-session), and several rounds of
`ollama.com` cloud instability (502 Bad Gateway, `429 too many concurrent
requests`, and one client-side DNS blip the user separately observed and
resolved) that hit both the sourcing-stage watermark checks and the verify
stage's vision judge (`F8`'s ungradeable-after-retries correctly failed loud
rather than silently passing, exactly as designed). Retried each time only
after confirming connectivity was actually restored (`ollama run` smoke test),
not blindly.

## Carried items (unchanged from Task 14, still true)

1. Music bed absent — `asset_library/music/eu.mp3` does not exist; mix path is
   implemented and tested, dropping a file in enables it.
2. Hook-strength / script "interesting-ness": tracked critique item, no gate
   change in this plan.
3. Per-voice `WORDS_PER_SECOND` calibration needed if a non-`eu` region is used.
4. Real `--publish` upload deferred until 3 consecutive human-approved videos
   (spec §10) — this session only ran dry-run publish.
5. `--resume` is still an unimplemented stub in `runner.py` (pre-existing
   Plan-2 code) — every attempt in this session ran fresh, per Task 15's plan.
6. Google Images CAPTCHA blocks that acquisition sub-tier on this network;
   `GOOGLE_IMAGES_INTERACTIVE=1` exists for manual solving but requires a live
   terminal able to relay a keypress into the subprocess, which this session's
   tooling could not do — not attempted again after one blocked try.

## New follow-up (this session)

7. **Caption/card-element overlap** (Step 4 finding above) — source-citation
   pills (STAT_CARD, QUOTE_CARD) and the CTA domain text (LOGO_CTA) can be
   visually clipped or crowded by the burned-in bottom caption when both
   occupy the same vertical band. The automated legibility gate does not
   catch this because it judges each text element in isolation. Needs a
   renderer-level fix (e.g. caption-aware vertical offset for card bottom
   elements, or excluding the caption band from where those elements render).

## Verdict

Ready to move past the smoke-gate for this post. The PACKAGE-stage bug was a
real, previously-untested code path (first live PACKAGE execution ever) and is
now fixed and regression-tested. The caption-overlap finding is real but
cosmetic/non-blocking (never-blank and never-unverified both held); recommend
fixing it before the next live batch rather than before this report.
