import json

from scheduler import textstage
from scheduler.director import CmdResult

URL = "https://hrsuindore.com/blog/x/"


def make_run(root, status, files=True, error=None):
    run = root / "run-abc123"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({"blog_url": URL, "status": status, "error": error}))
    if files:
        for n in ("script.json", "references.json", "video_description.txt", "beats_audio.json"):
            (run / n).write_text("{}")
    return run


def runner(out="[OK] Pipeline completed: run-abc123", rc=0, timed_out=False):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return CmdResult(rc, out, timed_out)
    run.seen = seen
    return run


def test_ready(tmp_path):
    run = make_run(tmp_path, "audio")
    r = runner()
    res = textstage.run_text_stages(URL, tmp_path, run=r)
    assert res.status == "ready" and res.run_dir == run
    assert r.seen["cmd"][-5:-3] == ["--until", "audio"] or "audio" in r.seen["cmd"]
    assert "_shorts_engine_impl" in r.seen["kw"]["env"]["PYTHONPATH"]


def test_held(tmp_path):
    make_run(tmp_path, "hold_for_review", files=False, error="only 1 verified step")
    res = textstage.run_text_stages(URL, tmp_path, run=runner("[HOLD] Pipeline completed: run-abc123"))
    assert res.status == "held" and "verified step" in res.error


def test_failed_and_timeout_and_missing(tmp_path):
    assert textstage.run_text_stages(URL, tmp_path, run=runner(timed_out=True)).status == "failed"
    assert textstage.run_text_stages(URL, tmp_path, run=runner("boom", rc=1)).status == "failed"


def test_missing_artifacts_fail(tmp_path):
    make_run(tmp_path, "audio", files=False)
    assert textstage.run_text_stages(URL, tmp_path, run=runner()).status == "failed"
