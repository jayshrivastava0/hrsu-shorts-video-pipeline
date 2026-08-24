# HyperFrames Assembly Stage — Design (Migration Phase 3, continued)

**Date:** 2026-08-25
**Status:** Approved by user, pending spec self-review
**Builds on:** `docs/superpowers/specs/2026-08-23-hyperframes-visual-pipeline-design.md` (the
`hyperframes-visuals-stage` branch, merged to `main` at `236496a`), which explicitly deferred the
`assembled` stage as a separate future plan. This document is that plan's design.

## Context

The `visuals` stage now authors and renders each shot's HyperFrames composition through a
creative subagent (`author_visual_scene`) instead of the old fixed PIL card renderers — the whole
point being to escape "seven hand-coded card types" and let the pipeline produce genuinely varied,
on-brand visuals. The `assembled` stage still uses the *original* mechanism: `assemble.py`
re-flows shot durations onto real audio timing, then hand-builds an ffmpeg filter graph that burns
ASS captions, overlays a static logo, and draws one fixed animated gold progress bar — the same
kind of rigid, predictable output the visuals-stage migration was meant to move away from.

An earlier draft of this design proposed porting that same fixed caption/logo/progress-bar
treatment mechanically into a Python-generated HyperFrames composition — same visual result, new
renderer. The user correctly rejected this: it would keep the single most visually repetitive part
of every video (captions, the connective tissue viewers see continuously) exactly as boring as
before, defeating the reason the pipeline went agentic in the first place. This design instead
gives assembly its own creative-authoring pass, structurally parallel to shot authoring.

## Decisions made in brainstorming (binding)

1. **Creative authoring, not deterministic generation, is the primary path.** A subagent
   (`author_assembly_composition`, new harness tool) receives a brief of *locked facts* — final
   shot order/durations, exact word-level caption timings, the mixed audio track, logo asset,
   brand facts, target video duration — and has real creative latitude over everything that is
   *style*: caption typography and motion (kinetic text, not a static fade box), how shots
   transition into each other (crossfade, wipe, scale-punch — GSAP animating nested `<video>`
   clips directly, which the old ffmpeg concat pipeline structurally couldn't do), whether/how a
   progress indicator appears, overall visual rhythm. It cannot alter shot timing, invent caption
   text/timing, or violate brand/banned-claims rules.
2. **A deterministic composition is the fallback, not the default.** Mirrors the shot-authoring
   pattern's `generic_fallback.html`: only used after the creative subagent's two authoring
   attempts both fail to render — the never-blank safety net for assembly, never the primary
   output.
3. **Reflow-triggered re-render is dropped**, not ported 1:1. The old code re-rendered a shot's
   card whenever reflow nudged its duration by >0.05s (`CARD_RERENDER_EPSILON_S`) — which in
   practice fires for nearly every shot, since reflow always nudges toward real audio timing.
   Re-invoking a subagent per shot at assembly time would reintroduce the slow/expensive
   authoring step for almost every run. Instead, each shot's rendered mp4 (already produced once
   by the `visuals` stage) is placed as a `<video>` clip whose `data-duration` is set to its
   **reflowed span**, independent of the source file's own native length — the same mechanism
   HyperFrames' own shipped template uses for nested-composition timing (see Research below).
   Whether a `<video>` clip whose `data-duration` differs from its native length holds the last
   frame cleanly, trims, or distorts is a **real open question, not yet confirmed** — verifying
   this empirically is the first implementation task, before any generation/authoring code is
   written against it.
4. **The assembly-authoring model is swappable**, same `process.env` override pattern as
   `ORCHESTRATOR_MODEL`/`SCENE_AUTHOR_MODEL`: `ASSEMBLY_AUTHOR_MODEL`, defaulting to
   `gemma4:31b-cloud`. `kimi-k2.7-code:cloud` is out of scope entirely as of this design
   (confirmed 403 — the Ollama account lacks the required subscription); the model roster to test
   is whatever's actually reachable — `gemma4:31b-cloud`, `nemotron-3-ultra:cloud`,
   `glm-5.1:cloud`, `minimax-m2.7:cloud`, `minimax-m3:cloud`, etc.
5. **Audio mixing stays exactly as it is.** `video_agent.music.mix_music_under_voice` already
   produces one pre-mixed audio file (voice + ducked music). This design references that single
   file as one `<audio>` clip in the composition — it does not attempt to reimplement ducking or
   multi-track mixing inside HyperFrames/GSAP. Not broken, not in scope to change.
6. **`reflow()`'s arithmetic is untouched.** Duration-law math (voice + `END_CARD_HOLD_S`, never
   `-shortest`) stays exactly as implemented in `assemble.py` today — only what consumes its
   output (an ffmpeg filter graph vs. a HyperFrames composition) changes.

## Architecture

Same three-piece split as the `visuals` stage, for the same reason: Python cannot call harness
tools, so authoring must happen in an orchestrator-driven loop, bookended by Python bridge calls
that own everything mechanical. `RunManifest` checkpoints once, at the end, to `status="assembled"`
— no schema change.

```
1. stage_assemble_prepare (Python, stage_cli.py — NEW subcommand, replaces assemble.run()'s
   current monolithic body up through the point captions/mux begin)
   - reflow(shots, beats_audio, voice_total) — UNCHANGED arithmetic
   - mix_music_under_voice(voice, ...) — UNCHANGED, produces the one mixed audio file
   - writes workspace/assembly_brief.json:
       {
         "shots": [{"id", "video_path" (existing rendered shot mp4, absolute),
                    "start_s" (cumulative), "duration_s" (reflowed span), "beat"}],
         "word_timings": [...],            # same shape as word_timings.json today
         "audio_path": "<mixed audio file, absolute>",
         "logo_path": "<config.BRAND_LOGO_FILE, absolute>",
         "target_duration_s": voice_total_s + config.END_CARD_HOLD_S,
         "voice_total_s": ...
       }
   - does NOT checkpoint the manifest (mirrors stage_visuals_prepare)
   - returns { assembly_brief_path } to the orchestrator

2. author_assembly_composition(assembly_brief, workspace) [harness tool, NEW]
   - spawns an ASSEMBLY_AUTHOR_MODEL subagent, scoped tools:
       - write_composition_file(html) -> writes
         hyperframes_scenes_project/compositions/<workspace_id>/assembly.html
       - render_composition() -> shells `hyperframes render -c
         compositions/<workspace_id>/assembly.html -o <workspace>/video_short.mp4
         --resolution portrait`, output path resolved server-side (same security pattern as
         render_scene — never subagent-controlled), returns the rendered mp4 path
   - subagent persona (assembly-author-persona.md, new — parallel to
     scene-author-persona.md) documents: the locked-facts/free-style boundary, the HyperFrames
     timing contract (same data-* attributes as shot authoring), how to place each shot as a
     `<video>` clip at its given start_s/duration_s, how to build a caption GSAP timeline from
     word_timings (the pattern HyperFrames' own warm-grain template demonstrates), brand facts
     for the logo/CTA treatment.
   - 2 attempts, then falls back to a hand-authored deterministic composition (Python-generated
     from the same assembly_brief.json — the safety net, not the default)
   - orchestrator calls this once per run (single call, not per-shot — assembly is one
     composition, unlike per-shot scene authoring)

   never-blank / duration-law enforcement (orchestrator-level):
     stage_assemble_finalize (next step) probes the rendered mp4's actual duration. If it
     violates the duration law, or a shot is visibly missing (frame-sampled sanity check, reusing
     visuals.py's content_pixels/sample_frame at a handful of timestamps derived from each shot's
     start_s), the orchestrator retries author_assembly_composition once with the failure reason
     appended to the brief; on exhaustion, falls back to the deterministic composition.

3. stage_assemble_finalize (Python, stage_cli.py — NEW subcommand)
   - probes the rendered mp4 (encoder.probe_duration, unchanged), asserts the same duration-law
     checks assemble.py enforces today (video >= voice + AUDIO_COMPLETENESS_MARGIN_S; video ≈
     voice + END_CARD_HOLD_S within 0.35s)
   - checkpoints manifest to status="assembled" with { video, assemble_report } artifacts
```

## What does NOT change

- `reflow()`, `beat_spans()` — pure duration-law arithmetic, untouched.
- `video_agent.music.mix_music_under_voice` — untouched.
- The duration-law invariant itself and its tolerance values (`END_CARD_HOLD_S`,
  `AUDIO_COMPLETENESS_MARGIN_S`) — untouched.
- `RunManifest`/`STATUS_ORDER` — unchanged, `assembled` still sits between `visuals` and
  `verified`.
- `stage_verify`'s responsibility — unchanged; only what it's checking pixels from changes.

## What's retired

- `build_ass`, `group_words_into_cues`, `ass_time`, `_ass_header`, `_ass_filter_path`,
  `_final_mux` — the ffmpeg-based caption-burn/logo-overlay/progress-bar filter graph. Not deleted
  immediately (kept as reference/rollback until the HyperFrames path is proven live, same
  precedent as the visuals-stage migration's retired PIL cards).
- The old `assemble.run()`'s per-shot re-render-on-reflow branch (the
  `CARD_RERENDER_EPSILON_S`/`rerender` logic) — replaced by video-clip retiming in the composition
  (pending the Task 1 empirical check in Decision 3 above).

## Composition authoring conventions (the subagent's persona)

`assembly-author-persona.md` is a first-task deliverable, parallel to `scene-author-persona.md`.
It must state explicitly which fields of `assembly_brief.json` are locked (shot `start_s`/
`duration_s`/`video_path` order, `word_timings` text and timestamps, `target_duration_s`, logo
asset path, banned-claims list) versus creative (caption styling/animation, transition treatment
between `<video>` clips, whether/how a progress indicator renders, overall pacing feel) — using the
same explicit, example-driven style as `scene-author-persona.md`'s brand-facts/banned-claims
section. It documents the same HyperFrames timing-attribute and GSAP contracts (paused timeline
registered on `window.__timelines`, no wall-clock/unseeded-randomness) since the subagent is
authoring raw HTML/CSS/GSAP again, just at the whole-composition level instead of per-shot.

## Testing approach

Mirrors `visuals-stage-regression.e2e.ts`'s pattern exactly:

- A mechanical regression stubbing `ctx.subagents` provider so the fake subagent calls the real
  `write_composition_file`/`render_composition` tool implementations against fixture data — proves
  the real seam (brief → composition file → rendered mp4 → duration-law check), no live cloud call
  required in CI.
- A forced-failure test (mock the subagent to fail both attempts) confirming the deterministic
  fallback composition is used and still satisfies duration law — the safety-net path must be
  tested as rigorously as the primary path, since it is what ships when creativity fails.
- Unit tests for `stage_assemble_prepare`/`stage_assemble_finalize` (fixture shotlist/beats_audio/
  word_timings in, correct `assembly_brief.json` out; correct manifest checkpoint), same pattern
  as the existing `stage_cli.py` tests.
- One live, real-cloud-model exploratory test analogous to Phase 1/2/3's existing live-check
  pattern — reported as findings, not a CI pass/fail gate.

## Resolved during spec research (verified against real, installed sources)

- **`<video>`/`<audio>` elements are first-class HyperFrames clips**, not just nested `.html`
  compositions — confirmed via the real, shipped
  `harness/node_modules/hyperframes/dist/templates/warm-grain/index.html` template: `<video
  id="a-roll" src="..." data-start="0" data-duration="__VIDEO_DURATION__" data-track-index="0">`
  alongside a matching `<audio>` element with `data-volume`. This is what lets each already-
  rendered shot mp4 be placed directly as a clip, without re-nesting its source `.html`.
- **Nested-composition timing is controlled by the referencing element's own `data-start`/
  `data-duration`**, independent of the nested file's internal content — confirmed by the same
  template: `<div data-composition-src="compositions/intro.html" data-start="0"
  data-duration="2.5" ...>` sits alongside compositions whose own internal duration isn't
  necessarily 2.5s. This is the mechanism Decision 3 relies on for shot retiming, though its exact
  behavior specifically for a `<video>` element (vs. a nested `.html` composition) still needs the
  Task 1 empirical check.
- **Word-level captions are a plain data-driven GSAP timeline**, not a special HyperFrames feature
  — confirmed via the same template's real `compositions/captions.html`: a hardcoded
  `[{text, start, end}, ...]` array (near-identical shape to this project's `word_timings.json`)
  drives `tl.set`/`tl.to` calls building fade-in/fade-out cues per line. This is why caption
  *content and timing* are locked facts handed to the subagent (they're just the existing
  word-timing data), while caption *styling and motion* are the subagent's creative surface.

## Open items for the implementation plan (still deferred to writing-plans)

- The exact empirical check and its pass/fail criteria for `<video>` clip retiming (Decision 3) —
  this gates whether shot-duration handling in the brief/persona needs any special-casing.
- Exact `resolveScopedPath`-style security enforcement for `write_composition_file`/
  `render_composition` — reuse the existing `scene-tools.ts` primitives if their scoping
  generalizes cleanly to a per-run (not per-shot) composition path, or note why a variant is
  needed.
- Exact content/structure of the deterministic fallback composition — needs the same
  `escapeHtml()`-level care as `generic_fallback.html` since caption text ultimately originates
  from generated content.
