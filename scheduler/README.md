# Blog post -> explainer video -> YouTube (scheduled)

A daily job that watches https://hrsuindore.com/blog/ and, for each NEW post, produces one verified
explainer video and uploads it to YouTube as **public**, with no approval step.

## How it works

```
discover (blog index + sitemap)
  -> new post? (not in data/video_state.json)
  -> text stages   python -m shorts_engine <url> --until audio   (free: Ollama + DuckDuckGo + TTS)
       status hold_for_review  => state "held", NO video, alert written, never retried automatically
  -> director      headless `claude -p` in a HyperFrames project (scheduler/director_prompt.md)
  -> quality gate  scheduler/gate.py (video, ffprobe duration, hyperframes lint, DIRECTOR_DONE, no hold)
       any failure => state "failed_gate", NOT published
  -> YouTube       video_agent/publishers/youtube_publisher.py, privacy from HRSU_YOUTUBE_PRIVACY
       then videos.list read-back -> state.privacy_actual (+ loud warning if it is not public)
```

Per-post states in `data/video_state.json`: `seeded, queued, text_done, held, directed, failed_gate, failed, published`
(plus `run_dir`, `video_id`, `privacy_actual`, `attempts`, `last_error`, timestamps, `director`).
A `failed` post is retried on later runs up to 2 attempts; `held` and `failed_gate` are never retried
automatically. Re-runs resume from files on disk (text stages and a finished director are not redone).
Defaults: 1 post per run (`--max-per-run`), 1 public upload per day (`--max-uploads-per-day`).

## First-time setup

1. **Seed** so the existing ~150 posts are NOT turned into videos (exhaustive: all index pages + sitemap):
   `python -m scheduler.run_daily --seed`
   Without a state file the job refuses to run (unless `--seed`, `--allow-backfill` or `--only-url`).
   If 11+ unseen posts ever appear in one run it also refuses (missing-seed protection).
2. **YouTube auth** (needs `client_secrets.json` in the repo root, or in `HRSU_YOUTUBE_DIR`):
   `python -m scheduler.youtube_auth` -- opens a browser once and writes `youtube_token.json`.
   The scheduled job never opens a browser: with no token it stops with
   "run `python -m scheduler.youtube_auth` interactively once", before spending any Claude allowance.
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
| Normal run | `python -m scheduler.run_daily` |
| Free rehearsal (text stages only, no Claude, no YouTube, state not saved) | `python -m scheduler.run_daily --dry-run --only-url <url>` |
| Force one post | `--only-url <url>` |
| Retry a held / failed / failed_gate post | `--retry-url <url>` |
| Use Gemma instead of Claude | `--director gemma` (`auto` = Claude, Gemma only if Claude usage is exhausted) |
| Disable | `scheduler/uninstall_task.ps1` (or Disable in Task Scheduler) |

Exit codes: 0 = ok / nothing to do / held / deferred, 1 = a post failed (`failed`, `failed_gate`, auth problem),
2 = refused (no seed).

## Where things live

- State: `data/video_state.json` (git-ignored; atomic writes). Lock: `data/video_scheduler.lock` (stale after 6 h).
- Logs: `data/video_runs/<date>.log` (+ per-post text-stage logs). Alerts (held, gate failed, privacy mismatch,
  Claude exhausted): `data/video_alerts.log`.
- Per post: `output/shorts/run-*/` (text artifacts, `director_claude.log`, `videos/explainer/` = HyperFrames project
  with `renders/video.mp4` and `DIRECTOR_DONE.json`).

## Caveats

- **Public uploads and the YouTube API audit.** YouTube Data API projects that have not passed the API compliance
  audit have uploads from unverified projects locked to **private**, whatever privacy is requested. The job reads the
  privacy back after upload, stores `privacy_actual` and logs `PRIVACY MISMATCH` + an alert. If you see it, apply for the
  audit (or publish the private videos from YouTube Studio). `HRSU_YOUTUBE_PRIVACY` (`public` default for this job,
  `unlisted`, `private`) changes only the scheduler; other callers keep `unlisted`.
- **Claude allowance.** One video costs roughly 4% of the weekly Claude allowance (measured earlier). At 1 video/day
  that is a large share of it; lower the cadence by changing the trigger in Task Scheduler or `--max-uploads-per-day`.
  If Claude reports a usage/rate limit, the post is deferred (state stays `text_done`, no attempt consumed, alert
  written) and retried on the next run. `--director auto` falls back to Gemma instead, but Gemma's visuals were judged
  too weak (sparse, clipped labels) to publish unreviewed -- prefer deferral.
- **Fully automatic and public.** The gate checks the file, duration, lint, and the director's own self-check, but not
  design quality or factual content beyond the text-stage verification. Review the channel occasionally; set
  `HRSU_YOUTUBE_PRIVACY=unlisted` to add a manual step.
- The headless director runs with `--permission-mode acceptEdits` plus an explicit tool allowlist
  (Bash, Edit, Write, Read, Glob, Grep, Skill, Agent, TodoWrite, WebFetch) in the HyperFrames project directory and
  has a hard 90-minute timeout.
