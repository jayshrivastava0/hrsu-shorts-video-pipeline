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


def _run_dir_with_narration(tmp_path, words):
    run = tmp_path / "r"
    run.mkdir(parents=True, exist_ok=True)
    beats = [{"narration": " ".join(["word"] * words)}]
    (run / "script.json").write_text(json.dumps({"beats": beats}), encoding="utf-8")
    return run


def test_orientation_file_overrides_duration_rule(tmp_path):
    short = _run_dir_with_narration(tmp_path, 10)          # ~4s narration -> portrait by duration
    assert director.orientation_for(short) == "portrait"
    (short / "orientation.txt").write_text("  Landscape \n", encoding="utf-8")
    assert director.orientation_for(short) == "landscape"

    long_run = _run_dir_with_narration(tmp_path / "long", 400)  # ~160s -> landscape by duration
    (long_run / "orientation.txt").write_text("portrait", encoding="utf-8")
    assert director.orientation_for(long_run) == "portrait"


def test_orientation_file_garbage_is_ignored(tmp_path):
    short = _run_dir_with_narration(tmp_path, 10)
    (short / "orientation.txt").write_text("diagonal", encoding="utf-8")
    assert director.orientation_for(short) == "portrait"


def test_prepare_project_copies_supplied_assets(tmp_path):
    run, proj = tmp_path / "r", tmp_path / "w" / "proj"
    (run / "assets" / "photos").mkdir(parents=True)
    (run / "assets" / "brief.pdf").write_bytes(b"pdf")
    (run / "assets" / "photos" / "site.jpg").write_bytes(b"jpg")

    def hook(cmd, kw):
        # Stand-in for `hyperframes init` + hf_prepare_project: creates the project and its voice assets.
        if "init" in cmd:
            proj.mkdir(parents=True, exist_ok=True)
        else:
            (proj / "assets" / "voice").mkdir(parents=True, exist_ok=True)
            (proj / "assets" / "voice" / "voiceover.mp3").write_bytes(b"mp3")

    director.prepare_project(run, proj, run=FakeRunner(hook=hook))
    assert (proj / "assets" / "brief.pdf").read_bytes() == b"pdf"
    assert (proj / "assets" / "photos" / "site.jpg").read_bytes() == b"jpg"
    assert (proj / "assets" / "voice" / "voiceover.mp3").exists()


def test_prepare_project_without_assets_folder_is_unchanged(tmp_path):
    run, proj = tmp_path / "r", tmp_path / "w" / "proj"
    run.mkdir()
    director.prepare_project(run, proj, run=FakeRunner())
    assert not (proj / "assets").exists()


def test_build_prompt_reflects_orientation_override(tmp_path):
    run = _run_dir_with_narration(tmp_path, 10)            # short narration would be portrait
    (run / "orientation.txt").write_text("landscape", encoding="utf-8")
    template = tmp_path / "t.md"
    template.write_text("{{ORIENTATION}} {{CANVAS}} {{CONTENT_Y}}", encoding="utf-8")
    text = director.build_prompt(tmp_path / "p", run, template=template)
    assert text == "landscape 1920x1080 110-895"


def _claude_cmd(tmp_path, monkeypatch, **env):
    for k in ("HRSU_DIRECTOR_MODEL", "HRSU_DIRECTOR_EFFORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    proj, run = tmp_path / "p", tmp_path / "r"
    proj.mkdir(); run.mkdir()
    fr = FakeRunner()
    director.run_claude(proj, run, run=fr)
    return fr.calls[0][0]


def test_director_defaults_to_haiku_low_effort(tmp_path, monkeypatch):
    cmd = _claude_cmd(tmp_path, monkeypatch)
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--effort") + 1] == "low"


def test_director_model_and_effort_can_be_overridden_by_env(tmp_path, monkeypatch):
    cmd = _claude_cmd(tmp_path, monkeypatch, HRSU_DIRECTOR_MODEL="claude-opus-5-5", HRSU_DIRECTOR_EFFORT="high")
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
