# Shorts Engine (v3) — Clean-Slate Design

**Date:** 2026-07-04
**Status:** Approved design (brainstorm complete 2026-07-04). Next step: implementation plan.
**Supersedes:** `2026-06-25-video-pipeline-vision-first-overhaul-design.md` (partially implemented; its diagnosis remains valid, its retrofit strategy is abandoned in favor of this clean slate).
**Project root:** `E:\Projects\HRSU Blog`

---

## 1. Problem statement (evidence from the 2026-06-25 run)

The run `output/videos/optimizing-nitrate-removal-via-granular-html` was inspected frame-by-frame, artifact-by-artifact. Confirmed failures:

| # | Failure | Evidence |
|---|---------|----------|
| F1 | 16 of 34.5 seconds are a blank navy screen | Scenes 1 (6s) and 2 (10s): no candidate passed, `degraded: true` renders an empty card. On-screen text ("Production Shutdowns. Massive Fines.") never drawn. |
| F2 | Watermarked, unlicensed stock preview shipped | Scene 0: Adobe Stock preview from `ftcdn.net`, 753×1000 (below the 1280px floor), scoring said **-100 (hard reject)** — shipped anyway because vision judge said 9/10. |
| F3 | Vision judge hallucinates | Scene 4 candidates (Springer figure, Wikipedia *ammonium* nitrate molecule, students on a factory tour) all scored **10/10** with reason "Perfectly matches: HRSU branded calcium nitrate bags stacked in a warehouse" — false for all three. Judge grades the caption/prompt, not pixels. Judge output text also corrupted ("brabranded", "spespecific") — CLI parser mangles characters. |
| F4 | Storyboard demands unfilmable photos | "EU government official inspecting a German chemical plant holding a tablet with red warning signs" → searched on DuckDuckGo. "HRSU branded packaging" cannot exist on the open web. |
| F5 | Diagram request never routed to the diagram renderer | Scene 2 asked for `type: diagram` (ion exchange) — pipeline web-searched for a diagram, found nothing, rendered blank. `visual_engine/infographic.py` (flow/bar/comparison/callout/line) was never called. |
| F6 | Script numbers are fabricated | Hero claim "reduces nitrate levels by 150 mg/L", "30°C max reaction temperature", "pH +25%" — **none appear anywhere in the blog post**. The Strategist invented them including a fake "verbatim" source quote. The blog's real numbers (1.5–3 kg/m³ dosing) were ignored. |
| F7 | LLM input is a poisoned scrape | The blog "content" fed to the model is the entire Blogger page: nav chrome, share buttons, and the *previous post's teaser* (German market forecast text bled into a wastewater video). |
| F8 | Verification is decorative | Post-render vision grading: 2 of 5 scenes "LLM call failed" → treated as skip; `cycles_used: 0` despite `hold: true`. The revise loop never ran. |
| F9 | Pacing wrong for the medium | One asset per story beat = single static Ken Burns shot held 6–10s. Short-form grammar is a cut every 2–3s. |
| F10 | Voice pipeline leaks failures | `voiceover_seg01.mp3` = 0 bytes on disk, silently. |

**Root cause, one sentence:** the pipeline treats "find a matching web photo for each narrated sentence" as the primary visual strategy and designed graphics as a failure state, and no gate between idea → script → visual → render is actually enforced.

---

## 2. Locked decisions (user-approved 2026-07-04)

| Decision | Choice |
|----------|--------|
| Visual grammar | **Designed-first.** Branded motion graphics are the default for every shot; real photos/footage appear only when a verified asset passes all gates. |
| Own footage | None yet; user will shoot phone footage soon. System must work 100% without it and auto-upgrade when files are dropped in. Capture checklist: Appendix C. |
| Fact policy | **Verbatim-only.** Every number/claim in narration must be string-locatable in the blog post body (or in `brand_facts.yaml`). Hard gate before TTS. |
| Allowed differentiators | Powder purity/quality; supply reliability & MOQ; sustainability story. **Export/compliance-docs claims (e.g., REACH) are NOT approved** — never claimed unless the user adds them to `brand_facts.yaml` personally. |
| Build scope | **Clean slate as a new package** (`shorts_engine/`), zero imports from the old creative stack, reusing proven leaf modules as libraries. Old `video_agent` untouched until v3 wins, then deleted. |
| Web sourcing | Free sources only, in ladder order: own library → blog's own images → free license-aware APIs (Pexels/Pixabay/Unsplash keys already held; Openverse + Wikimedia need no key) → **DDG/Google scraping kept as gated last resort** (user accepts license risk; watermark/quality gates still apply). Nothing paid, ever. |
| Cost constraint | No paid APIs or services anywhere in the design. Ollama Cloud plan is generous — lean on `gemma4:31b-cloud` freely. |

Non-goals for v3.0: AI-generated images/video (brand risk — permanent exclusion); paid stock; LinkedIn native video automation (v3 writes the caption/link file; posting stays with the existing `social_agent`); non-English narration.

---

## 3. Architecture

### 3.1 Package layout

```
shorts_engine/
├── __init__.py
├── cli.py                  # python -m shorts_engine <blog_url> [--review/--publish/--resume/--torture]
├── runner.py               # stage state machine over the manifest (checkpoint, resume)
├── manifest.py             # durable JSON run-state (pattern of harness/manifest.py, new status set)
├── config.py               # engine knobs; imports brand values from video_agent/config.py
├── stages/
│   ├── ingest.py           # Stage 1: blog URL → post.json (+canonical text)
│   ├── facts.py            # Stage 2: post.json → factsheet.json (verbatim gate)
│   ├── script.py           # Stage 3: factsheet → script.json (template + gates)
│   ├── shotlist.py         # Stage 4: script → shotlist.json (typed shots, pacing)
│   ├── audio.py            # Stage 5: TTS + word timings (wraps voiceover/subtitles)
│   ├── visuals.py          # Stage 6: per-shot renders + BROLL ladder
│   ├── assemble.py         # Stage 7: ffmpeg concat, captions, music, end-card hold
│   ├── verify.py           # Stage 8: heuristic + vision gates, revise loop
│   ├── package.py          # Stage 9: metadata via youtube_packager + linkedin caption file
│   └── publish.py          # Stage 10: youtube_publisher wrapper
├── llm/
│   ├── text_llm.py         # 31B text calls: schema-validated JSON, retry w/ error echo, fail loud
│   └── vision_judge.py     # describe-then-match protocol (2 calls), attach verification
├── cards/
│   ├── theme.py            # brand design system: colors, fonts, safe margins, motion params
│   ├── headline_card.py    # HEADLINE_CARD renderer → mp4 clip
│   ├── stat_card.py        # STAT_CARD renderer (count-up animation) → mp4 clip
│   ├── diagram_card.py     # DIAGRAM templates: flow / before-after / comparison / dosing scale
│   ├── quote_card.py       # QUOTE_CARD renderer (verbatim quote + source chip)
│   ├── paper_card.py       # PAPER_CARD renderer (cited paper's front page, "receipts" shot)
│   ├── broll_frame.py      # designed matte/inset frame + blur-fill for real assets
│   └── logo_cta_card.py    # LOGO_CTA end card (wraps brand_outro_card assets)
├── sourcing/
│   ├── ladder.py           # acquisition ladder orchestration + gates
│   ├── library_index.py    # asset_library vision-tagging at drop-in + query
│   ├── paper_page.py       # fetch citation front page: OA PDF page-1 render or Playwright screenshot
│   ├── openverse.py        # NEW free source (no key)
│   ├── gates.py            # domain blacklist, min-res, watermark, license tier
│   └── adapters.py         # thin wrappers over existing video_agent/sources/* modules
└── review/
    └── contact_sheet.py    # HTML review page (script, facts w/ quotes, every shot, decisions)
```

### 3.2 Reuse contract

**Reused as libraries (unchanged or lightly extended):**
`video_agent/voiceover.py`, `video_agent/subtitles.py`, `video_agent/music.py`, `video_agent/safezone.py`, `video_agent/text_normalizer.py`, `video_agent/visual_engine/infographic.py` (+`text_card.py`, `brand_outro_card.py`, `source_card.py` as rendering primitives), `video_agent/harness/verify_heuristic.py`, `video_agent/publishers/youtube_packager.py`, `video_agent/publishers/youtube_publisher.py`, `video_agent/vision/ollama_vision.py` (call helper — parser fix required, see 6.3), `video_agent/sources/{pexels,pixabay,unsplash,wikimedia,duckduckgo,google_images_browser,bing,watermark,cache}.py` (wrapped by `sourcing/adapters.py`), brand constants from `video_agent/config.py`.

**Forbidden imports (superseded by this design; deleted after v3 ships — see §10):**
`video_agent/agents/*`, `video_agent/orchestrator.py`, `video_agent/storyboard.py`, `video_agent/script_builder.py`, `video_agent/composer.py`, `video_agent/harness/{runner,rubric,revise_router,verify_vision}.py`, `video_agent/run_stage.py`, `video_agent/visual_engine/{footage_library,factory_broll,dispatcher}.py`, `video_agent/motion/*` (new assembler owns motion), `video_agent/sources/scoring.py` (replaced by `sourcing/gates.py` + judge).

CI guard: a unit test asserts `shorts_engine` has no import (direct or transitive at module level) from the forbidden list.

### 3.3 Stage state machine

Statuses: `init → ingested → facts → scripted → shotlisted → audio → visuals → assembled → verified → packaged → published`, plus `failed(stage, reason)` and `hold_for_review`. Every stage is idempotent and checkpointed; `--resume` re-enters at the first incomplete stage. Artifacts live in `output/videos/<slug>/` exactly as today.

### 3.4 The two guarantees (invariants, enforced by construction + tests)

1. **Never-blank:** every shot resolves to a renderable designed card. `BROLL` shots declare `fallback: {type, payload}` at planning time; ladder failure ⇒ fallback renders. There is no `degraded` state in the data model at all.
2. **Never-unverified:** narration cannot reach TTS containing a numeric token that doesn't trace to a `fact_id` (FactSheet) or `brand_fact_id` (`brand_facts.yaml`). FactSheet entries must string-match the canonical post text.

---

## 4. Stage contracts

Formats below show the shape of each artifact; fields marked opt are optional.

### Stage 1 — INGEST (`blog_url → post.json`)

- Fetch the post page. Isolate the **single** post body (Blogger: match the `<div class="post-body">`/entry whose permalink equals the input URL; strip `<style>/<script>`, nav, share widgets, comment blocks, and any sibling post teasers).
- Extract: `title`, `published`, `region`, `category` (cross-referenced from `blog_history.json` by URL when present), heading-structured text blocks, citation map (superscript markers → reference URLs from the post's sources section), and the post's own image URLs with alt text.
- Classify each citation URL: `kind: paper` (DOI links, publisher domains — springer/sciencedirect/mdpi/wiley/tandfonline/nature/acs/rsc/pubmed/pmc/arxiv — or `.pdf`), `kind: standard` (EU directives, EPA, ISO pages), else `kind: web`. Paper citations power `PAPER_CARD` shots.
- Write `canonical.txt`: the post body as normalized plain text (whitespace collapsed, HTML entities decoded). **All later verbatim verification matches against this file.**
- Acceptance: for the nitrate fixture post, `canonical.txt` contains "1.5 to 3 kg" and does NOT contain "150,000 metric tons" (that string belongs to the sibling post teaser).

```json
// post.json
{"url": "...", "title": "...", "published": "...", "region": "eu",
 "category": "wastewater_treatment", "blocks": [{"heading": "...", "text": "..."}],
 "citations": [{"marker": 1, "url": "..."}], "images": [{"src": "...", "alt": "..."}]}
```

### Stage 2 — FACTS (`post.json → factsheet.json`)

- **Pass A (deterministic):** regex mining of `canonical.txt` for numbers with units, ranges, percentages, temperatures, standards/directive names. Each hit captures its full sentence.
- **Pass B (31B):** for each mined sentence (and for headline qualitative claims), emit a fact entry: `verbatim_quote` (the exact sentence), `value`, `unit`, `claim_summary`, `tags` (`metric|spec|benefit|risk|region|compliance`), `procurement_significance` 1–5, `citation_marker` (opt).
- **Gate (deterministic):** `verbatim_quote` must be found in `canonical.txt` (after whitespace/entity normalization); record `char_offset`. Non-matching entries are dropped and logged. `value`+`unit` must appear inside the quote.
- Load `brand_facts.yaml` (§7). Output both pools.

```json
// factsheet.json
{"facts": [{"id": "f1", "verbatim_quote": "…dosage range of 1.5 to 3 kg per cubic meter of wastewater volume…",
  "char_offset": 3812, "value": "1.5–3", "unit": "kg/m³", "tags": ["spec","metric"],
  "procurement_significance": 5, "citation_marker": 1}],
 "brand_facts": [{"id": "b1", "text": "…", "kind": "differentiator|tagline|cta"}]}
```

### Stage 3 — SCRIPT (`factsheet.json → script.json`)

Fixed five-beat template (35–50s total):

| Beat | Length | Job | Content rule |
|------|--------|-----|--------------|
| 1 HOOK | 2–4s | Name the viewer's pain concretely | Must use a fact or a specific problem statement; generic filler banned |
| 2 STAKES | 4–6s | Why it matters now | Blog facts only (regulation/cost/downtime) |
| 3 MECHANISM | 8–12s | The post's ONE technical idea, correct & simple | Paired with DIAGRAM; claim must trace to fact(s) |
| 4 PROOF | 6–10s | Hardest numbers | ≥1 fact tagged `metric|spec`; citation shown on screen |
| 5 WHY HRSU + CTA | 6–8s | One differentiator + action | Exactly one `brand_fact` differentiator; CTA = blog link + hrsuindore.com |

- Writer = 31B (`SMART_TEXT_MODEL` via SDK). Input: factsheet + brand_facts + title/region/audience. **Raw blog HTML is never in the prompt.**
- Output per beat: `narration`, `fact_ids` (every factual claim), `card_text` proposal (≤7 words), `broll_wish` (opt, plain-English subject).
- **Gates (deterministic, pre-TTS):**
  - every numeric token in narration ∈ {digits of facts referenced by `fact_ids`} (number whitelist);
  - banned phrases: existing `SCRIPT_BANNED_PHRASES` + fear-filler patterns ("X is everything", "crippling", "game-changer", "revolutionary");
  - per-beat word budget (from beat length × 2.6 words/s ± 20%);
  - beat 5 contains exactly one differentiator id;
  - `card_text` must not equal any 5+-word substring of that beat's narration (kills `text_duplicates_voice`).
- **Critique pass:** one 31B call scores the draft against a rubric ("Would a procurement manager learn one actionable thing? Is the mechanism chemically coherent? Is there a reason to pick HRSU?"), returns `revise_notes`; one rewrite cycle max.
- Gate failure after 3 retries ⇒ run fails loudly with the gate report. No silent degradation.

### Stage 4 — SHOTLIST (`script.json → shotlist.json`)

- **Deterministic expansion** (code, no LLM): each beat → 1–4 shots; target shot length 2.0–3.5s (hard bounds 1.8–4.5s); shot boundaries snap to narration phrase breaks (comma/period estimates now, refined by real word timings in Stage 5→7 re-flow).
- Shot types and payloads:

| Type | Payload | Renderer |
|------|---------|----------|
| `HEADLINE_CARD` | text, accent word | `cards/headline_card.py` |
| `STAT_CARD` | value, unit, label, citation chip | `cards/stat_card.py` (count-up) |
| `DIAGRAM` | template ∈ {flow, before_after, comparison, dosing_scale}, labels | `cards/diagram_card.py` |
| `QUOTE_CARD` | verbatim quote (trimmed ≤120 chars), source chip | `cards/quote_card.py` |
| `PAPER_CARD` | citation marker + URL, highlight text, `fallback:{type: QUOTE_CARD}` **required** | `sourcing/paper_page.py` + `cards/paper_card.py` |
| `BROLL` | subject descriptor, `fallback:{type,payload}` **required** | ladder + `cards/broll_frame.py` |
| `LOGO_CTA` | cta line, domain | `cards/logo_cta_card.py` |

- Default beat→type mapping: HOOK→HEADLINE(+BROLL opt) · STAKES→STAT/HEADLINE · MECHANISM→DIAGRAM(+QUOTE opt) · PROOF→**PAPER_CARD when the cited fact's citation is `kind: paper`**, else STAT/QUOTE(+BROLL opt) · CTA→LOGO_CTA.
- `PAPER_CARD` acquisition (in `sourcing/paper_page.py`, cached in `output/_paper_cache/` by URL hash): (a) if an open-access PDF is reachable (arXiv/MDPI/PMC or a direct `.pdf` citation), render page 1 to PNG via `pypdfium2` (free, pip-installable; optional dependency); (b) else screenshot the article landing page (title/authors/journal header visible) with Playwright (already a project dependency), cookie-banner dismissal best-effort, 1200px-wide viewport clipped to the header region; (c) both fail ⇒ the declared QUOTE_CARD fallback renders. Never-blank holds.
- LLM's only involvement: it already proposed `card_text` and `broll_wish` in Stage 3. Stage 4 is pure code. A shotlist linter asserts: every BROLL has fallback; every STAT payload's number has a `fact_id`; total duration within 35–50s (auto re-flowed after TTS, §Stage 7); no shot > 4.5s.

### Stage 5 — AUDIO

- Wraps `video_agent/voiceover.py` (already single-engine with clamped ±6Hz prosody) + `video_agent/subtitles.py` (Whisper word timings).
- New guard: every synthesized segment file must exist and be >1KB, and total voice duration within ±15% of script estimate; else fail loudly (fixes F10).
- Output: `voiceover.mp3`, `word_timings.json`, per-beat actual durations.

### Stage 6 — VISUALS

- Designed shots render deterministically from payload + theme (§5).
- `BROLL` shots run the ladder (§6). Ladder result or declared fallback — recorded either way.
- Output: `shot_XX.mp4` per shot + `visuals_report.json` with full provenance: tiers tried, candidates seen, gate failures (which gate), judge scores + descriptions, final choice or fallback reason.

### Stage 7 — ASSEMBLE

- Re-flow: actual word timings stretch/shrink shot durations within bounds; card clips re-render at final durations (cards are cheap to re-render; that's a feature of designed-first).
- ffmpeg concat; transitions: cut (default) and 0.25s fade (beat boundaries only). No slides/flashes.
- Music via `music.py`, ducked under voice per the existing `MUSIC_VOLUME_DB`/`MUSIC_DUCKED_DB` config. Captions burned (ASS) inside safe zone: **bottom margin ≥ 420px, top ≥ 220px** (validated by `safezone.py` sampling). Progress bar. Logo bug top-right at 85% opacity, 96px.
- **Video length = final audio length + 1.5s** (end-card hold). `-shortest` is banned in the audio mux. Audio-completeness assert: rendered duration ≥ voice duration + 1.4s (permanently kills the clipped-CTA defect).

### Stage 8 — VERIFY (+ revise loop)

- Heuristic gate: reuse `verify_heuristic.py` (duration, streams, resolution, RMS, dark-ribbon, safe-zone).
- Vision gate: 1 frame per shot → describe-then-match judge vs. that shot's narration span + payload; also checks on-screen text legibility (contrast/size from the description call). Retries 3× exponential backoff per call. **Ungradeable after retries ⇒ run `failed`, not skipped** (fixes F8).
- Revise loop, max 2 cycles, deterministic fixes only: BROLL judge-fail → swap to declared fallback card; legibility fail → font size +15% / shorten text and re-render that card; safe-zone fail → re-position captions; audio fail → re-run Stage 5. All fixes converge to designed cards ⇒ loop always terminates publishable.
- Then `hold_for_review` (default) with contact sheet, or straight to Stage 9 with `--publish`.

### Stages 9–10 — PACKAGE & PUBLISH

- Reuse `youtube_packager.py` (title/description/hashtags; description links the source blog post — that's the funnel) and `youtube_publisher.py` (OAuth, resumable upload, `unlisted` default).
- Also emit `linkedin_caption.txt` (hook line + 3 takeaways + blog link) for the existing `social_agent` to post.

---

## 5. Card design system (`cards/theme.py`)

- Canvas 1080×1920 @30fps. Safe margins: top 220px, bottom 420px, sides 72px.
- Palette: `BRAND_DARK_NAVY #0a192f` → `BRAND_NAVY_2 #0a1428` animated gradient (8s drift loop); `BRAND_GOLD #d4af37` accents; text `#ccd6f6` / muted `#8892b0`. Fonts: Playfair Display (headlines), Poppins (body/numbers) — already brand standard.
- Motion vocabulary (uniform across cards): element fade+rise 300ms staggered 80ms; stat count-up 800ms with gold underline sweep; gradient drift; 2% film grain. Implemented as PIL/matplotlib frame sequences piped to ffmpeg (no moviepy dependency).
- Citation chip: gold-outlined pill, bottom-left inside safe zone: `Source [1] — <domain>`.
- `PAPER_CARD` look (the YT-shorts "receipts" shot): the fetched front page inset at ~78% width on the brand background, slight tilt (−2°) with soft shadow, gold underline sweep across the paper title, slow 1.05 push-in (portrait document — Ken Burns is safe here), citation chip naming journal/domain. On-screen text limited to the highlight phrase.
- `broll_frame.py` for real assets: **landscape/square images are never crop-panned** (kills the ¼-of-a-landscape-image defect). Two layouts: (a) *inset matte* — image centered at native aspect inside a branded frame with caption strip; (b) *blur-fill* — Gaussian-blurred cover layer behind the intact image. Ken Burns allowed only on portrait assets (aspect ≥ 4:5), max zoom 1.08.
- Acceptance for §5 overall: the torture-test video (§9) looks intentional — a reviewer cannot tell a fallback card from a planned card, because there is no visual difference.

---

## 6. BROLL acquisition ladder & judge

### 6.1 Ladder (all free)

1. **Own library** — `asset_library/{factory,footage,brand}/`. At drop-in (or first run), `library_index.py` sends each new file (image, or 3 sampled frames for video) to the vision model: "describe subjects, setting, motion, quality" → stored in `asset_library/index.json`. Query = token/tag match vs. `broll_wish`, then judge confirms (§6.2). Own footage passes at judge ≥5 (trust bonus — real HRSU footage beats stock).
2. **Blog's own images** — from `post.json.images` (user already curated these; charts/figures welcome).
3. **Free license-aware APIs** — Pexels, Pixabay, Unsplash (existing free keys/adapters), **Openverse** (new adapter, no key, CC-licensed corpus incl. Flickr/museums), Wikimedia (existing adapter). Judge threshold ≥6.
4. **Scrape tier (last resort)** — DuckDuckGo (existing adapter) and Google Images browser (existing, `GOOGLE_IMAGES_INTERACTIVE=1` for CAPTCHA). Bing is dropped (its image API is keyed/retired; no keyless path). Judge threshold ≥7 (higher bar: unlicensed source must be *clearly* right to be worth it).
- Global gates before any judge call (`sourcing/gates.py`): domain blacklist (Appendix B seed: ftcdn/shutterstock/alamy/istockphoto/gettyimages/dreamstime/123rf/depositphotos/adobe…); long edge ≥1280px; watermark OCR (`sources/watermark.py`); dedupe via `sources/cache.py`.
- Per-shot budget: ≤8 candidates judged per tier, ladder stops at first acceptance. No acceptance anywhere ⇒ declared fallback card. **A hard reject at any gate is final — no other signal can override it** (fixes F2).

### 6.2 Describe-then-match judge (`llm/vision_judge.py`)

Two separate model calls; the model never sees the desired subject while looking at pixels:

1. **DESCRIBE:** image only + fixed prompt "Describe exactly what this image shows: subjects, setting, any visible text or watermarks, image quality." → JSON `{description, visible_text, quality_notes}`. Attach-verification (deterministic): call succeeded AND `description` ≥120 chars AND contains none of a fixed refusal/error phrase list ("cannot see", "no image", "as an AI", "unable to") AND does not repeat the prompt text; otherwise the candidate is **rejected** (failure can never pass; fixes F3). If `visible_text` contains a stock-watermark term (Appendix B names), reject.
2. **MATCH (text-only):** description vs. `broll_wish` + narration span → `{score 0–10, reason, focal_hint ∈ {center,left,right,top,bottom}}`. `focal_hint` feeds `broll_frame.py` layout choice.

### 6.3 Vision transport

`vision/ollama_vision.py` is reused for the calls, with two fixes as part of Phase 5: (a) empirical transport check — try SDK `chat(images=…)` first, fall back to CLI `ollama run` — result cached in engine config (`VISION_TRANSPORT`); (b) **parser fix**: the ANSI/terminal-wrap stripping currently duplicates characters ("brabranded"); rewrite with a conservative ANSI-only regex and add a regression test asserting no character duplication on captured CLI fixtures.

---

## 7. `brand_facts.yaml` (human-owned, engine-read-only)

Lives at project root. The engine refuses to run without it. Template (values below are EXAMPLES — the user edits real ones):

```yaml
company: HRSU Indore Pvt. Ltd.
domain: hrsuindore.com
tagline: "Beyond Granules. The Purity of Powder."
differentiators:            # script may cite EXACTLY these, one per video
  - id: b_purity
    text: "Consistent high-purity powder grades with batch-level QC"
  - id: b_supply
    text: "Flexible MOQs and responsive quoting for trial orders"
  - id: b_esg
    text: "Solar power and steam-reuse at the Indore plant"
cta_lines:
  - "Full technical guide on the HRSU blog — link below."
  - "Sourcing calcium nitrate? Talk to HRSU — hrsuindore.com"
banned_claims:              # hard-blocked even if a blog post implies them
  - "REACH registered"
  - "certified"             # any certification wording unless added above explicitly
```

---

## 8. Reliability rules (all LLM calls in the engine)

1. JSON schema validation on every response (`text_llm.py`); on failure, retry with the validation error echoed into the prompt; max 3; then raise. 
2. **No silent model substitution.** If `gemma4:31b-cloud` is unreachable, the run fails with a clear message. `gemma3:4b` is never auto-substituted (root cause of the "secretly running 4B" era). A `--local-only` flag exists for offline dev and *labels the manifest* `model_tier: local`.
3. Every stage logs to `video_agent.log` + stdout; `quality_report_v2.json` aggregates: per-shot provenance, gate decisions, judge transcripts, timings, model tier, re-flow adjustments.
4. `review/contact_sheet.py` renders one HTML page: script with per-beat fact quotes (offsets → highlighted source sentences), every shot thumbnail, ladder decisions, verify scores. Default flow stops here (`hold_for_review`) until `--publish`.

---

## 9. Testing

- **Unit (per stage):** ingest isolates fixture post (F7 regression) and classifies citation kinds; fact gate drops non-matching quotes (F6); number-whitelist gate blocks un-traced numerics; banned-phrase gate; shot expansion bounds (F9); every-BROLL-and-PAPER_CARD-has-fallback linter; paper_page unreachable-URL → QUOTE_CARD fallback + cache-hit test; blacklist/min-res/watermark gates (F2); judge attach-verification rejects failed describes (F3); parser no-duplication regression (F3b); audio segment >1KB guard (F10); assemble duration ≥ voice+1.4s (D2 class); forbidden-import guard (§3.2).
- **Golden integration:** the nitrate blog HTML as fixture → full run with mocked LLM/vision responses → asserts: no blank frames (sample luma variance per shot), no unverified numeric in narration, no blacklisted domain in provenance, captions inside safe zone.
- **Torture test (the core guarantee, runnable forever as `--torture`):** all web tiers disabled + empty asset library + real 31B calls → must produce a complete watchable all-designed video. From Phase 6 onward the torture run must also pass VERIFY; at Phases 3–4 the criterion is a complete, watchable render.
- Live smoke: one real end-to-end run on a recent blog post, human-reviewed via contact sheet, uploaded `unlisted`.

---

## 10. Rollout & deletion

Build phases (each ends runnable):
1. Skeleton: `manifest.py`, `runner.py`, `cli.py`, INGEST + FACTS with gates + fixtures.
2. SCRIPT stage: template, gates, critique pass, `brand_facts.yaml` loader.
3. Card system: theme + 6 renderers + SHOTLIST expansion → **torture test passes here** (script→cards→silent slideshow assembled without audio is acceptable at this checkpoint; full torture criterion re-run at Phase 4).
4. AUDIO + ASSEMBLE: TTS wrap, re-flow, captions, music, end-card hold → torture test with sound = ship criterion.
5. BROLL + PAPER_CARD acquisition: ladder, gates, library index, Openverse adapter, `paper_page.py` (PDF render + Playwright screenshot), judge + transport check + parser fix. (Until Phase 5, PAPER_CARD shots render their QUOTE_CARD fallback — the torture path.)
6. VERIFY: heuristic + vision gates, revise loop, contact sheet.
7. PACKAGE/PUBLISH wiring + `linkedin_caption.txt` + live smoke run.

After v3 produces 3 consecutive human-approved videos, delete the superseded modules listed in §3.2 (one cleanup pass, separate session). `video_agent` keeps only the reused leaves; imports updated.

---

## Appendix A — Beat/shot walkthrough (nitrate post, with REAL facts)

- HOOK · HEADLINE_CARD 3s: "EU nitrate limits are tightening." (+BROLL wish: "wastewater treatment aeration basin")
- STAKES · STAT_CARD 4s: **1.5–3 kg/m³** — "typical calcium nitrate dosing window" · Source [1]
- MECHANISM · DIAGRAM(flow) 9s: Effluent → dosing → denitrifying filter → N₂ + clearer discharge (3 shots: flow build-up)
- PROOF · PAPER_CARD 4s (Source [5]'s front page, gold sweep on its title) + STAT_CARD 4s: verbatim blog sentence on denitrifying-filter co-benefit
- CTA · LOGO_CTA 7s: differentiator (one of b_purity/b_supply/b_esg) + "Full guide on the HRSU blog" + hrsuindore.com

## Appendix B — Domain blacklist seed

`ftcdn.net, shutterstock.com, alamy.com, istockphoto.com, gettyimages.*, dreamstime.com, 123rf.com, depositphotos.com, stock.adobe.com, bigstockphoto.com, canstockphoto.com, agefotostock.com, superstock.com, pond5.com, storyblocks.com` (+ `*.staticflickr.com` allowed only via Openverse license metadata).

## Appendix C — Phone footage capture checklist (10 shots, ~10s each, landscape AND portrait takes)

1. Product bag close-up, label readable, slow orbit
2. Granules/powder pouring into gloved hand
3. Pallet stack wide shot, walk-past
4. Bagging/production line, any angle
5. Lab QC: titration/beaker/scale in use
6. Warehouse forklift pass
7. Dispatch: truck loading dock
8. Solar panels on plant roof
9. Steam-reuse equipment exterior
10. The garden (ESG story)

Drop into `asset_library/factory/` — the engine vision-tags them automatically on next run; no manifest to write.

## Appendix D — Flag for the blog pipeline (out of scope here, worth a look)

The fixture post's premise ("nitrate removal via granular calcium nitrate" through "chemical precipitation") is chemically shaky — nitrates are highly soluble; calcium nitrate is conventionally dosed to *add* nitrate (H₂S control / denitrification support). The video engine's verbatim-grounding protects videos from amplifying such claims with invented numbers, but the blog QA pipeline may deserve a chemistry-coherence check of its own.
