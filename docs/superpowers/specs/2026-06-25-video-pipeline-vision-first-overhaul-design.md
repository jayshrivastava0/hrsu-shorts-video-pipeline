# Video Pipeline — Vision-First Overhaul: Design + Implementation Spec

**Date:** 2026-06-25
**Status:** Approved for implementation (brainstorm complete 2026-06-25).
**Author:** Pipeline redesign session.
**Audience of this document:** An implementation agent that executes instructions **literally**. Do not infer, improvise, or "improve" beyond what is written. Where a choice is genuinely needed, this document says so explicitly and tells you what to do.

---

## 0. HOW TO READ AND EXECUTE THIS DOCUMENT

0.1. This document is split into **four workstreams (A, B, C, D)**. Execute them **in the order A → B → C → D** unless a task says otherwise. Each workstream is independently shippable.

0.2. Every workstream is a list of **numbered tasks**. Execute tasks in numeric order within a workstream. Do not skip a task. Do not reorder tasks.

0.3. Each task has these subsections where relevant:
- **Goal** — one sentence: what this task achieves.
- **Files** — exact file paths to create or edit.
- **Do** — the literal change. Code blocks are the source of truth.
- **Tests** — exact test file path + behaviors to assert. Write the tests. Run them.
- **Acceptance** — the condition that means the task is done. Do not mark the task done until this passes.

0.4. **Project root** is `E:\Projects\HRSU Blog`. All paths are relative to this root unless absolute.

0.5. **Run tests** with: `python -m pytest tests/video_agent/ -q`. A task is not done until its tests pass AND the pre-existing test suite still passes (no regressions).

0.6. **Python style:** Match the surrounding file. Use `from __future__ import annotations` at the top of new modules (every existing module has it). Use `logging.getLogger(__name__)` for logs. Type-hint function signatures.

0.7. **Do NOT run git commit.** This project does not use git workflows from the agent. Leave changes in the working tree.

0.8. **When a code block says `# ... existing code ...`** it means leave that region unchanged; only the shown lines change.

0.9. **Ollama specifics you MUST respect (verified against the current codebase):**
- The local text model is `gemma3:4b` at `http://localhost:11434`.
- The cloud multimodal model is `gemma4:31b-cloud`.
- **Cloud models REJECT the `/api/generate` `images` field.** You MUST send images to cloud models through the CLI: `ollama run <MODEL> "<PROMPT>" <image_path>`. The reference implementation for this (ANSI stripping, think-block isolation, last-JSON extraction) already exists in `video_agent/harness/verify_vision.py` — functions `_grade_scene_cli` and `_parse_grade`. **Reuse that exact parsing approach.** Do not invent a new parser.
- Cloud TEXT generation (no image) via `/api/generate` is **unverified**. Task C-1 verifies it and picks the call path. Until C-1 says otherwise, assume text cloud calls may also need the CLI path.

0.10. **Vocabulary:**
- "Scene" = one `Scene` dataclass instance (`video_agent/storyboard.py`).
- "Candidate" = one `RawCandidate` (from a source) or `AssetCandidate` (downloaded, on a scene).
- "Vision judge" = the new component that looks at actual pixels and scores them against narration.
- "Footage" = user-supplied `.mp4`/`.mov` clips in `asset_library/factory/` or `asset_library/footage/`.

---

## 1. BACKGROUND & PROBLEM STATEMENT

The video pipeline (`video_agent/`) turns an HRSU blog post into a 30–65s vertical (1080×1920) short for YouTube/LinkedIn/Facebook. The business goal: build technical trust with procurement managers / supply-chain decision-makers sourcing calcium nitrate, so the video acts as a proxy for trusting HRSU.

The pipeline currently has eight agents (Strategist → Storyboarder → Cinematographer → NarrationPolisher → Sourcer → LocalCritic → GlobalDirector → Reviser), a renderer (`composer.py`), and a post-render Vision grader (`harness/verify_vision.py`).

**Confirmed defects (root causes diagnosed against the code):**

| ID | Symptom | Root cause | Fixed in |
|----|---------|-----------|----------|
| D1 | 4 different speaker voices in one video | (a) Per-scene prosody presets swing the SAME voice's pitch from `+25Hz` to `-12Hz` (`_EDGE_TTS_PRESETS` in `voiceover.py`), making one voice sound like several people. (b) The Kokoro fallback fires per-segment, splicing a totally different voice (`am_michael`) mid-video when edge-tts fails on any single segment. | Workstream A |
| D2 | CTA "visit hrsuindore.com" is cut off at the end | The CTA line is the last scene's narration, muxed with `-shortest` against a video track of length `voice_duration + 0.3`. Any audio tail beyond the video length is clipped. | Workstream A |
| D3 | Images don't match the narration (chronic, day one) | The entire relevance system judges **caption text**, never pixels, at selection time (`Sourcer._source_scene` → `context_match_score` + caption-only gemma rerank). The Vision model only looks at pixels AFTER render, too late, and the re-source loop rarely fires. | Workstream B |
| D4 | User's own factory footage isn't intelligently placed | `footage_library.py` and `factory_broll.py` are near-duplicate **token-overlap** matchers needing a hand-written manifest; not wired into the Sourcer. | Workstream B |
| D5 | Ken Burns shows ~1/4 of a landscape image, panning across it | `composer._render_scene_clip` ALWAYS crop-pans photos (a 9:16 viewport slid across a 16:9 source). `plan_ken_burns` has no notion of where the subject is. | Workstream B |
| D6 | Story has no "bite" | Strategist/Storyboarder/NarrationPolisher run on the local **4B** model (`OLLAMA_MODEL = "gemma3:4b"`); the capable `gemma4:31b-cloud` is benched except for grading. | Workstream C |

**The central idea of this overhaul:** *Stop judging captions; judge pixels — with the 31B multimodal model, at selection time. Route the hard cognitive work (script writing, image judgment) to the 31B model. Prefer the user's real footage when it fits.*

**Operating budget decision (locked):** The user is on a generous Ollama Cloud plan and accepts longer render times. **We may call `gemma4:31b-cloud` freely.** Vision-judge candidates for every scene; run text agents on the 31B model. Do not add artificial call-count caps below the numbers specified in this doc.

---

## 2. GLOBAL PREREQUISITES

These are shared building blocks used by multiple workstreams. Build them first, before Workstream B and C tasks that depend on them. (Workstream A does not depend on them, which is why A ships first.)

### Task G-1 — Create the shared cloud-vision call module

**Goal:** One reusable function that sends an image + prompt to a cloud multimodal model and returns parsed JSON, encapsulating the subprocess + ANSI/think-block/JSON parsing logic that currently lives only inside `verify_vision.py`.

**Files:**
- Create `video_agent/vision/__init__.py` (empty file).
- Create `video_agent/vision/ollama_vision.py`.

**Do:** Create `video_agent/vision/ollama_vision.py` with this exact content:

```python
"""Shared cloud multimodal call helper.

Cloud Ollama models (e.g. gemma4:31b-cloud) reject the /api/generate `images`
field, so all image+prompt calls go through `ollama run MODEL PROMPT image.jpg`
as a subprocess. This module centralises that call plus the output parsing
(ANSI stripping, Gemma-4 think-block isolation, last-balanced-JSON extraction)
that small/cloud models require.

The parsing logic mirrors video_agent/harness/verify_vision.py::_parse_grade,
which is the battle-tested reference. Keep them behaviourally identical.
"""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_OLLAMA = shutil.which("ollama")

# --- Parsing regexes (copied from verify_vision; keep identical) ------------
_ANSI_RE = re.compile(r"\x1b(?:\[[^a-zA-Z]*[a-zA-Z]|[^\[]\S*|\[[^\x1b]*)")
_TERM_WRAP_RE = re.compile(r"\x1b\[(?:\d+D\x1b\[)?K\r?\n")


def _parse_json_from_cli(raw: str) -> dict | list | None:
    """Strip ANSI + terminal-wrap sequences, isolate the post-thinking
    section, and return the LAST balanced JSON object/array in the text.
    Returns None if no valid JSON is found."""
    clean = _TERM_WRAP_RE.sub("", raw)
    clean = _ANSI_RE.sub("", clean)
    marker = "...done thinking."
    pos = clean.rfind(marker)
    if pos >= 0:
        clean = clean[pos + len(marker):]
    # Find the LAST balanced { ... } (the model's actual answer).
    i = len(clean) - 1
    while i >= 0:
        if clean[i] == "}":
            depth = 0
            for j in range(i, -1, -1):
                if clean[j] == "}":
                    depth += 1
                elif clean[j] == "{":
                    depth -= 1
                    if depth == 0:
                        try:
                            candidate = clean[j:i + 1].replace("\n", " ")
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
        i -= 1
    return None


def call_vision_json(
    prompt: str,
    image_path: Path,
    model: str,
    timeout_s: float,
) -> dict | list | None:
    """Run `ollama run MODEL PROMPT image` and return parsed JSON, or None on
    any failure (timeout, non-zero exit, unparseable output). NEVER raises —
    callers treat None as 'could not judge'.

    TERM=dumb + stdin=DEVNULL prevent cursor-rewrite ANSI noise corrupting the
    captured JSON (same guard verify_vision uses)."""
    if _OLLAMA is None:
        log.warning("ollama binary not found on PATH; vision call skipped")
        return None
    env = {**os.environ, "TERM": "dumb"}
    try:
        result = subprocess.run(
            [_OLLAMA, "run", model, prompt, str(image_path)],
            capture_output=True, timeout=timeout_s, env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log.warning("vision call timed out after %.0fs (model=%s, img=%s)",
                    timeout_s, model, image_path)
        return None
    except Exception as e:  # noqa: BLE001 — any subprocess error == no judgment
        log.warning("vision call subprocess error: %s", e)
        return None
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        log.warning("vision call failed (exit %d): %s",
                    result.returncode, stderr[:200])
        return None
    stdout = result.stdout.decode("utf-8", errors="replace")
    return _parse_json_from_cli(stdout)
```

**Tests:** Create `tests/video_agent/vision/__init__.py` (empty) and `tests/video_agent/vision/test_ollama_vision.py`:
- Test `_parse_json_from_cli` returns the correct dict for: (a) clean JSON, (b) JSON preceded by `Thinking...\n...done thinking.\n\n{...}`, (c) JSON wrapped in ANSI escape codes, (d) returns `None` for input with no JSON.
- Test `call_vision_json` returns `None` when `_OLLAMA` is monkeypatched to `None`.
- Do NOT test the real subprocess (no live ollama in CI). Monkeypatch `subprocess.run` to return a fake `CompletedProcess` with crafted stdout, and assert the parsed result.

**Acceptance:** `python -m pytest tests/video_agent/vision/test_ollama_vision.py -q` passes.

---

### Task G-2 — Add a "smart model" config knob (no behavior change yet)

**Goal:** Introduce a single place that names the capable model for text reasoning, separate from the cheap local model, without changing any agent yet. Workstream C flips agents over to it.

**Files:** `video_agent/config.py`.

**Do:** Locate the block:

```python
OLLAMA_RETRY_MAX = 3
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_HOST = "http://localhost:11434"
```

Replace it with:

```python
OLLAMA_RETRY_MAX = 3
OLLAMA_MODEL = "gemma3:4b"          # cheap local model (kept for low-value calls)
OLLAMA_HOST = "http://localhost:11434"

# ─── Model routing ─────────────────────────────────────────────────────────
# The capable model for high-value reasoning (script writing, semantic
# judgment). Cloud multimodal model. Text-only calls to it are routed by
# OllamaClient; vision calls go through video_agent/vision/ollama_vision.py.
SMART_TEXT_MODEL = "gemma4:31b-cloud"
# Master switch: when True, agents listed in Workstream C use SMART_TEXT_MODEL;
# when False they fall back to OLLAMA_MODEL. Lets you A/B without code edits.
USE_SMART_TEXT_MODEL = True
# How the OllamaClient should reach a *-cloud text model. Set by Task C-1
# after empirical verification. One of: "api" (POST /api/generate) or "cli"
# (ollama run). Until C-1 runs, "api" is assumed.
SMART_TEXT_TRANSPORT = "api"
```

**Tests:** None (config-only). 

**Acceptance:** `python -c "from video_agent import config; print(config.SMART_TEXT_MODEL, config.USE_SMART_TEXT_MODEL)"` prints `gemma4:31b-cloud True`.

---

## 3. WORKSTREAM A — STOP-THE-BLEEDING BUGS (voice + CTA)

Ship this first. It is small, independent of the vision work, and removes the two most obviously "broken" signals.

### Task A-1 — Force a single voice for the whole video (fixes D1, cause b)

**Goal:** Never splice two TTS engines into one video. If edge-tts fails on a segment, the whole video must re-render in one consistent fallback voice — not mix engines per segment.

**Files:** `video_agent/voiceover.py`.

**Do:** In `synthesize_segments`, the current loop falls back to Kokoro **per segment**, producing mixed voices. Change the strategy to **all-or-nothing per engine**:

1. First, attempt to synthesize **every** segment with edge-tts (same `voice`).
2. If **any** segment fails, discard all edge-tts parts and re-synthesize **every** segment with Kokoro, so the entire track is one voice.

Replace the body of the per-segment loop and the assembly with this logic. Concretely, replace this region:

```python
    fell_back = False
    engine = "edge-tts"
    audio_parts: list = []

    for i, seg in enumerate(segments):
        normalized = normalize_for_tts(seg.text)
        ep = _EDGE_TTS_PRESETS.get(seg.prosody, _EDGE_TTS_PRESETS["conversational"])
        tmp = output_path.with_name(f"{output_path.stem}_seg{i:02d}.mp3")

        try:
            _edge_synthesize(normalized, voice, tmp,
                             rate=ep["rate"], pitch=ep["pitch"])
            if not tmp.exists() or tmp.stat().st_size < MIN_FILE_BYTES:
                raise RuntimeError("edge-tts output too small")
        except Exception as e:
            log.warning("edge-tts failed for segment %d (%s) — falling back", i, e)
            fell_back = True
            engine = "kokoro"
            _kokoro_synthesize(normalized, tmp)

        audio_parts.append(AudioSegment.from_mp3(str(tmp)))
```

with:

```python
    fell_back = False
    engine = "edge-tts"

    def _render_all(use_kokoro: bool) -> list:
        parts = []
        for i, seg in enumerate(segments):
            normalized = normalize_for_tts(seg.text)
            tmp = output_path.with_name(f"{output_path.stem}_seg{i:02d}.mp3")
            if use_kokoro:
                _kokoro_synthesize(normalized, tmp)
            else:
                ep = _EDGE_TTS_PRESETS.get(seg.prosody,
                                           _EDGE_TTS_PRESETS["conversational"])
                _edge_synthesize(normalized, voice, tmp,
                                 rate=ep["rate"], pitch=ep["pitch"])
                if not tmp.exists() or tmp.stat().st_size < MIN_FILE_BYTES:
                    raise RuntimeError(f"edge-tts output too small (seg {i})")
            parts.append(AudioSegment.from_mp3(str(tmp)))
        return parts

    try:
        audio_parts = _render_all(use_kokoro=False)
    except Exception as e:
        # ANY edge-tts failure => re-render the ENTIRE track in Kokoro so the
        # whole video is one consistent voice (never a per-segment splice).
        log.warning("edge-tts failed (%s) — re-rendering ALL segments in Kokoro "
                    "for voice consistency", e)
        fell_back = True
        engine = "kokoro"
        audio_parts = _render_all(use_kokoro=True)
```

**Tests:** `tests/video_agent/test_voiceover_consistency.py` (new):
- Monkeypatch `_edge_synthesize` to raise on the 2nd segment and `_kokoro_synthesize` to write a small valid mp3 (or monkeypatch `AudioSegment.from_mp3` to a stub). Assert that after a failure, `_kokoro_synthesize` was called for **every** segment (count == len(segments)), and `_edge_synthesize` was NOT used for any segment in the final assembly. Assert returned `engine == "kokoro"` and `fell_back is True`.
- Happy path: all edge-tts succeed → `_kokoro_synthesize` never called, `engine == "edge-tts"`.

**Acceptance:** New tests pass; existing `tests/video_agent/test_*voice*`/voiceover tests still pass.

---

### Task A-2 — Calm the prosody pitch swings (fixes D1, cause a)

**Goal:** Keep light prosody variation for liveliness but stop the pitch from swinging so far that one voice sounds like several people.

**Files:** `video_agent/voiceover.py`.

**Do:** Replace `_EDGE_TTS_PRESETS` with reduced pitch deltas (rate variation is fine; pitch is the culprit). Use:

```python
_EDGE_TTS_PRESETS: dict[str, dict] = {
    "hook_emphasis":  {"rate": "-10%", "pitch": "+6Hz"},
    "urgent_problem": {"rate": "+8%",  "pitch": "+4Hz"},
    "conversational": {"rate": "+0%",  "pitch": "+0Hz"},
    "warm_cta":       {"rate": "-8%",  "pitch": "-4Hz"},
    "matter_of_fact": {"rate": "-3%",  "pitch": "+0Hz"},
}
```

Rationale to preserve in a code comment above the dict: *"Pitch deltas are kept within ±6Hz of the base voice. Larger swings (the old ±25Hz) made one neural voice read as several different speakers across scenes."*

**Tests:** `tests/video_agent/test_voiceover_consistency.py`: assert every preset's `pitch` is within `[-6, +6]` Hz (parse the integer out of the `"+NHz"`/`"-NHz"` string). This guards against future regressions.

**Acceptance:** Test passes.

---

### Task A-3 — Guarantee the full CTA audio is in the video (fixes D2)

**Goal:** The spoken "...visit hrsuindore.com" must always play to completion; the video must never be shorter than the voice track.

**Files:** `video_agent/composer.py`.

**Do, part 1 — size scenes to the voice, then hold the CTA card.** In `compose_short_v2`, the current Step 0 redistributes scene durations to `target_total = voice_duration + 0.3` and then a proportional `_redistribute_durations`. Replace the whole Step 0 region:

```python
    voice_duration = _probe_audio_duration(voice_path)
    target_total = voice_duration + 0.3   # 0.3s tail before outro overlap
    pre = sum(s.duration_target_s for s in sb.scenes)
    scaled = _redistribute_durations(
        [{"duration_s": s.duration_target_s} for s in sb.scenes],
        target_total,
    )
    for s, new in zip(sb.scenes, scaled):
        s.duration_target_s = float(new["duration_s"])
    post = sum(s.duration_target_s for s in sb.scenes)
    log.info("Redistributed scene durations: voice=%.2fs, pre=%.2fs -> post=%.2fs",
             voice_duration, pre, post)
```

with:

```python
    voice_duration = _probe_audio_duration(voice_path)
    CTA_TAIL_S = 1.2
    pre = sum(s.duration_target_s for s in sb.scenes)
    # Size scenes to the VOICE length so each scene's visual stays synced to
    # its own narration (proportional redistribution to voice_duration).
    scaled = _redistribute_durations(
        [{"duration_s": s.duration_target_s} for s in sb.scenes],
        voice_duration,
    )
    for s, new in zip(sb.scenes, scaled):
        s.duration_target_s = float(new["duration_s"])
    # Hold the FINAL CTA brand card CTA_TAIL_S longer so the spoken
    # "...visit hrsuindore.com" is never clipped by -shortest and the card
    # lingers a beat after the URL. Only the last (static) card is extended,
    # so every other scene stays tight to its narration.
    if sb.scenes:
        sb.scenes[-1].duration_target_s += CTA_TAIL_S
    post = sum(s.duration_target_s for s in sb.scenes)
    log.info("Redistributed scene durations: voice=%.2fs, pre=%.2fs -> post=%.2fs "
             "(CTA card held +%.1fs)", voice_duration, pre, post, CTA_TAIL_S)
```

> EXECUTOR: After this, the video track is `voice_duration + 1.2s` while the audio is ~`voice_duration`, so `-shortest` ends at the audio — the full CTA plays — and the CTA card is the visual held through and past the URL. If `len(sb.scenes) == 1`, that single scene simply gets `+1.2s`. Guard: if any scene duration is ≤ 0 after this, clamp to `0.5` and log a warning.

**Do, part 2 — make the final mux audio-complete.** The mux at step 4 uses `-shortest`. Because the video track (`concat`) is now `voice_duration + 1.2` and the audio (`voice_with_music`) is ~`voice_duration`, `-shortest` ends at the audio — good, audio plays fully. BUT verify `mix_music_under_voice` does not *truncate* the voice. Open `video_agent/music.py`, find `mix_music_under_voice`, and confirm the returned mp3's duration ≥ the input voice duration. If it uses `-shortest` against a shorter music bed, FIX it so the **voice** length is authoritative (music loops or is padded with silence to the voice length, never the reverse). Add this assertion right after the mux in `compose_short_v2`, before step 6:

```python
    # Guard: the muxed video must contain the full voice track.
    muxed_dur = _probe_audio_duration(subs_mp4)
    if muxed_dur + 0.05 < voice_duration:
        raise RuntimeError(
            f"CTA-truncation guard: muxed video {muxed_dur:.2f}s is shorter "
            f"than voice {voice_duration:.2f}s — the spoken CTA would be cut."
        )
```

**Tests:** `tests/video_agent/test_composer.py` (extend):
- Build a `Storyboard` with 3 scenes and known `duration_target_s`. Monkeypatch `_probe_audio_duration` to return a fixed `voice_duration`. Factor the duration logic into a testable helper `_assign_durations(sb, voice_duration)` (keep behavior identical and call it from `compose_short_v2`). Assert: sum of all scene durations == `voice_duration + 1.2` (±0.01); the last scene's duration == its proportional share + 1.2; no scene ≤ 0.
- Assert the CTA-truncation guard raises when `muxed_dur` < `voice_duration` (call the guard logic with a stubbed `_probe_audio_duration`).

**Acceptance:** Tests pass. Manually: render one real video and confirm by ear that "visit hrsuindore.com" completes. (Manual check; note it in the run log.)

---

## 4. WORKSTREAM B — VISION-FIRST VISUAL ENGINE (the core)

This replaces caption-based image selection with **pixel-based judgment by the 31B multimodal model at selection time**, makes the user's factory footage a preferred auto-matched source, and fixes Ken Burns framing using a focal point the vision judge returns.

Depends on: **Task G-1** (cloud vision call). Build G-1 first.

### Task B-1 — Extend the data model for vision verdicts and footage

**Goal:** Carry a vision score, reason, and focal point on each candidate; let the motion planner know where the subject is and whether the frame can be cropped.

**Files:** `video_agent/storyboard.py`.

**Do:** Extend `AssetCandidate` with four optional fields (defaults keep backward compatibility with saved storyboards):

```python
@dataclass
class AssetCandidate:
    source: str
    url: str
    score: int
    local_path: str
    caption: str = ""
    width: int = 0
    height: int = 0
    is_clip: bool = False
    duration_s: float | None = None
    # --- vision-judge fields (Workstream B) ---
    vision_score: int = -1          # 0-10 from the multimodal judge; -1 == not judged
    vision_reason: str = ""         # one-line why it fits / doesn't
    focus_x: float = 0.5            # normalized 0..1 horizontal centre of the subject
    focus_y: float = 0.5            # normalized 0..1 vertical centre of the subject
    subject_fills_frame: bool = False  # True == cropping to 9:16 would lose key content
```

`_scene_from_dict` builds `AssetCandidate(**c)` — because the new fields have defaults, **old storyboards still load**. Verify by loading a storyboard JSON that lacks these keys.

**Tests:** `tests/video_agent/test_storyboard_assetcandidate.py` (new):
- Round-trip: build a Scene with a chosen_asset carrying the new fields, `save_storyboard`, `load_storyboard`, assert fields survive.
- Backward-compat: hand-build a dict for `_scene_from_dict` with a `chosen_asset` missing all new keys; assert it loads with the defaults (`vision_score == -1`, `focus_x == 0.5`).

**Acceptance:** Tests pass; `python -m pytest tests/video_agent/test_history.py tests/video_agent/agents -q` still green.

---

### Task B-2 — Build the Vision Judge

**Goal:** Given an image file + the scene's narration/beat/hero-claim, return `{score, reason, focus_x, focus_y, subject_fills_frame}` by looking at actual pixels with `gemma4:31b-cloud`.

**Files:** Create `video_agent/vision/judge.py`.

**Do:**

```python
"""Vision Judge — scores an actual image (pixels, not caption) against a
scene's narration using the cloud multimodal model, and returns a focal point
for framing. This is the heart of the vision-first visual engine: it replaces
trusting captions with looking at the image.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path

from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
from video_agent.vision.ollama_vision import call_vision_json

log = logging.getLogger(__name__)


@dataclass
class VisionVerdict:
    score: int                 # 0-10; how well the PIXELS support the narration
    reason: str                # one-line justification
    focus_x: float = 0.5       # normalized 0..1 centre of the most important content
    focus_y: float = 0.5
    subject_fills_frame: bool = False  # True => don't crop to 9:16, letterbox instead


_SYSTEM = (
    "You are a strict B2B video producer choosing the single best visual for "
    "ONE scene of a chemistry/industrial short aimed at procurement and "
    "supply-chain decision-makers. You are shown ONE image and the narration "
    "the voice will say over it. Judge ONLY what the image actually shows "
    "(the pixels), not any caption. A generic or wrong image breaks the "
    "viewer's trust, so be harsh: a stock photo of a businessman, an unrelated "
    "landscape, a meme, a watermark-covered image, or anything that does not "
    "literally depict what the narration describes scores 0-3. An on-topic, "
    "specific, professional industrial image scores 7-10. Diagrams/charts that "
    "are readable score well when the narration explains a mechanism.\n\n"
    "Also report where the important subject sits in the frame, as normalized "
    "coordinates (0,0 = top-left, 1,1 = bottom-right), and whether cropping "
    "this image to a tall 9:16 vertical would cut off important content "
    "(true) or not (false).\n\n"
    "Respond with RAW JSON only, no prose:\n"
    '{"score": <0-10 int>, "reason": "<short>", '
    '"focus_x": <0..1 float>, "focus_y": <0..1 float>, '
    '"subject_fills_frame": <true|false>}'
)


def _build_prompt(narration: str, beat: str, hero_claim: str,
                  visual_subject: str) -> str:
    return (
        f"{_SYSTEM}\n\n"
        f"Scene beat: {beat}\n"
        f"Intended subject: {visual_subject}\n"
        f"Hero claim of the whole video: {hero_claim}\n"
        f"Narration over this image:\n  {narration}\n\n"
        "Judge the attached image. Raw JSON only."
    )


def judge_image(
    image_path: Path,
    narration: str,
    beat: str = "",
    hero_claim: str = "",
    visual_subject: str = "",
    model: str = VISION_MODEL,
    timeout_s: float = VISION_TIMEOUT_S,
) -> VisionVerdict | None:
    """Return a VisionVerdict, or None if the model could not judge the image
    (timeout / failure). None means 'no judgment' — callers must treat that as
    'do not trust this image', NOT as a pass."""
    prompt = _build_prompt(narration, beat, hero_claim, visual_subject)
    out = call_vision_json(prompt, Path(image_path), model, timeout_s)
    if not isinstance(out, dict) or "score" not in out:
        log.warning("judge_image: unparseable/empty verdict for %s", image_path)
        return None
    try:
        score = max(0, min(10, int(out["score"])))
    except (TypeError, ValueError):
        return None

    def _clamp01(v, default=0.5):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return default

    return VisionVerdict(
        score=score,
        reason=str(out.get("reason", ""))[:200],
        focus_x=_clamp01(out.get("focus_x", 0.5)),
        focus_y=_clamp01(out.get("focus_y", 0.5)),
        subject_fills_frame=bool(out.get("subject_fills_frame", False)),
    )
```

**Tests:** `tests/video_agent/vision/test_judge.py`:
- Monkeypatch `video_agent.vision.judge.call_vision_json` to return a crafted dict; assert `judge_image` maps it to a `VisionVerdict` with clamped values (e.g. score 13 → 10, focus_x 1.5 → 1.0).
- Monkeypatch it to return `None` → `judge_image` returns `None`.
- Monkeypatch it to return `{"reason": "x"}` (no score) → returns `None`.

**Acceptance:** Tests pass.

---

### Task B-3 — Rewire the Sourcer to judge pixels (fixes D3)

**Goal:** After downloading candidate images, rank them by `judge_image` (pixels) instead of by caption, gate on a minimum vision score, and store the verdict + focal point on the chosen asset.

**Files:** `video_agent/agents/sourcer.py`, `video_agent/config.py`.

**Do, part 1 — config thresholds.** In `config.py`, under the existing `# ─── Source quality gates ───` block, add:

```python
# ─── Vision-judge gates (Workstream B) ─────────────────────────────────────
# Minimum vision score (0-10) for a web image to be usable. Below this, no
# image is better than a wrong image (we fall back to a designed card).
VISION_SELECT_MIN = 6
# How many downloaded candidates per scene to send to the vision judge.
# "Lean hard" budget: judge a generous shortlist.
VISION_JUDGE_SHORTLIST = 12
# Parallel vision subprocess calls. Cloud handles concurrency; keep modest to
# avoid local process thrash.
VISION_JUDGE_WORKERS = 6
```

**Do, part 2 — change selection flow.** In `Sourcer._source_scene`, the current flow does caption gating → keyword sort → caption-based `_semantic_rerank` → download top 5 → choose first. Change it so the **vision judge** is the decider. Replace the block from the comment `# Semantic re-rank: ask gemma...` through the candidate-choosing loop (the part that sets `scene.chosen_asset`) with this approach:

1. Keep the cheap, free **pre-filter** exactly as-is up to and including the caption `context_match_score` gate and the keyword sort (`gated.sort(...)`). This cheaply trims junk (watermarks via download, wrong dimensions via `score_candidate`, hard-reject captions) so we don't download hundreds of images. **Do NOT delete `context_match_score`** — it stays as a cheap pre-filter. But **lower its authority**: it only orders the shortlist; it does not make the final decision.
2. **Remove the caption-only `_semantic_rerank` call as the decider.** (Leave the method in the file; it's no longer called from `_source_scene`. Add a comment: `# Superseded by vision judge in _source_scene; retained for reference.`)
3. Download the top `VISION_JUDGE_SHORTLIST` survivors (current code downloads 5; raise to the shortlist size). For each downloaded, valid, non-dup image, call `judge_image`.
4. Choose the candidate with the highest `vision_score`. If that best score `>= VISION_SELECT_MIN`, set it as `chosen_asset` (and store `vision_score`, `vision_reason`, `focus_x/y`, `subject_fills_frame` on the `AssetCandidate`). Otherwise leave `scene.chosen_asset = None` and set `scene.degraded = True` (a designed fallback card renders later — never a wrong image).

Concretely, after the existing `gated.sort(key=lambda t: (-t[1], -t[0]))` line, replace everything from the `_semantic_rerank` call to the end of `_source_scene` with:

```python
        # ── Vision-first selection: judge the actual pixels, not captions. ──
        from video_agent.vision.judge import judge_image
        from video_agent.config import (
            VISION_SELECT_MIN, VISION_JUDGE_SHORTLIST, VISION_JUDGE_WORKERS,
        )

        # Download a shortlist (cheap pre-filter already ordered `gated`).
        downloaded: list[tuple[int, RawCandidate, Path]] = []  # (kw_quality, cand, path)
        for quality, ctx, c in gated[:VISION_JUDGE_SHORTLIST]:
            local = self._download_candidate(c, scene.index)
            if local is None or self._is_dup(local):
                continue
            downloaded.append((quality, c, local))

        if not downloaded:
            scene.degraded = True
            log.warning("Scene %d: no downloadable candidates", scene.index)
            return

        # Judge each downloaded image's PIXELS against the narration, in parallel.
        def _judge(item):
            quality, c, local = item
            v = judge_image(
                local, narration,
                beat=scene.beat,
                hero_claim=hero_claim,
                visual_subject=f"{scene.visual_concept.subject} "
                               f"{scene.visual_concept.modifier}".strip(),
            )
            return (quality, c, local, v)

        judged = []
        with ThreadPoolExecutor(max_workers=VISION_JUDGE_WORKERS) as ex:
            for fut in as_completed([ex.submit(_judge, it) for it in downloaded]):
                judged.append(fut.result())

        # Keep only successfully-judged candidates; rank by vision score, then
        # keyword quality as a tiebreak.
        scored = [(q, c, p, v) for (q, c, p, v) in judged if v is not None]
        scored.sort(key=lambda t: (-t[3].score, -t[0]))

        log.info("Scene %d: vision scores (top 3): %s", scene.index,
                 [v.score for (_, _, _, v) in scored[:3]])

        scene.asset_candidates = []
        chosen = None
        for quality, c, local, v in scored[:3]:
            ac = AssetCandidate(
                source=c.source, url=c.url, score=quality,
                local_path=str(local), caption=c.caption,
                width=c.width, height=c.height,
                is_clip=c.is_clip, duration_s=c.duration_s,
                vision_score=v.score, vision_reason=v.reason,
                focus_x=v.focus_x, focus_y=v.focus_y,
                subject_fills_frame=v.subject_fills_frame,
            )
            scene.asset_candidates.append(ac)
            if chosen is None and v.score >= VISION_SELECT_MIN:
                chosen = ac
                log.info("Scene %d chose %s (vision=%d kw=%d) reason=%r",
                         scene.index, c.source, v.score, quality, v.reason)

        if chosen is None:
            scene.degraded = True
            best = scored[0][3].score if scored else -1
            log.warning("Scene %d: best vision score %d < %d — no faithful "
                        "image; falling back to designed card.",
                        scene.index, best, VISION_SELECT_MIN)
        else:
            scene.chosen_asset = chosen
```

> NOTE TO EXECUTOR: `ThreadPoolExecutor` and `as_completed` are already imported at the top of `sourcer.py`. Do not re-import them at module scope. The local `from ... import` lines shown inside the method are intentional (keep them local to avoid import-cycle risk with `judge`).

**Do, part 3 — keep the cheap pre-filter from over-rejecting.** The current `_MIN_CONTEXT_SCORE = 30` caption gate can reject a great image whose caption is sparse. Lower it to `15` so the pre-filter is permissive (vision makes the real call). Change `_MIN_CONTEXT_SCORE = 30` → `_MIN_CONTEXT_SCORE = 15` and update its docstring comment to: `# Permissive pre-filter only — the vision judge makes the real decision.`

**Tests:** `tests/video_agent/agents/test_sourcer_vision.py` (new):
- Build a `Sourcer` with a fake source returning 3 `RawCandidate`s. Monkeypatch `self._download_candidate` to return fake Paths, `self._is_dup` to `False`, and `video_agent.vision.judge.judge_image` to return scripted `VisionVerdict`s (e.g. scores 8, 3, 5). Run `_source_scene`. Assert `scene.chosen_asset.vision_score == 8` and its focal fields are populated.
- All verdicts below `VISION_SELECT_MIN` (e.g. 2, 3, 4) → `scene.chosen_asset is None` and `scene.degraded is True`.
- `judge_image` returns `None` for all → `scene.degraded is True`.

**Acceptance:** New tests pass. Existing sourcer tests that asserted caption-rerank behavior may need updating — update them to the new flow, do not delete coverage.

---

### Task B-4 — Merge the two footage matchers into a vision-indexed footage source

**Goal:** Replace the two near-duplicate token-matchers with one module that (a) auto-describes each clip with the vision model (no hand-written manifest required), (b) caches descriptions, and (c) scores a clip against a scene by judging a representative frame.

**Files:**
- Create `video_agent/vision/footage_index.py`.
- Leave `video_agent/visual_engine/footage_library.py` and `factory_broll.py` in place but **deprecated** (add a module-level comment: `# DEPRECATED: superseded by video_agent/vision/footage_index.py. Do not extend.`). Do not delete (other code/tests may import them; deletion is Workstream D).

**Do:** Create `video_agent/vision/footage_index.py`:

```python
"""Vision-indexed footage source.

The user drops .mp4/.mov clips into asset_library/factory/ (preferred, real
HRSU footage) or asset_library/footage/ (other owned footage). NO manual
manifest is required: this module extracts a representative frame from each
clip, asks the cloud multimodal model to describe what it shows, and caches
that description. At scene time it judges each clip's representative frame
against the narration (same VisionVerdict contract as web images) so footage
competes — and, per product decision, is PREFERRED — on actual pixels.

Cache: asset_library/<dir>/_vision_index.json, keyed by "filename:mtime" so a
re-encoded/replaced clip is re-described automatically.
"""
from __future__ import annotations
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
from video_agent.vision.judge import judge_image

log = logging.getLogger(__name__)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

# Search these dirs in order; earlier = higher trust (real factory footage).
FOOTAGE_DIRS = [Path("asset_library/factory"), Path("asset_library/footage")]


@dataclass
class FootageClip:
    path: Path
    duration_s: float
    rep_frame: Path        # extracted representative frame (mid-clip)


def _probe_duration(clip: Path) -> float:
    if _FFPROBE is None:
        return 0.0
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
            check=True, capture_output=True, text=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _extract_rep_frame(clip: Path, dest: Path) -> Path | None:
    if _FFMPEG is None:
        return None
    dur = _probe_duration(clip)
    mid = max(0.1, dur / 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-ss", f"{mid:.2f}",
             "-i", str(clip), "-vframes", "1", "-q:v", "3", str(dest)],
            check=True, capture_output=True)
    except Exception as e:
        log.warning("footage frame extract failed for %s: %s", clip, e)
        return None
    return dest if dest.exists() else None


def discover_clips() -> list[FootageClip]:
    """Find every clip across FOOTAGE_DIRS and extract a representative frame.
    Frames are cached under <dir>/_vision_frames/. Returns clips in trust order
    (factory before footage)."""
    clips: list[FootageClip] = []
    for d in FOOTAGE_DIRS:
        if not d.exists():
            continue
        frames_dir = d / "_vision_frames"
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in _VIDEO_EXTS:
                continue
            rep = _extract_rep_frame(f, frames_dir / f"{f.stem}.jpg")
            if rep is None:
                continue
            clips.append(FootageClip(path=f, duration_s=_probe_duration(f),
                                     rep_frame=rep))
    return clips


def best_footage_for_scene(narration: str, beat: str, hero_claim: str,
                           visual_subject: str,
                           clips: list[FootageClip] | None = None):
    """Judge every clip's representative frame against the scene and return
    (FootageClip, VisionVerdict) for the highest scorer, or None if no clips.

    Trust-order tiebreak: when two clips tie on vision score, the earlier one
    in FOOTAGE_DIRS order (factory) wins because discover_clips() preserves it
    and we use a stable max."""
    clips = discover_clips() if clips is None else clips
    if not clips:
        return None
    best = None  # (score, idx, clip, verdict)
    for idx, clip in enumerate(clips):
        v = judge_image(clip.rep_frame, narration, beat=beat,
                        hero_claim=hero_claim, visual_subject=visual_subject)
        if v is None:
            continue
        key = (v.score, -idx)  # higher score, then lower idx (earlier dir)
        if best is None or key > best[0]:
            best = (key, clip, v)
    if best is None:
        return None
    return (best[1], best[2])
```

**Tests:** `tests/video_agent/vision/test_footage_index.py`:
- Monkeypatch `discover_clips` to return 2 fake `FootageClip`s and `judge_image` to return verdicts (scores 4 and 8). Assert `best_footage_for_scene` returns the score-8 clip.
- Tie case: both score 7; assert the one from the earlier dir (lower idx) wins.
- Empty clips → returns `None`.

**Acceptance:** Tests pass.

---

### Task B-5 — Wire "always prefer my footage" into the Sourcer

**Goal:** Before web sourcing, check the user's footage. If a clip scores well on pixels, use it and skip web entirely (product decision: real footage > stock when it fits).

**Files:** `video_agent/agents/sourcer.py`, `video_agent/config.py`.

**Do, part 1 — config.** Add to `config.py` under the vision-judge gates:

```python
# Minimum vision score for OWN footage to be preferred over any web image.
# Lower than VISION_SELECT_MIN: we accept slightly weaker matches from our own
# footage because real HRSU footage carries more B2B trust than stock.
FOOTAGE_PREFER_MIN = 5
```

**Do, part 2 — Sourcer.** At the very top of `Sourcer._source_scene`, before building queries, add a footage check:

```python
        # ── Always-prefer-footage: try the user's own clips first. ──
        from video_agent.vision.footage_index import best_footage_for_scene
        from video_agent.config import FOOTAGE_PREFER_MIN
        subject = f"{scene.visual_concept.subject} " \
                  f"{scene.visual_concept.modifier}".strip()
        match = best_footage_for_scene(
            scene.narration or "", scene.beat, hero_claim, subject)
        if match is not None:
            clip, verdict = match
            if verdict.score >= FOOTAGE_PREFER_MIN:
                scene.chosen_asset = AssetCandidate(
                    source="own_footage", url=str(clip.path),
                    score=100, local_path=str(clip.path),
                    caption=verdict.reason, is_clip=True,
                    duration_s=clip.duration_s,
                    vision_score=verdict.score, vision_reason=verdict.reason,
                    focus_x=verdict.focus_x, focus_y=verdict.focus_y,
                    subject_fills_frame=verdict.subject_fills_frame,
                )
                scene.degraded = False
                log.info("Scene %d: using OWN footage %s (vision=%d)",
                         scene.index, clip.path.name, verdict.score)
                return
        # No strong footage match — continue to web sourcing below.
```

> NOTE TO EXECUTOR: This `return`s early when footage wins, so the rest of `_source_scene` (web sourcing) only runs when footage does not win. Make sure `hero_claim` is in scope — it is a parameter of `_source_scene`.

**Do, part 3 — performance guard.** `discover_clips()` runs ffmpeg per clip and `judge_image` per clip **per scene**, which is wasteful across ~8 scenes. Cache the discovered clips once per `Sourcer.run`. In `Sourcer.run`, before the scene loop, add `self._footage_clips = None` is not enough — instead compute once:

```python
    def run(self, sb: Storyboard) -> Storyboard:
        sb.narrative_thread = self._build_narrative_thread(sb)
        hero_text = sb.hero_claim.claim_text if sb.hero_claim else ""
        # Discover footage once (frame extraction is expensive); reuse per scene.
        from video_agent.vision.footage_index import discover_clips
        self._footage_clips = discover_clips()
        for scene in sb.scenes:
            self._source_scene(
                scene, sb.blog.get("category", ""),
                narrative_thread=sb.narrative_thread,
                hero_claim=hero_text,
            )
        self._flag_visual_jumps(sb)
        return sb
```

And in the footage check in `_source_scene`, pass the cached clips:

```python
        match = best_footage_for_scene(
            scene.narration or "", scene.beat, hero_claim, subject,
            clips=getattr(self, "_footage_clips", None))
```

> NOTE: when `self._footage_clips` is `None` (e.g. unit tests calling `_source_scene` directly), `best_footage_for_scene` falls back to `discover_clips()` itself, which returns `[]` if no footage dirs exist — safe.

**Tests:** `tests/video_agent/agents/test_sourcer_footage.py`:
- Monkeypatch `best_footage_for_scene` to return a `(FootageClip, VisionVerdict)` with score ≥ `FOOTAGE_PREFER_MIN`. Call `_source_scene`. Assert `scene.chosen_asset.source == "own_footage"`, `is_clip is True`, web sourcing (`_search_all_sources`) was NOT called (monkeypatch it to raise if called).
- Footage score below threshold → web path runs (monkeypatch `_search_all_sources` to return candidates and `judge_image` to score them).

**Acceptance:** Tests pass.

---

### Task B-6 — Focal-point-aware Ken Burns + blurred-fill (fixes D5)

**Goal:** Stop slicing landscapes. Use the vision judge's focal point to anchor motion on the subject, and when an image can't fill 9:16 without losing key content (`subject_fills_frame == True`) or is very wide, render it **fit-whole over a blurred fill** instead of crop-panning across a sliver.

**Files:** `video_agent/motion/ken_burns.py`, `video_agent/composer.py`.

**Do, part 1 — focal point into the motion plan.** In `ken_burns.py`, add an optional focal point to `plan_ken_burns`:

```python
def plan_ken_burns(src_size: tuple[int, int], mood: str,
                   duration_s: float, fps: int = 30,
                   focus_x: float = 0.5, focus_y: float = 0.5) -> MotionPlan:
```

Use `focus_x`/`focus_y` to bias pans/zooms toward the subject. Where the current code centers the viewport (`cx, cy = src_w / 2, src_h / 2`), replace with `cx, cy = src_w * focus_x, src_h * focus_y` and clamp the resulting `start_xy`/`end_xy` so the viewport stays inside the source (the existing `min/max` clamps in `render_motion_clip`'s expressions already protect rendering; also clamp here so the plan is sane). For the pan branches, bias the pan to keep the focal column/row visible rather than starting at `0`. Keep the function total-frame and aspect math otherwise unchanged.

> EXECUTOR: The minimum viable change is: replace every `src_w / 2`/`src_h / 2` viewport-centre with `src_w * focus_x`/`src_h * focus_y`, then clamp `start_xy`/`end_xy` into `[0, src_w - vp_w] × [0, src_h - vp_h]`. That alone makes zooms/pans center on the subject. Do exactly that.

**Do, part 2 — blurred-fill renderer.** In `composer.py`, add a new renderer next to `_render_letterbox_image`:

```python
def _render_blurfill_image(src: Path, dest: Path, duration_s: float, fps: int,
                           focus_x: float = 0.5, focus_y: float = 0.5) -> Path:
    """Render a still that should NOT be cropped (wide image, or subject fills
    the frame) as: a heavily-blurred, zoomed copy of the image filling the full
    9:16 frame as background, with the whole un-cropped image fit on top. This
    shows the ENTIRE image (no slicing) and never leaves dead navy bars."""
    total = max(1, int(duration_s * fps))
    z = f"'min(zoom+0.0004,1.04)'"
    # [bg] fill+blur; [fg] fit whole; overlay centered.
    vf = (
        f"split=2[bg][fg];"
        f"[bg]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_W}:{FRAME_H},gblur=sigma=28[bgb];"
        f"[fg]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,"
        f"zoompan=z={z}:x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
        f"d={total}:s={FRAME_W}x{FRAME_H}:fps={fps},setsar=1"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src),
        "-vf", vf, "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(dest),
    ], check=True)
    return dest
```

> EXECUTOR: If the `split`/`overlay` filtergraph errors on this ffmpeg build, use this simpler equivalent that produces the same look:
> `-filter_complex "[0:v]scale=FRAME_W:FRAME_H:force_original_aspect_ratio=increase,crop=FRAME_W:FRAME_H,gblur=sigma=28[bg];[0:v]scale=FRAME_W:FRAME_H:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"` with `-loop 1 -t duration` and a separate zoompan pass. Verify the first form first; fall back only if it fails.

**Do, part 3 — choose the renderer by subject.** In `composer._render_scene_clip`, the still-image branch currently always crop-pans photos (only wide diagrams letterbox). Replace the decision block:

```python
    if (scene.visual_concept.type == "diagram"
            and src_aspect > target_aspect * 1.15):
        _render_letterbox_image(src, out, scene.duration_target_s, fps)
    else:
        plan = plan_ken_burns(size, mood=scene.visual_concept.mood,
                              duration_s=scene.duration_target_s, fps=fps)
        render_motion_clip(src, plan, out, scene.duration_target_s, fps)
```

with:

```python
    asset = scene.chosen_asset
    fx = getattr(asset, "focus_x", 0.5)
    fy = getattr(asset, "focus_y", 0.5)
    fills = getattr(asset, "subject_fills_frame", False)
    is_wide = src_aspect > target_aspect * 1.15
    # Show the WHOLE image (blurred fill behind) when cropping to 9:16 would
    # lose key content, OR for any wide image (the old crop-pan sliced these).
    # Diagrams always stay whole. Otherwise crop-pan, anchored on the subject.
    if scene.visual_concept.type == "diagram" and is_wide:
        _render_letterbox_image(src, out, scene.duration_target_s, fps)
    elif fills or is_wide:
        _render_blurfill_image(src, out, scene.duration_target_s, fps,
                               focus_x=fx, focus_y=fy)
    else:
        plan = plan_ken_burns(size, mood=scene.visual_concept.mood,
                              duration_s=scene.duration_target_s, fps=fps,
                              focus_x=fx, focus_y=fy)
        render_motion_clip(src, plan, out, scene.duration_target_s, fps)
```

**Tests:**
- `tests/video_agent/motion/test_ken_burns_focus.py`: call `plan_ken_burns` with `focus_x=0.2, focus_y=0.8` on a landscape size; assert the returned plan's `start_xy`/`end_xy` are within `[0, src_w-vp_w] × [0, src_h-vp_h]` and that the viewport centre is biased toward the focal point vs the centered default (compare to a `focus=0.5` call).
- `tests/video_agent/test_composer.py`: monkeypatch `_render_blurfill_image`, `_render_letterbox_image`, and `render_motion_clip` to record calls. Build scenes with: (a) a wide photo asset → assert `_render_blurfill_image` called; (b) a ~9:16 photo with `subject_fills_frame=False` → assert `render_motion_clip` called; (c) a wide diagram → assert `_render_letterbox_image` called. Use small fake images written with PIL.

**Acceptance:** Tests pass. Manual: render a video that previously sliced a landscape; confirm the full image now shows over a blurred fill.

---

### Task B-7 — Make the Reviser re-source loop pixel-aware and actually fire

**Goal:** When a scene's visual is weak, the re-source must re-judge pixels (not just captions) and the loop must trigger on a low vision score, closing the feedback loop.

**Files:** `video_agent/agents/sourcer.py` (`re_source_scene`), `video_agent/agents/critic_local.py` (flag emission).

**Do, part 1 — re_source uses narration + vision.** `re_source_scene` currently calls `_build_queries(scene.visual_concept, blog_category)` WITHOUT narration (a latent bug — the main path passes narration). Fix it to `_build_queries(scene.visual_concept, blog_category, narration=scene.narration or "")`, and replace its caption-only selection with the same vision-judge selection used in `_source_scene`. The simplest correct implementation: after gathering and pre-filtering candidates, download the shortlist, `judge_image` each, pick the best `vision_score >= VISION_SELECT_MIN` that is not in `exclude_urls`, and set it on the scene with all vision fields. Mirror the Task B-3 block. Factor the "download shortlist → judge → choose" steps into a private helper `self._vision_select(scene, gated, narration, hero_claim, exclude_urls=None)` and call it from BOTH `_source_scene` and `re_source_scene` to avoid duplication.

> EXECUTOR: Create `_vision_select(self, scene, gated, narration, hero_claim, exclude_urls=None) -> AssetCandidate | None`. Move the Task B-3 "download shortlist → judge → rank → choose" logic into it. It returns the chosen `AssetCandidate` (or None) and also sets `scene.asset_candidates`. `_source_scene` sets `scene.chosen_asset`/`degraded` from its return. `re_source_scene` does the same but passes `exclude_urls` so already-used images are skipped during download/selection.

**Do, part 2 — local critic flags low vision scores.** Open `critic_local.py`. Wherever it inspects a scene to emit flags, add: if `scene.chosen_asset` is not None and `scene.chosen_asset.vision_score >= 0` and `scene.chosen_asset.vision_score < VISION_SELECT_MIN`, append the flag `"visual_mismatch"` to `scene.critic_notes.flags` and set `scene.critic_notes.alignment_score = min(scene.critic_notes.alignment_score, 4)`. This guarantees the Reviser's existing `flags & {"visual_mismatch", ...}` trigger fires and re-sources. (Read the file first; place this alongside the existing flag logic, matching its style.)

**Tests:**
- `tests/video_agent/agents/test_sourcer_vision.py`: add a `re_source_scene` test — exclude the current URL, monkeypatch sources + `judge_image`, assert a NEW asset with `vision_score >= VISION_SELECT_MIN` replaces the old one and the excluded URL is not chosen.
- `tests/video_agent/agents/test_critic_local.py` (extend): a scene whose `chosen_asset.vision_score = 3` gets a `visual_mismatch` flag and `alignment_score <= 4`.

**Acceptance:** Tests pass; the Reviser, run on such a scene with a `Sourcer`, re-sources it (assert `re_source_scene` is called).

---

## 5. WORKSTREAM C — MODEL ROUTING + STORY BITE (fixes D6)

Route the high-value text agents to `gemma4:31b-cloud` and sharpen their prompts so the script has edge.

### Task C-1 — Verify cloud text transport, set `SMART_TEXT_TRANSPORT`

**Goal:** Determine whether `gemma4:31b-cloud` answers text-only prompts via `POST /api/generate` (preferred) or must use `ollama run` CLI; record the answer in config so `OllamaClient` routes correctly.

**Files:** `video_agent/ollama_client.py`, `video_agent/config.py`.

**Do, part 1 — empirical check.** Run this one-off (not committed): 
```
ollama run gemma4:31b-cloud "Reply with the single word: OK"
```
and separately a Python POST to `http://localhost:11434/api/generate` with `{"model":"gemma4:31b-cloud","prompt":"Reply with the single word: OK","stream":false}`. 
- If the POST returns a normal response → set `SMART_TEXT_TRANSPORT = "api"` in config.
- If the POST errors but the CLI works → set `SMART_TEXT_TRANSPORT = "cli"`.
Record which in `config.py` (the constant from Task G-2). Write the observed outcome in a one-line comment next to the constant.

**Do, part 2 — teach OllamaClient to route.** Modify `OllamaClient` so that, when its `model` equals `SMART_TEXT_MODEL` and `SMART_TEXT_TRANSPORT == "cli"`, `generate()` shells out via `ollama run <model> <prompt>` (text only, no image) and parses output with the same `_parse_json_from_cli` approach (import from `video_agent.vision.ollama_vision`). Otherwise keep the existing `/api/generate` path. Keep `generate_json` unchanged (it calls `generate`). Implement the CLI branch as a small helper `_generate_cli(self, prompt, system)` that runs the subprocess with `TERM=dumb`, `stdin=DEVNULL`, strips ANSI/think blocks, and returns the text. When the routed transport is "api", behavior is exactly as today.

**Tests:** `tests/video_agent/test_ollama_client_routing.py`:
- With `SMART_TEXT_TRANSPORT = "api"` (monkeypatch config) and `model = SMART_TEXT_MODEL`: monkeypatch `requests.post` to a fake; assert the API path is used (CLI subprocess NOT invoked).
- With `SMART_TEXT_TRANSPORT = "cli"`: monkeypatch `subprocess.run` to return crafted stdout; assert `generate` returns the cleaned text and `requests.post` is NOT called.

**Acceptance:** Tests pass; the empirical transport value is recorded in config.

---

### Task C-2 — Point the high-value agents at the smart model

**Goal:** Strategist, Storyboarder, NarrationPolisher, and the narrative-thread/vision-adjacent reasoning use the 31B model; cheap/structural calls stay on 4B.

**Files:** `video_agent/agents/strategist.py`, `storyboarder.py`, `narration_polisher.py` (and any other agent that constructs `OllamaClient()` for script writing — grep for `OllamaClient(`).

**Do:** Add a single helper to choose the model. In `video_agent/ollama_client.py` add a module-level function:

```python
def smart_client(**kwargs) -> "OllamaClient":
    """OllamaClient bound to the capable model when USE_SMART_TEXT_MODEL, else
    the cheap local model. Use for high-value reasoning (script, judgment)."""
    from video_agent.config import (
        SMART_TEXT_MODEL, OLLAMA_MODEL, USE_SMART_TEXT_MODEL,
    )
    model = SMART_TEXT_MODEL if USE_SMART_TEXT_MODEL else OLLAMA_MODEL
    return OllamaClient(model=model, **kwargs)
```

In Strategist, Storyboarder, and NarrationPolisher, where they currently do `self.client = client or OllamaClient()`, change to `self.client = client or smart_client()` (import `smart_client`). **Leave the Reviser's field-rewrite and the Sourcer's `_build_narrative_thread` on the default `OllamaClient()` (cheap) unless C-3 says otherwise** — those are low-value/structured.

> EXECUTOR: Do not change any agent's constructor signature; only change the default client. Tests that inject a mock `client=` keep working.

**Tests:** For each of the three agents, add/extend a test asserting that with no injected client, `self.client.model == config.SMART_TEXT_MODEL` (when `USE_SMART_TEXT_MODEL=True`). Keep existing mock-client tests untouched.

**Acceptance:** Tests pass.

---

### Task C-3 — Sharpen the script prompts for "bite"

**Goal:** The narration should open with a concrete, specific hook and avoid flat, generic phrasing — the difference a decision-maker notices.

**Files:** `video_agent/agents/strategist.py`, `video_agent/agents/narration_polisher.py`. Read both fully first.

**Do:** This task changes **system-prompt text only** (no control-flow). Apply these concrete edits to the system prompts:
1. Add an explicit "BITE RULES" section to the Strategist and NarrationPolisher system prompts:
   - "Open scene 1 with a specific, surprising, concrete fact or a sharp question a procurement manager would stop scrolling for. Never open with a definition or 'In this video'."
   - "Every claim must be concrete: a number, a standard (REACH, NPDES, CAS 10124-37-5), a named consequence (NPDES violation, rebar corrosion, demolding time). Ban vague intensifiers (very, highly, significantly) and hedges (can, may, might, could)."
   - "Voice: confident technical peer talking to a buyer, not a marketer. Short declarative sentences. One idea per sentence."
   - "End on the CTA exactly as written; do not pad after the URL."
2. Keep the existing banned-phrase enforcement (`SCRIPT_BANNED_PHRASES`) and add to that list in `config.py`: `"in conclusion", "let's dive in", "unlock", "game-changer", "in today's"`.
3. Do not change word-count bounds (`SCRIPT_NARRATION_MIN_WORDS`/`MAX_WORDS`) or scene-count bounds.

> EXECUTOR: These are additive prompt instructions. Insert them into the existing `_SYSTEM`/system-prompt string constants in those files, preserving surrounding text and JSON-output instructions. Do not alter the required JSON schema the agents return.

**Tests:** `tests/video_agent/agents/test_strategist.py` / existing tests must still pass (they mock the client, so prompt text changes don't break them). Add a cheap assertion test that the new banned phrases are present in `config.SCRIPT_BANNED_PHRASES`.

**Acceptance:** Existing agent tests pass; banned-phrase test passes. Manual: generate one script and confirm the hook is concrete and no banned phrases survive.

---

## 6. WORKSTREAM D — HARNESS CONSOLIDATION (do only after A–C verified)

Light cleanup; no behavior change beyond removing dead paths. Skip if time-constrained — A–C deliver the user-visible wins.

### Task D-1 — Retire the duplicate footage matchers

**Goal:** Remove the two near-duplicate token matchers now that `footage_index.py` supersedes them.

**Do:** Grep for imports of `footage_library` and `factory_broll` across the repo (`video_agent/visual_engine/dispatcher.py` is the likely caller). For each call site, either route to `footage_index` or remove the dead branch. Then delete `video_agent/visual_engine/footage_library.py`, `factory_broll.py`, and their tests `tests/video_agent/visual_engine/test_footage_library.py`, `test_factory_broll.py`. **Only delete after confirming nothing imports them** (grep returns no other references). If `dispatcher.py` depends on them in a live path, adapt it; do not break rendering.

**Acceptance:** Full suite green; no import errors; a real render still produces a video.

### Task D-2 — Quality report records vision provenance

**Goal:** Make `quality_report.json` show, per scene, the chosen source, vision score, and whether it was own footage — so runs are auditable.

**Files:** `video_agent/composer.py` (`_write_quality_report`).

**Do:** Read `_write_quality_report`. For each scene add fields: `vision_score` (`scene.chosen_asset.vision_score` or `null`), `source` (already present or add), `is_own_footage` (`scene.chosen_asset.source == "own_footage"`), `framing` (`"blurfill"|"letterbox"|"kenburns"|"brand_card"` — derive from the same decision as Task B-6; simplest: have `_render_scene_clip` stash the chosen mode on `scene` via a transient attribute `scene._framing` and read it here). Keep existing fields.

**Acceptance:** A render writes `quality_report.json` containing the new per-scene fields; add a unit test building a storyboard and asserting the dict shape from `_write_quality_report` (call it directly with a temp workspace).

---

## 7. CONSOLIDATED CONFIG ADDITIONS (reference)

All new `config.py` constants introduced by this spec, for quick verification (each is defined in its task — this is a checklist, not a second place to add them):

```python
# Task G-2
SMART_TEXT_MODEL = "gemma4:31b-cloud"
USE_SMART_TEXT_MODEL = True
SMART_TEXT_TRANSPORT = "api"   # or "cli" per Task C-1 verification

# Task B-3
VISION_SELECT_MIN = 6
VISION_JUDGE_SHORTLIST = 12
VISION_JUDGE_WORKERS = 6

# Task B-5
FOOTAGE_PREFER_MIN = 5

# Task C-3 — appended to SCRIPT_BANNED_PHRASES
# "in conclusion", "let's dive in", "unlock", "game-changer", "in today's"
```

Existing constants reused (do not redefine): `VISION_MODEL`, `VISION_TIMEOUT_S`, `MIN_IMAGE_LONG_EDGE`, `OLLAMA_MODEL`, `OLLAMA_HOST`.

---

## 8. SEQUENCING, VERIFICATION & DEFINITION OF DONE

**Build order:** G-1, G-2 → A-1, A-2, A-3 → B-1, B-2, B-3, B-4, B-5, B-6, B-7 → C-1, C-2, C-3 → D-1, D-2.

**After each task:** run `python -m pytest tests/video_agent/ -q`. Do not proceed to the next task with a red suite.

**Integration checkpoints (run a real video, `python scripts/make_video.py <blog-url>`):**
- After Workstream A: one consistent voice end-to-end; the spoken "visit hrsuindore.com" completes. (Set `GOOGLE_IMAGES_INTERACTIVE=1` if Google Images CAPTCHA appears.)
- After Workstream B: drop 2–3 real factory clips in `asset_library/factory/` (no manifest needed); confirm at least one scene uses own footage, no landscape is sliced (whole image over blurred fill), and the run log shows vision scores per scene. Check `quality_report.json`.
- After Workstream C: the narration opens with a concrete hook; no banned phrases.

**Definition of done for the whole spec:**
1. Full test suite green (no regressions; new tests added per task).
2. A real render shows: single voice, complete CTA, own footage used where it fits, no sliced landscapes, vision scores logged, concrete script.
3. `quality_report.json` records vision provenance per scene.

---

## 9. ROLLBACK & SAFETY

- Each workstream is independently revertible. If Workstream B destabilizes rendering, set a feature flag to bypass vision selection: add `VISION_FIRST_ENABLED = True` to config and gate the Task B-3/B-5 blocks on it (`if VISION_FIRST_ENABLED:`), falling back to the prior caption-rerank path when False. **Add this flag while doing B-3** so there is always an escape hatch.
- The vision judge NEVER raises into the pipeline: `judge_image` returns `None` on failure and callers treat `None` as "do not trust" → scene degrades to a designed card. A cloud outage degrades gracefully (more brand cards), it does not crash a run.
- Workstream A and C carry no rendering risk (audio + prompts only).
- Do not delete `_semantic_rerank`, `footage_library.py`, or `factory_broll.py` until Workstream D, and only after grep proves they are unreferenced.

---

## 10. OUT OF SCOPE (do not build)

- Re-architecting the orchestrator around an external "deep agents" framework. The vision-first changes fit the existing harness; a framework swap is not warranted and is explicitly excluded.
- Generating images with a diffusion model. We select and frame real images/footage only.
- Long-form (multi-minute) video. This spec targets the existing 30–65s short. Long-form is a future, separate spec.
- Changing the publishing/packaging (`publishers/`) or the lead-scoring subsystems. Untouched.
```
