# HyperFrames Visual Pipeline — Design (Migration Phase 3)

**Date:** 2026-08-23
**Status:** Approved by user, pending spec review
**Supersedes:** the brief Phase 3 sketch in
`docs/superpowers/specs/2026-08-23-deepseek-harness-hyperframes-migration-design.md`'s
"Migration phases" section — this document is the real design for that phase.

## Context

Phases 1-2 (already shipped) gave the DeepSeek Harness agent the ability to drive the existing
`shorts_engine` Python pipeline stage-by-stage via tools, with the orchestration swap proven
end-to-end. The `visuals` and `assembled` stages, however, still use the *original* renderer:
seven hand-coded PIL card types (`shorts_engine/cards/{headline_card, stat_card, diagram_card,
quote_card, logo_cta_card, paper_card, broll_frame}.py`) plus a hand-tuned ffmpeg pipeline
(`shorts_engine/stages/assemble.py`) that burns ASS captions, overlays the brand logo, draws an
animated progress bar, and mixes narration audio.

This phase replaces both with [HyperFrames](https://hyperframes.heygen.com) (HTML/CSS/GSAP
compositions, rendered via `npx hyperframes render`), per the original migration spec's intent.

## Decisions made in brainstorming (binding)

1. **Freeform authoring, not rigid templates.** A `kimi-k2.7-code` subagent (code-specialized
   cloud model on Ollama) writes each shot's HyperFrames composition HTML/CSS/GSAP directly,
   rather than filling variable slots in a fixed template library. Quality is enforced by the
   existing never-blank/never-unverified vision-judge gate *after* rendering, not by constraining
   what the model can write.
2. **Scoped write access, not arbitrary filesystem/shell access.** The subagent's tools are
   narrow: write files under `hyperframes_scenes/` only, and invoke the HyperFrames render CLI.
   It cannot touch other parts of the repo or run arbitrary shell commands.
3. **Full replacement, including assembly.** HyperFrames' native timeline/audio/caption system
   replaces the ffmpeg-based `assemble.py` pipeline too — one coherent rendering system, not a
   split between per-shot HyperFrames and whole-video ffmpeg.
4. **Two-model split via the harness's subagent system.** The main orchestrator stays on
   `gemma4:31b-cloud` (tool sequencing, invariant enforcement, retry decisions). Scene *authoring*
   specifically delegates to a `kimi-k2.7-code` subagent, spawned per-shot via `dsh-subagent`
   (confirmed present in the harness: `dsh-subagent`, `dsh-subagent-spawn-in-process`,
   `dsh-tool-subagent`), not a session-wide model switch.

## Architecture

```
stage_visuals (existing Python stage, minimal changes)
  for each shot in shotlist:
    if BROLL or PAPER_CARD: run the EXISTING acquisition ladder (shorts_engine/sourcing/ladder.py,
      shorts_engine/sourcing/paper_page.py) — UNCHANGED, this phase does not touch acquisition,
      only what consumes its output. Acquired asset path (or "no verified match") becomes part of
      the shot brief handed to the authoring subagent.
    else: shot brief = { type, payload } straight from the shotlist (same shape as today's
      RENDERERS dict input — HEADLINE_CARD's payload, STAT_CARD's payload, etc.)

harness (new, this phase)
  tool: author_visual_scene(shot_brief, workspace) -> { scene_html_path, render_status }
    spawns a kimi-k2.7-code subagent, scoped tools:
      - write_scene_file(shot_id, html) -> writes hyperframes_scenes/<workspace_id>/<shot_id>.html
      - render_scene(shot_id) -> shells `npx hyperframes render` for that file, returns mp4 path
    subagent receives the shot brief + this phase's authored composition conventions (see below)
    as its system prompt/persona, and iterates write→render→(self-check) up to 2 attempts before
    returning whatever it has — matching the existing harness persona's "don't retry blindly more
    than twice" convention from Phase 1.

  never-blank / never-unverified enforcement (orchestrator-level, unchanged invariant):
    orchestrator calls the EXISTING vision-judge (same describe-then-match check already used for
    acquired b-roll) against the actual rendered mp4's sampled frame(s). On failure: retry
    author_visual_scene with the judge's rejection reason appended to the shot brief, up to 2
    retries total (matching the persona's existing "don't retry blindly more than twice"
    convention); on exhaustion: fall back to a single hand-authored generic HyperFrames template
    (the one remaining pre-built template this phase needs — the never-blank safety net).

stage_assemble (rewritten this phase)
  Instead of ffmpeg concat + ASS burn + logo overlay + progress-bar draw:
  one top-level HyperFrames composition per run, referencing each shot's rendered scene as a
  nested composition (HyperFrames' own `data-composition-src` nesting, confirmed real in the
  HTML schema reference) in sequence, with HyperFrames' native caption/audio/track features
  carrying narration audio, word-level captions (from the existing word-timing data
  `audio` stage already produces), brand logo, and progress bar. Rendered once via
  `npx hyperframes render` to the final mp4.
```

## What does NOT change

- `shorts_engine/sourcing/ladder.py` and `paper_page.py` (acquisition) — untouched.
- `shorts_engine/stages/facts.py`, `script.py`, `shotlist.py`, `audio.py` — untouched; they still
  produce the same shot briefs and word-timing data this phase consumes.
- The `RunManifest`/`STATUS_ORDER` state model — unchanged, same as Phases 1-2's constraint.
- The `stage_verify` stage's overall responsibility (run quality gates) — unchanged; only *what*
  it's checking pixels from changes (HyperFrames output instead of the old renderer's output).
- Phase 4 (`publish` gating, 3-consecutive-approved-dry-runs) is untouched and not started.

## What's retired

- `shorts_engine/cards/{headline_card, stat_card, diagram_card, quote_card, logo_cta_card,
  paper_card, broll_frame, encoder}.py` — the PIL-based renderers and their ffmpeg encoder. Not
  deleted in this phase's first task (kept as a fallback reference / rollback path until the
  HyperFrames path is proven live), but no longer called by `stage_visuals`.
- The ffmpeg-based `_final_mux`/`build_ass`/`reflow` logic in `assemble.py` — replaced by the
  HyperFrames top-level composition. `reflow`'s *duration-law arithmetic* (voice + END_CARD_HOLD_S,
  never `-shortest`) is a real invariant worth preserving conceptually even though the mechanism
  changes — the new assembly composition's total duration must still satisfy it.

## Composition authoring conventions (the subagent's persona)

The `kimi-k2.7-code` subagent needs a persona document (same pattern as Phase 1's harness
persona) covering: HyperFrames' `data-*` timing attribute contract, the GSAP animation contract
(paused timeline, registered on `window.__timelines`, no wall-clock/unseeded-randomness — both
confirmed real constraints from the HTML schema reference), this project's brand facts
(`brand_facts.yaml` — gold/navy palette, logo, banned claims) so generated scenes stay on-brand
without needing a human to hardcode colors into a template, and the shot-brief schema it will
receive. This persona is a deliverable of this phase's first task, not an afterthought.

## Testing approach

- Unit tests for the new tools (`write_scene_file`'s directory-scoping enforcement — reject a
  path outside `hyperframes_scenes/`; `render_scene`'s subprocess wrapping) — same pattern as
  Phase 2's `stage-tool.spec.ts`.
- A rendered-output regression: render one shot of each existing `type` (HEADLINE_CARD, STAT_CARD,
  etc.) through the new pipeline against the fixture data already used in Phase 2's tests, and
  visually/pixel-sanity-check the output isn't blank (reusing `visuals.py`'s existing
  `content_pixels`/`sample_frame` bright-pixel check as an automated smoke test, not a full
  vision-judge run in CI).
- The vision-judge retry/fallback path needs a test that forces a rejection (mock the judge) and
  confirms the generic fallback template is used, not a silent blank shot.
- No test in this phase should require live cloud-model calls to pass in CI-equivalent runs — live
  `kimi-k2.7-code`/vision-judge calls are exploratory/manual verification, mirroring Phase 2's
  Task 5 Step 4 pattern (report findings honestly, not a pass/fail gate), except for one automated
  live test analogous to Phase 1/2's `roundtrip.e2e.ts`.

## Open items for the implementation plan (not resolved here, deferred to writing-plans)

- Exact `hyperframes_scenes/` directory structure and naming for the "generic fallback template."
- Whether `npx hyperframes` needs installing per-workspace or once globally in the harness's own
  `node_modules` (likely the latter, consistent with Phase 1/2's dependency conventions) — verify
  against HyperFrames' actual install instructions before writing the plan's first task.
- Exact DSH subagent API (`dsh-tool-subagent`'s config shape for spawning with a different
  provider/model per call) — needs the same "read the real installed package" discipline Phase 1/2
  applied to `dsh-tools`/`dsh-llm`, not assumed from this design doc's sketch.
