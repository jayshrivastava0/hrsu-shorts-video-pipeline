"""Tests for shorts_engine.stage_cli — the harness-facing per-stage subcommand bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
