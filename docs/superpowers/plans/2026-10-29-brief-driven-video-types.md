# Brief-Driven Video Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner give the video pipeline any brief, pick or auto-pick one of five video types, and review every finished video before it publishes.

**Architecture:** A new package `scheduler/briefs/` holds the brief model, type recipes, facts loader, intake, text stage and type gate. It reuses the existing state store (`scheduler/state.py`), director (`scheduler/director.py`), gate (`scheduler/gate.py`), publish step (`scheduler/publish.py`) and the shorts engine (`stage_cli init` + `run-stage audio`). A review stop (`awaiting_review`) is added to both the brief flow and the existing daily blog flow.

**Tech Stack:** Python 3.12, pytest, existing `scheduler.*` and `shorts_engine.*` modules, HyperFrames CLI via `npx`, Claude Code (Opus 5.5) as director, Ollama cloud models for text.

**Spec:** `docs/superpowers/specs/2026-10-29-brief-driven-video-types-design.md`

## Global Constraints

- Brand tokens for every type: navy `#0a192f`, gold `#d4af37`, text `#ccd6f6`, muted `#8892b0`, Arial only, small letter-spaced "HRSU INDORE" wordmark, one CTA end card to hrsuindore.com.
- Orientation: narration of 90 s or more is landscape 1920x1080, otherwise portrait (existing `orientation_for`); a brief may override it.
- Visuals are Claude-only (Opus 5.5). No Gemma, Kaggle, Harness-squared or videobench work. No AI-generated or stock footage.
- Same state store (`scheduler/state.py`); no second store.
- Every video of every type, including the daily blog explainer, stops at `awaiting_review` and publishes only after approval.
- Max 3 rejects per brief. A reject re-runs only the director, with the owner's note appended.
- Assets: PNG, JPG and PDF only, copied into the project's assets folder at intake.
- Publish privacy follows the existing `HRSU_YOUTUBE_PRIVACY` setting, applied only after approval.
- Never touch the main checkout at `E:\Projects\HRSU Shorts`; work only in this worktree.
- Existing tests keep passing, changed only where the review stop alters behaviour.

## Review Focus

- Empty, whitespace-only or very long brief text, or non-ASCII text (Hindi, curly quotes): refused with a clear message or handled, never a crash on a Windows console.
- The same brief submitted twice: warn and ask, do not silently run again or silently skip.
- Approving a video whose file was deleted: clear error, status unchanged.
- Review pile growing unattended: the daily run stops producing at 5 videos awaiting review so Opus allowance is not burned on an unreviewed backlog.
- A fourth reject on one brief: refused with a message.
- A market brief naming a company or war event with no findable sources: fails before the director, never produces a video.
- An asset whose extension lies (a `.png` that is really a PDF): refused.

---

## File Structure

- `scheduler/state.py` (modify): new statuses.
- `brand_facts.yaml` (modify): add tiers.
- `scheduler/briefs/__init__.py`, `model.py`, `facts.py`, `types.py`, `intake.py`, `research.py`, `writer.py`, `claims.py`, `textstage.py`, `gate.py` (create).
- `scheduler/briefs/prompts/{proof,esg,market,marketing}.md` (create). The explainer keeps `scheduler/director_prompt.md`.
- `scheduler/director.py` (modify): optional prompt template and review note.
- `scheduler/publish.py` (modify): packages for videos with no blog URL.
- `scheduler/run_daily.py` (modify): review stop and review-pile cap.
- `scheduler/make_video.py`, `scheduler/review.py` (create): the two commands.
- `tests/scheduler/test_brief_*.py`, `test_sched_review.py` (create); existing `test_sched_run_daily.py`, `test_sched_publish.py` (modify).
- `scheduler/README.md` (modify).

---

### Task 1: State statuses for review

**Files:** Modify `scheduler/state.py`; Test `tests/scheduler/test_sched_discover_state.py`

**Interfaces:**
- Produces: `STATUSES` gains `"awaiting_review"` and `"rejected"`; `State.count_awaiting_review() -> int`; `State.update` accepts the new statuses.

- [ ] **Step 1: Write failing tests** `test_awaiting_review_and_rejected_are_valid_statuses` (update both statuses, assert no ValueError) and `test_count_awaiting_review` (three entries, two `awaiting_review`, assert 2).
- [ ] **Step 2: Run** `pytest tests/scheduler/test_sched_discover_state.py -v`. Expected: FAIL (`unknown status`).
- [ ] **Step 3: Add the statuses and `count_awaiting_review` to `scheduler/state.py`.**
- [ ] **Step 4: Run the same file.** Expected: PASS.
- [ ] **Step 5: Commit** `state: add awaiting_review and rejected statuses`.

---

### Task 2: Facts tiers

**Files:** Modify `brand_facts.yaml`; Create `scheduler/briefs/__init__.py`, `scheduler/briefs/facts.py`; Test `tests/scheduler/test_brief_facts.py`

**Interfaces:**
- Produces: `@dataclass Facts(in_place: list[str], in_progress: list[str], never_claimed: list[str], banned_claims: list[str], cta_lines: list[str])`; `load_facts(path: Path | None = None) -> Facts` (default: repo-root `brand_facts.yaml`).

- [ ] **Step 1: Write failing tests** in `test_brief_facts.py`:
  `test_in_place_includes_door_delivery_and_garden` (strings containing "deliver" and "garden"), `test_in_progress_never_in_place` (no item appears in both lists; "electric" only in `in_progress`), `test_banned_claims_kept` (`"certified"` in `banned_claims`), `test_never_claimed_lists_csr`, `test_no_solar_or_rainwater_is_dropped` (both present in `in_place`; owner said keep both).
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_facts.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Extend `brand_facts.yaml`** with keys `in_place`, `in_progress`, `never_claimed` holding exactly the owner's list in the spec section "Facts the owner supplied". Keep the existing keys (`company`, `domain`, `tagline`, `differentiators`, `cta_lines`, `banned_claims`) unchanged. Implement `load_facts` with `yaml.safe_load`.
- [ ] **Step 4: Run the new tests plus `pytest _shorts_engine_impl/tests -k brand -q`** (the engine also reads this file). Expected: PASS.
- [ ] **Step 5: Commit** `facts: in-place / in-progress / never-claimed tiers`.

---

### Task 3: Brief model and type recipes

**Files:** Create `scheduler/briefs/model.py`, `scheduler/briefs/types.py`; Test `tests/scheduler/test_brief_types.py`

**Interfaces:**
- Produces:
  - `VIDEO_TYPES = ("explainer", "proof", "esg", "market", "marketing")`
  - `@dataclass Brief(id: str, text: str, type: str, url: str | None, assets: list[Path], orientation: str | None)`
  - `@dataclass Recipe(name: str, needs_research: bool, facts_source: str, prompt_path: Path, cta_line: str, tags: list[str])` where `facts_source` is one of `"narration"`, `"documents"`, `"facts_and_sources"`, `"sources"`, `"facts_only"`.
  - `get_recipe(type_name: str) -> Recipe` (raises `KeyError` with the valid names for an unknown type).
  - `brief_id(text: str) -> str`: lowercase slug of the first 6 words plus `-` plus the first 6 hex chars of the sha1 of the whitespace-normalised text.

- [ ] **Step 1: Write failing tests**: `test_brief_id_is_stable_and_whitespace_insensitive`, `test_brief_id_differs_for_different_text`, `test_get_recipe_unknown_lists_valid_types`, `test_explainer_recipe_uses_existing_prompt` (`prompt_path.name == "director_prompt.md"`), `test_market_recipe_needs_research`, `test_marketing_recipe_facts_only`, `test_every_prompt_file_exists`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_types.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement both modules.** Recipes per the spec table: explainer `narration`, proof `documents`, esg `facts_and_sources`, market `sources`, marketing `facts_only`; only explainer-by-brief, esg and market set `needs_research=True`.
- [ ] **Step 4: Run tests.** Expected: PASS after Task 4 creates the prompt files; until then create empty placeholder prompt files so `test_every_prompt_file_exists` passes, and replace them in Task 4.
- [ ] **Step 5: Commit** `briefs: model and type recipes`.

---

### Task 4: Director prompts per type

**Files:** Create `scheduler/briefs/prompts/{proof,esg,market,marketing}.md`; Modify `scheduler/director.py`; Test `tests/scheduler/test_brief_prompts.py`

**Interfaces:**
- Consumes: `get_recipe` (Task 3); existing `build_prompt(project_dir, run_dir, template=PROMPT_PATH)`.
- Produces: `build_prompt(project_dir, run_dir, template=PROMPT_PATH, review_note: str | None = None) -> str`: when `review_note` is given, appends a section headed `## Owner review note (fix this)` with the note. `direct(run_dir, project_dir, mode="claude", *, prompt_path: Path | None = None, review_note: str | None = None, run=..., timeout=..., prepare=True)`. In `scheduler/run_daily.py`, `Deps.director` becomes `Callable[..., DirectorResult]` called as `(run_dir, project_dir, mode, **kw)`, and `default_deps` forwards `**kw` to `direct` (modify that file in this task; existing 3-argument test fakes keep working because `process_post` passes no keywords).

Each prompt file starts from the explainer prompt's recipe (BRIEF.md, frame.md, STORYBOARD.md, per-frame workers, hook and motion rules, brand block, caption keep-out) with the `hyperframes:faceless-explainer` line replaced by the type's own direction and with that type's content rules from the spec table:
- proof: only facts printed in supplied documents/photos in `assets/`; quote as printed.
- esg: only `in_place` facts from `brand_facts.yaml`, plus sourced external facts; never `in_progress` items as done; no CSR claims.
- market: every factual claim carries a source chip; opinion labelled "HRSU's view" on screen.
- marketing: story, characters and metaphor allowed; product claims only from `in_place`; exact CTA; no invented prices.

- [ ] **Step 1: Write failing tests**: `test_no_unreplaced_placeholders[type]` (for each of the four, `build_prompt(..., template=recipe.prompt_path)` contains no `{{`); `test_market_prompt_requires_source_chips_and_hrsu_view` (contains "HRSU's view" and "source chip"); `test_esg_prompt_forbids_in_progress_as_done` (contains "in progress"); `test_marketing_prompt_allows_story_but_not_invented_claims`; `test_review_note_appended` (note text and heading present); `test_explainer_prompt_unchanged_without_note` (output equals the pre-change output for the same inputs).
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_prompts.py -v`. Expected: FAIL.
- [ ] **Step 3: Write the four prompts and add `review_note` to `build_prompt`.** Thread `prompt_path` and `review_note` through `run_claude` and `direct` as keyword arguments with defaults that keep current behaviour.
- [ ] **Step 4: Run** `pytest tests/scheduler/test_brief_prompts.py tests/scheduler/test_sched_director.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `director: per-type prompts and review-note support`.

---

### Task 5: Intake, classifier and assets

**Files:** Create `scheduler/briefs/intake.py`; Test `tests/scheduler/test_brief_intake.py`

**Interfaces:**
- Consumes: `Brief`, `brief_id`, `VIDEO_TYPES` (Task 3).
- Produces:
  - `class NeedsType(Exception)` (low-confidence classification; carries `guess: str`, `confidence: float`)
  - `class BriefRefused(Exception)` (carries a human message)
  - `classify(text: str, llm: Callable[[str], dict]) -> tuple[str, float]`
  - `build_brief(text: str, type_arg: str = "auto", url: str | None = None, assets_dir: Path | None = None, orientation: str | None = None, llm: Callable | None = None, confidence_floor: float = 0.6) -> Brief`
  - `stage_assets(brief: Brief, project_assets_dir: Path) -> list[Path]` (copies validated assets)
  - `extract_asset_text(path: Path) -> str`: text of a PDF (use `pypdf` if installed, else `pdfplumber`; if neither is installed add `pypdf` to `requirements.txt`); images return `""` (their numbers cannot be traced, so a proof video that shows numbers from an image-only asset fails the number check by design). Add test `test_extract_asset_text_reads_pdf_text` and `test_extract_asset_text_image_is_empty`.

- [ ] **Step 1: Write failing tests**: `test_empty_or_whitespace_brief_refused`; `test_brief_over_4000_chars_refused`; `test_non_ascii_brief_accepted` (Hindi and curly quotes round-trip in `brief.text`); `test_explicit_type_skips_classifier`; `test_auto_low_confidence_raises_needs_type` (fake llm returns confidence 0.4); `test_auto_high_confidence_sets_type`; `test_proof_without_assets_refused` (message mentions supplying documents or photos); `test_asset_extension_not_allowed_refused` (`.mp4`); `test_asset_with_lying_extension_refused` (PDF bytes named `.png`, detected by magic bytes); `test_stage_assets_copies_into_project`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_intake.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Classifier prompt returns `{"type": ..., "confidence": ...}` constrained to `VIDEO_TYPES`; validate asset magic bytes (`%PDF`, PNG signature, JPEG `FFD8`).
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `briefs: intake, classifier, asset validation`.

---

### Task 6: Research and claim verification

**Files:** Create `scheduler/briefs/research.py`, `scheduler/briefs/claims.py`; Test `tests/scheduler/test_brief_claims.py`

**Interfaces:**
- Produces:
  - `@dataclass Source(id: str, title: str, url: str, snippet: str, publisher: str)`
  - `gather_sources(brief_text: str, search: Callable[[str], list[dict]] | None = None, limit: int = 12) -> list[Source]` (default `search` = the DuckDuckGo helper the engine already uses; check `shorts_engine/retrieval/`).
  - `@dataclass ClaimVerdict(claim: str, supported: bool, source_ids: list[str])`
  - `verify_claims(claims: list[str], sources: list[Source], llm: Callable) -> list[ClaimVerdict]`
  - `class ThesisUnsourced(Exception)`
  - `apply_verdicts(script: dict, verdicts: list[ClaimVerdict], thesis_claim: str) -> dict`: a claim is the `narration` of a beat with `kind == "fact"` (`ClaimVerdict.claim` equals that narration); removes beats whose claim is unsupported; raises `ThesisUnsourced` when the verdict for `thesis_claim` is unsupported.

- [ ] **Step 1: Write failing tests**: `test_gather_sources_dedupes_by_url`; `test_unsupported_claim_beat_dropped`; `test_supported_claims_keep_source_ids`; `test_unsupported_thesis_raises_before_any_director_call`; `test_opinion_beats_marked_hrsu_view_are_not_verified_or_dropped` (beat with `"kind": "opinion"` survives with no source).
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_claims.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** against injected `search` and `llm` so tests make no network calls.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `briefs: research and claim verification`.

---

### Task 7: Script writer

**Files:** Create `scheduler/briefs/writer.py`; Test `tests/scheduler/test_brief_writer.py`

**Interfaces:**
- Consumes: `Brief`, `Recipe`, `Facts`, `Source`.
- Produces: `write_script(brief: Brief, recipe: Recipe, facts: Facts, sources: list[Source], llm: Callable[[str, str, dict], dict]) -> dict`, returning the existing `script.json` shape: `{"beats": [{"beat", "purpose", "fact_ids", "narration", "card_text", "broll_wish", "kind"}], "critique": {}, "attempts": 1, "rewrites": 0}`. First beat is a hook (`purpose == "hook"`); last beat ends with the recipe's `cta_line`; `kind` is `"fact"`, `"opinion"` or `"story"`; the dict also carries a top-level `"thesis": str` (the single core claim of the video, equal to the narration of one `kind == "fact"` beat; `None` for marketing). The claims that Task 6 verifies are the narrations of the `kind == "fact"` beats. Also `write_support_files(run_dir: Path, brief: Brief, script: dict, sources: list[Source]) -> None` writing `script.json`, `references.json` (keys `claims`, `sources`, `post_kind` = type, `chips`), `video_description.txt` and a minimal `explanation_plan.json` `{"question": <title>, "post_kind": <type>}` (needed by `scripts/hf_prepare_project.py` and `publish.build_package`).

- [ ] **Step 1: Write failing tests** with a fake `llm`: `test_first_beat_is_hook_and_last_has_cta`; `test_marketing_story_beats_allowed`; `test_marketing_beat_with_banned_claim_rejected_and_rewritten` (fake llm first returns "certified", then clean text; assert the clean one is kept and `attempts == 2`); `test_in_progress_fact_never_in_narration` (narration containing "electric" or "steam reuse in all boilers" triggers a rewrite); `test_support_files_written` (all four files exist and parse).
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_writer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with at most 3 attempts; the banned-claim and in-progress check is a plain substring scan against `Facts.banned_claims` and `Facts.in_progress` keywords.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `briefs: script writer and support files`.

---

### Task 8: Brief text stage

**Files:** Create `scheduler/briefs/textstage.py`; Test `tests/scheduler/test_brief_textstage.py`

**Interfaces:**
- Consumes: Tasks 2 to 7; `scheduler.textstage.TextResult`, `run_cmd`.
- Produces: `run_brief_text(brief: Brief, workspace_root: Path | None = None, *, run: Runner = run_cmd, llm=..., search=...) -> TextResult`.
  - Explainer type: writes a synthetic source page (`<title>`, article body built from the brief plus gathered sources) and calls the engine with `html_override`, so the existing explanation-first stages run unchanged, up to `--until audio`.
  - Other types: `python -m shorts_engine.stage_cli init brief://<id> --workspace-root ...`, then `write_support_files`, then `run-stage audio`; `market` runs `gather_sources`, `verify_claims`, `apply_verdicts` before audio. Returns `TextResult("failed", ...)` with the reason on `ThesisUnsourced`. For every type it also copies the repo-root `brand_facts.yaml` to `run_dir / "brand_facts.yaml"` (the director prompts read it as `{{RUN_DIR}}/brand_facts.yaml`); add test `test_brand_facts_copied_into_run_dir`.

- [ ] **Step 1: Write failing tests** using a recording fake `run`: `test_marketing_brief_runs_init_then_audio_only`; `test_market_brief_unsourced_thesis_fails_without_calling_audio`; `test_explainer_brief_passes_html_override`; `test_result_ready_only_when_four_artifacts_exist`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_textstage.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Check first that `slug_from_url` and `RunManifest.create` accept a `brief://` URL; if they do not, use `https://hrsuindore.com/brief/<id>/` as the manifest key instead and keep the `Brief.url` separate.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `briefs: text stage for briefs`.

---

### Task 9: Type gate

**Files:** Create `scheduler/briefs/gate.py`; Test `tests/scheduler/test_brief_gate.py`

**Interfaces:**
- Consumes: existing `scheduler.gate.run_gate(run_dir, project_dir) -> GateResult`, `Facts`, `Recipe`.
- Produces:
  - `on_screen_text(project_dir: Path) -> str` (visible text from `compositions/**/*.html`, tags and scripts stripped)
  - `numbers_in(text: str) -> set[str]`
  - `run_type_gate(brief: Brief, recipe: Recipe, facts: Facts, run_dir: Path, project_dir: Path, *, allowed_text: str) -> GateResult`: runs `run_gate`, then (a) fails on any `banned_claims` phrase on screen, (b) for recipes with `facts_source` in `documents`, `narration`, `sources`, fails when a number on screen is not in `numbers_in(allowed_text)` (`allowed_text` = narration + supplied-document text + source snippets), (c) fails when an `in_progress` keyword appears on screen outside the word "planned" or "pending".

- [ ] **Step 1: Write failing tests**: `test_banned_phrase_on_screen_fails` ("certified"); `test_number_not_in_allowed_text_fails` ("99.9%" absent from allowed text); `test_number_in_coa_text_passes` ("99.8%" present in allowed text); `test_marketing_checks_only_banned_and_in_progress` (no number tracing for the `facts_only` and `facts_and_sources` recipes); `test_in_progress_claim_without_planned_fails`; `test_base_gate_failure_propagates`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_brief_gate.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `run_gate` injected through the same `ffprobe` and `lint` hooks so tests stay offline.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `briefs: type gate (banned claims, number tracing)`.

---

### Task 10: Publish package without a blog URL

**Files:** Modify `scheduler/publish.py`; Test `tests/scheduler/test_sched_publish.py`

**Interfaces:**
- Produces: `build_package(run_dir, blog_url: Optional[str], privacy, thumbnail_path=None, cta_line: Optional[str] = None)` and `publish_video(run_dir, video_path, blog_url: Optional[str], *, cta_line=None, ...)`. With `blog_url` the footer is unchanged. Without it the footer is `"\n\n{cta_line}\nHRSU Indore: {MAIN_SITE}/\n"` and the tags include the type from `references.json["post_kind"]`. When `cta_line` is `None` and there is no blog URL, it defaults to `get_recipe(references["post_kind"]).cta_line`, so the 3-argument `Deps.publish(run_dir, video, url)` callable keeps working unchanged.

- [ ] **Step 1: Write failing tests**: `test_package_without_blog_url_has_no_full_article_line`; `test_package_with_blog_url_unchanged` (identical to the old output); `test_package_uses_cta_line`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_sched_publish.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the optional parameters.**
- [ ] **Step 4: Run the whole file.** Expected: PASS.
- [ ] **Step 5: Commit** `publish: packages for videos without a blog URL`.

---

### Task 11: Review stop in `run_daily`

**Files:** Modify `scheduler/run_daily.py`; Test `tests/scheduler/test_sched_run_daily.py`

**Interfaces:**
- Produces: `MAX_AWAITING_REVIEW = 5`. After the gate passes, `process_post` sets `status="awaiting_review"` with `video_path` and returns `"awaiting_review"`; it no longer publishes. `run()` returns 0 without processing when `state.count_awaiting_review() >= MAX_AWAITING_REVIEW`. `pick_candidates` ignores `awaiting_review` and `rejected`.

- [ ] **Step 1: Update and add tests**: existing publish-path tests now assert `awaiting_review` and zero `publish` calls (`test_gate_pass_stops_for_review_without_publishing`); `test_run_skips_when_review_pile_full`; `test_awaiting_review_not_picked_again`; `test_retry_url_still_resets_failed`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_sched_run_daily.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `process_post` and `run`; leave `deps.publish` and the daily cap logic in place for `review approve` to call.
- [ ] **Step 4: Run the file.** Expected: PASS.
- [ ] **Step 5: Commit** `run_daily: stop every video for review; cap unreviewed pile`.

---

### Task 12: Review command

**Files:** Create `scheduler/review.py`; Test `tests/scheduler/test_sched_review.py`

**Interfaces:**
- Consumes: `State`, `Deps` (`publish`, `director`, `gate`), `run_type_gate` where the entry has `brief_id`.
- Produces: `main(argv: list[str] | None = None, deps: Deps | None = None) -> int` with subcommands `list`, `approve <id>`, `reject <id> --note TEXT`. `MAX_REJECTS = 3`.
  - `list` prints id, type, title and `video_path` for every `awaiting_review` entry.
  - `approve` publishes through `deps.publish` and sets `published` (existing fields); if the video file is missing it exits 1 and leaves the status unchanged.
  - `reject` increments `rejects`, stores `review_note`, re-runs only the director with the note (`deps.director(run_dir, project_dir, "claude", review_note=note, prompt_path=...)`; `prompt_path` is the recipe's for brief entries, omitted for blog entries) and then the gate, and returns the entry to `awaiting_review`; the fourth reject exits 1 with a message and changes nothing. The project directory is always `run_dir / "videos" / "explainer"` (stored as `project_dir` in the entry). Brief entries are gated with an injectable `type_gate=run_type_gate` argument to `main`, reading `allowed_text` from `run_dir / "allowed_text.txt"`; blog entries use `deps.gate`. For brief entries `approve` passes `entry.get("url")` (may be `None`) as the URL.

- [ ] **Step 1: Write failing tests**: `test_list_shows_only_awaiting`; `test_approve_publishes_and_marks_published`; `test_approve_missing_file_errors_and_keeps_status`; `test_reject_reruns_director_with_note_only` (text stage not called); `test_fourth_reject_refused`; `test_reject_gate_failure_marks_failed_gate`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_sched_review.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `review: list, approve, reject with note`.

---

### Task 13a: Director honours supplied assets and an orientation override

**Files:** Modify `scheduler/director.py`; Test `tests/scheduler/test_sched_director.py`

**Interfaces:**
- Produces: `orientation_for(run_dir)` returns `"landscape"` or `"portrait"` from `run_dir / "orientation.txt"` when that file exists and holds one of those two words (stripped, case-insensitive), otherwise the existing duration rule. `prepare_project(run_dir, project_dir, run=...)` copies every file under `run_dir / "assets"` (if that folder exists; skip `voice` is not an issue because it lives in the project) into `project_dir / "assets"` after `hf_prepare_project` ran, so the director prompts' `assets/` folder holds the supplied documents and photos on every (re-)direct, because the project folder is recreated each time.

- [ ] **Step 1: Write failing tests**: `test_orientation_file_overrides_duration_rule`, `test_orientation_file_garbage_is_ignored`, `test_prepare_project_copies_supplied_assets` (fake `run` that creates the project dir; assert files land in `project_dir/assets/`), `test_prepare_project_without_assets_folder_is_unchanged`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_sched_director.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement both changes in `scheduler/director.py`.**
- [ ] **Step 4: Run** `pytest tests/scheduler -q`. Expected: PASS.
- [ ] **Step 5: Commit** `director: supplied assets and orientation override`.

---

### Task 13: `make_video` command

**Files:** Create `scheduler/make_video.py`; Test `tests/scheduler/test_make_video.py`

**Interfaces:**
- Consumes: Tasks 1 to 12.
- Produces: `main(argv: list[str] | None = None, deps: Deps | None = None) -> int`: `python -m scheduler.make_video --brief TEXT [--type auto|explainer|proof|esg|market|marketing] [--url URL] [--assets DIR] [--orientation landscape|portrait] [--yes]`. Flow: `build_brief` -> duplicate check (existing id: print a warning and exit 3 unless `--yes`) -> state entry keyed by `brief.id` with `type`, `brief`, `assets`, `rejects=0` -> `run_brief_text` -> `direct` with the recipe's prompt -> `run_type_gate` -> `awaiting_review`, printing the video path and the review command. `main` takes an injectable `type_gate=run_type_gate`. Before gating it writes `run_dir / "allowed_text.txt"` = narration + `extract_asset_text` of every staged asset + source snippets; the director project directory is `run_dir / "videos" / "explainer"`; the state entry stores `project_dir`, `video_path`, `url` and `rejects`. `NeedsType` prints the guess and exits 2 before any Claude spend; `BriefRefused` prints its message and exits 2.

- [ ] **Step 1: Write failing tests** with fake deps: `test_marketing_brief_end_to_end_stops_awaiting_review`; `test_needs_type_exits_2_without_director`; `test_duplicate_brief_exits_3_without_yes`; `test_duplicate_brief_runs_with_yes`; `test_market_unsourced_fails_before_director`; `test_brief_with_url_uses_blog_url_in_package`.
- [ ] **Step 2: Run** `pytest tests/scheduler/test_make_video.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, reusing `setup_logging`, `RunLock` and `notify` from `run_daily`.
- [ ] **Step 4: Run** `pytest tests/scheduler -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `make_video: brief-driven entry point`.

---

### Task 14: Docs and dry check

**Files:** Modify `scheduler/README.md`

- [ ] **Step 1: Document** the new commands, the five types, the review loop, the 5-video review cap and the facts tiers in `scheduler/README.md`.
- [ ] **Step 2: Smoke check**: run `python -m scheduler.make_video --help` and `python -m scheduler.review list --state <empty temp file>`; both must exit 0.
- [ ] **Step 3: Run** `pytest tests -q` (full suite). Expected: PASS.
- [ ] **Step 4: Commit** `scheduler: document brief-driven videos and review`.
