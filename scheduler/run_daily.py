"""Daily entry point: new blog post -> explainer video -> public YouTube upload.

    python -m scheduler.run_daily --seed            # once, on first setup (no videos are made)
    python -m scheduler.run_daily                   # what Task Scheduler runs
    python -m scheduler.run_daily --dry-run         # discovery + free text stages only
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from scheduler.lock import LockBusy, RunLock
from scheduler.state import State

if TYPE_CHECKING:
    from scheduler.director import DirectorResult

logger = logging.getLogger("scheduler")

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
MAX_ATTEMPTS = 2
MAX_NEW_BURST = 10
MAX_DEFERRALS = 5
MAX_AWAITING_REVIEW = 5
RESUMABLE = ("queued", "text_done", "directed")


@dataclass
class Deps:
    discover: Callable[[], list]
    text: Callable            # (url, workspace_root, log_path) -> TextResult
    director: Callable[..., DirectorResult]   # (run_dir, project_dir, mode, **kw) -> DirectorResult
    gate: Callable            # (run_dir, project_dir) -> GateResult
    publish: Callable         # (run_dir, video_path, url) -> dict
    preflight: Callable[[], None]
    today: Callable[[], str]
    discover_all: Optional[Callable[[], list]] = None   # exhaustive listing used by --seed
    ollama_ready: Optional[Callable[[], bool]] = None   # None = assume ready


def default_deps() -> Deps:
    from scheduler import director as d, discover, gate, publish, textstage
    from video_agent.claude_client import backend_is_claude

    return Deps(
        discover=discover.fetch_post_urls,
        discover_all=discover.fetch_all_post_urls,
        ollama_ready=None if backend_is_claude() else textstage.wait_for_ollama,
        text=lambda url, root, log_path=None: textstage.run_text_stages(url, root, log_path=log_path),
        director=lambda run_dir, proj, mode, **kw: d.direct(run_dir, proj, mode, **kw),
        gate=lambda run_dir, proj: gate.run_gate(run_dir, proj),
        publish=lambda run_dir, video, url: publish.publish_video(run_dir, video, url),
        preflight=publish.preflight_auth,
        today=lambda: datetime.now().strftime("%Y-%m-%d"),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scheduler.run_daily", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", type=Path, default=DATA_DIR / "video_state.json")
    p.add_argument("--runs-dir", type=Path, default=DATA_DIR / "video_runs")
    p.add_argument("--lock", type=Path, default=DATA_DIR / "video_scheduler.lock")
    p.add_argument("--workspace-root", type=Path, default=None, help="default: output/shorts")
    p.add_argument("--seed", action="store_true", help="mark every currently listed post as seeded (no videos)")
    p.add_argument("--allow-backfill", action="store_true",
                   help="allow running without a state file (treats ALL listed posts as new)")
    p.add_argument("--only-url", help="process just this post URL (forces it even if not new)")
    p.add_argument("--retry-url", help="reset a held/failed/failed_gate post and process it again")
    p.add_argument("--max-per-run", type=int, default=1)
    p.add_argument("--max-uploads-per-day", type=int, default=1)
    p.add_argument("--director", choices=["claude"], default="claude")
    p.add_argument("--dry-run", action="store_true",
                   help="discovery + text stages only; never calls Claude/Gemma or YouTube; state not saved")
    return p


def setup_logging(runs_dir: Path, today: str) -> logging.Handler:
    runs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(runs_dir / f"{today}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root = logging.getLogger("scheduler")
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    if not any(getattr(h, "_hrsu_stdout", False) for h in root.handlers):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh._hrsu_stdout = True
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return fh


def notify(alerts_path: Path, message: str) -> None:
    logger.warning("NOTIFY: %s", message)
    try:
        alerts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(alerts_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def _text_ready(run_dir: Optional[str]) -> bool:
    if not run_dir:
        return False
    rd = Path(run_dir)
    return all((rd / n).exists() for n in ("script.json", "references.json", "video_description.txt", "beats_audio.json"))


def process_post(url: str, state: State, deps: Deps, args, alerts: Path, workspace_root: Optional[Path]) -> str:
    """Run one post through the pipeline. Returns an outcome label."""
    entry = state.ensure(url, "queued")
    if entry["status"] == "seeded":
        state.update(url, status="queued")
        entry = state.get(url)
    attempts = int(entry.get("attempts") or 0)

    def fail(msg: str, status: str = "failed") -> str:
        state.update(url, status=status, attempts=attempts + 1, last_error=msg[:500])
        logger.error("FAILED %s (%s): %s", url, status, msg)
        return status

    # 1) text stages
    run_dir = entry.get("run_dir")
    if not _text_ready(run_dir):
        if deps.ollama_ready is not None and not deps.ollama_ready():
            state.update(url, last_error="Ollama not reachable (no attempt consumed)")
            notify(alerts, f"Ollama not reachable; {url} deferred (start Ollama / check login)")
            return "infra_down"
        logger.info("text stages for %s ...", url)
        log_path = args.runs_dir / f"{deps.today()}_{url.rstrip('/').rsplit('/', 1)[-1]}_text.log"
        res = deps.text(url, workspace_root, log_path)
        if res.status == "held":
            state.update(url, status="held", run_dir=str(res.run_dir), last_error=res.error,
                         post_kind=getattr(res, "post_kind", None))
            notify(alerts, f"HELD (no video): {url} -- {res.error}")
            return "held"
        if res.status != "ready":
            if res.run_dir:
                state.update(url, run_dir=str(res.run_dir))
            return fail(f"text stages: {res.error}")
        run_dir = str(res.run_dir)
        state.update(url, status="text_done", run_dir=run_dir, last_error=None,
                     post_kind=getattr(res, "post_kind", None),
                     trust_summary=getattr(res, "trust_summary", None))
        logger.info("text ready for %s: post_kind=%s trust_summary=%s", url,
                    getattr(res, "post_kind", None), getattr(res, "trust_summary", None))
    run_dir_p = Path(run_dir)
    project = run_dir_p / "videos" / "explainer"

    if args.dry_run:
        logger.info("DRY-RUN: text stages OK for %s -> %s. Would run director=%s in %s, then gate, then "
                    "publish to YouTube (privacy per HRSU_YOUTUBE_PRIVACY, default public).",
                    url, run_dir, args.director, project)
        return "dry_run"

    # 2) fail fast on YouTube credentials BEFORE spending any Claude allowance
    try:
        deps.preflight()
    except Exception as exc:
        state.update(url, last_error=f"youtube auth: {exc}")
        notify(alerts, f"YouTube auth problem, nothing was directed: {exc}")
        return "auth_error"

    # 3) director (skipped when a previous attempt already finished it)
    from scheduler.director import read_done
    done = read_done(project)
    director_name = entry.get("director")
    if not (done and done.get("ok") is True and (project / (done.get("video") or "renders/video.mp4")).exists()):
        logger.info("director (%s) for %s ...", args.director, url)
        dres = deps.director(run_dir_p, project, args.director)
        if dres.usage_exhausted:
            deferrals = int(entry.get("deferrals") or 0) + 1
            if deferrals >= MAX_DEFERRALS:
                return fail(f"Claude usage still exhausted after {deferrals} deferrals")
            state.update(url, status="text_done", last_error=dres.reason, deferrals=deferrals)
            notify(alerts, f"Claude usage exhausted; {url} deferred to a later run")
            return "deferred"
        if not dres.ok:
            return fail(f"director[{dres.director}]: {dres.reason}")
        director_name = dres.director
    state.update(url, status="directed", director=director_name, last_error=None)

    # 4) quality gate
    gres = deps.gate(run_dir_p, project)
    if not gres.ok:
        msg = "; ".join(gres.reasons)
        state.update(url, status="failed_gate", last_error=msg[:500])
        notify(alerts, f"GATE FAILED (not published): {url} -- {msg}")
        return "failed_gate"

    # 5) hold for human review; publish_entry() uploads once the video is approved
    state.update(url, status="awaiting_review", video_path=str(gres.video_path),
                 project_dir=str(project), last_error=None)
    logger.info("AWAITING REVIEW %s (video %s)", url, gres.video_path)
    return "awaiting_review"


def publish_entry(url: str, state: State, deps: Deps, alerts: Path) -> str:
    """Upload an `awaiting_review` entry to YouTube. Returns "published" or "failed"."""
    entry = state.get(url)
    attempts = int(entry.get("attempts") or 0)
    try:
        # brief entries (have a `type`) may legitimately have no blog URL -> None; blog entries fall back to the key
        blog_url = entry.get("url") if "type" in entry else (entry.get("url") or url)
        pub = deps.publish(Path(entry["run_dir"]), Path(entry["video_path"]), blog_url)
    except Exception as exc:
        msg = f"publish: {exc}"
        state.update(url, status="failed", attempts=attempts + 1, last_error=msg[:500])
        logger.error("FAILED %s (failed): %s", url, msg)
        return "failed"
    state.update(url, status="published", video_id=pub.get("video_id"), video_url=pub.get("url"),
                 privacy_actual=pub.get("privacy_actual"), privacy_requested=pub.get("privacy_requested"),
                 published_at=pub.get("published_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
                 last_error=None)
    logger.info("PUBLISHED %s -> %s (privacy %s)", url, pub.get("url"), pub.get("privacy_actual"))
    if pub.get("privacy_actual") != pub.get("privacy_requested"):
        notify(alerts, f"Uploaded {pub.get('url')} but privacy is {pub.get('privacy_actual')!r}, not "
                       f"{pub.get('privacy_requested')!r} (YouTube API audit?)")
    return "published"


def pick_candidates(urls: list, state: State, args) -> list:
    """Resumable/retryable posts first (oldest first), then brand-new posts oldest first."""
    if args.only_url:
        e = state.get(args.only_url)
        if e is not None and "type" in e:
            logger.info("%s is a brief; use python -m scheduler.make_video --yes", args.only_url)
            return []
        if e and e["status"] in ("published", "held", "failed_gate", "awaiting_review", "rejected") \
                and args.only_url != args.retry_url:
            hint = ("use `python -m scheduler.review` to approve or reject it" if e["status"] in ("awaiting_review", "rejected")
                    else "use --retry-url to run it again")
            logger.info("%s is %s; %s", args.only_url, e["status"], hint)
            return []
        return [args.only_url]
    cands = []
    retry_entry = state.get(args.retry_url) if args.retry_url else None
    if args.retry_url and not (retry_entry is not None and "type" in retry_entry):
        cands.append(args.retry_url)
    for url, e in state.entries.items():
        if url == args.retry_url or "type" in e:   # brief entries belong to make_video, never the daily run
            continue
        if e["status"] in RESUMABLE or (e["status"] == "failed" and int(e.get("attempts") or 0) < MAX_ATTEMPTS):
            cands.append(url)
    # index lists newest first, so reversed() == oldest first
    cands += [u for u in reversed(urls) if u not in state and u not in cands]
    return cands


def run(args, deps: Deps) -> int:
    state = State(args.state, readonly=args.dry_run)
    alerts = args.runs_dir.parent / "video_alerts.log"

    if args.seed:
        urls = (deps.discover_all or deps.discover)()
        if not urls:
            logger.error("discovery returned no posts; nothing seeded")
            return 1
        added = state.seed(urls)
        logger.info("Seeded %d new post(s) (%d tracked in %s). Only posts published after this will get videos.",
                    added, len(state.entries), args.state)
        return 0

    if not state.exists() and not (args.allow_backfill or args.only_url):
        logger.error("No state file at %s. Run `python -m scheduler.run_daily --seed` first (or pass "
                     "--allow-backfill to treat every listed post as new).", args.state)
        return 2

    if args.retry_url:
        if args.retry_url not in state:
            logger.error("--retry-url %s is unknown to the state file", args.retry_url)
            return 2
        if "type" in state.get(args.retry_url):
            logger.error("%s is a brief; use python -m scheduler.make_video --yes", args.retry_url)
            return 2
        if state.get(args.retry_url)["status"] in ("awaiting_review", "rejected"):
            logger.error("%s is %s; a pending review would be discarded: use python -m scheduler.review",
                         args.retry_url, state.get(args.retry_url)["status"])
            return 2
        state.update(args.retry_url, status="queued", attempts=0, run_dir=None, last_error=None)

    if not args.dry_run and state.count_awaiting_review() >= MAX_AWAITING_REVIEW:
        logger.info("%d video(s) awaiting review (limit %d); nothing new until they are reviewed: "
                    "python -m scheduler.review list", state.count_awaiting_review(), MAX_AWAITING_REVIEW)
        return 0

    if not args.dry_run and state.count_published_on(deps.today()) >= args.max_uploads_per_day:
        logger.info("daily upload cap (%d) reached; nothing to do", args.max_uploads_per_day)
        return 0

    urls = [] if args.only_url else deps.discover()
    if not args.only_url and not urls:
        logger.warning("discovery returned no posts (site down?); only resuming known work")
    unseen = [u for u in urls if u not in state]
    if len(unseen) > MAX_NEW_BURST and not args.allow_backfill:
        logger.error("%d unseen posts found at once (limit %d) -- looks like a missing seed. Run "
                     "`python -m scheduler.run_daily --seed` or pass --allow-backfill.", len(unseen), MAX_NEW_BURST)
        return 1
    candidates = pick_candidates(urls, state, args)
    if not candidates:
        logger.info("no new posts to process")
        return 0

    for url in reversed(urls):  # oldest first, so resumption order stays oldest-first
        if url not in state and not args.dry_run:
            state.ensure(url, "queued")
    state.save()

    failures, processed = 0, 0
    for url in candidates:
        if processed >= args.max_per_run:
            break
        if not args.dry_run and state.count_published_on(deps.today()) >= args.max_uploads_per_day:
            logger.info("daily upload cap reached; stopping")
            break
        processed += 1
        logger.info("=== processing %s", url)
        try:
            outcome = process_post(url, state, deps, args, alerts, args.workspace_root)
        except Exception as exc:  # never let one post kill the run
            logger.exception("unexpected error on %s", url)
            state.update(url, status="failed", attempts=int((state.get(url) or {}).get("attempts") or 0) + 1,
                         last_error=f"unexpected: {exc}"[:500])
            outcome = "failed"
        logger.info("=== %s -> %s", url, outcome)
        if outcome in ("failed", "failed_gate", "auth_error", "infra_down"):
            failures += 1
        if outcome in ("deferred", "auth_error", "infra_down"):
            break
    return 1 if failures else 0


def main(argv: Optional[list] = None, deps: Optional[Deps] = None) -> int:
    args = build_parser().parse_args(argv)
    deps = deps or default_deps()
    handler = setup_logging(args.runs_dir, deps.today())
    try:
        with RunLock(args.lock):
            return run(args, deps)
    except LockBusy as exc:
        logger.info("skipping: %s", exc)
        return 0
    finally:
        logging.getLogger("scheduler").removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    sys.exit(main())
