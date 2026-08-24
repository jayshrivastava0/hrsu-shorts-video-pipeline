# HyperFrames Assembly Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `assemble.py`'s ffmpeg-based caption/logo/progress-bar mux with a HyperFrames
composition authored by a creative subagent, with a deterministic composition as its never-blank
fallback — completing the `assembled` stage of the DeepSeek Harness / HyperFrames migration.

**Architecture:** Same three-piece split as the already-shipped `visuals` stage, since Python
cannot call harness tools: `stage_assemble_prepare` (Python — reflow + music mix + brief) →
`author_assembly_composition` (harness tool — spawns a creative subagent, 2 attempts then falls
back to a deterministic generator) → `stage_assemble_finalize` (Python — duration-law + per-shot
presence verification, manifest checkpoint).

**Tech Stack:** Python 3 (`shorts_engine`), TypeScript/Cordis harness (`@deepseek-ai/dsh-*`),
HyperFrames CLI (`npx hyperframes render`), ffmpeg/ffprobe (already a hard dependency
project-wide).

**Spec:** `docs/superpowers/specs/2026-08-25-hyperframes-assembly-stage-design.md`

## Global Constraints

- Node >= 22.15.0, pnpm pinned via `packageManager` (`harness/package.json`) — same as every
  prior harness package.
- `cordis.yml`'s `!!js "..."` expressions run via `with (ctx) { eval(expr) }` — only real JS
  globals (`process`, etc.) are in scope; `require(...)` throws `ReferenceError`. Never write a
  `!!js require(...)` expression.
- On Windows, spawning `hyperframes` via `npx` MUST go through `spawn('cmd.exe', ['/c', 'npx',
  ...], { cwd })` — bare `spawn('npx', ...)` throws `ENOENT`, `spawn('npx.cmd', ..., {shell:true})`
  corrupts paths containing spaces, `spawn('npx.cmd', ...)` without shell throws `EINVAL`. Gate on
  `process.platform === 'win32'`.
- Identifier-scoping discipline (`resolveScopedPath`/`resolveScopedAssemblyPath`): reject any
  `workspaceId` that is `path.isAbsolute()` OR contains `/`, `\`, or `:` at all — these are
  identifiers, never paths.
- Subagent authoring gets exactly 2 attempts (1 initial + 1 corrected retry), then falls back —
  never retry a third time.
- Canvas: 1080×1920 (`config.CANVAS_W`/`CANVAS_H`), portrait resolution.
- Duration law (`shorts_engine/config.py`): `END_CARD_HOLD_S = 1.5`,
  `AUDIO_COMPLETENESS_MARGIN_S = 1.4`, tolerance `0.35s` around `voice_total_s + END_CARD_HOLD_S`.
- Never-blank threshold: `MIN_CONTENT_PIXELS = 500` bright pixels (luma >
  `LUMA_CONTENT_THRESHOLD = 140`) — `shorts_engine/stages/visuals.py`'s existing
  `content_pixels`/`sample_frame`.
- Model swap: `ASSEMBLY_AUTHOR_MODEL` env var (`process.env.ASSEMBLY_AUTHOR_MODEL ?? 'gemma4:31b-cloud'`),
  same pattern as `ORCHESTRATOR_MODEL`/`SCENE_AUTHOR_MODEL`. `kimi-k2.7-code:cloud` is out of scope
  (403, no subscription).
- Brand facts / banned claims (verbatim from `scene-author-persona.md`, reused): company "HRSU
  Indore Pvt. Ltd." / "HRSU INDORE", domain `hrsuindore.com`, tagline "Beyond Granules. The Purity
  of Powder.", gold `#d4af37`, navy `#0a192f`/`#0a1428`, text `#ccd6f6`/`#8892b0`. Banned: "REACH
  registered"/"REACH-registered", "certified", "ISO 9001", "FDA approved".
- `STATUS_ORDER` has `assembled` immediately after `visuals` — no manifest schema change; the
  stage checkpoints exactly once, in `stage_assemble_finalize`.
- Confirmed HyperFrames facts (verified empirically this session, not assumed):
  1. A `<video>` clip whose `data-duration` **exceeds** its native file length holds the last
     frame cleanly for the remainder (no loop, no blank, no distortion).
  2. A `<video>` clip whose `data-duration` is **shorter** than its native file length trims
     cleanly at that point (no distortion, no error).
  3. A plain absolute Windows path (e.g. `E:\Projects\HRSU Shorts\...\shot_1.mp4`) works directly
     as a `<video src>`/`<audio src>`, even for a file entirely outside the HyperFrames project
     root — no `file://` prefix, no need to copy/symlink shot mp4s into the project tree.
  4. `<video>`/`<audio>` are first-class clips with `data-start`/`data-duration`/`data-track-index`
     (`<audio>` also takes `data-volume`) — confirmed via HyperFrames' own shipped
     `node_modules/hyperframes/dist/templates/warm-grain/index.html`.
  5. If a `<video src>` cannot be resolved on disk at all, HyperFrames shows its first frame for
     the whole clip AND aborts the render with a "coverage" error (`HF_VIDEO_COVERAGE_THRESHOLD`
     gate) rather than silently shipping a broken file — this is a real safety net worth knowing
     about, not something to work around.
  6. **Not verified — do not assume:** whether `<img>` behaves as a clip, or whether CSS
     `background-image: url(...)` resolves a plain absolute Windows path the same way `<video
     src>` does. The deterministic fallback composition (Task 3) avoids this question entirely by
     reusing the already-proven text-based brand mark from `generic_fallback.html` instead of an
     image asset.

---

### Task 1: Encode the `<video>` clip retiming facts as a permanent regression test

**Files:**
- Create: `harness/tests/video-clip-retiming.e2e.ts`

**Interfaces:**
- Consumes: the real `hyperframes` CLI (`npx hyperframes render`) and `ffmpeg`/`ffprobe`, both
  already hard dependencies of this repo. No production code from later tasks.
- Produces: nothing later tasks import — this is a standalone characterization test of a
  third-party dependency's behavior, guarding the assumption Task 3's retiming logic depends on.

This is a **characterization test**, not TDD in the usual sense — there's no new production code
under test, only a confirmed fact about `hyperframes render`'s real behavior (verified manually
during this plan's design: a 1s-red + 1s-green synthetic clip, rendered at `data-duration=4` holds
green from t=2s onward; rendered at `data-duration=1.5` trims cleanly). This test makes that
finding permanent so a future `hyperframes` upgrade can't silently break Task 3's assumptions
without a test failing.

- [ ] **Step 1: Write the test**

```typescript
import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const PROJECT_ROOT = join(import.meta.dirname, '..', 'hyperframes_scenes_project')
const SCRATCH_DIR = join(PROJECT_ROOT, 'compositions', '_video_retime_check')
const OUT_DIR = join(import.meta.dirname, '..', '_video_retime_check_out')

function ffmpeg(args: string[]): void {
  const res = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', ...args])
  if (res.status !== 0) {
    throw new Error(`ffmpeg ${args.join(' ')} failed: ${res.stderr?.toString()}`)
  }
}

function averageColorAt(mp4Path: string, atSeconds: number): [number, number, number] {
  const res = spawnSync('ffmpeg', [
    '-y', '-loglevel', 'error', '-ss', atSeconds.toFixed(3), '-i', mp4Path,
    '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-',
  ])
  if (res.status !== 0 || res.stdout.length < 3) {
    throw new Error(`averageColorAt(${mp4Path}, ${atSeconds}) failed: ${res.stderr?.toString()}`)
  }
  return [res.stdout[0], res.stdout[1], res.stdout[2]]
}

function runHyperframesRenderSync(compositionRelPath: string, outputPath: string): void {
  // Same spawn strategy as harness/packages/tool-visual-scene/src/scene-tools.ts's
  // runHyperframesRender — see that file's comment for why cmd.exe /c is required on Windows.
  const [command, args] = process.platform === 'win32'
    ? ['cmd.exe', ['/c', 'npx', 'hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
    : ['npx', ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
  const res = spawnSync(command, args, { cwd: PROJECT_ROOT })
  if (res.status !== 0) {
    throw new Error(`hyperframes render failed: ${res.stderr?.toString() ?? res.stdout?.toString()}`)
  }
}

function compositionHtml(compositionDuration: number, clipDataDuration: number): string {
  return `<!doctype html>
<html><head><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>body,html{margin:0;padding:0;width:1080px;height:1920px;overflow:hidden;background:#000;}</style>
</head><body>
<div id="root" data-composition-id="retime-check" data-width="1080" data-height="1920" data-duration="${compositionDuration}">
<video id="clip" class="clip" src="compositions/_video_retime_check/red_green.mp4" muted playsinline
       data-start="0" data-duration="${clipDataDuration}" data-track-index="0"></video>
<script>
window.__timelines = window.__timelines || {};
window.__timelines['retime-check'] = gsap.timeline({ paused: true });
</script>
</div></body></html>`
}

describe('HyperFrames <video> clip retiming (data-duration vs native length)', () => {
  const redGreenFixture = join(SCRATCH_DIR, 'red_green.mp4')

  beforeAll(() => {
    mkdirSync(SCRATCH_DIR, { recursive: true })
    mkdirSync(OUT_DIR, { recursive: true })
    const red = join(SCRATCH_DIR, 'red.mp4')
    const green = join(SCRATCH_DIR, 'green.mp4')
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=1:r=10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', red])
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=green:s=64x64:d=1:r=10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', green])
    ffmpeg(['-i', red, '-i', green, '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-pix_fmt', 'yuv420p', redGreenFixture])
  }, 30_000)

  afterAll(() => {
    rmSync(SCRATCH_DIR, { recursive: true, force: true })
    rmSync(OUT_DIR, { recursive: true, force: true })
  })

  test('data-duration exceeding native length holds the last frame — no loop, no blank', () => {
    writeFileSync(join(SCRATCH_DIR, 'longer.html'), compositionHtml(4, 4), 'utf8')
    const outPath = join(OUT_DIR, 'longer.mp4')
    runHyperframesRenderSync('compositions/_video_retime_check/longer.html', outPath)

    const duringRed = averageColorAt(outPath, 0.5)
    const duringGreen = averageColorAt(outPath, 1.5)
    const afterNativeEnd = averageColorAt(outPath, 3.5)

    expect(duringRed[0]).toBeGreaterThan(150)
    expect(duringGreen[1]).toBeGreaterThan(80)
    // Still green 1.5s past the source's native 2s end — held, not looped back to red and not blank.
    expect(afterNativeEnd[1]).toBeGreaterThan(80)
    expect(afterNativeEnd[0]).toBeLessThan(80)
  }, 60_000)

  test('data-duration shorter than native length trims cleanly — no distortion, no error', () => {
    writeFileSync(join(SCRATCH_DIR, 'shorter.html'), compositionHtml(1.5, 1.5), 'utf8')
    const outPath = join(OUT_DIR, 'shorter.mp4')
    runHyperframesRenderSync('compositions/_video_retime_check/shorter.html', outPath)

    expect(averageColorAt(outPath, 0.3)[0]).toBeGreaterThan(150)
    expect(averageColorAt(outPath, 1.4)[1]).toBeGreaterThan(80)
  }, 60_000)
})
```

- [ ] **Step 2: Run it**

Run (from `harness/`): `pnpm exec vitest run tests/video-clip-retiming.e2e.ts`
Expected: both tests **PASS**, confirming the facts already verified manually. If either fails,
STOP — do not proceed to Task 3 with an unverified assumption; investigate what changed in the
installed `hyperframes` version instead.

- [ ] **Step 3: Commit**

```bash
cd "harness"
git add tests/video-clip-retiming.e2e.ts
git commit -m "Add regression test for HyperFrames video-clip data-duration retiming"
```

---

### Task 2: Python bridge — `stage_assemble_prepare` / `stage_assemble_finalize`

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stage_cli.py`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`

**Interfaces:**
- Consumes: `shorts_engine.stages.assemble.reflow` (unchanged), `shorts_engine.stages.visuals.{content_pixels, sample_frame}` (unchanged), `shorts_engine.cards.encoder.probe_duration` (unchanged), `video_agent.music.mix_music_under_voice(voice_path, output_path, region, ...) -> Path` (unchanged).
- Produces: `workspace/assembly_brief.json` with shape
  `{shots: [{id, video_path, start_s, duration_s, beat}], word_timings, audio_path, logo_path, voice_total_s, target_duration_s}`
  — Task 4's `author_assembly_composition` tool and Task 3's fallback generator both consume this
  exact shape. `stage_assemble_finalize` expects the composition's render to land at
  `workspace/video_short.mp4` and writes `workspace/assemble_report.json`.

- [ ] **Step 1: Write the failing tests**

Add to `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py` (same file the visuals-prepare/
finalize tests already live in — follow those tests' exact fixture-building style):

```python
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

    # Not checkpointed yet — still "visuals" (mirrors visuals-prepare's own contract).
    manifest_after = json.loads(manifest_path.read_text())
    assert manifest_after["last_ok_status"] == "visuals"


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
```

- [ ] **Step 2: Run to verify all four fail**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -k assemble -v`
Expected: FAIL — `assemble-prepare`/`assemble-finalize` are not yet registered subcommands
(`argparse` will error with an unrecognized command).

- [ ] **Step 3: Implement `cmd_assemble_prepare`/`cmd_assemble_finalize`**

Add near the top of `_shorts_engine_impl/shorts_engine/stage_cli.py`, alongside the existing
`from shorts_engine.stages.visuals import content_pixels, resolve_shot, sample_frame` line:

```python
from shorts_engine.stages.assemble import reflow
```

Add these two functions after `cmd_visuals_finalize` (before `def main`):

```python
def cmd_assemble_prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    order_error = _stage_order_error(manifest.last_ok_status, "assembled", "assemble-prepare")
    if order_error is not None:
        print(json.dumps({"status": "error", "message": order_error}), file=sys.stderr)
        return 1

    try:
        from shorts_engine.cards import encoder
        from video_agent.music import mix_music_under_voice

        shots = json.loads((workspace / "shotlist.json").read_text(encoding="utf-8"))["shots"]
        beats_audio = json.loads((workspace / "beats_audio.json").read_text(encoding="utf-8"))
        voice = workspace / "voiceover.mp3"
        voice_total = encoder.probe_duration(voice)
        final_shots = reflow(shots, beats_audio, voice_total)

        shots_dir = workspace / "shots"
        shots_brief = []
        cum = 0.0
        for shot in final_shots:
            video_path = shots_dir / f"shot_{shot['id']}.mp4"
            if not video_path.exists():
                raise EngineError(
                    f"assemble-prepare: shot {shot['id']} has no rendered mp4 at "
                    f"{video_path} — did stage_visuals_finalize run first?")
            shots_brief.append({
                "id": shot["id"], "video_path": str(video_path),
                "start_s": round(cum, 3), "duration_s": round(shot["duration_s"], 3),
                "beat": shot["beat"],
            })
            cum += shot["duration_s"]

        words = json.loads((workspace / "word_timings.json").read_text(encoding="utf-8"))
        region = json.loads((workspace / "post.json").read_text(encoding="utf-8")).get(
            "region") or "default"
        mixed = mix_music_under_voice(voice, workspace / "music_mix.mp3", region)

        brief = {
            "shots": shots_brief, "word_timings": words, "audio_path": str(Path(mixed)),
            "logo_path": str(config.BRAND_LOGO_FILE), "voice_total_s": round(voice_total, 3),
            "target_duration_s": round(voice_total + config.END_CARD_HOLD_S, 3),
        }
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"assemble-prepare: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    (workspace / "assembly_brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "assembly_brief": "assembly_brief.json"}))
    return 0


def cmd_assemble_finalize(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    order_error = _stage_order_error(manifest.last_ok_status, "assembled", "assemble-finalize")
    if order_error is not None:
        print(json.dumps({"status": "error", "message": order_error}), file=sys.stderr)
        return 1

    try:
        from shorts_engine.cards import encoder

        brief = json.loads((workspace / "assembly_brief.json").read_text(encoding="utf-8"))
        video = workspace / "video_short.mp4"
        if not video.exists():
            raise EngineError(f"assemble-finalize: no rendered video at {video}")
        vd = encoder.probe_duration(video)
        voice_total = brief["voice_total_s"]
        if vd < voice_total + config.AUDIO_COMPLETENESS_MARGIN_S:
            raise EngineError(
                f"ASSEMBLE: video {vd:.2f}s < voice {voice_total:.2f}s + "
                f"{config.AUDIO_COMPLETENESS_MARGIN_S}s — CTA would clip")
        if abs(vd - (voice_total + config.END_CARD_HOLD_S)) > 0.35:
            raise EngineError(
                f"ASSEMBLE: video {vd:.2f}s violates duration law (voice {voice_total:.2f}s "
                f"+ hold {config.END_CARD_HOLD_S}s)")

        report_shots = []
        for shot in brief["shots"]:
            png = workspace / "shots" / f"assembled_check_{shot['id']}.png"
            sample_frame(video, shot["start_s"] + shot["duration_s"] / 2, png)
            pixels = content_pixels(png)
            if pixels < config.MIN_CONTENT_PIXELS:
                raise EngineError(
                    f"assemble-finalize: shot {shot['id']} not visibly present in the "
                    f"assembled video ({pixels} bright px < {config.MIN_CONTENT_PIXELS}) — "
                    f"never-blank violated")
            report_shots.append({"id": shot["id"], "content_pixels": pixels})

        report = {"voice_total_s": voice_total, "video_duration_s": round(vd, 3),
                  "shots": report_shots}
        (workspace / "assemble_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"assemble-finalize: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    artifacts = {"video": "video_short.mp4", "assemble_report": "assemble_report.json"}
    manifest.checkpoint(status="assembled", **artifacts)
    print(json.dumps({"status": "ok", "status_after": "assembled", "artifacts": artifacts}))
    return 0
```

Register the two subcommands in `main()`, alongside `visuals_finalize_parser`:

```python
    assemble_prepare_parser = subparsers.add_parser("assemble-prepare")
    assemble_prepare_parser.add_argument("--workspace", required=True)
    assemble_prepare_parser.set_defaults(func=cmd_assemble_prepare)

    assemble_finalize_parser = subparsers.add_parser("assemble-finalize")
    assemble_finalize_parser.add_argument("--workspace", required=True)
    assemble_finalize_parser.set_defaults(func=cmd_assemble_finalize)
```

- [ ] **Step 4: Run to verify all four pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -k assemble -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine -q`
Expected: all previously-passing tests still pass (522 before this task).

- [ ] **Step 6: Commit**

```bash
cd "_shorts_engine_impl"
git add shorts_engine/stage_cli.py tests/shorts_engine/test_stage_cli.py
git commit -m "Add assemble-prepare/assemble-finalize stage_cli subcommands"
```

---

### Task 3: TS composition primitives — `write_composition_file`/`render_composition` + deterministic fallback generator

**Files:**
- Create: `harness/packages/tool-assembly/package.json`
- Create: `harness/packages/tool-assembly/src/composition-tools.ts`
- Create: `harness/packages/tool-assembly/tests/composition-tools.spec.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure TS module, only `node:child_process`/`node:fs`/`node:path`).
- Produces: `writeCompositionFile(workspaceId, html, projectRoot) -> string`,
  `renderComposition(workspaceId, projectRoot, outputPath, options?) -> Promise<string>`,
  `buildDeterministicAssemblyHtml(brief: AssemblyBrief) -> string`,
  `renderFallbackComposition(workspaceId, projectRoot, outputPath, brief, options?) -> Promise<string>`,
  and the `AssemblyBrief`/`AssemblyShot`/`WordTiming` types — Task 4's `index.ts` imports all of
  these directly.

- [ ] **Step 1: Scaffold the package**

Create `harness/packages/tool-assembly/package.json`:

```json
{
  "name": "@hrsu/dsh-tool-assembly",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "main": "src/index.ts",
  "scripts": {
    "test": "vitest run"
  },
  "dependencies": {
    "@deepseek-ai/dsh-tools": "0.1.1-rc.2",
    "@deepseek-ai/dsh-subagent": "0.1.1-rc.2"
  },
  "devDependencies": {
    "@deepseek-ai/cordis": "*",
    "@deepseek-ai/dsh-agent": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm": "0.1.1-rc.2",
    "@deepseek-ai/dsh-tool-subagent": "0.1.1-rc.2",
    "vitest": "*"
  }
}
```

(`pnpm-workspace.yaml`'s `packages: [packages/*]` glob picks this up automatically — no change
needed there.)

- [ ] **Step 2: Write the failing tests**

Create `harness/packages/tool-assembly/tests/composition-tools.spec.ts`:

```typescript
import { describe, expect, test, vi } from 'vitest'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  writeCompositionFile, renderComposition, buildDeterministicAssemblyHtml,
  groupWordsIntoCues, renderFallbackComposition, type AssemblyBrief,
} from '../src/composition-tools.ts'

function tempProject(): string {
  const root = mkdtempSync(join(tmpdir(), 'tool-assembly-test-'))
  return root
}

describe('writeCompositionFile', () => {
  test('writes to compositions/<workspaceId>/assembly.html', () => {
    const projectRoot = tempProject()
    const target = writeCompositionFile('run-123', '<html></html>', projectRoot)
    expect(target).toBe(join(projectRoot, 'compositions', 'run-123', 'assembly.html'))
    expect(readFileSync(target, 'utf8')).toBe('<html></html>')
  })

  test('rejects a workspaceId that is an absolute path', () => {
    const projectRoot = tempProject()
    expect(() => writeCompositionFile('C:\\evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
  })

  test('rejects a workspaceId containing a path separator or colon', () => {
    const projectRoot = tempProject()
    expect(() => writeCompositionFile('../evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
    expect(() => writeCompositionFile('C:evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
    expect(() => writeCompositionFile('a/b', '<html></html>', projectRoot)).toThrow(/outside compositions/)
  })
})

describe('renderComposition', () => {
  test('resolves the scoped composition path and shells hyperframes render via cmd.exe on win32', async () => {
    const projectRoot = tempProject()
    writeCompositionFile('run-123', '<html></html>', projectRoot)
    const calls: unknown[] = []
    const fakeSpawn = vi.fn((command: string, args: string[], _opts: unknown) => {
      calls.push([command, args])
      const listeners: Record<string, (...a: unknown[]) => void> = {}
      return {
        stderr: { on: () => {} },
        on: (event: string, cb: (...a: unknown[]) => void) => {
          listeners[event] = cb
          if (event === 'close') setTimeout(() => cb(0), 0)
        },
      } as unknown as ReturnType<typeof import('node:child_process').spawn>
    })
    const outputPath = join(projectRoot, 'out.mp4')
    const result = await renderComposition('run-123', projectRoot, outputPath, { spawn: fakeSpawn as never })
    expect(result).toBe(outputPath)
    expect(calls.length).toBe(1)
    const [command, args] = calls[0] as [string, string[]]
    if (process.platform === 'win32') {
      expect(command).toBe('cmd.exe')
      expect(args).toContain('run-123'.length > 0 ? join('compositions', 'run-123', 'assembly.html') : '')
    }
  })
})

describe('groupWordsIntoCues', () => {
  test('groups up to 3 words or 1.5s per cue, matching the retired Python group_words_into_cues policy', () => {
    const words = [
      { word: 'Cold', start: 0.0, end: 0.3 },
      { word: 'weather', start: 0.3, end: 0.7 },
      { word: 'pours', start: 0.7, end: 1.0 },
      { word: 'wait', start: 1.1, end: 1.4 },
    ]
    const cues = groupWordsIntoCues(words)
    expect(cues).toEqual([
      { start: 0.0, end: 1.0, text: 'COLD WEATHER POURS' },
      { start: 1.1, end: 1.4, text: 'WAIT' },
    ])
  })
})

describe('buildDeterministicAssemblyHtml', () => {
  const brief: AssemblyBrief = {
    shots: [
      { id: '1', video_path: 'E:\\ws\\shots\\shot_1.mp4', start_s: 0, duration_s: 2.5, beat: 'hook' },
      { id: '2', video_path: 'E:\\ws\\shots\\shot_2.mp4', start_s: 2.5, duration_s: 3.0, beat: 'cta' },
    ],
    word_timings: [{ word: 'Test', start: 0.1, end: 0.4 }],
    audio_path: 'E:\\ws\\music_mix.mp3', logo_path: 'E:\\assets\\Logo.png',
    voice_total_s: 4.0, target_duration_s: 5.5,
  }

  test('embeds one <video> clip per shot at its start_s/duration_s', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('src="E:\\ws\\shots\\shot_1.mp4"')
    expect(html).toContain('data-start="0"')
    expect(html).toContain('data-duration="2.5"')
    expect(html).toContain('src="E:\\ws\\shots\\shot_2.mp4"')
    expect(html).toContain('data-start="2.5"')
  })

  test('embeds an <audio> clip for the mixed track', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('<audio')
    expect(html).toContain('src="E:\\ws\\music_mix.mp3"')
  })

  test('root data-duration matches target_duration_s', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('data-duration="5.5"')
  })

  test('escapes caption text and does not reference logo_path as an image asset', () => {
    const withUnsafeCaption: AssemblyBrief = {
      ...brief,
      word_timings: [{ word: '<script>evil()</script>', start: 0, end: 1 }],
    }
    const html = buildDeterministicAssemblyHtml(withUnsafeCaption)
    expect(html).not.toContain('<script>evil()</script>')
    // The fallback deliberately uses the proven text-based brand mark, not an unverified
    // <img>/background-image asset load (see Global Constraints item 6).
    expect(html).not.toContain(brief.logo_path)
    expect(html).toContain('HRSU INDORE')
  })

  test('registers a paused GSAP timeline keyed to the composition id', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain("gsap.timeline({ paused: true })")
    expect(html).toContain("window.__timelines['assembly-fallback']")
  })
})

describe('renderFallbackComposition', () => {
  test('writes the generated HTML then renders it', async () => {
    const projectRoot = tempProject()
    const fakeSpawn = vi.fn((_command: string, _args: string[], _opts: unknown) => ({
      stderr: { on: () => {} },
      on: (event: string, cb: (...a: unknown[]) => void) => { if (event === 'close') setTimeout(() => cb(0), 0) },
    } as unknown as ReturnType<typeof import('node:child_process').spawn>))
    const brief: AssemblyBrief = {
      shots: [{ id: '1', video_path: 'E:\\ws\\shots\\shot_1.mp4', start_s: 0, duration_s: 2, beat: 'hook' }],
      word_timings: [], audio_path: 'E:\\ws\\voice.mp3', logo_path: 'E:\\Logo.png',
      voice_total_s: 2, target_duration_s: 3.5,
    }
    const outputPath = join(projectRoot, 'video_short.mp4')
    const result = await renderFallbackComposition('run-1', projectRoot, outputPath, brief, { spawn: fakeSpawn as never })
    expect(result).toBe(outputPath)
    const written = readFileSync(join(projectRoot, 'compositions', 'run-1', 'assembly.html'), 'utf8')
    expect(written).toContain('shot_1.mp4')
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd harness/packages/tool-assembly && pnpm install && pnpm exec vitest run`
Expected: FAIL — `src/composition-tools.ts` does not exist yet.

- [ ] **Step 4: Implement `composition-tools.ts`**

Create `harness/packages/tool-assembly/src/composition-tools.ts`:

```typescript
import { spawn as nodeSpawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

function compositionsRoot(projectRoot: string): string {
  return join(projectRoot, 'compositions')
}

/** Same discipline as tool-visual-scene/src/scene-tools.ts's resolveScopedPath (including the
 * cross-drive/UNC/drive-relative hardening from that package's final review) — identifiers that
 * reach this function are never themselves paths. Simplified for one composition per workspace
 * (`assembly.html`, not `<shotId>.html`) since assembly has no shot-level fan-out. */
function containsPathSeparatorOrColon(value: string): boolean {
  return value.includes('/') || value.includes('\\') || value.includes(':')
}

function resolveScopedAssemblyPath(workspaceId: string, projectRoot: string): string {
  const root = compositionsRoot(projectRoot)
  const outsideError = () => new Error(
    `resolveScopedAssemblyPath: resolved path is outside compositions/: ${join(root, workspaceId, 'assembly.html')}`,
  )
  if (isAbsolute(workspaceId)) throw outsideError()
  if (containsPathSeparatorOrColon(workspaceId)) throw outsideError()
  const candidate = resolve(root, workspaceId, 'assembly.html')
  const rel = relative(root, candidate)
  if (rel.startsWith('..') || resolve(root, rel) !== candidate) throw outsideError()
  return candidate
}

export function writeCompositionFile(workspaceId: string, html: string, projectRoot: string): string {
  const target = resolveScopedAssemblyPath(workspaceId, projectRoot)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, html, 'utf8')
  return target
}

export interface RenderOptions {
  spawn?: typeof nodeSpawn
}

function runHyperframesRender(
  compositionRelPath: string, projectRoot: string, outputPath: string, options: RenderOptions = {},
): Promise<string> {
  // Identical spawn strategy to tool-visual-scene's scene-tools.ts — see that file's comment for
  // the full investigation of why cmd.exe /c (not bare npx, not shell:true) is required on
  // Windows when the harness's own path contains a space ("HRSU Shorts").
  const spawnFn = options.spawn ?? nodeSpawn
  const [command, args] = process.platform === 'win32'
    ? ['cmd.exe', ['/c', 'npx', 'hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
    : ['npx', ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
  return new Promise((resolvePromise, reject) => {
    const child = spawnFn(command, args, { cwd: projectRoot })
    let stderr = ''
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) resolvePromise(outputPath)
      else reject(new Error(stderr.trim() || `hyperframes render exited ${code}`))
    })
  })
}

export async function renderComposition(
  workspaceId: string, projectRoot: string, outputPath: string, options: RenderOptions = {},
): Promise<string> {
  const compositionAbsPath = resolveScopedAssemblyPath(workspaceId, projectRoot)
  const compositionRelPath = relative(projectRoot, compositionAbsPath)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}

// ---- Deterministic fallback composition (the never-blank safety net) ----

export interface AssemblyShot {
  id: string
  video_path: string
  start_s: number
  duration_s: number
  beat: string
}

export interface WordTiming {
  word: string
  start: number
  end: number
}

export interface AssemblyBrief {
  shots: AssemblyShot[]
  word_timings: WordTiming[]
  audio_path: string
  logo_path: string
  voice_total_s: number
  target_duration_s: number
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** Groups word-level timings into short caption cues — same policy (max 3 words, max 1.5s per
 * cue) as the retired shorts_engine/stages/assemble.py's group_words_into_cues, ported to TS
 * since this generator has no Python in its call path. Used only by the deterministic fallback —
 * the creative subagent (Task 4) is free to caption however it wants. */
export function groupWordsIntoCues(
  words: WordTiming[], maxWords = 3, maxDurS = 1.5,
): { start: number; end: number; text: string }[] {
  const cues: { start: number; end: number; text: string }[] = []
  let buf: WordTiming[] = []
  const flush = () => {
    if (buf.length > 0) {
      cues.push({
        start: buf[0].start, end: buf[buf.length - 1].end,
        text: buf.map((w) => w.word.trim().toUpperCase()).join(' '),
      })
      buf = []
    }
  }
  for (const w of words) {
    if (buf.length > 0 && (buf.length >= maxWords || w.end - buf[0].start > maxDurS)) flush()
    buf.push(w)
  }
  flush()
  return cues
}

/** Builds a complete, self-contained HyperFrames composition HTML string from an AssemblyBrief:
 * one <video> clip per shot (plain absolute path — confirmed to work directly, without copying
 * into the project tree, per Global Constraints item 3), a caption track built from
 * word_timings via groupWordsIntoCues, a text-based brand mark (the same proven technique
 * generic_fallback.html uses — NOT an <img>/background-image asset load, which is unverified,
 * see Global Constraints item 6), and a simple GSAP-animated progress bar. This is the
 * never-blank safety net for the `assembled` stage — deliberately plain, since it only ships
 * after the creative subagent has failed twice. */
export function buildDeterministicAssemblyHtml(brief: AssemblyBrief): string {
  const videoClips = brief.shots.map((shot) => `
      <video id="shot-${escapeHtml(shot.id)}" class="clip" src="${escapeHtml(shot.video_path)}" muted playsinline
             data-start="${shot.start_s}" data-duration="${shot.duration_s}" data-track-index="0"></video>`).join('')

  const cues = groupWordsIntoCues(brief.word_timings)
  const captionTimelineCalls = cues.map((cue) => `
        tl.set(box, { visibility: 'visible' }, ${cue.start});
        tl.to(box, { opacity: 1, duration: 0.1, onStart: () => { textEl.textContent = ${JSON.stringify(cue.text)}; } }, ${cue.start});
        tl.to(box, { opacity: 0, duration: 0.1 }, ${cue.end});
        tl.set(box, { opacity: 0, visibility: 'hidden' }, ${cue.end + 0.1});`).join('')

  const totalDuration = brief.target_duration_s

  return `<!doctype html>
<html><head>
  <meta charset="UTF-8" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    body, html { margin: 0; padding: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0a192f; }
    .brand-mark { position: absolute; top: 60px; left: 0; width: 1080px; text-align: center;
                  font-family: Georgia, "Times New Roman", serif; font-weight: 700; font-size: 44px;
                  letter-spacing: 2px; color: #d4af37; }
    .caption-box { position: absolute; left: 90px; width: 900px; bottom: 260px; display: flex; justify-content: center;
                   background: rgba(10,25,47,0.85); border-radius: 24px; padding: 16px 28px; opacity: 0; visibility: hidden; }
    .caption-text { color: #ccd6f6; font-family: Arial, "Segoe UI", sans-serif; font-weight: 700; font-size: 44px; text-align: center; }
    .progress-track { position: absolute; left: 0; bottom: 6px; width: 1080px; height: 6px; background: rgba(255,255,255,0.15); }
    .progress-bar { position: absolute; left: 0; bottom: 6px; width: 0px; height: 6px; background: #d4af37; }
  </style>
</head><body>
  <div id="root" data-composition-id="assembly-fallback" data-width="1080" data-height="1920" data-duration="${totalDuration}">
    ${videoClips}
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="1">
      <div class="brand-mark">HRSU INDORE</div>
    </div>
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="2">
      <div class="progress-track"></div>
      <div id="progress-bar" class="progress-bar"></div>
    </div>
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="3">
      <div id="caption-box" class="caption-box"><span id="caption-text" class="caption-text"></span></div>
    </div>
    <audio id="mix" src="${escapeHtml(brief.audio_path)}" data-start="0" data-duration="${totalDuration}" data-track-index="4" data-volume="1"></audio>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    const box = document.getElementById('caption-box');
    const textEl = document.getElementById('caption-text');
    tl.to('#progress-bar', { width: 1080, duration: ${totalDuration}, ease: 'none' }, 0);
    ${captionTimelineCalls}
    window.__timelines['assembly-fallback'] = tl;
  </script>
</body></html>`
}

export async function renderFallbackComposition(
  workspaceId: string, projectRoot: string, outputPath: string, brief: AssemblyBrief,
  options: RenderOptions = {},
): Promise<string> {
  const html = buildDeterministicAssemblyHtml(brief)
  writeCompositionFile(workspaceId, html, projectRoot)
  return renderComposition(workspaceId, projectRoot, outputPath, options)
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd harness/packages/tool-assembly && pnpm exec vitest run`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd "harness"
git add packages/tool-assembly/package.json packages/tool-assembly/src/composition-tools.ts packages/tool-assembly/tests/composition-tools.spec.ts pnpm-lock.yaml
git commit -m "Add tool-assembly package: scoped composition write/render + deterministic fallback"
```

---

### Task 4: `author_assembly_composition` tool + creative-authoring persona

**Files:**
- Create: `harness/packages/tool-assembly/src/assembly-author-persona.md`
- Create: `harness/packages/tool-assembly/src/index.ts`
- Create: `harness/packages/tool-assembly/tests/index.spec.ts`

**Interfaces:**
- Consumes: `writeCompositionFile`, `renderComposition`, `renderFallbackComposition`,
  `AssemblyBrief` (Task 3). `ctx.subagents` (`SubagentRuntime` from `@deepseek-ai/dsh-subagent`,
  already registered in `cordis.yml` by the visuals-stage work).
- Produces: the `author_assembly_composition` tool (registered name), plus
  `write_composition_file`/`render_composition` as ordinary global tools the subagent is scoped
  to via `toolFilter.allow` — same registration pattern as `tool-visual-scene`. Task 5's
  `cordis.yml` wires this package's `apply()` in.

- [ ] **Step 1: Write the persona document**

Create `harness/packages/tool-assembly/src/assembly-author-persona.md`:

```markdown
# HyperFrames Assembly Composition Author

You are a focused visual-composition specialist. You receive one `assembly_brief` for an entire
short-form video and your job is to compose the ONE top-level HyperFrames HTML/CSS/GSAP
composition that assembles it: every shot placed at its correct time, word-level captions styled
and animated with real creative intent (not a static fade box), and — if you want — a progress
indicator and any other polish that serves the video. You have exactly two tools:
`write_composition_file` and `render_composition`. You have no filesystem read access, no shell
access, and no other tools.

## What is locked (never change these)

- **Shot placement.** Every entry in `assembly_brief.shots` MUST appear as a `<video>` clip with
  `data-start` exactly equal to that shot's `start_s` and `data-duration` exactly equal to that
  shot's `duration_s`. Use the shot's `video_path` verbatim as the `<video src>` — it is a plain
  absolute filesystem path (confirmed to work directly, do not rewrite it, do not prefix it with
  `file://`). Do not add, remove, reorder, or resize shots.
- **Caption content and timing.** `assembly_brief.word_timings` is an array of
  `{word, start, end}` — these are real transcript timestamps. You must render every word
  somewhere on screen at a time overlapping its `[start, end]` window (grouping words into short
  phrases is fine and encouraged — see Creative surface below). Do not invent words, drop words,
  or shift timing.
- **Total duration.** The root composition's `data-duration` MUST equal
  `assembly_brief.target_duration_s` exactly.
- **Audio.** Reference `assembly_brief.audio_path` as one `<audio>` element,
  `data-start="0"`, `data-duration` equal to `target_duration_s`, `data-volume="1"`. This file is
  already the final mixed voice+music track — do not attempt any additional mixing or add a
  second audio source.
- **Brand facts (HRSU Indore Pvt. Ltd.)** — use verbatim, never paraphrase, never invent claims:
  - Company: "HRSU Indore Pvt. Ltd." (short form "HRSU INDORE" is fine for on-screen marks)
  - Domain/footer CTA: `hrsuindore.com`
  - Tagline: "Beyond Granules. The Purity of Powder."
  - Brand gold `#d4af37`, brand navy `#0a192f`/`#0a1428`, text `#ccd6f6`/`#8892b0`
  - **Never render or imply**, even if the brief's text suggests them: "REACH registered",
    "certified", "ISO 9001", "FDA approved".

## What is creative (your actual job)

- **Caption styling and motion.** Kinetic typography, word-by-word reveal, scale/position
  animation on entry — anything GSAP can do to a text element. A static centered box that just
  fades is the least interesting option available to you, not the safe default.
- **Transitions between shots.** GSAP can animate the `<video>` clip elements directly (opacity,
  scale, position) — you can crossfade, wipe, or punch-scale between consecutive shots instead of
  a hard cut. This was not possible in the old ffmpeg-based pipeline; use it.
- **Progress indicator.** Optional. If you include one, it should still communicate "how far into
  the video am I" — but the exact visual treatment (a bar, a set of dots, a subtle edge glow) is
  yours to choose.
- **Overall pacing and visual rhythm.** You are composing the connective tissue between every
  shot a viewer will actually watch — make deliberate choices, don't default to the most
  predictable option for every element.

## The HyperFrames timing-attribute contract

Same contract as shot authoring: root element needs `id`, `data-composition-id`, `data-width="1080"`,
`data-height="1920"`, `data-duration`. Every visible/audible element needs `class="clip"` (video/
audio elements included), `data-start`, `data-duration`, `data-track-index` (use distinct indices
for layers that must not be treated as the same visual track — e.g. `0` for shot clips, higher
values for captions/brand marks/progress bars layered on top).

## The GSAP animation contract

Build **one paused GSAP timeline**: `gsap.timeline({ paused: true })`. Never call `.play()`.
Register it on `window.__timelines`, keyed by your `data-composition-id`. No wall-clock state
(`Date.now()`, `performance.now()`, `setTimeout`, `requestAnimationFrame`). No unseeded randomness
(`Math.random()`) — derive any per-run variation deterministically from the brief's own fields
(e.g. hash a shot id) if you want variation, never from an RNG. GSAP is already loaded via a
`<script src="https://cdn.jsdelivr.net/npm/gsap@.../gsap.min.js">` tag — do not add your own.

## A note on the brand mark / logo

`assembly_brief.logo_path` is available if you want to try an `<img src="...">` or a CSS
`background-image: url(...)` referencing it — but neither has been verified to resolve a plain
absolute Windows path the way `<video src>`/`<audio src>` do (those two ARE confirmed to work
directly). If you're not confident it will render, use a text-based brand mark instead — "HRSU
INDORE" in brand gold, styled however you like — which is guaranteed to work and is exactly what
the safety-net fallback composition does.

## The assembly brief you will receive

```json
{
  "shots": [
    {"id": "1", "video_path": "E:\\...\\shots\\shot_1.mp4", "start_s": 0.0, "duration_s": 2.5, "beat": "hook"},
    {"id": "2", "video_path": "E:\\...\\shots\\shot_2.mp4", "start_s": 2.5, "duration_s": 3.0, "beat": "cta"}
  ],
  "word_timings": [{"word": "Cold", "start": 0.1, "end": 0.4}, {"word": "pours", "start": 0.5, "end": 0.9}],
  "audio_path": "E:\\...\\music_mix.mp3",
  "logo_path": "E:\\...\\asset_library\\brand\\Logo.png",
  "voice_total_s": 24.3,
  "target_duration_s": 25.8
}
```

## What to do, in order

1. Compose the one top-level HTML/CSS/GSAP composition following every "locked" constraint above,
   with real creative effort on every "creative" surface.
2. Call `write_composition_file` with the complete HTML and the exact `workspace_id` you were
   given.
3. Call `render_composition` with that same `workspace_id` (no output path — the pipeline decides
   where the rendered mp4 lands).
4. If `render_composition` throws or returns an error, read the error carefully — it usually
   names a missing/invalid `data-*` attribute or an HTML/CSS problem — fix the specific issue,
   call `write_composition_file` again with the corrected HTML, and call `render_composition`
   once more.
5. You get **two total attempts**. If the second `render_composition` call also fails, stop and
   report the failure plainly — the pipeline falls back to a safety-net composition. Do not try a
   third time.
```

- [ ] **Step 2: Write the failing tests**

Create `harness/packages/tool-assembly/tests/index.spec.ts`:

```typescript
import { describe, expect, test, vi } from 'vitest'
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { authorAssemblyComposition, type AuthorAssemblyCompositionArgs, type AuthorAssemblyCompositionDeps } from '../src/index.ts'
import type { SubagentResult } from '@deepseek-ai/dsh-subagent'

function tempProject(): string {
  return mkdtempSync(join(tmpdir(), 'tool-assembly-index-test-'))
}

function baseArgs(workspace: string): AuthorAssemblyCompositionArgs {
  return {
    assembly_brief: {
      shots: [{ id: '1', video_path: join(workspace, 'shots', 'shot_1.mp4'), start_s: 0, duration_s: 2, beat: 'hook' }],
      word_timings: [], audio_path: join(workspace, 'music_mix.mp3'),
      logo_path: 'E:\\Logo.png', voice_total_s: 2, target_duration_s: 3.5,
    },
    workspace_id: 'run-1',
    workspace,
  }
}

function makeDeps(overrides: Partial<AuthorAssemblyCompositionDeps> = {}): AuthorAssemblyCompositionDeps {
  return {
    subagents: { start: vi.fn() },
    subagentProviderName: 'spawn',
    agentOptions: { provider: 'ollama-local', model: 'gemma4:31b-cloud' },
    agent: {} as never,
    signal: new AbortController().signal,
    projectRoot: tempProject(),
    existsSync,
    renderFallbackComposition: vi.fn(async () => 'fallback-called'),
    ...overrides,
  }
}

describe('authorAssemblyComposition', () => {
  test('returns the rendered path on first-attempt success', async () => {
    const workspace = mkdtempSync(join(tmpdir(), 'assemble-ws-'))
    const outputPath = join(workspace, 'video_short.mp4')
    writeFileSync(outputPath, 'fake mp4 bytes')
    const deps = makeDeps({
      subagents: {
        start: vi.fn(async () => ({
          result: Promise.resolve({ output: [], stopReason: 'completed' } as SubagentResult),
          dispose: vi.fn(async () => {}),
        })),
      },
    })
    const result = await authorAssemblyComposition(baseArgs(workspace), deps)
    expect(result.used_fallback).toBe(false)
    expect(result.attempts).toBe(1)
    expect(result.mp4_path).toBe(outputPath)
  })

  test('falls back after two failed attempts and calls renderFallbackComposition', async () => {
    const workspace = mkdtempSync(join(tmpdir(), 'assemble-ws-'))
    const deps = makeDeps({
      subagents: {
        start: vi.fn(async () => ({
          result: Promise.resolve({ output: [], stopReason: 'error', diagnostic: 'render failed: bad html' } as SubagentResult),
          dispose: vi.fn(async () => {}),
        })),
      },
    })
    const result = await authorAssemblyComposition(baseArgs(workspace), deps)
    expect(result.used_fallback).toBe(true)
    expect(result.attempts).toBe(2)
    expect(deps.renderFallbackComposition).toHaveBeenCalledTimes(1)
    expect(deps.subagents.start).toHaveBeenCalledTimes(2)
  })

  test('output path is fixed at <workspace>/video_short.mp4 regardless of subagent input', async () => {
    const workspace = mkdtempSync(join(tmpdir(), 'assemble-ws-'))
    const outputPath = join(workspace, 'video_short.mp4')
    writeFileSync(outputPath, 'fake mp4 bytes')
    let registeredPath: string | undefined
    const deps = makeDeps({
      subagents: {
        start: vi.fn(async () => ({
          result: Promise.resolve({ output: [], stopReason: 'completed' } as SubagentResult),
          dispose: vi.fn(async () => {}),
        })),
      },
      registerOutputPath: (workspaceId: string, outPath: string) => { registeredPath = outPath },
    } as never)
    await authorAssemblyComposition(baseArgs(workspace), deps)
    expect(registeredPath).toBe(outputPath)
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd harness/packages/tool-assembly && pnpm exec vitest run tests/index.spec.ts`
Expected: FAIL — `src/index.ts` does not exist yet.

- [ ] **Step 4: Implement `index.ts`**

Create `harness/packages/tool-assembly/src/index.ts`:

```typescript
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { Agent, AgentOptions } from '@deepseek-ai/dsh-agent'
import type { SubagentResult, SubagentRuntime } from '@deepseek-ai/dsh-subagent'
import {
  writeCompositionFile, renderComposition, renderFallbackComposition, type AssemblyBrief,
} from './composition-tools.ts'

export {
  writeCompositionFile, renderComposition, renderFallbackComposition,
} from './composition-tools.ts'

export const name = 'tool-assembly'
export const inject = ['tools', 'subagents']

export interface Config {
  /** Absolute path to hyperframes_scenes_project/ — same project root tool-visual-scene uses. */
  projectRoot: string
  /** The assembly-authoring child's model route — see ASSEMBLY_AUTHOR_MODEL in cordis.yml. */
  agentOptions: AgentOptions
  subagentProviderName?: string
}

export interface AuthorAssemblyCompositionArgs {
  assembly_brief: AssemblyBrief
  workspace_id: string
  /** Absolute path to the Python run's workspace directory (stage_init's `workspace` output).
   * The rendered mp4 lands at `<workspace>/video_short.mp4`, matching what
   * cmd_assemble_finalize expects. */
  workspace: string
}

export interface AuthorAssemblyCompositionOutput {
  mp4_path: string
  attempts: number
  used_fallback: boolean
}

const PERSONA_PATH = fileURLToPath(new URL('./assembly-author-persona.md', import.meta.url))
const PERSONA_TEXT = readFileSync(PERSONA_PATH, 'utf8')

const MAX_ATTEMPTS = 2
const CHILD_TOOL_NAMES = ['write_composition_file', 'render_composition'] as const

function resolveOutputPath(workspace: string): string {
  return join(workspace, 'video_short.mp4')
}

function buildPrompt(
  brief: AssemblyBrief, workspaceId: string, previousFailureReason: string | undefined,
): string {
  const briefJson = JSON.stringify(brief, null, 2)
  const retrySection = previousFailureReason === undefined
    ? ''
    : `\n\n## Your previous attempt failed\n\nYour first attempt's \`render_composition\` call failed with this error:\n\n\`\`\`\n${previousFailureReason}\n\`\`\`\n\nThis is your final attempt (2 of ${MAX_ATTEMPTS}). Fix the specific problem named above and try once more.`
  return `${PERSONA_TEXT}

## This run's assembly brief

\`\`\`json
${briefJson}
\`\`\`

## Exact value to use in your tool calls

- \`workspace_id\`: \`${workspaceId}\`

Note: \`render_composition\` takes only \`workspace_id\` — it never takes an output path. Where the
rendered mp4 lands is decided by the pipeline, not by you.
${retrySection}`
}

function describeFailure(result: SubagentResult): string {
  if (result.diagnostic) return result.diagnostic
  const text = result.output
    .map((block) => (block.type === 'text' ? block.text : ''))
    .filter((chunk) => chunk.length > 0)
    .join('\n')
    .trim()
  if (text.length > 0) return text
  return `subagent stopped with reason "${result.stopReason}" and produced no output`
}

export interface AuthorAssemblyCompositionDeps {
  subagents: Pick<SubagentRuntime, 'start'>
  subagentProviderName: string
  agentOptions: AgentOptions
  agent: Agent | undefined
  signal: AbortSignal
  projectRoot: string
  existsSync: (path: string) => boolean
  renderFallbackComposition: typeof renderFallbackComposition
  /** Records the trusted output path, keyed by workspace_id, before spawning the child — same
   * anti-prompt-injection discipline as tool-visual-scene's registerOutputPath. */
  registerOutputPath: (workspaceId: string, outputPath: string) => void
}

export async function authorAssemblyComposition(
  args: AuthorAssemblyCompositionArgs, deps: AuthorAssemblyCompositionDeps,
): Promise<AuthorAssemblyCompositionOutput> {
  const { assembly_brief: brief, workspace_id: workspaceId, workspace } = args
  const outputPath = resolveOutputPath(workspace)
  deps.registerOutputPath(workspaceId, outputPath)

  let previousFailureReason: string | undefined
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (!deps.agent) {
      throw new Error(
        'author_assembly_composition: no parent agent available to spawn the assembly-authoring subagent from (exec.agent is undefined)',
      )
    }
    const run = await deps.subagents.start(deps.subagentProviderName, {
      label: `author_assembly_composition:attempt${attempt}`,
      prompt: [{ type: 'text', text: buildPrompt(brief, workspaceId, previousFailureReason) }],
      parent: deps.agent,
      signal: deps.signal,
      agentOptions: deps.agentOptions,
      toolFilter: { allow: [...CHILD_TOOL_NAMES] },
    })
    let result: SubagentResult
    try {
      result = await run.result
    } finally {
      await run.dispose()
    }

    if (result.stopReason === 'completed' && deps.existsSync(outputPath)) {
      return { mp4_path: outputPath, attempts: attempt, used_fallback: false }
    }
    previousFailureReason = describeFailure(result)
  }

  await deps.renderFallbackComposition(workspaceId, deps.projectRoot, outputPath, brief)
  return { mp4_path: outputPath, attempts: MAX_ATTEMPTS, used_fallback: true }
}

export function apply(ctx: Context, config: Config): void {
  const outputPathsByWorkspace = new Map<string, string>()

  ctx.tools.register(defineTool({
    name: 'write_composition_file',
    description: 'Write the one top-level HyperFrames assembly composition (HTML/CSS/GSAP) to disk for later rendering.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
      html: { type: 'string', required: true, description: 'Complete self-contained HTML document for the composition.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const path = writeCompositionFile(args.workspace_id, args.html, config.projectRoot)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'render_composition',
    description: 'Render the previously written assembly composition to an MP4.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const outputPath = outputPathsByWorkspace.get(args.workspace_id)
      if (outputPath === undefined) {
        throw new Error(
          `render_composition: no registered output path for workspace_id=${JSON.stringify(args.workspace_id)} ` +
          '— author_assembly_composition must be the one starting this run\'s subagent.',
        )
      }
      const path = await renderComposition(args.workspace_id, config.projectRoot, outputPath)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'author_assembly_composition',
    description:
      'Author, render, and (on repeated failure) fall back to a safety-net composition for the whole video\'s ' +
      'final assembly, by delegating composition to a creative assembly-authoring subagent.',
    parameters: {
      assembly_brief: { type: 'object', additionalProperties: true, required: true, description: 'The run\'s assembly_brief.json contents.' },
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (namespaces this run\'s HyperFrames composition file).' },
      workspace: {
        type: 'string', required: true,
        description: 'Absolute path to the run workspace, from stage_init\'s `workspace` output. The rendered mp4 lands at `<workspace>/video_short.mp4`, matching what stage_assemble_finalize expects.',
      },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          mp4_path: { type: 'string', required: true },
          attempts: { type: 'integer', required: true },
          used_fallback: { type: 'boolean', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return authorAssemblyComposition(args as unknown as AuthorAssemblyCompositionArgs, {
        subagents: ctx.subagents,
        subagentProviderName: config.subagentProviderName ?? 'spawn',
        agentOptions: config.agentOptions,
        agent: exec.agent,
        signal: exec.signal,
        projectRoot: config.projectRoot,
        existsSync,
        renderFallbackComposition,
        registerOutputPath: (workspaceId, outputPath) => {
          outputPathsByWorkspace.set(workspaceId, outputPath)
        },
      })
    },
  }))
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd harness/packages/tool-assembly && pnpm exec vitest run`
Expected: all tests PASS (Task 3's tests + Task 4's tests).

- [ ] **Step 6: Commit**

```bash
cd "harness"
git add packages/tool-assembly/src/assembly-author-persona.md packages/tool-assembly/src/index.ts packages/tool-assembly/tests/index.spec.ts
git commit -m "Add author_assembly_composition tool with creative-authoring persona"
```

---

### Task 5: Wire into `cordis.yml`/`system_prompt.md`, register in `stage-cli`-facing tools, end-to-end regression test

**Files:**
- Modify: `harness/package.json` (add `@hrsu/dsh-tool-assembly` dependency)
- Modify: `harness/cordis.yml`
- Modify: `harness/system_prompt.md`
- Modify: `harness/packages/tool-shorts-stage/src/index.ts` (register `stage_assemble_prepare`/`stage_assemble_finalize` bridge tools, same pattern as the existing `stage_visuals_prepare`/`stage_visuals_finalize` entries)
- Create: `harness/tests/assembly-stage-regression.e2e.ts`

**Interfaces:**
- Consumes: everything from Tasks 2-4.
- Produces: nothing further — this is the final integration task for this plan.

- [ ] **Step 1: Read the existing `stage_visuals_prepare`/`stage_visuals_finalize` registrations to mirror exactly**

Run: `Read harness/packages/tool-shorts-stage/src/index.ts` and locate the two `stage_visuals_*`
tool registrations. They call `python -m shorts_engine.stage_cli visuals-prepare --workspace <ws>`
/ `visuals-finalize --workspace <ws>` via the same subprocess-spawning helper every other
`stage_*` tool in that file uses, and return the subcommand's parsed JSON stdout. Add two new
entries, `stage_assemble_prepare` and `stage_assemble_finalize`, calling
`assemble-prepare --workspace <ws>` / `assemble-finalize --workspace <ws>` — copy the exact
subprocess-invocation helper and error-handling already used for the visuals pair; only the
subcommand name and description text change. (This step has no separate test — its correctness
is exercised by Step 5's e2e test, and the underlying `stage_cli.py` subcommands already have
their own unit tests from Task 2.)

- [ ] **Step 2: Add `@hrsu/dsh-tool-assembly` to `harness/package.json`**

In the `dependencies` block, alongside `"@hrsu/dsh-tool-visual-scene": "workspace:*"`:

```json
    "@hrsu/dsh-tool-assembly": "workspace:*",
```

Run: `cd harness && pnpm install`
Expected: `@hrsu/dsh-tool-assembly` resolves from the local workspace package (Task 3/4).

- [ ] **Step 3: Wire `cordis.yml`**

Add a new plugin entry after `tool-visual-scene`'s entry, before `agent-spine`:

```yaml
- id: tool-assembly
  name: '@hrsu/dsh-tool-assembly'
  config:
    projectRoot: !!js "process.cwd() + '/hyperframes_scenes_project'"
    agentOptions:
      provider: ollama-local
      # Swappable independently of both ORCHESTRATOR_MODEL and SCENE_AUTHOR_MODEL — same
      # process.env pattern. Defaults to gemma4:31b-cloud (kimi-k2.7-code:cloud is out of scope,
      # see SCENE_AUTHOR_MODEL's comment above).
      model: !!js "process.env.ASSEMBLY_AUTHOR_MODEL ?? 'gemma4:31b-cloud'"
      maxTokens: 8192
```

Update the `agent-spine` persona block (both here in `cordis.yml` AND in `system_prompt.md` — the
persona-sync test enforces these stay byte-identical) to document the new stage. In the "How to
use your tools" bullet list, after the `stage_visuals_prepare`/`stage_visuals_finalize` line, add:

```
  - Assembly is a three-step flow too, mirroring visuals. Call `stage_assemble_prepare` once — it
    reflows shot durations onto the real audio timing, mixes music under the voiceover, and writes
    `assembly_brief.json`, returning its path. Call `author_assembly_composition` once (not once
    per shot — this is the whole video's final assembly in one composition), passing the brief,
    `stage_init`'s `run_id` as `workspace_id`, and `stage_init`'s `workspace` as `workspace` — same
    two params, same meaning, as `author_visual_scene`. It authors, renders, and — on repeated
    failure — falls back to a safety-net composition, so it never returns without a usable video.
    Once it returns, call `stage_assemble_finalize` once to verify the duration law and every
    shot's presence, and advance the workspace to `assembled`.
```

Also update the `## Non-negotiable invariants` section's stage list (`init → ingested → ... →
visuals → assembled → verified → ...`) if it currently only names `stage_visuals_*` tools by name
anywhere — search both files for every place `stage_visuals_prepare`/`stage_visuals_finalize` are
named and add the assemble equivalents alongside, so the persona document stays accurate about
the full tool surface.

- [ ] **Step 4: Run `pnpm exec vitest run tests/persona-sync.spec.ts`**

Expected: PASS — confirms `cordis.yml`'s persona block and `system_prompt.md` are still
byte-identical after Step 3's edits to both files.

- [ ] **Step 5: Write and run the end-to-end regression test**

Create `harness/tests/assembly-stage-regression.e2e.ts`, mirroring
`tests/visuals-stage-regression.e2e.ts`'s pattern exactly: stub `ctx.subagents`'s `start` method
(the PROVIDER, not the tool) so the fake subagent calls the real `write_composition_file`/
`render_composition` tool implementations against fixture data, proving the real seam (brief →
composition file → rendered mp4 → duration-law check) without a live cloud-model call.

```typescript
import { afterAll, beforeAll, describe, expect, test } from 'vitest'
// Follow tests/visuals-stage-regression.e2e.ts's exact setup: build a Cordis context loading
// cordis.yml's real composition, run stage_init + a fixture-seeded manifest already at
// "visuals", then:
//
// 1. "reaches manifest status=\"assembled\" via a stubbed subagent that calls the real
//    write_composition_file/render_composition tools" — stub ctx.subagents.start so the
//    "subagent" it returns actually calls ctx.tools.execute('write_composition_file', ...) and
//    ctx.tools.execute('render_composition', ...) with a small deterministic composition
//    (e.g. reuse buildDeterministicAssemblyHtml directly against the test's assembly_brief.json),
//    then resolves with { output: [], stopReason: 'completed' }. Call stage_assemble_prepare,
//    then author_assembly_composition, then stage_assemble_finalize through the real registered
//    tool dispatch path (ctx.tools.execute), and assert the final manifest's last_ok_status ===
//    'assembled'.
//
// 2. "falls back to the safety-net composition when the subagent fails twice" — stub
//    ctx.subagents.start to always resolve stopReason: 'error', assert
//    author_assembly_composition's tool result has used_fallback: true and attempts: 2, and that
//    stage_assemble_finalize still succeeds against the fallback-rendered video (proving the
//    safety net alone satisfies the duration law and never-blank check).
```

Run: `cd harness && pnpm exec vitest run tests/assembly-stage-regression.e2e.ts`
Expected: both tests PASS.

- [ ] **Step 6: Run the full harness and Python suites to confirm no regression**

Run: `cd harness && pnpm exec vitest run`
Expected: all tests pass except the one pre-known stale test (`stage-regression.e2e.ts`'s "Python
bridge CLI parity" case, already tracked as a separate follow-up, unrelated to this plan).

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine -q`
Expected: all tests pass (522 + Task 2's 4 new tests = 526).

- [ ] **Step 7: Commit**

```bash
cd "harness"
git add package.json pnpm-lock.yaml cordis.yml system_prompt.md packages/tool-shorts-stage/src/index.ts tests/assembly-stage-regression.e2e.ts
git commit -m "Wire assembly stage into harness composition; add end-to-end regression test"
```

---

## Self-Review Notes

- **Spec coverage:** Decision 1 (creative primary path) → Task 4. Decision 2 (deterministic
  fallback) → Task 3's `buildDeterministicAssemblyHtml`/`renderFallbackComposition`, invoked by
  Task 4's `authorAssemblyComposition` on exhaustion. Decision 3 (retiming, not re-render) → Task
  1 verifies it, Task 3/4's composition generators rely on it directly (no rerender logic
  anywhere in this plan). Decision 4 (swappable model) → Task 5's `ASSEMBLY_AUTHOR_MODEL`.
  Decision 5 (audio unchanged) → Task 2's `mix_music_under_voice` call, untouched signature.
  Decision 6 (`reflow()` unchanged) → Task 2 imports and calls it verbatim. Architecture's 3-piece
  split → Tasks 2/4/2 respectively (`stage_assemble_prepare` / `author_assembly_composition` /
  `stage_assemble_finalize`). "What's retired" → not deleted in this plan (mirrors the
  visuals-stage precedent of keeping old code as reference until the new path is proven live) —
  `assemble.py`'s `run()` simply stops being called once Task 5 removes it from
  `tool-shorts-stage`'s dispatch (implicit: no task references `assemble.run` anymore after this
  plan, only `reflow` is imported).
- **Placeholder scan:** No TBD/TODO. Task 5's Step 1 and Step 5 describe rather than fully
  transcribe two pieces of code (mirroring an existing file's pattern, and an e2e test mirroring
  another e2e test's pattern) — this is deliberate: both require reading the real current content
  of `tool-shorts-stage/src/index.ts` and `visuals-stage-regression.e2e.ts` at execution time
  (which may have shifted slightly since this plan was written) rather than risking a stale
  verbatim copy diverging from the real file. Task 1-4 have zero such deferrals — full code
  throughout.
- **Type consistency:** `AssemblyBrief`/`AssemblyShot`/`WordTiming` (Task 3) are used identically
  in Task 4's `AuthorAssemblyCompositionArgs.assembly_brief` and its persona doc's JSON example.
  `resolveOutputPath(workspace)` (Task 4) matches Task 2's `video_short.mp4` filename exactly.
  `groupWordsIntoCues`'s `{word, start, end}` input shape matches Task 2's `word_timings.json`
  output shape (itself unchanged from the retired `assemble.py`'s original schema).
