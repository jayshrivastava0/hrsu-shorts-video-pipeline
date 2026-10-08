# tests/shorts_engine/test_hold_for_review.py
from __future__ import annotations

import argparse
import json

from shorts_engine import runner, stage_cli
from shorts_engine.errors import EngineError, HoldForReview
from shorts_engine.manifest import RunManifest


def test_hold_for_review_is_an_engine_error_with_reasons():
    exc = HoldForReview(["a", "b"])
    assert isinstance(exc, EngineError)
    assert exc.reasons == ["a", "b"]
    assert str(exc) == "a; b"


def test_runner_holds_without_raising_and_skips_later_stages(tmp_path):
    ran = []

    def first(ctx):
        ran.append("first")
        return {}

    def holds(ctx):
        ran.append("holds")
        raise HoldForReview(["only 2 steps survived"])

    def never(ctx):
        ran.append("never")
        return {}

    manifest = runner.run(
        "https://example.com/blog/x",
        [("first", "ingested", first), ("holds", "facts", holds), ("never", "scripted", never)],
        tmp_path,
    )
    assert ran == ["first", "holds"]
    assert manifest.status == "hold_for_review"
    assert manifest.last_ok_status == "ingested"
    assert "only 2 steps survived" in manifest.error
    assert RunManifest.load(manifest.workspace).status == "hold_for_review"


def test_stage_cli_reports_hold_and_exits_zero(tmp_path, monkeypatch, capsys):
    m = RunManifest.create("https://example.com/blog/x", tmp_path)
    m.checkpoint("ingested")

    def holds(ctx):
        raise HoldForReview(["thin article"])

    monkeypatch.setitem(stage_cli.STAGE_FUNCTIONS, "facts", holds)
    args = argparse.Namespace(stage_name="facts", workspace=m.workspace, local_only=False,
                              html_override=None, torture=False, publish=False)
    assert stage_cli.cmd_run_stage(args) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "hold"
    assert out["reasons"] == ["thin article"]
    reloaded = RunManifest.load(m.workspace)
    assert reloaded.status == "hold_for_review"
    assert reloaded.last_ok_status == "ingested"
