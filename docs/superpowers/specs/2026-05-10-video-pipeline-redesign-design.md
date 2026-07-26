# Video Pipeline Redesign — Director-Driven Multi-Agent Architecture

**Status:** Draft for review
**Date:** 2026-05-10
**Owner:** sujay.shrivastava@swastika.co.in

## 1. Problem Statement

The current video pipeline (`video_agent/`) produces videos that are mechanically functional but commercially weak:

- **No coherent takeaway.** Voiceover crams 5+ disjoint stats; a viewer can't summarize what they just watched.
- **No real visuals.** All scenes fall through to text cards or matplotlib charts. The blog cites authority PDFs that the pipeline tries to fetch, but most are CDN-blocked. Factory footage exists for ~2% of intended videos.
- **Charts that don't communicate.** Bar charts mix incompatible units; labels are sentence fragments. Even after the recent fix, charts are abstract enough that they hurt rather than help.
- **No narrative–visual alignment.** The LLM picks a `visual_type` for each scene before knowing whether any matching visual exists. On-screen text duplicates the voiceover instead of reinforcing it.
- **Choppy stitching.** Hard cuts between every scene, no motion on stills, no music bed, no consistent transition language.

The pipeline must scale to 100+ videos generated autonomously per week without manual asset provision.

## 2. Goals & Non-Goals

### Goals

1. Every video says **one thing well**, ending with an HRSU tie-back.
2. Every scene's visual is a real image or clip sourced from the open web (not a text card or generic chart) for ≥ 90% of scenes.
3. Voiceover, on-screen text, and visual all reinforce the same beat — verified by an automated critic, not hoped for.
4. Pipeline runtime, LLM cost, and failure modes are predictable across 100 videos.
5. Every intermediate artefact (outline, storyboard, sourcer output, critic verdicts) is on disk and debuggable in isolation.

### Non-goals (v1)

- CLIP-based or any ML visual-similarity scoring (token-overlap is sufficient at v1).
- Paid stock APIs (Shutterstock, Getty). Free sources only.
- Per-pixel watermark detection.
- Unbounded revision loops. Exactly one revision pass per video, ever.
- Localised translation of narration. Region affects voice and topic emphasis, not language re-translation.

## 3. High-Level Architecture

A 5-stage pipeline operating on a single shared object — `storyboard.json` — that grows progressively richer as it passes through each agent. Each stage reads the storyboard and writes back its slice; later stages can read everything earlier stages produced.

```
blog_record  ──►  Strategist     ─── adds: hero_claim, arc, supporting_facts
                       │
                       ▼
                  Storyboarder   ─── adds: scenes[].narration, on_screen_text,
                       │                    visual_concept, role_in_arc
                       ▼
                  Sourcer        ─── adds: scenes[].asset_candidates[],
                       │                    scenes[].chosen_asset
                       ▼
            ┌─►  Local Critic    ─── adds: scenes[].critic_notes
   parallel │
            └─►  Global Director ─── adds: storyboard.director_notes
                       │
                       ▼
                  Reviser        ─── ONE pass: regenerates only flagged
                       │                fields, with critic notes in prompt
                       ▼
                  Renderer       ─── voiceover + Ken Burns motion +
                                      transitions + music bed → MP4
```

### 3.1 Why this shape

- **Single shared object.** Every stage's I/O is a JSON patch on `storyboard.json`. Any stage can be re-run in isolation against an existing storyboard (`python -m video_agent.run_stage sourcer storyboard.json`). Debugging a bad video doesn't mean re-running the whole pipeline.
- **Bounded critique.** The Local Critic and Global Director run in parallel after sourcing, then the Reviser gets exactly one pass. No infinite loops; predictable cost.
- **Composable agents.** Each agent has a strict input/output contract. We can swap any individual agent later (e.g. upgrade Ollama → Claude API for the Strategist) without touching the others.

## 4. The Strategist

### 4.1 Responsibilities

- Reads the blog HTML and extracted facts (existing 5-tier extractor stays, unchanged).
- Picks **exactly one** hero claim — the single thing the video will say.
- Builds a 5-beat arc that supports that claim.
- Demotes other numeric facts to "supporting evidence" — they may appear in the mechanism or proof beats, but not as their own scene.

### 4.2 Hero-claim selection rule

Each candidate fact is scored on three axes (each 0–10):

- **Surprise** — is this counterintuitive or notable? (e.g., "90% reduction" beats "improved performance")
- **Specificity** — is there a precise number with a unit and clear context?
- **Audience-fit** — would a procurement manager in this region care?

Highest combined score becomes the hero claim. Ties are broken by preferring percentages over raw quantities (more cognitively sticky).

### 4.3 The 5-beat arc

| # | Beat | Purpose | Duration |
|---|------|---------|----------|
| 1 | **Hook** | State the hero stat as a question or claim. Hook the procurement manager in the first 3 seconds. | 3–4s |
| 2 | **Stakes** | Why this number matters — what's the cost of *not* solving it? | 5–7s |
| 3 | **Mechanism** | How calcium nitrate (or whatever the chemistry is) actually delivers that number. The science, simply. | 8–12s |
| 4 | **Proof** | One concrete validation — a regional case, a regulatory standard (REACH, EPA), or a comparison to the alternative (lime, aeration). | 8–12s |
| 5 | **HRSU tie-back + CTA** | "HRSU supplies this grade with X spec. Visit hrsuindore.com." | 4–6s |

Total target duration: 30–55s (well within the 30–65s composer constraint). One scene per beat — the storyboard's `scenes[]` and `arc[]` arrays are 1:1 by index.

### 4.4 Output (Strategist's slice of `storyboard.json`)

```json
{
  "hero_claim": {
    "stat": "90%",
    "claim_text": "Calcium nitrate cuts H₂S by 90% in industrial wastewater",
    "source_quote": "field trials at Hunter Valley showed 98–99% removal..."
  },
  "arc": [
    { "index": 0, "beat": "hook",      "purpose": "...", "duration_target_s": 3.5 },
    { "index": 1, "beat": "stakes",    "purpose": "...", "duration_target_s": 6.0 },
    { "index": 2, "beat": "mechanism", "purpose": "...", "duration_target_s": 10.0 },
    { "index": 3, "beat": "proof",     "purpose": "...", "duration_target_s": 10.0 },
    { "index": 4, "beat": "cta",       "purpose": "...", "duration_target_s": 5.0 }
  ],
  "supporting_facts": [ /* facts demoted from headline */ ]
}
```

## 5. The Storyboarder

### 5.1 Responsibilities

For each beat in the arc, generate:

- **narration** — verbatim sentence(s) the voice will say.
- **on_screen_text** — short phrase that *reinforces* the narration without duplicating it. Hard rule: the on-screen text must add information not in the voice (a number, a contrast, a name).
- **visual_concept** — a structured object describing what should be shown (not a free-text prompt).

### 5.2 Visual concept schema

```json
{
  "subject":    "acid mine drainage runoff",
  "modifier":  "rust-colored, industrial, stream",
  "type":       "photo",                 // photo | diagram | clip | chart_data
  "mood":       "problem",               // problem | mechanism | proof | brand
  "style_hint": "documentary"
}
```

The `type` field guides the Sourcer's source priority:

- `photo` → Google Images, Bing, Unsplash, DuckDuckGo
- `diagram` → Wikimedia Commons (priority), Google Images
- `clip` → YouTube
- `chart_data` → matplotlib (only when no visual makes sense — e.g., a numeric comparison)

### 5.3 On-screen text rules

The Storyboarder is given hard constraints:

- ≤ 6 words
- Must NOT be a substring or paraphrase of the narration
- Must add a number, a brand reference, or a contrast
- ALL CAPS for hook/CTA beats; sentence case for mechanism/proof

These constraints are validated programmatically before passing to the Critic. Failures retry up to 2× before passing through with a `text_unrelated` flag pre-set.

## 6. The Sourcer

### 6.1 Query generation

Each scene's `visual_concept` produces **3 query variants** to maximize hit rate:

```
specific  ← "{subject} {modifier}"
abstract  ← "{subject}"
generic   ← topic_default(blog.category)  // e.g., "industrial water treatment plant"
```

If the specific query returns ≥ 3 quality assets, abstract/generic fall away.

### 6.2 The six sources (parallel fan-out)

| Source | Purpose | Mechanism | Risk |
|--------|---------|-----------|------|
| **Google Images** | Highest hit-rate, broadest pool | Headless scrape; no API | Layout changes break it. Wrap in try/except, log breakage as WARNING. |
| **Bing Image Search** | Backup when Google fails | Free 1k req/mo with API key | Stable. |
| **Unsplash** | Hero shots, clean aesthetic | Official API | Stock-photo aesthetic; weighted lower for "mechanism" beats. |
| **Wikimedia Commons** | Diagrams, chemistry structures, regulatory references | Free API | Limited to factual/scientific content; perfect for mechanism beats. |
| **YouTube** | Real-world motion: industrial plants, lab footage | yt-dlp; pull a 6–10s segment from 20–40% mark to skip intros | Requires ffmpeg trim; clip licensing relies on fair-use convention. |
| **DuckDuckGo** | Deduplicated backup search | Existing image-search endpoint reused from citation flow | Low ceiling but free. |

### 6.3 Quality scoring

Each candidate scores 0–100 across these signals:

| Signal | Weight | Notes |
|--------|--------|-------|
| Resolution ≥ 1280×720 | +30 | Below threshold = hard reject |
| Aspect ratio 0.5–2.0 | +10 | Banners and tall-narrow images penalized |
| Token overlap (caption ↔ query) | +25 | Reuse `_tokenize` from existing source_extractor |
| Source authority (Wikimedia, Unsplash > scrape) | +10 | Lower legal risk, higher quality bias |
| File integrity (downloads, opens in PIL) | +15 | Dead links / broken files dropped |
| Duplicate of already-chosen scene (perceptual hash) | −100 | Hard kill |
| YouTube only: views > 10k & duration > 30s | +10 | "Is this real footage" signal |

Top 3 candidates per scene are written to `asset_candidates[]`; top-1 becomes `chosen_asset` (Critic may swap during revision).

### 6.4 Caching

```
output/_image_cache/<sha1(query)>/
  ├── meta.json
  ├── 01_full.jpg
  └── …
```

A repeat run on the same blog hits cache for ~100% of scenes. Cache TTL: 30 days.

### 6.5 Aspect handling — Ken Burns by default

Output video is 1080×1920 portrait; most sourced content is landscape. **Default behaviour: pan a portrait viewport across the landscape image at ~0.6 px/frame.** This shows more content than any static crop and feels cinematic.

Direction is mood-aware:

- `problem` → slow downward drift
- `mechanism` → zoom in (focusing attention)
- `proof` → pan left-to-right (revealing)
- `brand`/`cta` → zoom out (concluding)

Near-square images (aspect 0.9–1.1) get a slow centred zoom-in instead — Ken Burns motion would be too small.

The Ken Burns trajectory is constrained by §9.4's safe-zone rule: pan endpoints are clamped to keep the focal subject (or centre 60% as fallback) within the 1080×1920 visible area at every frame. Candidates whose aspect would force unsafe motion are rejected at scoring time.

YouTube clips bypass Ken Burns — they already have native motion. Trim, time-stretch to scene duration, ship.

### 6.6 Failure & fallback chain

If after fanning all six sources, **zero** candidates score ≥ 40 for a scene:

1. **Wider query**: drop specific terms, retry generic
2. **Stock fallback**: search Unsplash with topic-only query
3. **Last resort**: render a matplotlib chart or text card; mark scene `degraded: true`

Anything degraded surfaces in the Director's review; the pipeline does not hard-fail. The video ships, and `quality_report.json` records which scenes were degraded so we can track this metric across the 100-video corpus.

## 7. The Critics

Two critics run **in parallel** after sourcing.

### 7.1 Local Critic (per-scene sense-maker)

One Ollama call per scene. Inputs: hero claim, beat role, this scene's narration / on-screen text / visual caption / chosen asset metadata, plus the previous scene's narration for transition context.

**Strict JSON output:**

```json
{
  "alignment_score": 8,
  "flags": ["text_duplicates_voice"],
  "revision": "On-screen text just repeats the narration. Replace with the
               number being illustrated, e.g. '2× faster than aeration'."
}
```

Hard flags the Critic enforces:

| Flag | Means |
|------|-------|
| `voice_visual_mismatch` | Narration talks about X, image shows Y |
| `text_duplicates_voice` | On-screen text just transcribes the voiceover |
| `text_unrelated` | On-screen text talks about something neither voice nor visual mentions |
| `weak_transition` | Doesn't logically follow the previous scene |
| `degraded_visual` | Sourcer fell back to text card |
| `off_hero_claim` | Scene doesn't serve the hero claim — feels tangential |
| `unit_confusion` | Numbers without units, or comparing apples to oranges |

`alignment_score < 7` flags the scene for revision.

### 7.2 Global Director (arc-checker)

One Ollama call per video. Input: a 5-line outline summarising the storyboard.

**Strict JSON output:**

```json
{
  "arc_quality": 7,
  "hero_claim_supported": true,
  "weakest_beat": 2,
  "missing": ["proof beat lacks a regional anchor"],
  "redundant": [],
  "ending_strength": 6,
  "revision_for_strategist": "Mechanism beat (#2) is too abstract for a
    procurement audience. Add a one-line cost-comparison vs the alternative
    (lime) so the proof beat (#3) has something concrete to point to."
}
```

(Beat indices in director output reference the 5-beat arc, so values are 0–4.)

The Director catches whole-arc problems no per-scene critic can see (redundancy, missing beats, weak ending). Its revision feedback goes back to the Strategist, not just the Storyboarder — structural rewrites happen at the right level.

## 8. The Reviser

**Hard rule: maximum one revision pass per video, ever.** No loops.

```
1. If Director flagged a structural issue (missing beat / redundant scene):
     - Strategist regenerates the outline with director_notes in prompt
     - Storyboarder regenerates only affected scenes
     - Sourcer re-runs for those scenes

2. Otherwise, for each scene with Local Critic alignment_score < 7:
     - Regenerate ONLY the flagged field (narration | on_screen_text |
       visual_concept) with the critic note in the prompt
     - If visual_concept changed, Sourcer re-runs for that scene

3. Local Critic re-evaluates only touched scenes. Scores logged but no
   further revision happens — we ship what we have.
```

After revision, anything still flagged is logged to `quality_report.json` alongside the video. After 50 videos this corpus tells us where to invest next.

## 9. The Renderer

### 9.1 Motion

As described in §6.5: Ken Burns on stills with mood-aware direction; native motion on YouTube clips.

### 9.2 Transitions — beat-aware, not scene-aware

- **Within the same beat** (rare but possible) → hard cut
- **Between adjacent beats** → 250ms cross-dissolve
- **Hook → Stakes** specifically → whip-pan (energy injection at the front of the video)
- **Last beat → CTA** → slow fade-up to HRSU brand card

### 9.3 On-screen text — animated, not popped

- Fade up from opacity 0 over 200ms, with a 2px upward drift
- Stays for `narration_duration - 300ms` (exits before voice finishes — viewers' eyes follow the next visual)
- Style: HRSU gold (`#d4af37`), Poppins Bold, 30%-opacity drop shadow for legibility on busy images

### 9.4 Safe-zone enforcement (no clipping, no overflow)

Every frame the composer renders must respect a 1080×1920 safe zone — nothing important is allowed to bleed off the canvas. This is checked, not assumed.

**Hard rules:**

- **Outer safe zone:** 60px margin on all sides. No content (text, chart, focal subject) extends into this margin.
- **Bottom safe zone:** 240px reserved at the bottom for subtitles. On-screen text and footer chrome (`hrsuindore.com`) live above subtitles, never overlapping.
- **Top safe zone:** 120px reserved at the top for the title bar / hook line.

**On-screen text:**

- Wrapped to ≤ 18 characters per line via the existing label-cap rule (already in `infographic._bar`); extended to all text overlays.
- Auto-shrinks font size by 10% steps until the rendered bounding box fits the safe zone, with a hard floor of 22pt — below that the text is split across two lines instead of shrinking further.
- Verified after rendering by re-measuring the rasterised glyph extent (PIL `ImageDraw.textbbox`); fail-loud if any text exits the safe zone.

**Charts (matplotlib fallback):**

- The recent fix in `infographic._bar` (margins 0.16 / 0.72, 18-char label cap, 30° rotation) becomes the standard for all chart variants. Same enforcement applied to `_callout_stat`, `_comparison`, `_flow`, `_line`.
- After rendering, the composer reads the PNG and checks the bottom-most non-background pixel row. If it's below the bottom safe zone (i.e., the chart is being clipped by subtitles), the chart is re-rendered with a tighter axes box.

**Images / clips (Ken Burns frames):**

- The Ken Burns viewport is constrained so its trajectory never moves the focal subject outside the safe zone. The pan endpoints are clamped to keep at least 80% of the source image's largest face/object detection box (or its centre 60% if no detection) within the visible 1080×1920 area.
- For source images smaller than the target frame in either dimension, the composer pillarboxes with a blurred copy of the same image at 30% opacity rather than upscaling and degrading.
- Aspect-ratio sanity: if a candidate image's aspect requires Ken Burns motion of >0.6 px/frame to avoid clipping, the candidate is rejected by the Sourcer at scoring time (added as a hard signal alongside resolution).

**Validation gate:**

Before the composer writes the final MP4, it samples 12 evenly-spaced frames and runs three assertions per frame:

1. No text glyph's bounding box exits the safe zone.
2. No chart visual extends below the subtitle band.
3. No focal subject (or, absent detection, the centre 50%) falls outside the visible frame.

Failure on any frame raises a `ComposerError` with the offending frame number — the video is not shipped, and the failure is logged to `quality_report.json` for that scene to be re-rendered (degraded fallback applies if rerender fails).

### 9.5 Music bed

- One royalty-free track per region under `asset_library/music/<region>.mp3` (user-provided). Same music in all videos for that region = brand association.
- Mixed at -20dB under voiceover with sidechain ducking (-12dB additional duck when voice is present).
- Fades in over the hook, fades out under the CTA's last second.

## 10. File Layout

```
output/videos/<slug>/
├── storyboard.json        # single source-of-truth shared object
├── strategist.log         # raw Ollama output for debugging
├── storyboarder.log
├── sourcer.log
├── critic_local.json      # per-scene critic output
├── critic_global.json     # director output
├── revision_diff.json     # what changed during the rewrite pass
├── quality_report.json    # final scores + any unresolved flags
├── voiceover.mp3
├── subtitles.srt
├── scenes/
│   ├── scene_00.png       # final chosen asset (or rendered chart/text card)
│   └── …
└── video_short.mp4
```

Cache:

```
output/_image_cache/<sha1(query)>/
  ├── meta.json
  └── *.jpg
```

## 11. Module Decomposition

```
video_agent/
├── agents/                     # NEW — one file per agent
│   ├── strategist.py
│   ├── storyboarder.py
│   ├── sourcer.py
│   ├── critic_local.py
│   ├── critic_global.py
│   └── reviser.py
├── sources/                    # NEW — one file per image/video source
│   ├── base.py                 # BaseSource interface
│   ├── google_images.py
│   ├── bing.py
│   ├── unsplash.py
│   ├── wikimedia.py
│   ├── youtube.py
│   └── duckduckgo.py
├── motion/                     # NEW — Ken Burns + transitions
│   ├── ken_burns.py
│   └── transitions.py
├── orchestrator.py             # NEW — the 5-stage pipeline
├── run_stage.py                # NEW — re-run a single stage on existing storyboard
├── storyboard.py               # NEW — schema + load/save helpers
│
├── script_builder.py           # KEPT but slimmed; fact extraction stays
├── ollama_client.py            # KEPT
├── voiceover.py                # KEPT
├── subtitles.py                # KEPT
├── composer.py                 # MAJOR REWRITE — uses motion/, transitions, music
├── visual_engine/              # KEPT (text_card, chart fallback) but de-prioritised
│   └── …
└── config.py                   # KEPT
```

Existing modules retained: `ollama_client`, `voiceover`, `subtitles`, fact extraction (the 5-tier extractor in `script_builder`), `text_card` and `infographic` as last-resort fallbacks.

Existing modules deprecated (replaced by new agents):

- `script_builder.build_script` orchestration → replaced by `orchestrator.py`
- `_scene_breakdown`, `_write_narration` → absorbed by Storyboarder
- `_inject_bar_chart`, `_fill_callout_stats` → no longer needed (charts are last-resort fallback only; not the default visual)
- `_attach_sources` → absorbed by Sourcer (with broader source list)

`composer.py` gets a major rewrite to consume motion specs and apply transitions/music.

## 12. Testing Strategy

- **Per-agent unit tests** (mocking Ollama with canned JSON responses): each agent's input/output contract.
- **Per-source unit tests** (mocking HTTP): each Sourcer source produces well-formed asset records.
- **Integration test** (mocked Ollama + mocked HTTP): full pipeline end-to-end on a fixture blog. Should always produce a valid storyboard with all five beats present.
- **Smoke test** (real Ollama, real network): one canonical blog URL run end-to-end. Asserts `video_short.mp4` exists, duration ∈ [30, 65], at least 60% of scenes have a real image (not text card).
- **Safe-zone test**: a synthetic storyboard with deliberately oversized text/charts/extreme-aspect images. The composer's safe-zone validation gate (§9.4) must reject every offending frame and trigger the rerender path. No frame in the final MP4 may have content outside the safe zone — verified by sampling 12 frames and running glyph/contour bounding-box checks.
- **Quality regression corpus**: 5 canonical blogs covering each region; their `quality_report.json` outputs are committed and diffed on each pipeline change. Catches regressions in critic outputs.

## 13. Cost & Runtime Budget

Per video:

| Stage | LLM calls | Approx wall time |
|-------|-----------|------------------|
| Strategist | 1 | ~5s |
| Storyboarder | 1 (5 scenes in one call) | ~10s |
| Sourcer | 0 (HTTP only) | ~30–60s (parallel, network-bound) |
| Local Critic | 5 (parallel) | ~15s |
| Global Director | 1 | ~5s |
| Reviser | 0–3 | 0–15s |
| Voiceover | edge-tts | ~10s |
| Subtitles | whisper | ~10s |
| Composer | ffmpeg | ~60–90s |
| **Total per video** | **~8–11 Ollama calls** | **~3–4 minutes** |

100 videos/week ≈ 400 Ollama calls and ~6 hours of total runtime — comfortably within local Ollama capacity.

## 14. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Google Images scrape breaks on layout change | Bing API as automatic fallback; failure logged loudly; degraded scenes flagged in `quality_report` |
| Ollama JSON parse failures from a critic | Retry 2× with stricter prompt; if still failing, scene passes through without critique flag (logged) |
| Sourcer returns watermarked or NSFW content | Watermark detection deferred (v2). NSFW filter via `safe-search` parameter on each source where available; user manually reviews `quality_report` for the first 20 videos to catch leaks |
| Music track not provided for a region | Composer renders without music; logs a WARNING; video still ships |
| YouTube fair-use clip causes a Content ID strike | Only used for ≤ 10s segments; if strikes start happening, disable YouTube source globally via config flag |

## 15. Migration Plan (high level — detailed plan follows in implementation plan)

Phased rollout:

1. **Phase 1 — Sourcer + storyboard schema.** Build the new sourcer and the shared `storyboard.json` schema, plug into the *existing* script-builder via a thin adapter. Validates "real images" on top of the current script. Ship as opt-in flag: `python scripts/make_video.py <url> --new-sourcer`.
2. **Phase 2 — Strategist + Storyboarder.** Replace the existing narration/scene-breakdown logic. Default flag flips: new is default, old is `--legacy`.
3. **Phase 3 — Critics + Reviser.** Add the critique loop. No new flag — always-on once enabled.
4. **Phase 4 — Renderer upgrades.** Ken Burns, transitions, music bed.
5. **Phase 5 — Cleanup.** Delete legacy paths; remove `--legacy` flag.

Each phase is independently shippable and produces a video the user can compare against the previous phase's output.

## 16. Open Questions

None. All decisions captured above.

---

## Appendix A — Storyboard schema (authoritative)

```json
{
  "version": "2.0",
  "blog": { "id": "...", "url": "...", "title": "...", "region": "...",
            "category": "...", "persona": "..." },

  "hero_claim": {
    "stat": "90%",
    "claim_text": "...",
    "source_quote": "..."
  },

  "arc": [ /* 5 beat objects */ ],

  "supporting_facts": [ /* facts demoted from headline */ ],

  "scenes": [
    {
      "index": 0,
      "beat": "hook",
      "narration": "...",
      "on_screen_text": "...",
      "visual_concept": {
        "subject": "...", "modifier": "...",
        "type": "photo|diagram|clip|chart_data",
        "mood": "problem|mechanism|proof|brand",
        "style_hint": "..."
      },
      "duration_target_s": 3.5,
      "transition_in": "cut|fade|whip_pan",
      "asset_candidates": [
        { "source": "google_images", "url": "...", "score": 78,
          "local_path": "scenes/_cache/...", "caption": "..." }
      ],
      "chosen_asset": { /* one of the candidates */ },
      "motion": { "type": "ken_burns|zoom|none",
                  "direction": "down|left|right|in|out", "speed_px_per_frame": 0.6 },
      "critic_notes": {
        "alignment_score": 8,
        "flags": [],
        "revision": null
      },
      "degraded": false
    }
  ],

  "director_notes": {
    "arc_quality": 7,
    "hero_claim_supported": true,
    "weakest_beat": 3,
    "missing": [],
    "redundant": [],
    "ending_strength": 6,
    "revision_for_strategist": null
  },

  "metadata": {
    "title": "...", "description": "...", "hashtags": [],
    "estimated_duration_s": 38.2,
    "_pipeline_version": "2.0"
  }
}
```
