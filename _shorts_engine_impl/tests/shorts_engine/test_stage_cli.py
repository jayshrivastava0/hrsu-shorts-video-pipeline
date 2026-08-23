"""Tests for shorts_engine.stage_cli — the harness-facing per-stage subcommand bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shorts_engine import config

FIXTURE_HTML = Path(__file__).parent / "fixtures" / "nitrate_post.html"


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shorts_engine.stage_cli", *args],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent,  # _shorts_engine_impl/
    )


def test_init_creates_manifest_and_prints_workspace(tmp_path):
    result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "workspace" in payload
    assert "run_id" in payload
    manifest_file = Path(payload["workspace"]) / "run_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text())
    assert manifest["status"] == "init"
    assert manifest["blog_url"] == "https://blog.hrsuindore.com/test-post"


def test_run_stage_ingest_advances_manifest_status(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]

    result = run_cli([
        "run-stage", "ingest",
        "--workspace", workspace,
        "--html-override", str(FIXTURE_HTML),
    ])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["status_after"] == "ingested"
    assert "artifacts" in payload

    manifest = json.loads((Path(workspace) / "run_manifest.json").read_text())
    assert manifest["status"] == "ingested"
    assert manifest["last_ok_status"] == "ingested"


def test_run_stage_on_missing_manifest_fails_loud(tmp_path):
    result = run_cli([
        "run-stage", "ingest",
        "--workspace", str(tmp_path / "nonexistent"),
    ])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"


def test_run_stage_out_of_order_fails_loud(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]

    ingest_result = run_cli([
        "run-stage", "ingest",
        "--workspace", workspace,
        "--html-override", str(FIXTURE_HTML),
    ])
    assert ingest_result.returncode == 0, ingest_result.stderr

    # Manifest has only reached "ingested" — jumping straight to "assemble"
    # (which targets "assembled", several stages further along) must be
    # rejected instead of silently attempted.
    result = run_cli(["run-stage", "assemble", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "out of order" in payload["message"]

    manifest = json.loads((Path(workspace) / "run_manifest.json").read_text())
    assert manifest["last_ok_status"] == "ingested"


def test_run_stage_on_unknown_stage_name_fails_loud(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]

    result = run_cli(["run-stage", "not_a_real_stage", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"


def test_visuals_prepare_writes_shot_briefs_without_checkpointing(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]
    # Advance the manifest to `audio` directly (bypassing earlier stages — this test
    # only exercises visuals-prepare, not the whole pipeline). `audio` is the real
    # immediate predecessor of `visuals` in STATUS_ORDER — NOT `shotlisted` — so this is
    # what `_stage_order_error` requires as the "next stage is visuals" precondition.
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "audio"
    manifest["last_ok_status"] = "audio"
    manifest_path.write_text(json.dumps(manifest))
    (Path(workspace) / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": "1", "beat": "hook", "type": "HEADLINE_CARD",
         "payload": {"text": "Test headline"}, "duration_s": 2.5},
    ]}))
    (Path(workspace) / "post.json").write_text(json.dumps({"images": []}))

    result = run_cli(["visuals-prepare", "--workspace", workspace])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["shot_briefs"] == "shot_briefs.json"

    briefs = json.loads((Path(workspace) / "shot_briefs.json").read_text())
    assert briefs[0]["shot_id"] == "1"
    assert briefs[0]["type"] == "HEADLINE_CARD"
    assert briefs[0]["payload"]["text"] == "Test headline"

    # Manifest must NOT have advanced past audio — visuals-prepare never checkpoints.
    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["last_ok_status"] == "audio"


def test_visuals_prepare_fade_in_s_only_fades_on_real_beat_transitions(tmp_path):
    # Regression test: fade_in_s must be computed by tracking the *previous* shot's
    # beat across the loop (matching shots_engine/stages/visuals.py:run()'s prev_beat
    # logic), not by comparing every shot to the very first shot's beat. With beats
    # [hook, hook, body, body, hook], a naive "!= first_beat" comparison would
    # incorrectly fade the second "body" shot (since "body" != "hook") even though it
    # is not a beat transition — the correct sequence only fades at index 2, where the
    # beat actually changes from "hook" to "body".
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "audio"
    manifest["last_ok_status"] = "audio"
    manifest_path.write_text(json.dumps(manifest))
    beats = ["hook", "hook", "body", "body", "hook"]
    (Path(workspace) / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": str(i + 1), "beat": beat, "type": "HEADLINE_CARD",
         "payload": {"text": f"Test headline {i + 1}"}, "duration_s": 2.5}
        for i, beat in enumerate(beats)
    ]}))
    (Path(workspace) / "post.json").write_text(json.dumps({"images": []}))

    result = run_cli(["visuals-prepare", "--workspace", workspace])
    assert result.returncode == 0, result.stderr

    briefs = json.loads((Path(workspace) / "shot_briefs.json").read_text())
    fades = [brief["fade_in_s"] for brief in briefs]
    assert fades == [0.0, 0.0, config.TRANSITION_FADE_S, 0.0, 0.0]


def test_visuals_finalize_checkpoints_when_all_shots_present_and_nonblank(tmp_path, monkeypatch):
    # Build a workspace at the same state visuals-prepare would leave it in, plus a real
    # (tiny, solid-color) rendered mp4 for the one shot, so content_pixels() has something
    # concrete to measure.
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "audio"
    manifest["last_ok_status"] = "audio"
    manifest_path.write_text(json.dumps(manifest))
    (Path(workspace) / "shot_briefs.json").write_text(json.dumps([
        {"shot_id": "1", "beat": "hook", "type": "HEADLINE_CARD",
         "payload": {"text": "Test headline"}, "duration_s": 2.5, "fade_in_s": 0.0,
         "provenance": {"resolved": "designed"}},
    ]))
    shots_dir = Path(workspace) / "shots"
    shots_dir.mkdir()
    # A real, tiny, bright-content mp4 — generate with ffmpeg directly rather than mocking,
    # so content_pixels()'s real frame-sampling and threshold logic actually runs.
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:d=2.5",
        str(shots_dir / "shot_1.mp4"),
    ], check=True, capture_output=True)

    result = run_cli(["visuals-finalize", "--workspace", workspace])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["status_after"] == "visuals"

    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["last_ok_status"] == "visuals"
    assert manifest_after["artifacts"]["shots_dir"] == "shots"


def test_visuals_finalize_fails_loud_on_missing_shot_mp4(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "audio"
    manifest["last_ok_status"] = "audio"
    manifest_path.write_text(json.dumps(manifest))
    (Path(workspace) / "shot_briefs.json").write_text(json.dumps([
        {"shot_id": "1", "beat": "hook", "type": "HEADLINE_CARD",
         "payload": {"text": "x"}, "duration_s": 2.5, "fade_in_s": 0.0,
         "provenance": {"resolved": "designed"}},
    ]))
    (Path(workspace) / "shots").mkdir()
    # Deliberately no shot_1.mp4 written.

    result = run_cli(["visuals-finalize", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "1" in payload["message"]

    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["status"] == "failed"
