# Video Harness — Autonomous YouTube/LinkedIn Shorts Pipeline

**Status:** Approved design (all phases), ready for Phase 1 implementation plan
**Date:** 2026-06-08
**Supersedes scope of:** `2026-05-17-video-v2.2-design.md` (generation quality — now complete)
**Branch target:** `video-harness` (per-phase sub-branches off it)

## Problem

The video pipeline generates a single vertical 1080×1920 MP4 and opens it locally
(`scripts/make_video.py`). It stops at the file. The end goal is **autonomous
generation AND distribution** of shorts to YouTube and LinkedIn. Three structural
gaps stand between today and that goal, none of which the working generator
(`Strategist → … → Reviser`) currently addresses:

1. **No artifact verification.** The Critics grade the *storyboard* (text + asset
   choices). Nothing inspects the *rendered MP4*. The core harness-engineering
   principle — "the evaluator interacts with the real running artifact" — is absent.
   `quality_report.json` is written but no loop consumes it.
2. **No publishing.** `video_agent/publishers/` is empty. The actual distribution
   to YouTube/LinkedIn does not exist.
3. **No durable lifecycle.** `make_video.py` is a linear script. A mid-pipeline
   failure means restarting from scratch. The scheduler config (`video_queue.db`,
   retry backoff) is wired but no video runner consumes it.

Additionally, the model backbone is moving from local `gemma3:4b` to a 31B-class
Gemma on Ollama Cloud (a one-line model/host swap). That capability jump means the
eventual generator re-architecture should **remove** small-model compensations
(JSON-repair regexes, the `generate_tool_calls` shim, heavy fallback paths), not
just wrap them — per the harness principle that "every component encodes an
assumption about what the model can't do on its own."

## Goal

Wrap the existing generation pipeline in a formal **harness**:
`Plan → Generate → Verify(artifact) → Package → Publish`, with checkpointed
resumable state, measurable definition-of-done, bounded retries, an artifact-level
quality gate, and platform publishing — delivered in four sequenced phases so a
real published short ships **before** any risk is taken with the working generator.

## Harness framing

This design applies two reference frameworks:

- **harness-creator's five subsystems** — every phase supplies: **Instructions**
  (per-stage system prompt / contract), **State** (the durable `RunManifest`),
  **Verification** (a gate before the next phase), **Scope boundaries** (each stage
  declares the tools it may call), **Lifecycle handoff** (structured manifest
  written between stages; context reset, not accumulation).
- **Anthropic harness-design principles** — decomposition & specialization;
  external evaluation over self-assessment; grading criteria as a written contract
  negotiated up front; tool integration on real artifacts; iterative
  simplification as the model improves.

**Orchestration model: hybrid.** The phase ordering (`Plan → … → Publish`) is known
and fixed, so it is a **deterministic, resumable state machine** — predictable and
testable. Model *agency* is scoped to exactly where judgment lives: the
**verify→revise loop** (Phase 3). A pure "single planner agent drives every tool
call" (ReAct) design is explicitly **rejected** — it would trade testability for
flexibility the fixed ordering does not need.

## Phased roadmap

```
PHASE 1 — Publish path + harness skeleton        [this spec → Phase 1 plan now]
PHASE 2 — Autonomous scheduling + go-public
PHASE 3 — Vision-LLM verifier + closed revise loop
PHASE 4 — Generator re-architecture (remove small-model band-aids)
```

**Sequencing rationale.** Every gap to the end goal (verify / package / publish /
runner) is *additive* — none requires touching the working generator. The
re-architecture is the only piece that can *break* working code, so it goes last.
This sequencing delivers a published short in Phase 1 and still delivers the full
re-architecture — without making the first upload hostage to it.

---

## PHASE 1 — Publish path + harness skeleton

**Outcome:** `python scripts/publish_video.py <blog-url>` runs the full pipeline and
auto-uploads a short to YouTube as **unlisted**, gated by deterministic
(no-model) artifact checks. The generator is reused unchanged. Zero risk to
working code.

### State — `RunManifest`

**New file:** `video_agent/harness/manifest.py`

A dataclass mirroring the `Storyboard` save/load pattern (`asdict` → JSON,
`version` field, `_from_dict` loader). Persisted as `run_manifest.json` in the
run workspace; checkpointed after every phase → **resumable**.

```python
RunStatus = Literal["planned", "generated", "rendered", "verified",
                    "packaged", "published", "failed"]

@dataclass
class VerifyReport:
    passed: bool
    checks: dict[str, Any]      # {"duration_s": 47.2, "audio_rms_ok": True, ...}
    defects: list[str]          # human-readable failed-check descriptions

@dataclass
class PublishPackage:
    title: str
    description: str
    tags: list[str]
    category_id: str
    thumbnail_path: str
    caption_srt_path: str
    privacy_status: str         # "unlisted" in Phase 1

@dataclass
class PublishResult:
    platform: str               # "youtube"
    video_id: str
    url: str
    visibility: str
    uploaded_at: str

@dataclass
class RunManifest:
    version: str                # "1.0"
    run_id: str
    blog_url: str
    slug: str
    status: RunStatus
    workspace: str
    storyboard_path: str | None = None
    video_path: str | None = None
    srt_path: str | None = None
    voice_path: str | None = None
    verify: VerifyReport | None = None
    package: PublishPackage | None = None
    publish: PublishResult | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""
```

### Lifecycle — `HarnessRunner`

**New file:** `video_agent/harness/runner.py`

A deterministic phase state-machine over a uniform `Stage` interface:

```python
class Stage(Protocol):
    name: str
    def precondition(self, m: RunManifest) -> bool: ...   # already-done? skip
    def run(self, m: RunManifest) -> RunManifest: ...      # do work, mutate manifest
```

Phases (Phase 1 set): `PLAN → GENERATE → RENDER → VERIFY → PACKAGE → PUBLISH`.

- **PLAN** — fetch blog HTML + history record; init `RunManifest`
  (lifts `_build_blog_record` / `_load_history_record` from `make_video.py`).
- **GENERATE** — call existing `orchestrator.build_storyboard` unchanged; record
  `storyboard_path`.
- **RENDER** — voiceover (`synthesize_segments`) → subtitles (`generate_srt`) →
  `composer.compose_short_v2`; record `video_path`, `srt_path`, `voice_path`.
  (This is today's `make_video.py` body, relocated.)
- **VERIFY** — heuristic gate (below). Fail → `status="failed"`, stop, no publish.
- **PACKAGE** — build `PublishPackage`.
- **PUBLISH** — upload (skipped when `publish=False`).

Runner behavior: checkpoint manifest after each phase; `precondition` makes phases
**idempotent** so a resumed run skips completed phases; bounded retry
(`attempts`, capped) with `last_error` recorded; `publish=False` stops after VERIFY.
The uniform `Stage` interface is deliberately the seed Phase 4 extends down into
the six generator sub-stages.

### Verification — heuristic gate (no model)

**New file:** `video_agent/harness/verify_heuristic.py`

Deterministic checks on the rendered MP4; returns `VerifyReport`:

- **ffprobe** — duration ∈ `[SHORT_FORMAT.min_duration_s, max_duration_s]`; has
  both video and audio streams; filesize ≤ `max_filesize_mb`; resolution ==
  `(1080, 1920)`.
- **Audio RMS** — not silent (RMS above floor) and not clipped (peak below ceiling).
- **Dark-ribbon scan** — sample N frames, inspect the bottom strip; fail if it is a
  near-solid dark band (the known v2.x defect).
- **Caption safe-zone OCR** — sample frames, OCR (reuse the existing OCR dependency
  already used for watermark detection); assert detected text bounding boxes sit
  within safe margins.

Phase 1 is a **gate only** — on failure the run is marked `failed` and nothing
publishes. The closed verify→revise loop is Phase 3.

### Packaging — `YouTubePackager`

**New file:** `video_agent/publishers/youtube_packager.py`

Builds `PublishPackage` from `Storyboard.hero_claim` + blog record:

- **title** ≤ 100 chars, keyword-front-loaded (region + category + hook noun phrase).
- **description** — hero claim + 1–2 line summary + CTA link to `hrsuindore.com` +
  `#Shorts` + regional/use-case hashtags.
- **tags**, **category_id** (`"28"` Science & Technology), language from region.
- **thumbnail** — hero-frame grab from the rendered video, or the brand outro card.
- **caption_srt_path** — the existing `subtitles.srt`.

Copy (title/description) is LLM-assisted via `OllamaClient`, but wrapped in
**deterministic length/format validation** (truncation, char-limit enforcement,
banned-phrase filter reusing `SCRIPT_BANNED_PHRASES`).

### Publishing — `YouTubePublisher`

**New file:** `video_agent/publishers/youtube_publisher.py`

- OAuth via `InstalledAppFlow` reusing `client_secrets.json`, scopes
  `youtube.upload` + `youtube.force-ssl`, stored in a **separate**
  `youtube_token.json` (must not clobber the Blogger token).
- `videos.insert` resumable upload (`MediaFileUpload`, chunked),
  `status.privacyStatus="unlisted"`, `selfDeclaredMadeForKids=False`.
- `thumbnails.set`, `captions.insert` (SRT).
- Returns `PublishResult`; appends to `video_history.json` for dedup/audit.
- **`--dry-run`** builds the request and validates auth without uploading.

### Entry points

- `scripts/make_video.py` — stays the dev single-shot; now calls
  `HarnessRunner.run(url, publish=False)` (render + verify, no upload). Behavior
  for the developer is unchanged.
- **New** `scripts/publish_video.py <url>` — `HarnessRunner.run(url, publish=True)`,
  full path to unlisted upload.

### Phase 1 prerequisites (manual, user-performed)

- Enable **YouTube Data API v3** in the Google Cloud project behind
  `client_secrets.json`.
- Re-consent OAuth with the two YouTube scopes (generates `youtube_token.json`).
- Confirm the target YouTube channel and that it is allowed to upload Shorts.

### Phase 1 testing (TDD)

`tests/video_agent/harness/`:
- `test_manifest.py` — round-trip save/load, status transitions.
- `test_runner.py` — phase ordering, resume skips completed phases, retry caps,
  `publish=False` stops after VERIFY.
- `test_verify_heuristic.py` — ffmpeg-generated good/bad fixtures (silent audio,
  wrong duration, dark ribbon) → correct pass/fail + defects.
- `test_youtube_packager.py` — title ≤100 chars, banned phrases stripped, required
  fields present.
- `test_youtube_publisher.py` — `googleapiclient` mocked; assert request shape,
  `privacyStatus="unlisted"`, no real network call.

---

## PHASE 2 — Autonomous scheduling + go-public

**Outcome:** unattended, scheduled production with public visibility once the
heuristic gate is trusted.

- **Video queue runner** — consume `video_queue` on the existing scheduler infra
  (model on `scheduler.py` / `social_scheduler.py`); APScheduler sqlite jobstore
  (`video_queue.db`) + `SCHEDULER_RETRY_BACKOFF_S`.
- **Dedup** — skip blogs already turned into videos via `video_history.json`
  (reuse the blog dedup approach).
- **Throughput design constrained by YouTube quota** — `videos.insert` is
  quota-expensive (~1600 units; default daily quota 10,000 → a handful of uploads/
  day). Runner caps daily uploads and queues overflow. (Confirm current quota
  numbers at implementation time.)
- **Go-public switch** — config flag flips `privacy_status` `unlisted → public`
  once the operator trusts the gate. Per-region posting schedule reuses
  `REGION_POSTING_SCHEDULE` / `REGION_TO_TZ`.

## PHASE 3 — Vision-LLM verifier + closed revise loop

**Outcome:** the artifact gate gains judgment, and failures trigger targeted
re-work instead of a hard stop — the brand-safety gate autonomous public
publishing depends on.

- **Sampled-frame vision grade** — extract hook/mid/CTA frames, send to the cloud
  Gemma vision endpoint (via Ollama `images` field), grade against the rubric the
  Planner wrote: readability, framing, brand presence, visual coherence,
  originality. Returns per-criterion scores + actionable defects.
  **OPEN QUESTION (blocks this phase):** confirm the chosen cloud Gemma is
  multimodal and accepts images via Ollama. If text-only, substitute a vision path
  (e.g. a separate VLM) — design contingent on this check.
- **Agentic verify→revise router** — map graded defects back to the responsible
  stage (caption clipped → re-RENDER; scene image off-topic → re-source that scene;
  flat hook → re-cinematograph hook), bounded to ≤2 revise cycles to avoid infinite
  loops / "context anxiety."
- **Hold-for-review on uncertainty** — grades in a middle band route to an
  operator queue rather than binary pass/fail; protects the public channel.
- **Definition-of-done as a written contract** — the Planner emits the grading
  rubric up front (Anthropic "contract negotiation"); the Verifier grades against it.

## PHASE 4 — Generator re-architecture

**Outcome:** the six generation stages adopt the uniform `Stage`/contract interface,
and the small-model scaffolding is **removed**, not wrapped.

- Refactor `Strategist, Storyboarder, Cinematographer, NarrationPolisher, Sourcer,
  Critics, Reviser` to the `Stage` protocol with explicit preconditions, declared
  tool scope, and manifest handoff (no shared in-memory god object beyond the
  manifest; context reset between stages).
- **Delete compensations** the 31B cloud model makes unnecessary: the JSON-repair
  regexes (`_repair_json`, `_MISSING_COMMA_RE`, …), the `generate_tool_calls`
  shim, the heavy mood-based fallback paths. Replace with native tool-calling.
- **LinkedIn publisher** — add a `LinkedInPublisher` behind the same Publisher
  interface (official API once the pending access is approved, else the existing
  `social_agent` Playwright pattern). YouTube-first means this is the last channel
  added.

---

## Cross-cutting

- **No new heavy deps for Phase 1** — `googleapiclient` already installed;
  `ffprobe`/`ffmpeg` already used by the composer; OCR dep already present.
- **`run_manifest.json`** generalizes today's per-stage `storyboard.json`
  checkpointing to the whole lifecycle; `quality_report.json` becomes the
  verification block inside the run report.
- **No git** — project convention. No commits, no branches created by the agent;
  "checkpoint" = verify diff and continue.

## Open questions (resolve at the relevant phase)

1. **Cloud Gemma multimodality** — blocks Phase 3 Verify design. Confirm before
   writing that phase's plan.
2. **YouTube Data API v3 quota** — confirm current daily quota and `videos.insert`
   cost; constrains Phase 2 throughput.

## Out of scope

- AI-generated featured imagery (separate roadmap item).
- Animation/motion-graphics generation beyond stills + Ken Burns + clip footage.
- Cross-platform browser-profile sync.
- Multi-account / multi-channel fan-out.

## Risks

- **Autonomous publish to a public channel makes the verifier the sole brand-safety
  gate.** Mitigation: Phase 1 publishes unlisted; go-public (Phase 2) only after
  the heuristic gate is trusted; vision verifier + hold-for-review (Phase 3)
  before scale.
- **YouTube quota throttles throughput.** Mitigation: daily upload cap + queue in
  the runner (Phase 2).
- **OAuth scope/token collision with Blogger.** Mitigation: separate
  `youtube_token.json`; never reuse the Blogger token file.
- **Re-architecture regresses a working generator.** Mitigation: sequenced last,
  behind a passing artifact gate that catches regressions on the rendered output.
