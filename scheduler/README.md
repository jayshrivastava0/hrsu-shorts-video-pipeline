# Blog post or brief -> explainer video -> review -> YouTube

Two entry points produce videos, and every finished video waits for the owner:

- **Daily job** (`scheduler.run_daily`): watches https://hrsuindore.com/blog/ and, for each NEW post, produces one
  verified explainer video. This is the `explainer` type.
- **Brief command** (`scheduler.make_video`): one free-text brief (optionally with a blog URL and supplied assets)
  becomes one video of a chosen type. See "Briefs and video types".

No video is published on creation. Every video stops at `awaiting_review`, and the YouTube upload happens only when the
owner runs `python -m scheduler.review approve <id>`. See "Reviewing videos".

## How it works

```
discover (blog index + sitemap)  -- daily job only
  -> new post? (not in data/video_state.json)
  -> text stages   python -m shorts_engine <url> --until audio   (free: Ollama + DuckDuckGo + TTS)
       status hold_for_review  => state "held", NO video, alert written, never retried automatically
  -> director      headless `claude -p` in a HyperFrames project (scheduler/director_prompt.md)
  -> quality gate  scheduler/gate.py (video, ffprobe duration, hyperframes lint, DIRECTOR_DONE, no hold)
       any failure => state "failed_gate", NOT published
  -> awaiting_review   video kept on disk; the daily run stops producing new videos at 5 waiting (see below)
  -> approve       video_agent/publishers/youtube_publisher.py, privacy from HRSU_YOUTUBE_PRIVACY,
                   then videos.list read-back -> state.privacy_actual (+ loud warning if it is not what was asked)
```

Brief-driven videos use the same director, gate and review steps. Their text stage and type gate differ
(see "Briefs and video types").

Per-post states in `data/video_state.json`:
`seeded, queued, text_done, held, directed, failed_gate, failed, awaiting_review, rejected, published`
(plus `run_dir`, `video_path`, `video_id`, `privacy_actual`, `attempts`, `last_error`, timestamps, `director`).

- `awaiting_review`: the video is ready and waits for the owner. Approve publishes it; reject re-directs it.
- `rejected`: a re-direct after a reject was interrupted. The previous video is still valid but was never re-gated, so `approve` refuses it; run `reject` again.
- `published`: uploaded to YouTube (`video_id`, `privacy_actual`).
- A `failed` post is retried on later daily runs up to 2 attempts; `held` and `failed_gate` are never retried
  automatically. Re-runs resume from files on disk (text stages and a finished director are not redone).
- **Review cap:** when 5 videos are in `awaiting_review` (`MAX_AWAITING_REVIEW` in `scheduler/run_daily.py`), the daily
  run produces nothing new and exits 0 until some are reviewed.

Defaults: 1 post per run (`--max-per-run`), 1 publish per day (`--max-uploads-per-day`), counted at approval time.

## Briefs and video types

```
python -m scheduler.make_video --brief "..." [--type auto|explainer|proof|esg|market|marketing]
    [--url URL] [--assets DIR] [--orientation landscape|portrait] [--yes]
```

- `--brief` is required; its first line (up to 80 characters) becomes the title.
- `--type auto` (default) asks the text model to classify the brief and reports its guess. A low-confidence guess
  stops before any Claude spend (exit 2): rerun with an explicit `--type`.
- `--url` adds `Full article: <url>` to the YouTube description. Without `--url` the type's CTA line is used instead.
- `--assets DIR` is a folder of supporting files: PDF, PNG or JPEG only, checked by file signature, not just extension.
  **Proof briefs require `--assets`**; without it the brief is refused (exit 2), because proof videos never use invented imagery.
- `--orientation` overrides the default: narration of 90 s or more is landscape 1920x1080, otherwise portrait.
- `--yes` reruns a brief whose entry is `queued`, `text_done`, `directed`, `failed`, `failed_gate` or `held`. An entry
  that is `awaiting_review`, `rejected` or `published` is never reset: the command exits 3 and leaves it untouched.
- `--resume` continues an unfinished brief (status `text_done`, `directed`, `failed` or `failed_gate`) from its saved
  text stage: research, script and voice are not redone, only the director and the gate run again. Use the same
  `--brief` text (and `--type`), plus the same `--state`, `--runs-dir`, `--lock` and `--workspace-root` flags if you
  used them. If the run folder or its text files are gone it exits 1 and tells you to use `--yes`. A killed run
  can leave `--lock` behind; delete that file (it is only a marker) before resuming.
- A brief run takes the scheduler lock, so it cannot overlap the daily job.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Video produced and `awaiting_review`. Prints the approve and reject commands. |
| 1 | Pipeline failure (text stage held or failed, director failed, gate failed, YouTube auth problem, unexpected error), or another run holds the lock. Claude usage exhausted leaves the entry at `text_done`; rerun with `--yes` later. |
| 2 | Needs `--type` (classifier not confident; nothing spent), or the brief or assets were refused (e.g. proof without assets, or an unknown type). |
| 3 | The brief ID already exists: rerun with `--yes` if it is in a rerunnable status; `awaiting_review`, `rejected` and `published` entries are refused. |

The five types (from `scheduler/briefs/types.py` and the design spec):

| Type | May state on screen | Creative latitude | Facts source | Extra checks |
|---|---|---|---|---|
| `explainer` | Only what the verified narration and plan state | Schematic visuals, marked "illustration" | narration | Existing gate; numbers on screen must appear in the narration |
| `proof` | Only supplied documents (CoA, TDS, `brand_facts.yaml`) and supplied photos | Motion and layout only | supplied documents | Every number on screen must appear in a supplied document |
| `esg` | `brand_facts.yaml` plus sourced external facts, only practices HRSU actually has | Motion and layout | facts and sources | Every claim sourced; no greenwashing phrasing |
| `market` | Factual claims about named companies, countries or events, each verified with a source; analysis labelled as HRSU's view | Layout and metaphor | sources | Source shown for each verified claim, dated; numbers must appear in the sources |
| `marketing` | Product and service claims only from `brand_facts.yaml`; exact CTA | Story, characters and metaphor allowed, with no false factual statements | `brand_facts.yaml` only | No product claim outside `brand_facts.yaml`; CTA "Ask for a quotation at hrsuindore.com"; no delivery terms are ever stated (they are private to each client) |

Every type also gets the automatic checks on every run: banned claims and never-claimed terms must not appear on
screen, and in-progress wording must say "planned" or "pending" on the same line. The "every claim sourced" and
"no product claim outside brand facts" rules rely on the text stage and the owner's review; the gate does not prove them alone.

## Reviewing videos

```
python -m scheduler.review list
python -m scheduler.review approve <id>
python -m scheduler.review reject <id> --note "what to change"
```

- `list` prints `id | type | title | video path` for each `awaiting_review` or `rejected` entry, or
  `nothing awaiting review`. Interrupted re-directs are marked with a hint.
- `approve <id>` publishes the video to YouTube. Privacy comes from `HRSU_YOUTUBE_PRIVACY` (default `public`;
  `unlisted` or `private` also work). If the upload fails, the entry returns to its previous status with the error
  kept, and the daily job does not retry it.
- `reject <id> --note "..."` re-runs only the director with the note added to its prompt, then re-runs the gate. The
  text (script and voice) is kept. The video returns to `awaiting_review`. At most **3 rejects per brief**; a fourth is
  refused. A re-direct that fails (or runs out of Claude allowance) returns the entry to `awaiting_review` without
  using up one of the three. If the note needs different wording, edit the brief and run `make_video --yes` instead.
- `--state` and `--runs-dir` go before the subcommand, for example
  `python -m scheduler.review --state PATH list` (after the subcommand argparse rejects them). Without them the default
  `data/video_state.json` is used.

## Facts the videos may state

`brand_facts.yaml` (repo root) holds the facts every video is checked against. `scheduler/briefs/facts.py` reads it on
each run.

| Tier | Meaning for a video |
|---|---|
| `in_place` | True today. The only tier that may be stated as true. |
| `in_progress` | Under way; never stated as done. Only as "planned" or "pending approval" on the same line. The gate rejects in-progress KEYWORDS (e.g. "all boilers") that lack "planned" or "pending" on the same line; it does not check the tier text itself. |
| `never_claimed` | Must not be presented as true. CSR: no video may present HRSU as having a CSR programme. |
| `banned_claims` | Phrases that must not appear on screen or in the script (e.g. "certified", "ISO 9001", "REACH registered", "FDA approved"). The gate rejects them. |

The file also has `cta_lines`. To change a fact, edit its list item in the matching tier and keep the YAML quoting and
indentation. Move an item between tiers only when the owner says its status changed. After editing, check the file loads:
`python -c "from scheduler.briefs.facts import load_facts; print(load_facts())"`.

## Design and plan

- Design spec: `docs/superpowers/specs/2026-10-29-brief-driven-video-types-design.md`
- Implementation plan: `docs/superpowers/plans/2026-10-29-brief-driven-video-types.md`

## First-time setup

1. **Seed** so the existing ~150 posts are NOT turned into videos (exhaustive: all index pages + sitemap):
   `python -m scheduler.run_daily --seed`
   Without a state file the job refuses to run (unless `--seed`, `--allow-backfill` or `--only-url`).
   If 11+ unseen posts ever appear in one run it also refuses (missing-seed protection).
2. **YouTube auth** (needs `client_secrets.json` in the repo root, or in `HRSU_YOUTUBE_DIR`):
   `python -m scheduler.youtube_auth` -- opens a browser once and writes `youtube_token.json`.
   The scheduled job never opens a browser: with no token it stops with
   "run `python -m scheduler.youtube_auth` interactively once", before spending any Claude allowance.
   Approval checks the same token before uploading.
3. **Install the task** (does not run anything by itself):
   `powershell -ExecutionPolicy Bypass -File scheduler/install_task.ps1 [-At 11:00]`
   Task "HRSU-Blog-To-Video": daily, current user logged on only, no elevation, StartWhenAvailable,
   6 h time limit, runs `scheduler/run_scheduled.bat` (sets `PYTHONUTF8=1`, `cd`s to the repo, uses `python`
   or `%HRSU_PYTHON%`, activates `venv`/`.venv` if present).
   Test it: `Start-ScheduledTask -TaskName HRSU-Blog-To-Video`. Remove: `scheduler/uninstall_task.ps1`.

The PC must be on and logged in (Ollama cloud, Claude and Chrome-less tooling are user-level), with Ollama (the .bat launches it via `ollama list` if it is not running),
ffmpeg, Node/npx (HyperFrames) and faster-whisper available. A missed 11:00 run starts when the PC wakes.

## Commands

| Goal | Command |
|---|---|
| Daily run | `python -m scheduler.run_daily` |
| Free rehearsal (text stages only, no Claude, no YouTube, state not saved) | `python -m scheduler.run_daily --dry-run --only-url <url>` |
| Force one post | `--only-url <url>` |
| Retry a held / failed / failed_gate post | `--retry-url <url>` |
| Use Gemma instead of Claude | `--director gemma` (`auto` = Claude, Gemma only if Claude usage is exhausted) |
| Brief-driven video | `python -m scheduler.make_video --brief "..." [--type ...]` (see above) |
| Review queue | `python -m scheduler.review list` / `approve <id>` / `reject <id> --note "..."` |
| Disable | `scheduler/uninstall_task.ps1` (or Disable in Task Scheduler) |

Exit codes for `run_daily`: 0 = ok / nothing to do / held / deferred / awaiting_review / review cap reached,
1 = a post failed (`failed`, `failed_gate`, auth problem), 2 = refused (no seed; `--retry-url` for an unknown,
brief, or awaiting-review / rejected entry).
Exit codes for `make_video`: see the table above.
Exit codes for `review`: 0 success, 1 failed action, 2 bad arguments.

## Where things live

- State: `data/video_state.json` (git-ignored; atomic writes). Lock: `data/video_scheduler.lock` (stale after 6 h).
- Logs: `data/video_runs/<date>.log` (+ per-post text-stage logs). Alerts (held, gate failed, privacy mismatch,
  Claude exhausted): `data/video_alerts.log`.
- Per post: `output/shorts/run-*/` (text artifacts, `director_claude.log`, `videos/explainer/` = HyperFrames project
  with `renders/video.mp4` and `DIRECTOR_DONE.json`).

## Caveats

- **Nothing publishes without approval.** Approval is the only route to YouTube. Check the queue with
  `python -m scheduler.review list`; the daily run pauses at 5 waiting videos.
- **Public uploads and the YouTube API audit.** YouTube Data API projects that have not passed the API compliance
  audit have uploads from unverified projects locked to **private**, whatever privacy is requested. The job reads the
  privacy back after upload, stores `privacy_actual` and logs `PRIVACY MISMATCH` + an alert. If you see it, apply for the
  audit (or publish the private videos from YouTube Studio). `HRSU_YOUTUBE_PRIVACY` (`public` default, `unlisted`,
  `private`) changes only the scheduler; other callers keep `unlisted`.
- **Claude allowance.** One video costs roughly 4% of the weekly Claude allowance (measured earlier). Each reject
  re-runs the director and costs about the same again. If Claude reports a usage/rate limit, the post is deferred
  (state stays `text_done`, no attempt consumed, alert written) and retried on the next run. `--director auto` falls
  back to Gemma instead, but Gemma's visuals were judged too weak (sparse, clipped labels) to publish, so prefer deferral.
- **The gate is not a quality review.** It checks the file, duration, lint, the director's self-check, banned and
  never-claimed terms, in-progress wording and (for explainer, proof and market) numbers against the source text. Design
  quality and factual accuracy beyond that are the owner's review at `awaiting_review`.
- The headless director runs with `--permission-mode acceptEdits` plus an explicit tool allowlist
  (Bash, Edit, Write, Read, Glob, Grep, Skill, Agent, TodoWrite, WebFetch) in the HyperFrames project directory and
  has a hard 90-minute timeout.

## Known gaps

Deliberately deferred by the owner; each one is a limit on what the automatic checks prove:

- **ESG external claims** have no automatic source check. The reviewer at `awaiting_review` is the check.
- **No LLM "fuzzy pass"** for greenwashing, story claims or marketing claims outside `in_place`. The gate matches
  keywords and numbers only.
- **Market source chips are not dated**, and there is no entity-claim manifest for named companies, countries or events.
- **Mixed-type briefs are not flagged.** A brief that asks for two types is classified as one.
- **Image-only (scanned) documents are not read.** Numbers shown from them fail the proof number check.
- **The number check reads static HTML.** Animated counters show their start value ("0%") in the HTML and their end
  value comes from a script the gate does not execute, so a bare zero is ignored and a counter's animated end value is
  not checked. The reviewer at `awaiting_review` watches the final video.
- **Explainer by brief is stricter than the daily explainer.** A brief explainer goes through the type gate (base gate
  plus banned-claim, never-claimed, number and in-progress checks). The daily blog explainer uses only the base gate.
