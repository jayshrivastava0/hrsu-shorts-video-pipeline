# Creative Flow & Real-Pipeline Wiring — Design

**Date:** 2026-08-26
**Status:** Approved by user, pending spec self-review
**Builds on:** `docs/superpowers/specs/2026-08-23-hyperframes-visual-pipeline-design.md` (visuals
stage, merged to `main` 2026-08-23) and
`docs/superpowers/specs/2026-08-25-hyperframes-assembly-stage-design.md` (assembly stage, merged
to `main` this session). Both are fully built and tested but — this design's core finding —
**neither has ever produced a real video**, because `python -m shorts_engine`, the only entry
point that runs the pipeline end-to-end, never touches the Node harness at all.

## Context

Two real end-to-end runs this session (`run-f9964b26`, `run-a03b3787`) both used the exact same
visual skeleton: `HEADLINE_CARD` → `HEADLINE_CARD`/`STAT_CARD` → `DIAGRAM` → `STAT_CARD`/
`QUOTE_CARD` → `LOGO_CTA`, every time, regardless of topic. Investigating why surfaced two
separate, compounding problems:

1. **`shotlist.py` hardcodes shot type per beat NAME.** `plan_beat_shots()` branches on
   `if name == "hook"`, `elif name == "stakes"`, etc. — five fixed beat names, each deterministically
   mapped to one or two fixed card types (`shotlist.py`'s docstring: *"deterministic beat→shots
   expansion. No LLM."*). Every video gets the identical narrative skeleton because `script.py`'s
   schema only ever produces those five beats, in that order.
2. **Even the already-built creative rendering is not wired into the real run.** The
   `hyperframes-visuals-stage` and `hyperframes-assembly-stage` branches gave shots real
   HTML/CSS/GSAP authoring freedom and gave assembly real composition freedom — but
   `cli.py`'s `STAGE_FUNCS` still points `"visuals"` and `"assemble"` at the original
   `shorts_engine.stages.visuals.run`/`assemble.run` (PIL cards, ffmpeg captions). The Node-harness
   tools those branches built (`stage_visuals_prepare`, `author_visual_scene`,
   `stage_visuals_finalize`, `stage_assemble_prepare`, `author_assembly_composition`,
   `stage_assemble_finalize`) have only ever been driven by their own isolated e2e tests — never
   by a real run against a real blog post.
3. **Even wired in, the visuals-stage subagent's creative freedom is bounded to *execution*, not
   *concept*.** Its persona (`scene-author-persona.md`) is handed a fixed `type` from the shot
   brief and told *"use it to decide layout — a HEADLINE_CARD is large centered text, a STAT_CARD
   foregrounds one number."* It can make a stat card look great; it cannot decide a beat deserves
   something other than a stat card. That decision is baked into `shotlist.py` before the subagent
   ever sees the shot.

All three have to be fixed together — wiring in the existing subsystems alone would still produce
the same skeleton every time, just with nicer motion on each card.

## Decisions made in brainstorming (binding)

1. **Full creative freedom, no fixed palette, no fixed beat count/order** (user's explicit choice
   over a curated-template-library alternative). Structural rule kept: the **final beat must be a
   CTA** — not a creative constraint, the pipeline's entire purpose is lead generation, and dropping
   it silently would defeat that.
2. **Hard floor: 30 seconds minimum total video duration. No ceiling.** (down from today's 35s
   floor.)
3. **Reuse the already-built visuals-stage and assembly-stage HyperFrames subsystems — do not
   build a third, parallel, pure-Python rendering system.** The gap is wiring and scope, not
   missing rendering capability.
4. **The shot-authoring subagent's `type` becomes a hint from `shotlist.py`, not a constraint.**
   Combined with #1, the subagent is free to choose a different visual archetype than whatever
   `shotlist.py` suggested, provided it still satisfies the beat's narration and fact-grounding.
5. **Fact-grounding survives the move to freeform authoring.** Numbers/claims a shot displays must
   still come verbatim from `factsheet.json` via the beat's `fact_ids` — the subagent's prompt
   includes the exact cited figures for its beat and is instructed never to compute, round
   differently, or invent a number. This is not new policy; it's carrying forward the same
   verbatim-fact-injection discipline `shotlist.py`'s `_stat_payload()` already enforces today, into
   a prompt instruction instead of a Python dict literal.
6. **Real b-roll/photo acquisition stays available to the authoring subagent, not lost to
   "everything is now a synthetic card."** The existing acquisition ladder
   (`shorts_engine/sourcing/ladder.py`, already vision-judged) is exposed to the subagent as a
   callable tool (`request_broll`) it can use when it judges a beat calls for a real photo instead
   of a synthetic composition — preserving the project's existing "real photos only when
   independently verified to match" principle instead of narrowing it.

## Architecture

### A. `script.py` — flexible beats

`SCRIPT_SCHEMA`'s fixed 5-beat array becomes a variable-length array (minimum 3 beats, no
maximum). Each beat keeps `narration`, `fact_ids`, `card_text` as today, and gains a `purpose` tag
(`hook | stakes | mechanism | proof | cta | other`) used for pacing/duration reasoning and to
identify the mandatory closing CTA beat — `purpose` is metadata for downstream logic, not a literal
name the writer must use verbatim for every beat.

Gate changes: `run_gates()`'s current per-beat-name word budgets (`budget[cta]`, `budget[hook]`,
etc.) become per-`purpose` budgets instead, since beat names are no longer fixed strings. The
existing banned-word gate, citation/`fact_ids` grounding check, and total-duration floor
(now 30s, no ceiling) are unchanged in mechanism, just re-keyed off `purpose`.

### B. `shotlist.py` — replaced by freeform shot planning

`plan_beat_shots()`'s beat-name branching ladder is deleted. In its place, a lighter planning pass
does only what has to stay deterministic:

- phrase-splitting/packing narration into shot-sized spans (`split_phrases`/`pack_phrases`,
  unchanged — this is just text chunking, not a creative decision)
- resolving each span's `fact_ids` to their verbatim `factsheet.json` entries, so the authoring
  subagent gets exact figures, never asked to recall or compute them
- suggesting (not fixing) a `type` per span, using today's heuristics as a hint (e.g. "this span
  cites a number → suggest STAT_CARD") — purely advisory, per decision #4

This produces `shot_briefs.json` in the same shape the visuals-stage branch already defined, with
`type` demoted from directive to `suggested_type`, plus the resolved fact text and
`beat_purpose`.

`assemble.py`'s CTA-length cap (`config.LOGO_CTA_MAX_S`, applied today via
`shot["type"] == "LOGO_CTA"`) and `reflow()`'s per-shot cap selection key off literal type strings
that no longer reliably appear once type is freeform. Both move to keying off `beat_purpose ==
"cta"` instead (already present on every shot from decision #1's mandatory closing CTA beat),
decoupling duration/business logic from the now-free-form visual type entirely.

### C. `scene-author-persona.md` — freed from the type constraint

Update the persona so `suggested_type` is explicitly a starting point the subagent may override,
add the `request_broll` tool alongside `write_scene_file`/`render_scene`, and add explicit
verbatim-fact instructions (decision #5) sourced from the resolved fact text now present in the
shot brief.

### D. The Python↔Node bridge (the actual missing piece)

A new Node script, `harness/scripts/run-creative-stages.mts <workspace>`, drives the exact fixed
tool-call sequence the two existing branches already prove works (their own e2e tests exercise
each piece in isolation) — no orchestrating agent intelligence needed, the sequence is always the
same:

```
1. boot cordis (Loader/Include against cordis.yml — same pattern the e2e tests use)
2. ctx.tools.execute('stage_visuals_prepare', { workspace })   [shells to Python stage_cli.py]
3. for each shot_brief: ctx.tools.execute('author_visual_scene', { shot_brief, workspace })
4. ctx.tools.execute('stage_visuals_finalize', { workspace })  [Python checkpoints status=visuals]
5. ctx.tools.execute('stage_assemble_prepare', { workspace })  [Python]
6. ctx.tools.execute('author_assembly_composition', { assembly_brief, workspace })
7. ctx.tools.execute('stage_assemble_finalize', { workspace }) [Python checkpoints status=assembled]
8. exit 0 (or a non-zero code with the failing step named, on any failure)
```

`shorts_engine/cli.py`'s `STAGE_FUNCS` entries for `"visuals"` and `"assemble"` change to thin
Python functions that `subprocess.run(["node", "--import", "tsx/esm", ".../run-creative-stages.mts",
str(workspace)])`, block on it, and — since the bridge script writes directly into the same
workspace path Python already expects (`shots/`, `visuals_report.json`, `video_short.mp4`,
`assemble_report.json`) — just return the same artifact dict these stages return today. No changes
needed anywhere downstream: `verify.py`'s gates, `_reassemble()`'s `assembly_brief.json` check
(this run now genuinely produces one, so a revise cycle correctly hits the "not yet implemented"
guard rather than the safe fallback path — reassembly on this path stays real follow-up work,
unchanged from the assembly-stage design), `package.py`, `publish.py` are all untouched.

## What does NOT change

- `facts.py`, `audio.py`, `ingest.py` — untouched.
- The acquisition ladder (`sourcing/ladder.py`, `paper_page.py`) — untouched, just newly reachable
  via the `request_broll` tool instead of only via `shotlist.py`'s old BROLL type.
- `verify.py`'s vision-judge gates, revise loop, heuristic checks — all already generic over
  arbitrary shot content; nothing here assumed a fixed type palette.
- `assemble.py`'s `reflow()` duration-law arithmetic, `_reassemble()`'s HyperFrames-detection
  guard (both fixed this session) — unchanged, and now actually exercised for real for the first
  time.
- The `RunManifest`/`STATUS_ORDER` state model.

## What's retired

- `shotlist.py`'s beat-name branching ladder (`plan_beat_shots`'s `if name == "hook": ...` chain).
- The old PIL card renderers (`shorts_engine/cards/*.py`) and `visuals.py`'s `RENDERERS` dispatch
  as the *default* path — kept only as `_isolate_site_post`-style fallback reference until the
  bridged path is proven live across enough real runs, per the visuals-stage design's own
  precedent for not deleting the rollback path prematurely.

## Testing approach

- `script.py`: gate tests updated for `purpose`-keyed budgets instead of name-keyed; a new test
  asserting a script with beats not named `hook`/`stakes`/`mechanism`/`proof`/`cta` still passes
  gates and still resolves a `purpose="cta"` beat last.
- `shotlist.py`: replace the deterministic-ladder tests with tests asserting `shot_briefs.json`'s
  shape (`suggested_type` present but not enforced downstream, fact text resolved verbatim,
  `beat_purpose` present).
- New Node test for `run-creative-stages.mts`: same stubbed-subagent-provider pattern the two
  existing e2e suites use, asserting the full 7-step sequence executes in order and a mid-sequence
  failure (e.g. `author_visual_scene` failing twice) still reaches `stage_visuals_finalize`'s
  fallback rather than hanging or crashing the bridge.
- One real, live, model-backed smoke run (not CI — a manual task, like the visuals-stage branch's
  own "Step 7... exercises the real subagent path once, manually") against one already-ingested
  real blog post, to confirm actual creative output before calling this done — this was the whole
  point of this design.
