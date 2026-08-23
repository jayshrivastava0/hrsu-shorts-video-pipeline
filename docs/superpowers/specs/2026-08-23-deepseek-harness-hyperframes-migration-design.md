# DeepSeek Harness + HyperFrames Migration — Design

**Date:** 2026-08-23
**Status:** Approved by user, pending spec review

## Context

The shorts video pipeline (`_shorts_engine_impl/shorts_engine`) is not producing reliable
output. This design replaces two pieces of it:

1. **Orchestration** — the pipeline's own Python state machine (`init → ingested → facts →
   scripted → shotlisted → audio → visuals → assembled → verified → packaged → publish`) is
   replaced by [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) ("everything
   is a plugin" agent harness), using Ollama as its local-model plugin.
2. **Visual rendering** — the current branded motion-graphics card renderer and ffmpeg assembly
   step are replaced by [HyperFrames](https://hyperframes.heygen.com/guides/pipeline)
   (HTML/CSS/JS → headless-browser capture → FFmpeg encode), covering both the `visuals` and
   `assembled` stages.

The existing Python stage logic (facts grounding, script/shotlist generation, stock-visual
acquisition, vision-judge verification) is retained and reused — not rewritten — per the
language-bridge decision below.

## Non-goals

- Not rewriting shorts_engine's business logic in TypeScript.
- Not changing the workspace directory format or CLI flags (`--until`, `--local-only`,
  `--workspace-root`, `--publish`, etc.) that other tooling already expects.
- Not touching `hrsu-blog-publishing` or `hrsu-rl-scoring-loop` (separate repos).

## Architecture

```
harness/                         (new, Node/pnpm — DeepSeek Harness install + custom plugin)
  cordis.yml                     per-session plugin composition: model=ollama, tools=stage
                                  subcommands + broll_acquire, system prompt (see below)
  plugins/shorts-pipeline/       defines the 10-stage loop as harness tool calls

_shorts_engine_impl/shorts_engine/   (existing, Python — mostly unchanged)
  stages/<stage>.py              NEW: thin CLI subcommand per stage, JSON in/out over stdio,
                                  wraps the existing stage logic so the harness can shell out to it
  acquisition/broll.py           existing free-sources-only ladder, exposed as a harness tool
                                  (see below) instead of only being called internally

hyperframes_scenes/              (new) HTML/CSS/JS scene templates per shot type (stat card,
                                  quote card, comparison card, photo-overlay + a generic fallback
                                  template for the never-blank guarantee)
```

## Language bridge

The harness (TypeScript/Node, via pnpm) drives the existing Python pipeline logic as **tools**,
not as a rewrite: each stage gets a `python -m shorts_engine.stages.<stage> --json` subcommand
that reads a JSON request on stdin/args and writes a JSON result to stdout. A harness tool plugin
wraps each subcommand as a `child_process` call. This keeps all tested Python logic (fact
grounding, vision-judge, ffmpeg-adjacent code not being replaced) intact and reviewable
independently of the harness migration.

## B-roll / stock-visual acquisition as an attached plugin

The current acquisition ladder (own asset library → blog's own images → stock APIs
[Pexels/Pixabay/Unsplash/Openverse/Wikimedia] → scrape as last resort, each candidate checked by
a describe-then-match vision judge before acceptance) becomes its own harness **tool plugin**,
`broll_acquire`, rather than logic buried inside the `visuals` stage:

- Input: a shot's description/keywords + the grounded facts it must visually match
- Output: either a verified image/clip path + provenance (source, license), or an explicit
  "no verified match" signal
- On "no verified match", the orchestrator falls back to a HyperFrames-rendered scene from
  `hyperframes_scenes/` (never-blank) rather than shipping an unverified visual
  (never-unverified)

Making this a standalone plugin means it can be reused by other stages (or other harness agents
later) without going through the `visuals` stage specifically, and matches the harness's
plugin-first philosophy.

## Invariants preserved

- **Never-blank** — every shot resolves to *some* valid visual: verified b-roll, or a generic
  HyperFrames fallback template if nothing verifies.
- **Never-unverified** — nothing ships without a vision-judge check against actual rendered
  pixels (from HyperFrames output) or actual acquired images, never against captions/metadata
  alone.
- **Fact grounding** — script claims are grounded against source citations before TTS runs
  (unchanged, still Python, still called as a harness tool).
- **Publish gating** — `--publish` stays a deliberate second step; the existing 3-consecutive-
  approved-dry-runs gate is preserved.

## System prompt for the harness agent

The harness's per-session plugin composition (`cordis.yml`, per DeepSeek Harness docs) carries a
system prompt for the orchestrating agent. Drafted below; final file lives at
`harness/system_prompt.md` and is loaded into the session config.

```markdown
# HRSU Shorts Pipeline Agent

You orchestrate a fixed pipeline that turns one published HRSU blog post into one short-form
vertical video: init → ingested → facts → scripted → shotlisted → audio → visuals → assembled →
verified → packaged → publish. You do not skip stages, reorder them, or invent new ones. Each
stage is a tool call; treat its JSON output as the only source of truth about whether that stage
succeeded — do not assume success and do not paraphrase a failure into a success.

## Non-negotiable invariants

1. **Never-blank.** Every shot in the shotlist must resolve to an actual visual before the
   `assembled` stage runs. If `broll_acquire` returns "no verified match," render the shot from
   `hyperframes_scenes/` instead. Do not leave a shot without a visual and do not silently drop a
   shot to avoid the problem.
2. **Never-unverified.** No visual — acquired or rendered — ships without a vision-judge check
   against its actual rendered pixels, not its caption, filename, or the prompt used to generate
   it. If verification fails, retry acquisition/rendering for that shot; do not lower the bar to
   make it pass.
3. **Grounded facts only.** Every factual claim in the script must trace to a source citation
   collected in the `facts` stage. If a claim can't be grounded, cut it — do not soften it into
   vague language to avoid dropping it.
4. **Publish is gated.** Never call the `publish` tool unless the workspace shows 3 consecutive
   human-approved dry runs. Default behavior stops at `hold_for_review`.

## How to use your tools

- Stage tools (`stage_ingest`, `stage_facts`, `stage_script`, `stage_shotlist`, `stage_audio`,
  `stage_visuals`, `stage_assemble`, `stage_verify`, `stage_package`) each wrap existing, tested
  Python logic. Pass them exactly the JSON shape they document; if a call fails, read the actual
  error in the JSON response before retrying — don't retry blindly more than twice on the same
  stage without changing your input.
- `broll_acquire` is independent of the `visuals` stage tool — call it per-shot as needed. It
  will tell you explicitly when nothing verified was found; that is not an error, it's a signal
  to use the HyperFrames fallback template.
- Use the local Ollama model for text and vision judging. If a judgment call is ambiguous, prefer
  the conservative outcome (reject the visual, cut the claim) over shipping something unverified.

## What "done" looks like

A run is complete when the workspace reaches `packaged` with every shot's visual verified and
every script claim grounded. A run that reaches `packaged` by skipping verification or grounding
is not done — it is broken, even if a video file exists at the end.

## What not to do

- Do not fabricate a citation, a verification result, or a "verified match" that didn't happen.
- Do not silently downgrade never-blank/never-unverified to hit a deadline or avoid a retry loop.
- Do not call `publish` speculatively "to see what happens."
- Do not invent pipeline stages or reorder the fixed sequence above.
```

## Migration phases

1. Install/configure DeepSeek Harness + Ollama model plugin in `HRSU Shorts`; verify a trivial
   tool call round-trips end to end.
2. Refactor shorts_engine stages into `stages/<stage>.py` CLI subcommands; wrap each as a harness
   tool; get the harness driving the *existing* renderer/assembly end-to-end (orchestration swap
   only, no visual changes yet) — regression-check against a known-good prior dry run.
3. Build the `hyperframes_scenes/` template library and the `broll_acquire` tool plugin; swap
   `visuals`/`assembled` stages to HyperFrames; re-verify never-blank/never-unverified against
   real rendered pixels, not the old renderer's output.
4. Full dry run, then require 3 consecutive human-approved dry runs before enabling `--publish`
   on the new stack.

## Testing

- Existing `pytest` suite for `shorts_engine` continues to pass unmodified for stage logic not
  touched by the bridge refactor (subcommand wrapping should be additive, not a rewrite of stage
  internals).
- New tests: one per stage subcommand (JSON contract), one for `broll_acquire`'s fallback
  behavior, one end-to-end dry run against a fixture blog post per phase.
- No `--publish` runs during migration testing — everything stays behind the dry-run gate until
  Phase 4.
