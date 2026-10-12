import json

from scheduler import director
from scheduler.director import CmdResult


class FakeRunner:
    """Records calls; optional hook writes files like a real director would."""

    def __init__(self, results=None, hook=None):
        self.calls = []
        self.results = list(results or [])
        self.hook = hook

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.hook:
            self.hook(cmd, kw)
        return self.results.pop(0) if self.results else CmdResult(0, "")


def test_claude_success_parses_done(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()

    def hook(cmd, kw):
        (proj / "DIRECTOR_DONE.json").write_text(json.dumps({"ok": True, "video": "renders/video.mp4"}))
    fr = FakeRunner(hook=hook)
    res = director.run_claude(proj, run, run=fr, timeout=123)
    cmd, kw = fr.calls[0]
    assert res.ok and res.director == "claude" and res.video == "renders/video.mp4"
    assert cmd[1:4] == ["-p", "--permission-mode", "acceptEdits"]
    assert kw["cwd"] == proj and kw["timeout"] == 123
    assert "faceless-explainer" in kw["input_text"] and str(proj) in kw["input_text"]
    assert "{{" not in kw["input_text"]


def test_claude_reported_failure(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    fr = FakeRunner(hook=lambda c, k: (proj / "DIRECTOR_DONE.json").write_text(
        json.dumps({"ok": False, "reason": "empty canvas"})))
    res = director.run_claude(proj, run, run=fr)
    assert not res.ok and res.reason == "empty canvas" and not res.usage_exhausted


def test_claude_timeout(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    res = director.run_claude(proj, run, run=FakeRunner([CmdResult(-1, "", True)]))
    assert not res.ok and res.reason == "timeout"


def test_stale_done_marker_is_cleared(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    (proj / "DIRECTOR_DONE.json").write_text(json.dumps({"ok": True, "video": "renders/video.mp4"}))
    res = director.run_claude(proj, run, run=FakeRunner([CmdResult(1, "boom")]))
    assert not res.ok and "no DIRECTOR_DONE" in res.reason


def test_usage_exhausted_detected(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    res = director.run_claude(proj, run, run=FakeRunner([CmdResult(1, "Claude usage limit reached. resets 5pm")]))
    assert res.usage_exhausted and not res.ok


def test_auto_falls_back_to_gemma_on_usage(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    (proj / "renders").mkdir(parents=True); run.mkdir()
    (proj / "renders" / "video.mp4").write_bytes(b"x")
    fr = FakeRunner([CmdResult(0, USAGE_OK), CmdResult(1, "usage limit reached"), CmdResult(0, "ok")])
    res = director.direct(run, proj, "auto", run=fr, prepare=False)
    assert res.ok and res.director == "gemma"
    assert read_ok(proj)


def test_claude_mode_does_not_fall_back(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    fr = FakeRunner([CmdResult(0, USAGE_OK), CmdResult(1, "usage limit reached")])
    res = director.direct(run, proj, "claude", run=fr, prepare=False)
    assert res.usage_exhausted and len(fr.calls) == 2


USAGE_OK = '{"status":"known","session":{"remainingPercent":79},"weekly":{"remainingPercent":92}}'
USAGE_LOW = '{"status":"known","session":{"remainingPercent":79},"weekly":{"remainingPercent":5}}'


def test_low_weekly_allowance_defers_before_claude_runs(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    fr = FakeRunner([CmdResult(0, USAGE_LOW)])
    res = director.direct(run, proj, "claude", run=fr, prepare=False)
    assert res.usage_exhausted and len(fr.calls) == 1 and "weekly" in res.reason


def test_unreadable_usage_proceeds(tmp_path):
    assert director.claude_usage_block(run=FakeRunner([CmdResult(1, "garbage")])) is None


def test_gemma_failure_writes_failed_marker(tmp_path):
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    res = director.run_gemma(proj, run, run=FakeRunner([CmdResult(1, "bad")]))
    assert not res.ok
    assert json.loads((proj / "DIRECTOR_DONE.json").read_text())["ok"] is False


def test_prepare_project_runs_init_then_prepare(tmp_path):
    run, proj = tmp_path / "r", tmp_path / "w" / "proj"
    run.mkdir()
    fr = FakeRunner()
    director.prepare_project(run, proj, run=fr)
    assert "init" in fr.calls[0][0] and "--resolution=portrait" in fr.calls[0][0]
    assert fr.calls[1][0][-2:] == [str(run), str(proj)]


def test_prepare_failure_raises(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        director.prepare_project(tmp_path, tmp_path / "x", run=FakeRunner([CmdResult(1, "no")]))


def test_run_cmd_real_timeout_and_log(tmp_path):
    import sys
    log = tmp_path / "l.log"
    r = director.run_cmd([sys.executable, "-c", "import time;print('hi',flush=True);time.sleep(30)"],
                         cwd=tmp_path, timeout=2, log_path=log)
    assert r.timed_out and "hi" in log.read_text()


def read_ok(proj):
    return json.loads((proj / "DIRECTOR_DONE.json").read_text())["ok"] is True
