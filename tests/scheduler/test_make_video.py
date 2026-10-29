import json
from pathlib import Path

from scheduler import make_video, review, run_daily
from scheduler.briefs.model import brief_id
from scheduler.director import DirectorResult
from scheduler.gate import GateResult
from scheduler.state import State
from scheduler.textstage import TextResult
from scheduler.briefs.types import get_recipe

BLOG = "https://hrsuindore.com/blog/one/"
TEXT = "Calcium nitrate cuts cold-weather concrete set time for site crews."


def _make_run(root: Path, narration=("Calcium nitrate sets concrete faster.",),
              opinion=None, assets_text=None, sources=()) -> Path:
    run = root / "run-abc123"
    run.mkdir(parents=True, exist_ok=True)
    beats = [{"narration": n, "kind": "fact"} for n in narration]
    if opinion:
        beats.append({"narration": opinion, "kind": "opinion"})
    (run / "script.json").write_text(json.dumps({"beats": beats}), encoding="utf-8")
    (run / "references.json").write_text(json.dumps({"sources": list(sources)}), encoding="utf-8")
    (run / "video_description.txt").write_text("desc", encoding="utf-8")
    (run / "beats_audio.json").write_text("[]", encoding="utf-8")
    if assets_text is not None:
        (run / "assets_text.txt").write_text(assets_text, encoding="utf-8")
    return run


def _brief_text(root: Path, status="ready", error=None, **run_kw):
    calls = []

    def brief_text(brief, workspace_root, **kw):
        calls.append(brief)
        if status != "ready":
            return TextResult(status, None, error)
        return TextResult("ready", _make_run(root, **run_kw))

    brief_text.calls = calls
    return brief_text


def _director(result=None):
    calls = []

    def director(run_dir, proj, mode, **kw):
        calls.append({"run_dir": run_dir, "project": proj, "mode": mode, "kw": kw})
        return result or DirectorResult(True, "claude", video="renders/video.mp4")

    director.calls = calls
    return director


def _gate(result=None):
    calls = []

    def type_gate(brief, recipe, facts, run_dir, project_dir, *, allowed_text):
        calls.append({"brief": brief, "allowed_text": allowed_text, "project": project_dir})
        if result is not None:
            return result
        video = Path(project_dir) / "renders" / "video.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"mp4")
        return GateResult(True, [], video_path=video)

    type_gate.calls = calls
    return type_gate


def _deps(director=None, preflight=None, publish_calls=None):
    publish_calls = publish_calls if publish_calls is not None else []

    def publish(run_dir, video, url):
        publish_calls.append(url)
        return {"video_id": "V1", "url": "https://youtube.com/watch?v=V1",
                "privacy_requested": "public", "privacy_actual": "public"}

    def no_text(*a, **k):
        raise AssertionError("the run_daily text stage must not run for a brief")

    return run_daily.Deps(discover=lambda: [], text=no_text,
                          director=director or _director(),
                          gate=lambda *a: (_ for _ in ()).throw(AssertionError("gate")),
                          publish=publish, preflight=preflight or (lambda: None),
                          today=lambda: "2026-10-29")


def _argv(tmp: Path, *extra: str, brief: str = TEXT, type_: str = "marketing"):
    return ["--brief", brief, "--type", type_,
            "--state", str(tmp / "state.json"), "--runs-dir", str(tmp / "runs"),
            "--lock", str(tmp / "lock"), "--workspace-root", str(tmp / "ws"), *extra]


def _entry(tmp: Path, brief: str = TEXT) -> dict:
    return State(tmp / "state.json").get(brief_id(brief))


def test_marketing_brief_end_to_end_stops_awaiting_review(tmp_path, capsys):
    bt = _brief_text(tmp_path / "ws")
    director = _director()
    gate = _gate()
    deps = _deps(director=director)

    rc = make_video.main(_argv(tmp_path), deps, type_gate=gate, brief_text=bt)

    assert rc == 0
    entry = _entry(tmp_path)
    assert entry["status"] == "awaiting_review"
    assert entry["type"] == "marketing"
    assert entry["video_path"].endswith("video.mp4")
    assert entry["project_dir"].endswith(str(Path("videos") / "explainer"))
    assert entry["rejects"] == 0
    assert director.calls[0]["kw"]["prompt_path"] == get_recipe("marketing").prompt_path
    assert bt.calls and bt.calls[0].type == "marketing"
    out = capsys.readouterr().out
    assert "python -m scheduler.review approve " + brief_id(TEXT) in out


def test_needs_type_exits_2_without_director(tmp_path, capsys):
    director = _director()
    rc = make_video.main(_argv(tmp_path, type_="auto"), _deps(director=director),
                         type_gate=_gate(), brief_text=_brief_text(tmp_path / "ws"),
                         llm=lambda prompt: {"type": "proof", "confidence": 0.2})

    assert rc == 2
    assert director.calls == []
    out = capsys.readouterr().out
    assert "proof" in out and "0.20" in out and "--type" in out


def test_duplicate_brief_exits_3_without_yes(tmp_path, capsys):
    deps = _deps()
    bt = _brief_text(tmp_path / "ws")
    assert make_video.main(_argv(tmp_path), deps, type_gate=_gate(), brief_text=bt) == 0

    rc = make_video.main(_argv(tmp_path), deps, type_gate=_gate(), brief_text=bt)

    assert rc == 3
    assert len(bt.calls) == 1
    assert "awaiting_review" in capsys.readouterr().out


def test_duplicate_brief_runs_with_yes(tmp_path):
    deps = _deps()
    bt = _brief_text(tmp_path / "ws")
    assert make_video.main(_argv(tmp_path), deps, type_gate=_gate(), brief_text=bt) == 0

    rc = make_video.main(_argv(tmp_path, "--yes"), deps, type_gate=_gate(), brief_text=bt)

    assert rc == 0
    assert len(bt.calls) == 2
    assert _entry(tmp_path)["status"] == "awaiting_review"


def test_market_unsourced_fails_before_director(tmp_path):
    director = _director()
    bt = _brief_text(tmp_path / "ws", status="failed", error="no verified claims")

    rc = make_video.main(_argv(tmp_path, type_="market"), _deps(director=director),
                         type_gate=_gate(), brief_text=bt)

    assert rc == 1
    assert director.calls == []
    entry = _entry(tmp_path)
    assert entry["status"] == "failed"
    assert "no verified claims" in entry["last_error"]


def test_brief_with_url_uses_blog_url_in_package(tmp_path):
    publish_calls = []
    deps = _deps(publish_calls=publish_calls)
    bt = _brief_text(tmp_path / "ws")

    rc = make_video.main(_argv(tmp_path, "--url", BLOG), deps, type_gate=_gate(), brief_text=bt)

    assert rc == 0
    assert _entry(tmp_path)["url"] == BLOG
    rc = review.main(["--state", str(tmp_path / "state.json"), "--runs-dir", str(tmp_path / "runs"),
                      "approve", brief_id(TEXT)], deps=deps)
    assert rc == 0
    assert publish_calls == [BLOG]


def test_orientation_written_to_run_dir(tmp_path):
    bt = _brief_text(tmp_path / "ws")

    rc = make_video.main(_argv(tmp_path, "--orientation", "portrait"), _deps(),
                         type_gate=_gate(), brief_text=bt)

    assert rc == 0
    run_dir = Path(_entry(tmp_path)["run_dir"])
    assert (run_dir / "orientation.txt").read_text(encoding="utf-8") == "portrait"


def test_allowed_text_combines_narration_assets_and_sources(tmp_path):
    bt = _brief_text(tmp_path / "ws",
                     narration=("Fact narration one.", "Fact narration two."),
                     opinion="Opinion line must not appear.",
                     assets_text="Asset page text 42 tonnes.",
                     sources=[{"id": "s1", "snippet": "Source snippet about nitrates."},
                              {"id": "s2"}])
    gate = _gate()

    rc = make_video.main(_argv(tmp_path), _deps(), type_gate=gate, brief_text=bt)

    assert rc == 0
    allowed = (Path(_entry(tmp_path)["run_dir"]) / "allowed_text.txt").read_text(encoding="utf-8")
    assert "Fact narration one." in allowed and "Fact narration two." in allowed
    assert "Asset page text 42 tonnes." in allowed
    assert "Source snippet about nitrates." in allowed
    assert "Opinion line must not appear." not in allowed
    assert gate.calls[0]["allowed_text"] == allowed


def test_preflight_failure_exits_1_without_text_stage(tmp_path):
    def preflight():
        raise RuntimeError("no youtube credentials")

    bt = _brief_text(tmp_path / "ws")
    rc = make_video.main(_argv(tmp_path), _deps(preflight=preflight), type_gate=_gate(), brief_text=bt)

    assert rc == 1
    assert bt.calls == []
    assert _entry(tmp_path) is None  # no entry for a new brief until preflight succeeds


def test_director_usage_exhausted_exits_1_and_keeps_text_done(tmp_path):
    director = _director(DirectorResult(False, "claude", reason="weekly limit reached",
                                        usage_exhausted=True))
    rc = make_video.main(_argv(tmp_path), _deps(director=director), type_gate=_gate(),
                         brief_text=_brief_text(tmp_path / "ws"))

    assert rc == 1
    entry = _entry(tmp_path)
    assert entry["status"] == "text_done"
    assert "weekly limit reached" in entry["last_error"]


def test_gate_failure_marks_failed_gate(tmp_path):
    rc = make_video.main(_argv(tmp_path), _deps(), type_gate=_gate(GateResult(False, ["banned claim"])),
                         brief_text=_brief_text(tmp_path / "ws"))

    assert rc == 1
    entry = _entry(tmp_path)
    assert entry["status"] == "failed_gate"
    assert "banned claim" in entry["last_error"]


def _seed_awaiting(tmp_path: Path) -> dict:
    """An awaiting_review brief with a rendered video from an earlier run."""
    state = State(tmp_path / "state.json")
    state.ensure(brief_id(TEXT), "queued")
    fields = dict(status="awaiting_review", run_dir=str(tmp_path / "old-run"),
                  video_path=str(tmp_path / "old.mp4"), project_dir=str(tmp_path / "old-proj"),
                  director="claude", video_id="OLD", rejects=2)
    state.entries[brief_id(TEXT)].update(fields)
    state.save()
    return fields


def test_yes_rerun_preflight_failure_preserves_existing_entry(tmp_path):
    seeded = _seed_awaiting(tmp_path)

    def preflight():
        raise RuntimeError("no youtube credentials")

    bt = _brief_text(tmp_path / "ws")
    rc = make_video.main(_argv(tmp_path, "--yes"), _deps(preflight=preflight), type_gate=_gate(), brief_text=bt)

    assert rc == 1
    entry = _entry(tmp_path)
    for field, value in seeded.items():
        assert entry[field] == value, field
    assert "no youtube credentials" in entry["last_error"]
    assert bt.calls == []


def test_yes_rerun_success_clears_stale_run_fields(tmp_path):
    _seed_awaiting(tmp_path)

    rc = make_video.main(_argv(tmp_path, "--yes"), _deps(), type_gate=_gate(),
                         brief_text=_brief_text(tmp_path / "ws"))

    assert rc == 0
    entry = _entry(tmp_path)
    assert entry["status"] == "awaiting_review"
    assert entry["video_id"] is None
    assert entry["rejects"] == 0
    assert entry["video_path"] != str(tmp_path / "old.mp4")
    assert Path(entry["video_path"]).name == "video.mp4"
    assert Path(entry["run_dir"]).name == "run-abc123"


def test_malformed_script_json_marks_failed_and_exits_1(tmp_path):
    def bt(brief, workspace_root, **kw):
        run = _make_run(tmp_path / "ws")
        (run / "script.json").write_text("{not json", encoding="utf-8")
        return TextResult("ready", run)

    director = _director()
    rc = make_video.main(_argv(tmp_path), _deps(director=director), type_gate=_gate(), brief_text=bt)

    assert rc == 1
    assert director.calls == []
    entry = _entry(tmp_path)
    assert entry["status"] == "failed"
    assert entry["last_error"].startswith("unexpected:")


def test_director_exception_marks_failed_and_exits_1(tmp_path):
    def director(run_dir, proj, mode, **kw):
        raise RuntimeError("director blew up")

    rc = make_video.main(_argv(tmp_path), _deps(director=director), type_gate=_gate(),
                         brief_text=_brief_text(tmp_path / "ws"))

    assert rc == 1
    entry = _entry(tmp_path)
    assert entry["status"] == "failed"
    assert "director blew up" in entry["last_error"]


def test_type_gate_exception_marks_failed_and_exits_1(tmp_path):
    def gate(brief, recipe, facts, run_dir, project_dir, *, allowed_text):
        raise RuntimeError("gate blew up")

    rc = make_video.main(_argv(tmp_path), _deps(), type_gate=gate, brief_text=_brief_text(tmp_path / "ws"))

    assert rc == 1
    entry = _entry(tmp_path)
    assert entry["status"] == "failed"
    assert "gate blew up" in entry["last_error"]


def test_title_handles_whitespace_only_text():
    assert make_video._title("   \n  ") == ""
    assert make_video._title("  First line  \nsecond") == "First line"


def test_busy_lock_exits_1(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("12345", encoding="utf-8")  # fresh lock held by another run
    bt = _brief_text(tmp_path / "ws")

    rc = make_video.main(_argv(tmp_path), _deps(), type_gate=_gate(), brief_text=bt)

    assert rc == 1
    assert bt.calls == []
