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
