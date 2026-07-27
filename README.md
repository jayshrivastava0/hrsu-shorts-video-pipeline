# HRSU Shorts Video Pipeline

Turns a published HRSU blog post into a short-form vertical video: script → shotlist → voiceover
→ branded motion-graphics cards / verified stock visuals → assembled, captioned MP4 → packaged
for publishing.

A sample output is checked in at [`samples/sample_output.mp4`](samples/sample_output.mp4) — a
real video from an end-to-end pipeline run (script, TTS voiceover, sourced/rendered visuals,
captions, and assembly, all produced automatically from a blog post).

This is one of three HRSU pipelines split out of a single project for clarity:
- [`hrsu-blog-publishing`](https://github.com/jayshrivastava0/hrsu-blog-publishing) — produces
  the blog posts this pipeline turns into video
- [`hrsu-rl-scoring-loop`](https://github.com/jayshrivastava0/hrsu-rl-scoring-loop) — scoring
  loop for the blog/social side
- **This repo** — the video pipeline

## Two implementations in this repo

- **`_shorts_engine_impl/shorts_engine/`** — the current, actively developed pipeline. A
  clean-slate rebuild with a "designed-first" visual grammar: branded motion-graphics cards are
  the default visual, real photos/footage are used only when independently verified to match the
  script. Built around two hard invariants — **never-blank** (every shot has *some* valid visual)
  and **never-unverified** (nothing ships without a vision-judge check against the actual pixels,
  not just a caption). Facts are grounded verbatim against source citations before TTS ever runs.
- **`video_agent/`** — the earlier agent-based pipeline. Several of its components (the YouTube
  packager, storyboard types, stock-visual sourcing) are still imported by `shorts_engine` today;
  see `tests/shorts_engine/test_boundaries.py` for which imports are and aren't allowed across
  the boundary.

## Pipeline stages

```
init → ingested → facts → scripted → shotlisted → audio → visuals
    → assembled → verified → packaged → publish
```

Visual sourcing follows a free-sources-only acquisition ladder: own asset library → the blog's
own images → stock APIs (Pexels/Pixabay/Unsplash/Openverse/Wikimedia) → scrape as a last resort —
each candidate is checked by a describe-then-match vision judge before acceptance.

## Usage

Run from inside `_shorts_engine_impl/`:

```bash
python -m shorts_engine <blog_url> [options]

Options:
  --until {ingest,facts,script,shotlist,audio,visuals,assemble,verify,package,publish}
  --resume              (stub — not yet implemented)
  --local-only          skip network acquisition, use local asset library only
  --workspace-root PATH default: output/shorts
  --html-override FILE
  --torture             run repeated torture-test iterations
  --publish             actually publish (default stops at hold_for_review / dry-run)
```

Example — dry run through review:
```bash
python -m shorts_engine https://blog.hrsuindore.com/2026/02/optimizing-german-concrete-c70c80-set.html \
  --workspace-root ../output/shorts
```

Real publishing is gated behind 3 consecutive human-approved dry runs (see the design spec) —
`--publish` is a deliberate second step, not the default.

## Setup

```bash
pip install -r requirements.txt
```

Requires: Ollama (local or cloud model) for text/vision judging, edge-tts for voiceover,
Whisper for word timing, ffmpeg for assembly, and API keys for the stock-image sources you want
to use in `.env` / `secrets.txt` (not committed).

## Testing

```bash
# Full shorts_engine suite (run from _shorts_engine_impl/)
pytest

# Root-level video_agent suite
pytest tests/video_agent
```

## Docs

`docs/superpowers/specs/2026-07-04-shorts-engine-design.md` is the current design spec — start
there for the "designed-first" architecture, failure modes from prior runs, and the invariants
above. `docs/superpowers/progress/` has run-by-run write-ups from live smoke tests.

## What's not committed

`.env`, credential files, and generated run output (`output/`, `output_live/`,
`output_torture/`, `.cache/`) are excluded via `.gitignore`. Large binary test fixtures under
`tests/video_agent/harness/fixtures/verify/` were intentionally left out of this repo (not code).
