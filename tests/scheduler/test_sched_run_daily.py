import json
from types import SimpleNamespace

import pytest

from scheduler import run_daily
from scheduler.director import DirectorResult
from scheduler.gate import GateResult
from scheduler.lock import RunLock
from scheduler.state import State
from scheduler.textstage import TextResult

U = ["https://hrsuindore.com/blog/%s/" % s for s in ("new-c", "new-b", "old-a")]  # newest first


class Env:
    def __init__(self, tmp_path, text="ready", director="ok", gate_ok=True, urls=None, publish_priv="public",
                 auth_error=None):
        self.tmp = tmp_path
        self.urls = list(urls if urls is not None else U)
        self.calls = {"text": 0, "director": 0, "gate": 0, "publish": 0, "discover": 0}
        self.text_mode, self.director_mode, self.gate_ok = text, director, gate_ok
        self.publish_priv, self.auth_error = publish_priv, auth_error
        self.today = "2026-10-12"
        self.published = []
        self.deps = run_daily.Deps(
            discover=self._discover, text=self._text, director=self._director, gate=self._gate,
            publish=self._publish, preflight=self._preflight, today=lambda: self.today)

    def argv(self, *extra):
        t = self.tmp
        return ["--state", str(t / "state.json"), "--runs-dir", str(t / "runs"), "--lock", str(t / "x.lock"),
                "--workspace-root", str(t / "ws"), *extra]

    def run(self, *extra):
        return run_daily.main(self.argv(*extra), self.deps)

    def state(self):
        return State(self.tmp / "state.json")

    def _discover(self):
        self.calls["discover"] += 1
        return self.urls

    def _text(self, url, root, log_path=None):
        self.calls["text"] += 1
        run = self.tmp / "ws" / ("run-" + url.rstrip("/").rsplit("/", 1)[-1])
        run.mkdir(parents=True, exist_ok=True)
        if self.text_mode == "held":
            return TextResult("held", run, "only 1 verified step")
        if self.text_mode == "failed":
            return TextResult("failed", None, "ollama down")
        for n in ("script.json", "references.json", "video_description.txt", "beats_audio.json"):
            (run / n).write_text("{}")
        return TextResult("ready", run)

    def _director(self, run_dir, proj, mode):
        self.calls["director"] += 1
        if self.director_mode == "usage":
            return DirectorResult(False, "claude", "usage", usage_exhausted=True)
        if self.director_mode == "fail":
            return DirectorResult(False, "claude", "timeout")
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "renders").mkdir(exist_ok=True)
        (proj / "renders" / "video.mp4").write_bytes(b"x")
        (proj / "DIRECTOR_DONE.json").write_text(json.dumps({"ok": True, "video": "renders/video.mp4"}))
        return DirectorResult(True, "claude", video="renders/video.mp4")

    def _gate(self, run_dir, proj):
        self.calls["gate"] += 1
        if not self.gate_ok:
            return GateResult(False, ["video too short"])
        return GateResult(True, [], video_path=proj / "renders" / "video.mp4")

    def _publish(self, run_dir, video, url):
        self.calls["publish"] += 1
        self.published.append(url)
        return {"video_id": "V%d" % len(self.published), "url": "https://youtube.com/watch?v=V",
                "privacy_requested": "public", "privacy_actual": self.publish_priv}

    def _preflight(self):
        if self.auth_error:
            raise RuntimeError(self.auth_error)


def seed(env):
    assert env.run("--seed") == 0


def test_refuses_without_seed(tmp_path):
    env = Env(tmp_path)
    assert env.run() == 2
    assert env.calls["text"] == 0


def test_seed_marks_all_and_no_video(tmp_path):
    env = Env(tmp_path)
    seed(env)
    assert {e["status"] for e in env.state().entries.values()} == {"seeded"}
    assert env.run() == 0 and env.calls["text"] == 0


def test_new_post_published_end_to_end(tmp_path):
    env = Env(tmp_path)
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/brand-new/"] + U
    assert env.run() == 0
    e = env.state().get("https://hrsuindore.com/blog/brand-new/")
    assert e["status"] == "published" and e["video_id"] == "V1" and e["privacy_actual"] == "public"
    assert e["director"] == "claude"
    assert (tmp_path / "runs" / "2026-10-12.log").exists()


def test_held_never_directs(tmp_path):
    env = Env(tmp_path, text="held")
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/n/"] + U
    assert env.run() == 0
    assert env.state().get("https://hrsuindore.com/blog/n/")["status"] == "held"
    assert env.calls["director"] == 0 and env.calls["publish"] == 0
    assert "HELD" in (tmp_path / "video_alerts.log").read_text()
    # not retried automatically
    env.run()
    assert env.calls["text"] == 1


def test_gate_failure_no_publish_and_not_retried(tmp_path):
    env = Env(tmp_path, gate_ok=False)
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/n/"] + U
    assert env.run() == 1
    e = env.state().get("https://hrsuindore.com/blog/n/")
    assert e["status"] == "failed_gate" and "too short" in e["last_error"]
    assert env.calls["publish"] == 0
    env.run()
    assert env.calls["director"] == 1


def test_director_failure_retries_then_stops(tmp_path):
    env = Env(tmp_path, director="fail")
    seed(env)
    new = "https://hrsuindore.com/blog/n/"
    env.urls = [new] + U
    assert env.run() == 1
    assert env.state().get(new)["attempts"] == 1
    assert env.run() == 1
    assert env.state().get(new)["attempts"] == 2
    assert env.run() == 0                         # gave up
    assert env.calls["director"] == 2 and env.calls["publish"] == 0
    assert env.calls["text"] == 1                 # text stages resumed from disk


def test_usage_exhausted_defers_without_attempt(tmp_path):
    env = Env(tmp_path, director="usage")
    seed(env)
    new = "https://hrsuindore.com/blog/n/"
    env.urls = [new] + U
    assert env.run() == 0
    e = env.state().get(new)
    assert e["status"] == "text_done" and e["attempts"] == 0
    env.director_mode = "ok"
    assert env.run() == 0
    assert env.state().get(new)["status"] == "published"


def test_auth_missing_fails_fast_before_director(tmp_path):
    env = Env(tmp_path, auth_error="run `python -m scheduler.youtube_auth` interactively once")
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/n/"] + U
    assert env.run() == 1
    assert env.calls["director"] == 0


def test_lock_prevents_double_run(tmp_path):
    env = Env(tmp_path)
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/n/"] + U
    with RunLock(tmp_path / "x.lock"):
        assert env.run() == 0
    assert env.calls["text"] == 0


def test_max_per_run_and_oldest_first(tmp_path):
    env = Env(tmp_path, urls=[])
    env.urls = ["https://hrsuindore.com/blog/%s/" % s for s in ("c", "b", "a")]  # c newest
    assert env.run("--allow-backfill", "--max-per-run", "2", "--max-uploads-per-day", "5") == 0
    assert env.published == ["https://hrsuindore.com/blog/a/", "https://hrsuindore.com/blog/b/"]
    assert env.state().get("https://hrsuindore.com/blog/c/")["status"] == "queued"


def test_daily_upload_cap(tmp_path):
    env = Env(tmp_path, urls=[])
    env.urls = ["https://hrsuindore.com/blog/%s/" % s for s in ("c", "b", "a")]
    env.run("--allow-backfill", "--max-per-run", "3")            # cap default 1
    assert len(env.published) == 1
    env.run()
    assert len(env.published) == 1                                 # same day
    env.today = "2026-10-13"
    env.run()
    assert len(env.published) == 2


def test_dry_run_never_directs_or_publishes_or_saves(tmp_path):
    env = Env(tmp_path)
    seed(env)
    env.urls = ["https://hrsuindore.com/blog/n/"] + U
    before = (tmp_path / "state.json").read_text()
    assert env.run("--dry-run") == 0
    assert env.calls["text"] == 1 and env.calls["director"] == 0 and env.calls["publish"] == 0
    assert (tmp_path / "state.json").read_text() == before


def test_only_url_without_state_and_force_published_skipped(tmp_path):
    env = Env(tmp_path)
    url = "https://hrsuindore.com/blog/solo/"
    assert env.run("--only-url", url) == 0
    assert env.published == [url] and env.calls["discover"] == 0
    env.today = "2026-10-14"
    env.run("--only-url", url)
    assert env.published == [url]                                   # already published


def test_retry_url_resets_held(tmp_path):
    env = Env(tmp_path, text="held")
    seed(env)
    new = "https://hrsuindore.com/blog/n/"
    env.urls = [new] + U
    env.run()
    env.text_mode = "ready"
    assert env.run("--retry-url", new) == 0
    assert env.state().get(new)["status"] == "published"


def test_privacy_mismatch_alerts(tmp_path):
    env = Env(tmp_path, publish_priv="private")
    seed(env)
    new = "https://hrsuindore.com/blog/n/"
    env.urls = [new] + U
    env.run()
    assert env.state().get(new)["privacy_actual"] == "private"
    assert "privacy" in (tmp_path / "video_alerts.log").read_text()


def test_discovery_empty_does_not_crash(tmp_path):
    env = Env(tmp_path)
    seed(env)
    env.urls = []
    assert env.run() == 0


def test_seed_with_empty_discovery_fails(tmp_path):
    env = Env(tmp_path, urls=[])
    assert env.run("--seed") == 1
    assert not (tmp_path / "state.json").exists()
