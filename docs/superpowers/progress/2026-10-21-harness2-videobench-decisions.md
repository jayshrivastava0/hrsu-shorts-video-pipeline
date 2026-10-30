# Harness² + HRSU-VideoBench — research notes and decisions (2026-10-21)

Status: **decisions agreed in chat, design spec not yet written.** Next step is a design spec
(brainstorming → spec → user approval → plan → build). Nothing here is implemented yet.

## Problem

Script stage is good (explanation-first, Ollama cloud). Visuals are weak with open models.
Claude-authored HyperFrames (faceless-explainer workflow) produced an "EPIC" video, but costs
Claude allowance. Goal: make the open-model visual harness produce good videos. Runtime is
NOT a concern (user: fine if it runs all day); output quality is.

## Harness² (google-research/harness2, Apache 2.0) — what it is

Improves the *harness* (prompts, skills, scripts, hooks, memory) around a fixed model, without
ground truth during refinement. Read from source (`harness2/core/recursion.py`, `prompts.py`,
`selection.py`, `library.py`; clone at commit 1cfe254, 2026-10-13):

1. **Base rollouts**: run the current harness k times on the same task. Divergence between
   replicates = the signal (the harness leaves that decision to chance).
2. **Propose**, two independent dimensions:
   - **INV** — how the agent works: tool use, procedure, scripts, hooks.
   - **DOM** — what a practitioner knows: domain skills with narrow trigger lines.
   Every edit must cite a trajectory moment; `KEEP_BASE` is a valid answer. Prefer on-demand
   skills/scripts/hooks over always-on prose (always-on prose burns output budget).
3. **Validate**: invalid harness (untriggered skill, missing script, template braces) is
   rejected, one retry, else fall back.
4. **Probe**: roll each candidate harness once.
5. **Aggregate/compose**: synthesizer reads every candidate's *product*, writes
   `kept|revised|dropped -> edit -> evidence`, emits one merged harness.
6. **Select**: anonymous, answer-free judge picks best final output (coverage → correctness →
   fidelity); picking the unmodified base is allowed.
7. **Library**: adopted harness + EXPERIENCE.md (cue, lesson, evidence) stored per domain;
   later tasks read it.
8. **Answer-free**: proposers/composers never see a rubric or score.

Modes: parallel `agg` (2k proposals → k syntheses), sequential `seq` (k rounds).

**Decision: port the method, not the package.** Package needs Linux/Py3.12/uv, OpenCode/Codex
executors, Vertex AI; benchmark-runner shaped. We port the loop, INV/DOM split, cite-the-moment
rule, validation, library, answer-free rule, and adapt its prompts closely (keep Apache notice).
Mapping: harness = scene-author persona + skills (`harness/packages/tool-visual-scene/src/`);
INV = HyperFrames/GSAP/layout/timing mechanics; DOM = explainer craft (one continuous diagram
per beat, 3b1b-style cueing on word timings, HRSU brand); rollout = one beat's composition +
snapshot frames.

## Rejected / deferred

- **Hermes Agent (Nous Research)**: general self-improving agent; no Ollama mention; overlaps
  our harness. Skipped — Harness²'s library covers the useful idea.
- **Decision models (Ollama `/v1/systemone`: laya 421M, nimble 9B, tev1; TypeSafe Jev)**:
  classifiers only (choice / noul / score), text-only, no generation. Laya: 83.8% in-task,
  65.1% zero-shot. Jev: no documented fine-tuning. **Deferred.** User requirement: they must be
  trained so quality doesn't degrade. Plan: log every judge verdict + user rating from the
  benchmark → once a few hundred labels exist, fine-tune laya (ModernBERT, HF) → run in shadow
  mode → only replace a judge if agreement ≥ ~90%. Also needs Ollama server ≥ 0.40 (local is
  0.31.2).

## HRSU-VideoBench (agreed: yes, build it)

Without a benchmark we can't tell if a harness edit helped or regressed.

- **Tasks**: 8–10 real blog posts, each with FROZEN script + voiceover + word timings, so only
  visuals vary. ~6 dev / 3–4 held-out.
- **Scoring**:
  - Deterministic DOM/render checks (hard gates): empty thirds, min text size, overlaps,
    repeated cards, cue lag vs word timings.
  - Vision-judge rubric on snapshots: explains the mechanism, continuity, brand.
  - **User ratings 1–5 on a small sample per round = ground truth**; calibrates the judge and
    becomes training data for decision models.
- **Reference videos**: the floor is Gemma's old per-shot render; the ceiling is
  Claude-authored HyperFrames renders. User approved using Claude (not Ollama) to author
  reference videos for a few benchmark tasks (~4% weekly allowance each, so 2–3, not all).
- Benchmark judge is separate from the loop (answer-free preserved).

## Budget constraint → local models for the inner loop

Ollama cloud has a budget; k × candidates × beats × rounds would exhaust it. **Decision: run the
Harness² inner loop (base rollouts, INV/DOM probes) on LOCAL models** (no budget), under the
user's transfer assumption: a harness that improves video quality for small open-weight models
will also improve it for the cloud models.

Local hardware (checked 2026-10-21): GTX 1660 Ti **6 GB VRAM**, **16 GB RAM**, Ollama server
0.31.2. Local models present: `gemma3:4b`, `granite3.1-moe`. Realistic ceiling ≈ 4–8B quantized.

Risks to handle in the spec:
- Transfer can fail: edits that patch a 4B model's weaknesses (heavy hand-holding, long
  always-on prose) can *hurt* a stronger model. Mitigation: after each adopted harness, one
  cheap **cloud validation run** on 1–2 benchmark tasks; adopt only if cloud score doesn't drop.
- A 4B model may be too weak to author HyperFrames HTML at all (all rollouts fail → no
  divergence signal). Mitigation: constrain output (templates + structured JSON filling, with
  hooks/scripts doing the HTML), and test a few local models first.
- Proposer/composer (the editor) needs a stronger model than the solver; candidates: one cloud
  call per stage (few calls), or Claude for the editor role. Decide in spec.

## v1 scope and build order (user said "start building", 2026-10-21)

Discovered during build: the open-model visuals harness to improve already exists —
`scripts/gemma_director.py` (+ `_prompts.py`, `_tools.py`), which drives the HyperFrames
faceless-explainer workflow with an Ollama model (design → storyboard → frames → assemble).
Its prompts (`gemma_director_prompts.py`) + the plugin's frame role packet are the Harness²
editable surface. Model is set by `GEMMA_DIRECTOR_MODEL`; `GEMMA_DIRECTOR_NUM_CTX` /
`GEMMA_DIRECTOR_TIMEOUT` added for local models (Ollama's VRAM default ctx is 4096).

1. [done] `videobench/` tier-1 pixel scorer (coverage, empty thirds, cut pacing, repeats,
   frozen holds) + `tasks.json`. `python -m videobench.score --references` checks ceiling >
   floor: mining 0.884 vs 0.715, H2S 0.989 vs 0.844. Known gap: repeat detector not yet
   validated on a real repeat (scores 0 on the old Gemma video that repeats a card).
2. [running] base rollout: gemma_director with a LOCAL model on h2s-sewers
   (`output/videobench/<task>/<label>/`), scored by videobench.
3. Rollout runner (`videobench/rollout.py`): init project → hf_prepare_project → director with a
   given model + harness dir → render → score → log.
4. Harness² loop over the director's prompts: k base rollouts → INV/DOM proposals (citing
   rollout moments) → validate → probe → aggregate → select → library/EXPERIENCE.md.
5. Tier-2 judge (vision model on snapshots) + user 1-5 ratings; label log for later laya.
6. Cloud validation run before adopting any harness.

## Compute

- Local: GTX 1660 Ti 6 GB — smoke tests with 4–9B models.
- **Kaggle (user's account, free weekly GPU/TPU; CLI configured at ~/.kaggle)**: batch rollouts
  with bigger open models (12–27B on 2×T4), and later laya fine-tuning. Run the whole batch
  inside a Kaggle notebook and download results; avoid tunnelling Kaggle's Ollama to the PC.
- Environment gotcha: Apply_Bot's Docker `ollama-server` (0.31.2, CPU-only) also binds 11434;
  native Ollama was upgraded to 0.40.7 (2026-10-21) and now answers on 127.0.0.1:11434.

## Benchmark redesign + first validation (2026-10-21/22)

User rejected pixel coverage as a quality measure ("a video is conveying information, making it
catchy"). Pixel checks are now a pass/fail GATE only. Quality tiers: comprehension (does it
convey the verified explanation), alignment, engagement (hook / first ~20 s — YouTube retention
shows viewers leave by ~25 s), with YouTube retention (`scheduler/youtube_analytics.py`) as the
long-run truth.

Comprehension protocol (`videobench/comprehension.py`, Kaggle twin `videobench/kaggle_task.py`):
- Frozen quiz per task, one MC item per verified claim (10-11 items), each tied to its beat.
- Frontier viewers know the chemistry (100% with no video), so accuracy-lift is useless.
  Viewer must answer ONLY from the frames, with "E) Not shown"; every item is also asked on a
  fixed foreign control (other task's hook) where the answer is always "not shown".
  comprehension = conveyed - leakage (abstaining scores 0).
- Planted-false-fact canary (user's idea): false label drawn on the hook frames; grounded =
  truth without video AND planted answer with doctored frames. Under the old "best guess"
  wording Claude Sonnet 5.5 / Gemini 3.6 ignored the planted card; under "answer only from the
  frames" all pass. Canary must sit on frames that do not contradict it (hook, not mechanism).

Result on Kaggle Benchmarks (all viewers grounded, leakage 0.00):

| task / video            | Claude Sonnet 5.5 | Gemini 3.6 Flash | Gemini 3.8 Flash | Gemma 4 31B |
|-------------------------|------|------|------|------|
| H2S, Claude-made        | 1.00 | 0.90 | 0.95 | 0.95 |
| H2S, gemma_director     | 0.75 | 0.70 | 0.70 | 0.70 |
| mining, Claude-made     | 1.00 | 0.95 | 0.95 | 1.00 |
| mining, old Gemma shots | 0.27 | 0.18 | 0.27 | 0.27 |

Ceiling > floor on both tasks for every viewer, and the viewers agree with each other.
Kaggle Benchmarks facts: see memory `kaggle-benchmarks-judges` (media only grounded when run ON
Kaggle; local proxy is text-only).

Gemma 4 31B (the production director's model family) is also grounded as a viewer and agrees
with the frontier viewers, so it can judge too.

## Move to Kaggle (2026-10-22, user: "run everything on kaggle")

Ollama's library has the production model's open weights (`gemma4:31b`, ~20 GB q4), so the
director rollouts can run the SAME model as production on Kaggle's free GPUs (2x T4) instead
of 4-9B proxies on the 6 GB laptop GPU -- no cloud budget used. Supersedes "inner loop on local
models". Caveats: local q4 != the cloud deployment, so adoption still wants one cloud check;
`gemma4:26b-a4b` (MoE, ~4B active) is the fast same-family fallback for inner-loop iterations.
Kaggle limits: ~12 h per GPU session, ~30 GPU-h per week -- budget k rollouts per week.
Step 0: smoke kernel `kaggle/rollout_smoke/` checks 2x T4, gemma4:31b on GPU at 32k ctx,
thinking support, and that HyperFrames renders headless on Kaggle.
