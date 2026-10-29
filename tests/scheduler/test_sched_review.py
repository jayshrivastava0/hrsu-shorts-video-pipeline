from pathlib import Path

from scheduler import review, run_daily
from scheduler.briefs.facts import Facts
from scheduler.briefs.types import get_recipe
from scheduler.director import DirectorResult
from scheduler.gate import GateResult
from scheduler.state import State

BLOG = "https://hrsuindore.com/blog/one/"
BRIEF = "brief-abc123"


def _deps(director_result=None, gate_result=None, publish_raises=None):
    calls = {"director": [], "gate": [], "publish": [], "text": 0}

    def director(run_dir, proj, mode, **kw):
        calls["director"].append((run_dir, proj, mode, kw))
        return director_result or DirectorResult(True, "claude", video="renders/video.mp4")

    def gate(run_dir, proj):
        calls["gate"].append((run_dir, proj))
        return gate_result or GateResult(True, [], video_path=Path(proj) / "renders" / "video.mp4")

    def publish(run_dir, video, url):
        calls["publish"].append(url)
        if publish_raises:
            raise publish_raises
        return {"video_id": "V1", "url": "https://youtube.com/watch?v=V1",
                "privacy_requested": "public", "privacy_actual": "public"}

    def text(*a, **k):
        calls["text"] += 1
        raise AssertionError("text stage must not run during review")

    deps = run_daily.Deps(discover=lambda: [], text=text, director=director, gate=gate,
                          publish=publish, preflight=lambda: None, today=lambda: "2026-10-29")
    return deps, calls


def _env(tmp_path, entries):
    state_path = tmp_path / "state.json"
    st = State(state_path)
    for eid, fields in entries.items():
        st.entries[eid] = {"status": "awaiting_review", "run_dir": str(tmp_path / "ws" / eid),
                           "video_path": None, "attempts": 0, "last_error": None, **fields}
    st.save()
    return state_path, tmp_path / "runs"


def _video(tmp_path, eid):
    proj = tmp_path / "ws" / eid / "videos" / "explainer"
    (proj / "renders").mkdir(parents=True, exist_ok=True)
    (proj / "renders" / "video.mp4").write_bytes(b"x")
    return proj


def _entry(tmp_path, eid, **over):
    proj = _video(tmp_path, eid)
    base = {"status": "awaiting_review", "video_path": str(proj / "renders" / "video.mp4"),
            "project_dir": str(proj)}
    base.update(over)
    return base


def _argv(state_path, runs_dir, *rest):
    return ["--state", str(state_path), "--runs-dir", str(runs_dir), *rest]


def test_list_shows_only_awaiting(tmp_path, capsys):
    entries = {BLOG: _entry(tmp_path, "one"),
               "other": {"status": "published", "run_dir": "x", "video_path": "y"}}
    state_path, runs = _env(tmp_path, entries)
    assert review.main(_argv(state_path, runs, "list"), deps=_deps()[0]) == 0
    out = capsys.readouterr().out
    assert BLOG in out and "blog" in out and "other" not in out


def test_list_empty(tmp_path, capsys):
    state_path, runs = _env(tmp_path, {})
    assert review.main(_argv(state_path, runs, "list"), deps=_deps()[0]) == 0
    assert "nothing awaiting review" in capsys.readouterr().out


def test_approve_publishes_and_marks_published(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one")})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "approve", BLOG), deps=deps) == 0
    assert calls["publish"] == [BLOG]
    e = State(state_path).get(BLOG)
    assert e["status"] == "published" and e["video_id"] == "V1" and e["published_at"]


def test_approve_missing_file_errors_and_keeps_status(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one", video_path=str(tmp_path / "nope.mp4"))})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "approve", BLOG), deps=deps) == 1
    assert calls["publish"] == []
    assert State(state_path).get(BLOG)["status"] == "awaiting_review"


def test_approve_failure_restores_awaiting_review(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one", attempts=1)})
    deps, _ = _deps(publish_raises=RuntimeError("quota exceeded"))
    assert review.main(_argv(state_path, runs, "approve", BLOG), deps=deps) == 1
    e = State(state_path).get(BLOG)
    assert e["status"] == "awaiting_review"
    assert e["attempts"] == 1
    assert "quota exceeded" in e["last_error"]


def test_approve_unknown_id_errors(tmp_path):
    state_path, runs = _env(tmp_path, {})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "approve", "nope"), deps=deps) == 1
    assert calls["publish"] == []


def test_reject_requires_note(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one")})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "   "), deps=deps) == 1
    assert calls["director"] == []
    e = State(state_path).get(BLOG)
    assert e["status"] == "awaiting_review" and not e.get("rejects")


def test_reject_reruns_director_with_note_only(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one")})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "pacing too slow"), deps=deps) == 0
    assert calls["text"] == 0
    assert len(calls["director"]) == 1
    _, _, mode, kw = calls["director"][0]
    assert mode == "claude"
    assert kw == {"review_note": "pacing too slow"}          # blog: no prompt_path keyword
    assert len(calls["gate"]) == 1
    e = State(state_path).get(BLOG)
    assert e["status"] == "awaiting_review" and e["rejects"] == 1
    assert e["review_note"] == "pacing too slow" and e["last_error"] is None


def test_fourth_reject_refused(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one", rejects=3)})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "again"), deps=deps) == 1
    assert calls["director"] == [] and calls["gate"] == []
    e = State(state_path).get(BLOG)
    assert e["rejects"] == 3 and e["status"] == "awaiting_review"


def test_reject_gate_failure_marks_failed_gate(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one")})
    deps, _ = _deps(gate_result=GateResult(False, ["video too short", "no CTA"]))
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "fix"), deps=deps) == 1
    e = State(state_path).get(BLOG)
    assert e["status"] == "failed_gate" and "video too short" in e["last_error"]


def test_reject_director_failure_restores_awaiting_review(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one", rejects=1, review_note="older")})
    deps, calls = _deps(director_result=DirectorResult(False, "claude", reason="timeout"))
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "fix"), deps=deps) == 1
    assert calls["gate"] == []
    e = State(state_path).get(BLOG)
    assert e["status"] == "awaiting_review"
    assert e["rejects"] == 1 and e["review_note"] == "older"
    assert "timeout" in e["last_error"]


def test_reject_director_usage_exhausted_returns_to_awaiting(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one")})
    deps, calls = _deps(director_result=DirectorResult(False, "claude", reason="usage limit",
                                                       usage_exhausted=True))
    assert review.main(_argv(state_path, runs, "reject", BLOG, "--note", "fix"), deps=deps) == 1
    assert calls["gate"] == []
    e = State(state_path).get(BLOG)
    assert e["status"] == "awaiting_review"
    assert not e.get("rejects") and e.get("review_note") is None
    assert "usage" in e["last_error"].lower()


def test_list_shows_interrupted_rejected_entry(tmp_path, capsys):
    entries = {BLOG: _entry(tmp_path, "one", status="rejected", rejects=1)}
    state_path, runs = _env(tmp_path, entries)
    assert review.main(_argv(state_path, runs, "list"), deps=_deps()[0]) == 0
    out = capsys.readouterr().out
    assert BLOG in out
    assert "(re-direct interrupted: run reject again or approve)" in out


def test_approve_accepts_interrupted_rejected_entry(tmp_path):
    state_path, runs = _env(tmp_path, {BLOG: _entry(tmp_path, "one", status="rejected")})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "approve", BLOG), deps=deps) == 0
    assert calls["publish"] == [BLOG]
    assert State(state_path).get(BLOG)["status"] == "published"


def test_reject_brief_entry_uses_recipe_prompt_and_type_gate(tmp_path, monkeypatch):
    entry = _entry(tmp_path, BRIEF, type="proof", brief="Proof text", url=None)
    state_path, runs = _env(tmp_path, {BRIEF: entry})
    deps, calls = _deps()
    gate_calls = []

    def type_gate(brief, recipe, facts, run_dir, project_dir, *, allowed_text, **kw):
        gate_calls.append((brief, recipe, facts, run_dir, project_dir, allowed_text))
        return GateResult(True, [], video_path=Path(project_dir) / "renders" / "video.mp4")

    monkeypatch.setattr(review, "load_facts", lambda: Facts())
    run_dir = tmp_path / "ws" / BRIEF
    (run_dir / "allowed_text.txt").write_text("allowed 42", encoding="utf-8")

    assert review.main(_argv(state_path, runs, "reject", BRIEF, "--note", "tighten"), deps=deps,
                       type_gate=type_gate) == 0
    recipe = get_recipe("proof")
    _, _, _, kw = calls["director"][0]
    assert kw == {"review_note": "tighten", "prompt_path": recipe.prompt_path}
    assert calls["gate"] == []                                  # blog gate not used for briefs
    assert len(gate_calls) == 1
    brief, rec, facts, _, _, allowed = gate_calls[0]
    assert brief.id == BRIEF and brief.text == "Proof text" and brief.type == "proof"
    assert brief.url is None and rec == recipe and isinstance(facts, Facts)
    assert allowed == "allowed 42"
    assert State(state_path).get(BRIEF)["status"] == "awaiting_review"


def test_approve_brief_without_url_passes_none(tmp_path):
    entry = _entry(tmp_path, BRIEF, type="proof", brief="x", url=None)
    state_path, runs = _env(tmp_path, {BRIEF: entry})
    deps, calls = _deps()
    assert review.main(_argv(state_path, runs, "approve", BRIEF), deps=deps) == 0
    assert calls["publish"] == [None]
    assert State(state_path).get(BRIEF)["status"] == "published"
