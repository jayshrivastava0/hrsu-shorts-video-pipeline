"""Text stage for brief-driven videos: fake run, fake llm/search/verifier, no network."""
import json
from pathlib import Path

import pytest

from scheduler.briefs.model import Brief, brief_id
from scheduler.briefs.textstage import run_brief_text
from scheduler.director import CmdResult
from scheduler.textstage import REPO_ROOT

RUN_ID = "run-abc123"
ARTIFACTS = ("script.json", "references.json", "video_description.txt", "beats_audio.json")
FACT = "Calcium nitrate dosing reduces sulfide in sewers."
TEXT = "How does calcium nitrate cut H2S in sewers?"


def _brief(type_name, text=TEXT):
    return Brief(id=brief_id(text), text=text, type=type_name)


def _key(brief):
    return f"https://hrsuindore.com/brief/{brief.id}/"


def _set_manifest(run: Path, key: str, status: str):
    run.mkdir(parents=True, exist_ok=True)
    (run / "run_manifest.json").write_text(json.dumps({"blog_url": key, "status": status, "error": None}))


class FakeRun:
    """Recording fake for run_cmd. Simulates init, run-stage audio and the explainer engine."""

    def __init__(self, drop=None):
        self.calls = []
        self.drop = drop
        self.html = None

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if "shorts_engine.stage_cli" in cmd and "init" in cmd:
            root = Path(cmd[cmd.index("--workspace-root") + 1])
            key = cmd[cmd.index("init") + 1]
            _set_manifest(root / RUN_ID, key, "init")
            return CmdResult(0, json.dumps({"workspace": str(root / RUN_ID), "run_id": RUN_ID}))
        if "shorts_engine.stage_cli" in cmd and "run-stage" in cmd:
            run = Path(cmd[cmd.index("--workspace") + 1])
            key = json.loads((run / "run_manifest.json").read_text())["blog_url"]
            _set_manifest(run, key, "audio")
            (run / "beats_audio.json").write_text("{}")
            self._apply_drop(run)
            return CmdResult(0, '{"status": "ok", "status_after": "audio"}')
        # explainer: `python -m shorts_engine <key> --until audio --workspace-root R --html-override F`
        root = Path(cmd[cmd.index("--workspace-root") + 1])
        key = cmd[3]
        self.html = Path(cmd[cmd.index("--html-override") + 1]).read_text(encoding="utf-8")
        run = root / RUN_ID
        _set_manifest(run, key, "audio")
        for name in ARTIFACTS:
            (run / name).write_text("{}")
        self._apply_drop(run)
        return CmdResult(0, f"[OK] Pipeline completed: {RUN_ID}")

    def _apply_drop(self, run):
        if self.drop:
            (run / self.drop).unlink(missing_ok=True)

    def stage_calls(self):
        return [c for c in self.calls if "shorts_engine.stage_cli" in c]


def _script_llm(thesis=FACT):
    beats = [
        {"beat": "hook", "purpose": "hook", "fact_ids": [], "narration": "Sewers smell because of H2S.",
         "card_text": "H2S", "broll_wish": "sewer", "kind": "story"},
        {"beat": "claim", "purpose": "mechanism", "fact_ids": [], "narration": FACT,
         "card_text": "Dose", "broll_wish": "plant", "kind": "fact"},
        {"beat": "view", "purpose": "mechanism", "fact_ids": [], "narration": "Operators should dose early.",
         "card_text": "Early", "broll_wish": "tank", "kind": "opinion"},
        {"beat": "proof", "purpose": "proof", "fact_ids": [], "narration": "Plants run this daily.",
         "card_text": "Daily", "broll_wish": "plant", "kind": "story"},
        {"beat": "cta", "purpose": "cta", "fact_ids": [], "narration": "Watch the plant.",
         "card_text": "Watch", "broll_wish": "plant", "kind": "story"},
    ]
    return lambda system, user, schema: {"thesis": thesis, "beats": beats}


def _search(*hits):
    rows = hits or ({"title": "Dosing study", "url": "https://www.epa.gov/x",
                     "snippet": "Calcium nitrate dosing reduces sulfide."},)
    return lambda query: list(rows)


def test_marketing_brief_runs_init_then_audio_only(tmp_path):
    brief = _brief("marketing")
    run = FakeRun()
    res = run_brief_text(brief, tmp_path, run=run, llm=_script_llm(thesis=None))
    assert res.status == "ready"
    calls = run.stage_calls()
    assert len(calls) == 2 and len(run.calls) == 2
    assert "init" in calls[0] and calls[0][calls[0].index("init") + 1] == _key(brief)
    assert "run-stage" in calls[1] and "audio" in calls[1]
    assert "--html-override" not in " ".join(run.calls[0])


def test_market_brief_unsourced_thesis_fails_without_calling_audio(tmp_path):
    brief = _brief("market")
    run = FakeRun()
    res = run_brief_text(
        brief, tmp_path, run=run, llm=_script_llm(), search=_search(),
        verifier=lambda prompt: {"supported": False, "source_ids": []},
    )
    assert res.status == "failed" and res.error
    assert not any("run-stage" in c for c in run.calls)


def test_explainer_brief_passes_html_override(tmp_path):
    brief = _brief("explainer")
    run = FakeRun()
    res = run_brief_text(brief, tmp_path, run=run, search=_search())
    assert res.status == "ready"
    assert len(run.calls) == 1
    cmd = run.calls[0]
    assert cmd[cmd.index("--until") + 1] == "audio"
    assert cmd[3] == _key(brief)
    assert "--html-override" in cmd
    assert f"<title>{TEXT}</title>" in run.html
    assert "Dosing study" in run.html


def test_result_ready_only_when_four_artifacts_exist(tmp_path):
    brief = _brief("marketing")
    assert run_brief_text(brief, tmp_path, run=FakeRun(), llm=_script_llm(None)).status == "ready"
    for name in ARTIFACTS:
        root = tmp_path / name.replace(".", "_")
        res = run_brief_text(brief, root, run=FakeRun(drop=name), llm=_script_llm(None))
        assert res.status == "failed", name
        assert name in res.error, name


def test_brand_facts_copied_into_run_dir(tmp_path):
    brief = _brief("marketing")
    run_brief_text(brief, tmp_path, run=FakeRun(), llm=_script_llm(None))
    run_dir = tmp_path / RUN_ID
    assert (run_dir / "brand_facts.yaml").read_text(encoding="utf-8") == \
        (REPO_ROOT / "brand_facts.yaml").read_text(encoding="utf-8")


def test_market_references_get_deduplicated_chips(tmp_path):
    brief = _brief("market")
    hits = (
        {"title": "A", "url": "https://www.epa.gov/a", "snippet": FACT},
        {"title": "B", "url": "https://epa.gov/b", "snippet": FACT},
        {"title": "C", "url": "https://www.example.org/c", "snippet": FACT},
    )
    res = run_brief_text(
        brief, tmp_path, run=FakeRun(), llm=_script_llm(), search=_search(*hits),
        verifier=lambda prompt: {"supported": True, "source_ids": ["S1"]},
    )
    assert res.status == "ready"
    refs = json.loads((tmp_path / RUN_ID / "references.json").read_text(encoding="utf-8"))
    assert refs["chips"] == ["epa.gov", "example.org"]
    assert [c["text"] for c in refs["claims"].values()] == [FACT]


def test_esg_brief_gathers_sources_but_does_not_verify_claims(tmp_path):
    brief = _brief("esg")
    searched, verified = [], []

    def search(query):
        searched.append(query)
        return _search()(query)

    res = run_brief_text(
        brief, tmp_path, run=FakeRun(), llm=_script_llm(), search=search,
        verifier=lambda prompt: verified.append(prompt) or {"supported": False, "source_ids": []},
    )
    assert res.status == "ready"
    assert searched == [brief.text]
    assert verified == []
