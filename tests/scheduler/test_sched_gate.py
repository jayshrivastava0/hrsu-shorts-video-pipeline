import json

from scheduler.gate import count_lint_errors, run_gate


def make_run(tmp_path, status="audio", done=True, video=True, voice=(10.0, 25.0)):
    run = tmp_path / "run"
    proj = run / "videos" / "explainer"
    (proj / "renders").mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({"status": status}))
    (run / "script.json").write_text("{}")
    (run / "references.json").write_text("{}")
    (proj / "audio_meta.json").write_text(json.dumps({"voices": [{"duration_s": d} for d in voice]}))
    if video:
        (proj / "renders" / "video.mp4").write_bytes(b"x")
    if done is True:
        (proj / "DIRECTOR_DONE.json").write_text(json.dumps({"ok": True, "video": "renders/video.mp4"}))
    elif done:
        (proj / "DIRECTOR_DONE.json").write_text(json.dumps(done))
    return run, proj


def gate(run, proj, dur=35.0, lint=(0, "")):
    return run_gate(run, proj, ffprobe=lambda p: dur, lint=lambda p: lint)


def test_ok(tmp_path):
    run, proj = make_run(tmp_path)
    r = gate(run, proj, dur=36.5)
    assert r.ok, r.reasons
    assert r.video_path == proj / "renders" / "video.mp4"


def test_hold_for_review_fails(tmp_path):
    run, proj = make_run(tmp_path, status="hold_for_review")
    assert any("hold_for_review" in x for x in gate(run, proj).reasons)


def test_missing_video_and_marker(tmp_path):
    run, proj = make_run(tmp_path, done=False, video=False)
    reasons = " ".join(gate(run, proj).reasons)
    assert "DIRECTOR_DONE" in reasons and "video missing" in reasons


def test_director_failure_reason_surfaced(tmp_path):
    run, proj = make_run(tmp_path, done={"ok": False, "reason": "ran out of time"})
    assert any("ran out of time" in x for x in gate(run, proj).reasons)


def test_too_short(tmp_path):
    run, proj = make_run(tmp_path, voice=(10.0, 15.0))
    assert any("too short" in x for x in gate(run, proj, dur=25.0).reasons)


def test_duration_mismatch(tmp_path):
    run, proj = make_run(tmp_path)  # voice 35s
    assert any("differs" in x for x in gate(run, proj, dur=40.0).reasons)
    assert gate(run, proj, dur=37.9).ok


def test_lint_errors_fail(tmp_path):
    run, proj = make_run(tmp_path)
    assert any("lint" in x for x in gate(run, proj, lint=(2, "boom")).reasons)


def test_missing_references_fails(tmp_path):
    run, proj = make_run(tmp_path)
    (run / "references.json").unlink()
    assert any("references.json" in x for x in gate(run, proj).reasons)


def test_ffprobe_exception_fails(tmp_path):
    run, proj = make_run(tmp_path)
    def bad(p):
        raise RuntimeError("no ffprobe")
    r = run_gate(run, proj, ffprobe=bad, lint=lambda p: (0, ""))
    assert not r.ok


def test_count_lint_errors_shapes():
    assert count_lint_errors({"errorCount": 3}) == 3
    assert count_lint_errors({"summary": {"errors": 0, "warnings": 4}}) == 0
    assert count_lint_errors({"errors": [1, 2]}) == 2
    assert count_lint_errors([{"severity": "error"}, {"severity": "warning"}]) == 1
    assert count_lint_errors({"findings": [{"severity": "error"}]}) == 1
