"""Tests for shorts_engine.stage_cli — the harness-facing per-stage subcommand bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shorts_engine import config

FIXTURE_HTML = Path(__file__).parent / "fixtures" / "nitrate_post.html"


def test_broll_request_calls_acquisition_ladder(tmp_path, monkeypatch, capsys):
    from shorts_engine import stage_cli
    (tmp_path / "post.json").write_text(json.dumps({"images": []}), encoding="utf-8")
    called = {}

    def fake_acquire(wish, narration_span, workspace, post_images, torture=False):
        called.update(wish=wish, narration_span=narration_span)
        return {"image_path": None, "focal_hint": "center", "provenance": {"reason": "no_wish"}}

    monkeypatch.setattr("shorts_engine.sourcing.ladder.acquire", fake_acquire)
    args = stage_cli.build_parser().parse_args([
        "broll-request", "--workspace", str(tmp_path),
        "--wish", "close-up of white powder", "--narration-span", "The powder dissolves.",
    ])
    exit_code = args.func(args)
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out.strip().split("\n")[-1])
    assert out["provenance"]["reason"] == "no_wish"
    assert called["wish"] == "close-up of white powder"


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


def test_visuals_prepare_returns_briefs_and_run_id_inline(tmp_path):
    """The orchestrating agent has NO file-read tool, so returning only the
    `shot_briefs.json` filename left it unable to enumerate shots or to supply
    `author_visual_scene`'s required `shot_brief`/`workspace_id` arguments (observed live:
    it tried `skill{name:"read_file"}`, got "invalid skill name", and skipped straight to
    `stage_visuals_finalize`). The briefs and the run id must come back in the tool result."""
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    init_payload = json.loads(init_result.stdout.strip().splitlines()[-1])
    workspace = init_payload["workspace"]
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "audio"
    manifest["last_ok_status"] = "audio"
    manifest_path.write_text(json.dumps(manifest))
    (Path(workspace) / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": "1", "beat": "hook", "suggested_type": "HEADLINE_CARD",
         "payload": {"text": "Test headline"}, "duration_s": 2.5},
    ]}))
    (Path(workspace) / "post.json").write_text(json.dumps({"images": []}))

    result = run_cli(["visuals-prepare", "--workspace", workspace])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["run_id"] == init_payload["run_id"]
    assert payload["briefs"] == json.loads(
        (Path(workspace) / "shot_briefs.json").read_text())
    assert payload["briefs"][0]["shot_id"] == "1"


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


def test_visuals_finalize_out_of_order_fails_loud(tmp_path):
    # Mirrors test_run_stage_out_of_order_fails_loud: cmd_visuals_prepare and cmd_run_stage both
    # gate on _stage_order_error before proceeding; cmd_visuals_finalize must too. A manifest
    # that hasn't even reached visuals-prepare's precondition (still at "ingested", several
    # stages behind "audio") calling visuals-finalize directly must be rejected loudly instead
    # of falling through to the generic except branch and marking an otherwise-fine run failed.
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

    result = run_cli(["visuals-finalize", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "out of order" in payload["message"]

    manifest = json.loads((Path(workspace) / "run_manifest.json").read_text())
    assert manifest["last_ok_status"] == "ingested"
    assert manifest["status"] != "failed"


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


def _write_assemble_prepare_fixtures(workspace: Path) -> None:
    (workspace / "shotlist.json").write_text(json.dumps({"shots": [
        {"id": "1", "beat": "hook", "type": "HEADLINE_CARD",
         "payload": {"text": "Cold weather pours"}, "duration_s": 2.5},
        {"id": "2", "beat": "cta", "type": "LOGO_CTA",
         "payload": {"text": "Visit hrsuindore.com"}, "duration_s": 3.0},
    ]}))
    (workspace / "beats_audio.json").write_text(json.dumps([
        {"beat": "hook", "start_s": 0.0},
        {"beat": "cta", "start_s": 2.4},
    ]))
    (workspace / "word_timings.json").write_text(json.dumps([
        {"word": "Cold", "start": 0.1, "end": 0.4},
        {"word": "pours", "start": 0.5, "end": 0.9},
    ]))
    (workspace / "post.json").write_text(json.dumps({"region": "usa", "images": []}))
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3.4",
        str(workspace / "voiceover.mp3"),
    ], check=True, capture_output=True)
    shots_dir = workspace / "shots"
    shots_dir.mkdir()
    for shot_id in ("1", "2"):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:d=2.5",
            str(shots_dir / f"shot_{shot_id}.mp4"),
        ], check=True, capture_output=True)


def test_assemble_prepare_writes_brief_without_checkpointing(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]
    manifest_path = Path(workspace) / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "visuals"
    manifest["last_ok_status"] = "visuals"
    manifest_path.write_text(json.dumps(manifest))
    _write_assemble_prepare_fixtures(Path(workspace))

    result = run_cli(["assemble-prepare", "--workspace", workspace])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["assembly_brief"] == "assembly_brief.json"

    brief = json.loads((Path(workspace) / "assembly_brief.json").read_text())
    assert [s["id"] for s in brief["shots"]] == ["1", "2"]
    assert brief["shots"][0]["start_s"] == 0.0
    assert brief["shots"][1]["start_s"] == pytest.approx(brief["shots"][0]["duration_s"], abs=0.01)
    assert brief["word_timings"][0]["word"] == "Cold"
    assert Path(brief["audio_path"]).exists()
    assert brief["logo_path"] == str(config.BRAND_LOGO_FILE)
    assert brief["target_duration_s"] == pytest.approx(
        brief["voice_total_s"] + config.END_CARD_HOLD_S, abs=0.01)
    # No music track exists for region "usa" under asset_library/music in this test
    # environment, so mix_music_under_voice returns the voice path unchanged.
    assert brief["music_used"] is False

    # Not checkpointed yet — still "visuals" (mirrors visuals-prepare's own contract).
    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["last_ok_status"] == "visuals"

    # Same reason as test_visuals_prepare_returns_briefs_and_run_id_inline: the agent has no
    # file-read tool, and author_assembly_composition requires the brief OBJECT plus the run id.
    assert payload["run_id"] == json.loads(
        init_result.stdout.strip().splitlines()[-1])["run_id"]
    assert payload["brief"] == brief


def test_assemble_finalize_checkpoints_when_duration_law_holds_and_shots_present(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = Path(json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"])
    manifest_path = workspace / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "visuals"
    manifest["last_ok_status"] = "visuals"
    manifest_path.write_text(json.dumps(manifest))

    voice_total_s = 3.0
    target = voice_total_s + config.END_CARD_HOLD_S
    shots_dir = workspace / "shots"
    shots_dir.mkdir()
    # Source shot mp4s -- same white content as the assembled video below, so the
    # shot-presence similarity check (I1) passes for a legitimate composition.
    for shot_id in ("1", "2"):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={target / 2}",
            str(shots_dir / f"shot_{shot_id}.mp4"),
        ], check=True, capture_output=True)
    (workspace / "assembly_brief.json").write_text(json.dumps({
        "shots": [
            {"id": "1", "video_path": str(workspace / "shots" / "shot_1.mp4"),
             "start_s": 0.0, "duration_s": target / 2, "beat": "hook"},
            {"id": "2", "video_path": str(workspace / "shots" / "shot_2.mp4"),
             "start_s": target / 2, "duration_s": target / 2, "beat": "cta"},
        ],
        "word_timings": [], "audio_path": str(workspace / "voiceover.mp3"),
        "logo_path": str(config.BRAND_LOGO_FILE),
        "voice_total_s": voice_total_s, "target_duration_s": target,
        "music_used": False,
    }))
    # The rendered final video: bright/white throughout, duration exactly on the law.
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={target}",
        str(workspace / "video_short.mp4"),
    ], check=True, capture_output=True)

    result = run_cli(["assemble-finalize", "--workspace", str(workspace)])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["status_after"] == "assembled"

    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["last_ok_status"] == "assembled"
    report = json.loads((workspace / "assemble_report.json").read_text())
    assert len(report["shots"]) == 2
    assert all(s["content_pixels"] >= config.MIN_CONTENT_PIXELS for s in report["shots"])
    assert all("final_duration_s" in s and "beat" in s for s in report["shots"])
    assert report["music_used"] is False

    # The finalize output must be consumable by verify.shot_timeline() (C1 regression) --
    # the old shape's KeyError on "final_duration_s" broke every real run.
    from shorts_engine.stages.verify import shot_timeline
    timeline = shot_timeline(report)
    assert len(timeline) == 2
    assert timeline[0]["start_s"] == pytest.approx(0.0, abs=0.01)
    assert timeline[0]["duration_s"] == pytest.approx(target / 2, abs=0.01)
    assert timeline[0]["mid_s"] == pytest.approx(target / 4, abs=0.01)
    assert timeline[1]["start_s"] == pytest.approx(target / 2, abs=0.01)


def test_assemble_finalize_out_of_order_fails_loud(tmp_path):
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

    result = run_cli(["assemble-finalize", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "out of order" in payload["message"]

    manifest = json.loads((Path(workspace) / "run_manifest.json").read_text())
    assert manifest["last_ok_status"] == "ingested"
    assert manifest["status"] != "failed"


def test_assemble_finalize_fails_loud_on_duration_law_violation(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = Path(json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"])
    manifest_path = workspace / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "visuals"
    manifest["last_ok_status"] = "visuals"
    manifest_path.write_text(json.dumps(manifest))

    voice_total_s = 3.0
    (workspace / "assembly_brief.json").write_text(json.dumps({
        "shots": [], "word_timings": [], "audio_path": str(workspace / "voiceover.mp3"),
        "logo_path": str(config.BRAND_LOGO_FILE), "voice_total_s": voice_total_s,
        "target_duration_s": voice_total_s + config.END_CARD_HOLD_S,
    }))
    # Deliberately too short — violates the duration law.
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:d=1.0",
        str(workspace / "video_short.mp4"),
    ], check=True, capture_output=True)

    result = run_cli(["assemble-finalize", "--workspace", str(workspace)])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"

    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["status"] == "failed"


def test_assemble_finalize_catches_swapped_shot_content(tmp_path):
    # I1 regression: the never-blank check alone (content_pixels >= MIN_CONTENT_PIXELS)
    # passes for ANY bright content, so a shot silently dropped/reordered/swapped in the
    # assembled composition would previously slip through undetected as long as SOMETHING
    # bright was on screen. Build an assembled video whose first half is mostly black
    # (with just enough of a bright box to still clear the never-blank floor) while shot
    # 1's own source mp4 is plain white -- the new similarity check must catch this.
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = Path(json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"])
    manifest_path = workspace / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "visuals"
    manifest["last_ok_status"] = "visuals"
    manifest_path.write_text(json.dumps(manifest))

    voice_total_s = 3.0
    target = voice_total_s + config.END_CARD_HOLD_S
    half = target / 2
    shots_dir = workspace / "shots"
    shots_dir.mkdir()
    # Each shot's own source mp4: plain white, as a legitimate render would be.
    for shot_id in ("1", "2"):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={half}",
            str(shots_dir / f"shot_{shot_id}.mp4"),
        ], check=True, capture_output=True)

    (workspace / "assembly_brief.json").write_text(json.dumps({
        "shots": [
            {"id": "1", "video_path": str(shots_dir / "shot_1.mp4"),
             "start_s": 0.0, "duration_s": half, "beat": "hook"},
            {"id": "2", "video_path": str(shots_dir / "shot_2.mp4"),
             "start_s": half, "duration_s": half, "beat": "cta"},
        ],
        "word_timings": [], "audio_path": str(workspace / "voiceover.mp3"),
        "logo_path": str(config.BRAND_LOGO_FILE),
        "voice_total_s": voice_total_s, "target_duration_s": target,
        "music_used": False,
    }))
    # The "assembled" video: mostly-black first half (with a small bright box so it
    # still clears the never-blank floor) followed by a white second half -- shot 1's
    # position doesn't actually show shot 1's content.
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={half}",
        "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={half}",
        "-filter_complex",
        "[0:v]drawbox=x=17:y=17:w=30:h=30:color=white:t=fill[v0];"
        "[v0][1:v]concat=n=2:v=1:a=0[outv]",
        "-map", "[outv]",
        str(workspace / "video_short.mp4"),
    ], check=True, capture_output=True)

    result = run_cli(["assemble-finalize", "--workspace", str(workspace)])
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "shot 1" in payload["message"]
    assert "doesn't match its own source shot mp4" in payload["message"]

    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["status"] == "failed"


def test_assemble_finalize_handles_reflow_grown_shot_without_seek_past_eof(tmp_path):
    # I1 regression #2: shot["duration_s"] in assembly_brief.json is the POST-reflow slot
    # duration (computed by reflow() in cmd_assemble_prepare), but the shot's own source
    # mp4 at shots/shot_<id>.mp4 was rendered at the PRE-reflow shotlist duration by the
    # visuals stage. When reflow grows a shot to more than ~2x its originally-rendered
    # length, sampling the source mp4 at duration_s / 2 seeks past its actual end and
    # sample_frame() raises "frame sample failed" -- a confusing hard failure unrelated to
    # actual shot content. The fix clamps the sample time to the source file's real
    # duration. Build a shot whose source mp4 is short (1.5s) but whose brief duration_s
    # is long (4.0s, > 2x), and assert we get either a clean pass or the legitimate
    # content-mismatch message -- never the seek-past-EOF "frame sample failed" error.
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = Path(json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"])
    manifest_path = workspace / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "visuals"
    manifest["last_ok_status"] = "visuals"
    manifest_path.write_text(json.dumps(manifest))

    voice_total_s = 3.0
    target = voice_total_s + config.END_CARD_HOLD_S
    shots_dir = workspace / "shots"
    shots_dir.mkdir()

    source_duration = 1.5  # pre-reflow render length
    grown_duration_s = 4.0  # post-reflow slot duration, > 2x source_duration
    # Shot 1's source mp4 is short (pre-reflow) and plain white -- same content as what
    # the assembled video will show at shot 1's slot, so this should legitimately pass
    # once the seek is clamped to the source's real duration.
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={source_duration}",
        str(shots_dir / "shot_1.mp4"),
    ], check=True, capture_output=True)
    remaining = target - grown_duration_s
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={remaining}",
        str(shots_dir / "shot_2.mp4"),
    ], check=True, capture_output=True)

    (workspace / "assembly_brief.json").write_text(json.dumps({
        "shots": [
            {"id": "1", "video_path": str(shots_dir / "shot_1.mp4"),
             "start_s": 0.0, "duration_s": grown_duration_s, "beat": "hook"},
            {"id": "2", "video_path": str(shots_dir / "shot_2.mp4"),
             "start_s": grown_duration_s, "duration_s": remaining, "beat": "cta"},
        ],
        "word_timings": [], "audio_path": str(workspace / "voiceover.mp3"),
        "logo_path": str(config.BRAND_LOGO_FILE),
        "voice_total_s": voice_total_s, "target_duration_s": target,
        "music_used": False,
    }))
    # The assembled video: plain white throughout, matching both shots' source content.
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s=64x64:d={target}",
        str(workspace / "video_short.mp4"),
    ], check=True, capture_output=True)

    result = run_cli(["assemble-finalize", "--workspace", str(workspace)])
    if result.returncode != 0:
        payload = json.loads(result.stderr.strip().splitlines()[-1])
        assert "frame sample failed" not in payload["message"], (
            f"seek past source mp4 EOF was not clamped: {payload['message']}")
        assert "doesn't match its own source shot mp4" in payload["message"], payload["message"]
    else:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["status_after"] == "assembled"
        report = json.loads((workspace / "assemble_report.json").read_text())
        assert len(report["shots"]) == 2
