# HyperFrames Visuals Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `visuals` stage's PIL card renderer with HyperFrames: a `kimi-k2.7-code`
subagent freely authors each shot's HTML/CSS/GSAP composition and renders it via the real
`hyperframes` CLI, gated by the existing never-blank/never-unverified checks.

**Architecture:** `stage_visuals` splits into three pieces because per-shot authoring must be
driven by the orchestrator agent, not Python (Python can't spawn harness subagents). Two Python
bridge subcommands (`visuals-prepare`, `visuals-finalize`) bookend N orchestrator-driven calls to
a new `author_visual_scene` harness tool, which spawns a `kimi-k2.7-code` subagent scoped to two
tools (`write_scene_file`, `render_scene`) writing into one shared HyperFrames project directory.
`RunManifest` checkpoints exactly once, in `visuals-finalize` — no schema change.

**Tech Stack:** Python 3.12 (bridge, unchanged acquisition ladder), TypeScript/Node (harness tool
+ subagent config), the real `hyperframes` npm CLI (puppeteer-core-based headless rendering).

**Spec:** `docs/superpowers/specs/2026-08-23-hyperframes-visual-pipeline-design.md`

## Global Constraints

- `RunManifest`'s on-disk schema and `STATUS_ORDER` do not change — `last_ok_status` stays at
  `shotlisted` until `visuals-finalize` checkpoints to `visuals`.
- `shorts_engine/sourcing/ladder.py` and `paper_page.py` (acquisition) are not modified — reused
  as-is via `resolve_shot()`.
- The old PIL renderers (`shorts_engine/cards/*.py`) are not deleted in this plan — kept as a
  rollback reference; `stage_visuals.run()` (the old monolithic function) is superseded but not
  removed, since `runner.py`'s own loop (used only by the legacy `shorts_engine` CLI entry point,
  not the harness path) still references it.
- The subagent's file-write access is scoped to one directory
  (`compositions/<workspace_id>/` inside the shared HyperFrames project) — reject any path that
  escapes it, with a real check, not a trust-the-model assumption.
- No `--publish` or real external calls anywhere in this plan.
- Every new TypeScript file is ESM (`"type": "module"`), matching Phase 1/2's convention.

---

## File Structure

```
harness/
  hyperframes_scenes_project/       NEW — one shared `hyperframes init` project (portrait, 1080x1920)
    compositions/<workspace_id>/<shot_id>.html   (created per-run, per-shot, at runtime)
    templates/generic_fallback.html               NEW — hand-authored never-blank safety net
  packages/
    llm-ollama/                     (existing — no changes this plan)
    tool-shorts-stage/               (existing — Python-bridge stage_* tools, no changes this plan)
    tool-visual-scene/               NEW — "@hrsu/dsh-tool-visual-scene"
      package.json
      src/
        index.ts                     plugin entry: registers author_visual_scene tool +
                                      spawns the kimi-k2.7-code subagent
        scene-tools.ts                write_scene_file / render_scene primitives + path scoping
      tests/
        scene-tools.spec.ts
  cordis.yml                         MODIFIED — register tool-visual-scene, wire subagent config
  system_prompt.md                   MODIFIED — describe the new per-shot visuals loop (both files
                                      kept byte-identical per persona-sync.spec.ts)
  tests/
    visuals-stage-regression.e2e.ts  NEW — mechanical plumbing test (stub scene, no live subagent)

_shorts_engine_impl/
  shorts_engine/
    stage_cli.py                     MODIFIED — add `visuals-prepare`/`visuals-finalize` subcommands
  tests/shorts_engine/
    test_stage_cli.py                MODIFIED — tests for the two new subcommands
```

---

## Task 1: Set up the shared HyperFrames project and verify a manual render

**Files:**
- Create: `harness/hyperframes_scenes_project/` (via `hyperframes init`, not hand-written)
- Modify: `harness/package.json` (add `hyperframes` devDependency)

**Interfaces:**
- Produces: a working HyperFrames project directory every later task's `render_scene` calls
  operate against. Nothing later in this plan can be tested without this existing and rendering
  successfully first — same "prove the tool works before building on it" principle as Phase 1's
  Task 1.

- [ ] **Step 1: Add `hyperframes` as a harness devDependency**

Edit `harness/package.json`, add to `"devDependencies"`:

```json
"hyperframes": "0.8.10"
```

Run: `cd harness && pnpm install`

If `0.8.10` is no longer the latest version by the time this runs, check
`npm view hyperframes versions --json` and use the actual latest — this CLI ships frequently
(confirmed: three point-releases were visible in exploration the same week this plan was
written). Record whatever version you actually used in this task's commit message.

- [ ] **Step 2: Initialize the shared project**

Run from `harness/`:

```bash
npx hyperframes init hyperframes_scenes_project --example blank --resolution portrait --non-interactive --skip-transcribe --skip-skills
```

`--resolution portrait` produces a 1080x1920 project — confirmed to match
`config.CANVAS_W`/`config.CANVAS_H` (1080, 1920) exactly. `--skip-transcribe` and `--skip-skills`
avoid pulling in HyperFrames' own whisper-transcription and AI-coding-tool-skill installation
flows, which this project doesn't use (we bring our own audio/word-timing and don't install
HyperFrames as a Claude/Cursor skill). `--non-interactive` is required for this to run
unattended.

If `--example blank` isn't a real example name, run `npx hyperframes init --help` again (its
`-e/--example` flag documented `warm-grain`, `swiss-grid`, `blank` in one exploration pass done
while writing this plan, but example names could differ by version) and pick whichever minimal
starter example exists; the goal is the smallest possible starting project, not a styled one —
you will replace its default `index.html` content in a later task anyway.

- [ ] **Step 3: Render the project's default composition, prove the pipeline works end to end**

Run: `cd harness/hyperframes_scenes_project && npx hyperframes render -o /tmp/hyperframes_smoke_test.mp4`

Expected: a real mp4 file is produced (check with `ffprobe /tmp/hyperframes_smoke_test.mp4` or
simply confirm the file exists and has nonzero size — this is the first real proof HyperFrames'
headless-Chrome capture + ffmpeg encode actually works on this machine, the same kind of
"trivial round-trip" proof Phase 1 required before building anything on the DeepSeek Harness
side).

If this fails, run `npx hyperframes doctor` (a real subcommand: "Check system dependencies and
environment") and report exactly what it says — do not proceed to Task 2 with a broken render
pipeline.

- [ ] **Step 4: Gitignore generated render output, commit the project scaffold**

Add to `harness/.gitignore` (create the file if it doesn't exist, or append if `harness/` files
are covered by the repo-root `.gitignore` already — check first):

```
hyperframes_scenes_project/compositions/
hyperframes_scenes_project/renders/
```

(Keep the project's own scaffolded files — `hyperframes.json`, its default `index.html`, any
committed example assets — tracked; only per-run generated compositions and render output are
ignored, matching this repo's existing pattern of gitignoring `output/`, `.cache/`, etc.)

```bash
cd harness
git add package.json pnpm-lock.yaml hyperframes_scenes_project .gitignore
git commit -m "Initialize shared HyperFrames project, verify a manual render works"
```

---

## Task 2: Python bridge — `visuals-prepare` and `visuals-finalize`

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stage_cli.py`
- Modify: `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`

**Interfaces:**
- Consumes: `shorts_engine.stages.visuals.resolve_shot`, `sample_frame`, `content_pixels` (all
  read directly from source in this session — signatures confirmed exact), `shorts_engine.config`.
- Produces: two CLI operations Task 4's harness tool orchestration depends on:
  - `python -m shorts_engine.stage_cli visuals-prepare --workspace PATH [--torture]` → runs
    `resolve_shot()` for every shot in `workspace/shotlist.json` (acquisition for BROLL/PAPER_CARD
    happens here, exactly as it did in the old `stage_visuals.run()`), writes
    `workspace/shot_briefs.json` as a JSON array of `{shot_id, type, payload, duration_s,
    fade_in_s, provenance}`, prints `{"status": "ok", "shot_briefs": "shot_briefs.json"}` on
    stdout. **Does NOT checkpoint the manifest** — `last_ok_status` stays at `shotlisted`.
  - `python -m shorts_engine.stage_cli visuals-finalize --workspace PATH` → reads
    `workspace/shot_briefs.json`, verifies every shot has a corresponding
    `workspace/shots/shot_<id>.mp4` that exists and passes the never-blank bright-pixel check
    (reusing `sample_frame`/`content_pixels`/`config.MIN_CONTENT_PIXELS` exactly as the old
    `stage_visuals.run()` did), writes `workspace/visuals_report.json`, checkpoints the manifest
    to `status="visuals"` with artifacts `{shots_dir: "shots", visuals_report:
    "visuals_report.json"}`. On any missing/blank shot: same JSON-error contract as
    `cmd_run_stage`'s failure path (`{"status": "error", "message": "..."}` on stderr, exit 1,
    manifest set to `status="failed"`).

- [ ] **Step 1: Write the failing tests**

Add to `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`:

```python
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
```

Add `import subprocess` to the test file's imports if not already present (it already imports
`subprocess` per the existing `run_cli` helper — verify before adding a duplicate import).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -k visuals -v`

Expected: FAIL — `visuals-prepare`/`visuals-finalize` subcommands don't exist yet.

- [ ] **Step 3: Add the two subcommands to `stage_cli.py`**

Add these imports near the top of `stage_cli.py` (alongside the existing ones):

```python
from shorts_engine import config
from shorts_engine.errors import EngineError
from shorts_engine.stages.visuals import content_pixels, resolve_shot, sample_frame
```

Add these functions (after `cmd_run_stage`, before `main`):

```python
def cmd_visuals_prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    order_error = _stage_order_error(manifest.last_ok_status, "visuals", "visuals-prepare")
    if order_error is not None:
        print(json.dumps({"status": "error", "message": order_error}), file=sys.stderr)
        return 1

    ctx = StageContext(manifest=manifest, workspace=workspace, flags=_flags_from_args(args))
    try:
        shots = json.loads((workspace / "shotlist.json").read_text(encoding="utf-8"))["shots"]
        post = json.loads((workspace / "post.json").read_text(encoding="utf-8"))
        briefs = []
        for shot in shots:
            rtype, payload, prov = resolve_shot(shot, ctx, post)
            briefs.append({
                "shot_id": shot["id"], "beat": shot["beat"], "type": rtype,
                "payload": payload, "duration_s": shot["duration_s"],
                "fade_in_s": config.TRANSITION_FADE_S if shot["beat"] != shots[0]["beat"] else 0.0,
                "provenance": prov,
            })
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"visuals-prepare: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    (workspace / "shot_briefs.json").write_text(json.dumps(briefs, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "shot_briefs": "shot_briefs.json"}))
    return 0


def cmd_visuals_finalize(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    try:
        briefs = json.loads((workspace / "shot_briefs.json").read_text(encoding="utf-8"))
        shots_dir = workspace / "shots"
        report = {"shots": []}
        for brief in briefs:
            shot_id = brief["shot_id"]
            mp4 = shots_dir / f"shot_{shot_id}.mp4"
            if not mp4.exists():
                raise EngineError(f"visuals-finalize: shot {shot_id} has no rendered mp4 at {mp4}")
            png = shots_dir / f"shot_{shot_id}_mid.png"
            sample_frame(mp4, brief["duration_s"] / 2, png)
            pixels = content_pixels(png)
            if pixels < config.MIN_CONTENT_PIXELS:
                raise EngineError(
                    f"visuals-finalize: shot {shot_id} ({brief['type']}) rendered without "
                    f"visible content ({pixels} bright px < {config.MIN_CONTENT_PIXELS}) — "
                    f"never-blank violated")
            report["shots"].append({**brief, "content_pixels": pixels})
        (workspace / "visuals_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = f"visuals-finalize: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    artifacts = {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
    manifest.checkpoint(status="visuals", **artifacts)
    print(json.dumps({"status": "ok", "status_after": "visuals", "artifacts": artifacts}))
    return 0
```

Register both in `main()`, alongside the existing `run_stage_parser` block:

```python
    visuals_prepare_parser = subparsers.add_parser("visuals-prepare")
    visuals_prepare_parser.add_argument("--workspace", required=True)
    visuals_prepare_parser.add_argument("--local-only", action="store_true")
    visuals_prepare_parser.add_argument("--torture", action="store_true")
    visuals_prepare_parser.set_defaults(func=cmd_visuals_prepare)

    visuals_finalize_parser = subparsers.add_parser("visuals-finalize")
    visuals_finalize_parser.add_argument("--workspace", required=True)
    visuals_finalize_parser.set_defaults(func=cmd_visuals_finalize)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -v`

Expected: PASS (all tests in the file, old and new — 8 total: the 5 from Phase 2 plus 3 new).

- [ ] **Step 5: Run the full shorts_engine suite to confirm no regression**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine -q`

Expected: all 517 tests still pass (per this session's earlier fix of the `video_agent.config`
bug — there should be zero pre-existing failures left to worry about at this point).

- [ ] **Step 6: Commit**

```bash
cd _shorts_engine_impl
git add shorts_engine/stage_cli.py tests/shorts_engine/test_stage_cli.py
git commit -m "Add visuals-prepare/visuals-finalize stage_cli subcommands"
```

---

## Task 3: Generic fallback template + scene-writing/rendering primitives

**Files:**
- Create: `harness/hyperframes_scenes_project/templates/generic_fallback.html`
- Create: `harness/packages/tool-visual-scene/package.json`
- Create: `harness/packages/tool-visual-scene/src/scene-tools.ts`
- Create: `harness/packages/tool-visual-scene/tests/scene-tools.spec.ts`

**Interfaces:**
- Produces: `writeSceneFile(workspaceId, shotId, html, projectRoot)` — validates the target path
  stays inside `compositions/<workspaceId>/`, writes the file, returns the absolute path. Throws
  on any attempt to escape that directory (`../` traversal, absolute paths, etc.).
- Produces: `renderScene(workspaceId, shotId, projectRoot, outputPath)` — shells `hyperframes
  render -c compositions/<workspaceId>/<shotId>.html -o <outputPath> --resolution portrait`
  from `projectRoot`, returns the output path on success, throws with the CLI's stderr on
  failure. Task 4's `author_visual_scene` tool wraps both of these as the subagent's two tools.
- Produces: `renderFallbackScene(workspaceId, shotId, projectRoot, outputPath, captionText)` —
  copies `templates/generic_fallback.html` into
  `compositions/<workspaceId>/<shotId>_fallback.html` with `captionText` substituted in (simple
  string replace, no templating engine needed for one placeholder), then calls the same render
  path. This is the never-blank safety net Task 4/5 fall back to after 2 failed authoring
  attempts.

- [ ] **Step 1: Write the fallback template**

Create `harness/hyperframes_scenes_project/templates/generic_fallback.html`. Read
`harness/brand_facts.yaml`'s real values first (via `cat ../../brand_facts.yaml` from
`harness/`, or however you inspect a file — the point is: read the actual file, don't guess the
palette) and use them here — this plan's earlier phases already established `brand_facts.yaml` as
this repo's single source of brand truth. Structure it as a real HyperFrames composition matching
the `data-*` schema confirmed in the spec's research (root `data-composition-id`, `data-start`,
`data-duration`, `data-width="1080"`, `data-height="1920"`; a single `class="clip"` section with
`data-start="0"`, a duration, `data-track-index="0"` containing a centered text element showing a
`{{CAPTION}}` placeholder token — literal text, not a HyperFrames variable, since this file gets
substituted by a simple string replace, not rendered through HyperFrames' own variable-binding
system, to keep this one safety-net template trivially simple and not dependent on
`author_visual_scene`'s more complex machinery). Style it with the brand's actual navy/gold
colors and company name — this is the one visual users could see if everything else fails, so it
should look intentional, not like an error state.

- [ ] **Step 2: Write the failing tests**

Create `harness/packages/tool-visual-scene/tests/scene-tools.spec.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeSceneFile, renderScene, renderFallbackScene } from '../src/scene-tools.ts'

describe('writeSceneFile', () => {
  it('writes inside compositions/<workspaceId>/ and returns the absolute path', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    const result = writeSceneFile('run-42', 'shot-1', '<html></html>', projectRoot)
    expect(existsSync(result)).toBe(true)
    expect(readFileSync(result, 'utf8')).toBe('<html></html>')
    expect(result).toContain(join('compositions', 'run-42', 'shot-1.html'))
  })

  it('rejects a shotId that attempts path traversal', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', '../../../etc/passwd', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })
})

describe('renderScene', () => {
  it('spawns the hyperframes CLI with the expected composition/output/resolution args', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as
        { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
      return child
    })

    const result = await renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', {
      spawn: spawnMock as never,
    })

    expect(spawnMock).toHaveBeenCalledWith(
      'npx',
      ['hyperframes', 'render', '-c', join('compositions', 'run-42', 'shot-1.html'),
       '-o', '/out/shot-1.mp4', '--resolution', 'portrait'],
      expect.objectContaining({ cwd: '/project' }),
    )
    expect(result).toBe('/out/shot-1.mp4')
  })

  it('throws with the CLI stderr on a non-zero exit', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as
        { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => {
        child.stderr.emit('data', Buffer.from('composition lint error: missing data-duration'))
        ;(child as never as EventEmitter).emit('close', 1)
      })
      return child
    })

    await expect(renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', {
      spawn: spawnMock as never,
    })).rejects.toThrow(/missing data-duration/)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && pnpm --filter @hrsu/dsh-tool-visual-scene test`

Expected: FAIL — package/module don't exist yet (create a minimal `package.json` first per Step 4
below if the `pnpm --filter` invocation itself fails before reaching the module-not-found error).

- [ ] **Step 3: Write `scene-tools.ts`**

Create `harness/packages/tool-visual-scene/src/scene-tools.ts`:

```typescript
import { spawn as nodeSpawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'

function compositionsRoot(projectRoot: string): string {
  return join(projectRoot, 'compositions')
}

function resolveScopedPath(workspaceId: string, shotId: string, projectRoot: string, ext: string): string {
  const root = compositionsRoot(projectRoot)
  const candidate = resolve(root, workspaceId, `${shotId}${ext}`)
  const rel = relative(root, candidate)
  if (rel.startsWith('..') || resolve(root, rel) !== candidate) {
    throw new Error(`writeSceneFile: resolved path is outside compositions/: ${candidate}`)
  }
  return candidate
}

export function writeSceneFile(
  workspaceId: string,
  shotId: string,
  html: string,
  projectRoot: string,
): string {
  const target = resolveScopedPath(workspaceId, shotId, projectRoot, '.html')
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, html, 'utf8')
  return target
}

export interface RenderOptions {
  /** Injectable for tests; defaults to `node:child_process`'s real `spawn`. */
  spawn?: typeof nodeSpawn
}

function runHyperframesRender(
  compositionRelPath: string,
  projectRoot: string,
  outputPath: string,
  options: RenderOptions = {},
): Promise<string> {
  const spawnFn = options.spawn ?? nodeSpawn
  return new Promise((resolvePromise, reject) => {
    const child = spawnFn(
      'npx',
      ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait'],
      { cwd: projectRoot },
    )
    let stderr = ''
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) resolvePromise(outputPath)
      else reject(new Error(stderr.trim() || `hyperframes render exited ${code}`))
    })
  })
}

export function renderScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  options: RenderOptions = {},
): Promise<string> {
  const compositionRelPath = join('compositions', workspaceId, `${shotId}.html`)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}

export function renderFallbackScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  captionText: string,
  options: RenderOptions = {},
): Promise<string> {
  const templatePath = join(projectRoot, 'templates', 'generic_fallback.html')
  const template = readFileSync(templatePath, 'utf8')
  const html = template.replace('{{CAPTION}}', captionText)
  const fallbackShotId = `${shotId}_fallback`
  writeSceneFile(workspaceId, fallbackShotId, html, projectRoot)
  const compositionRelPath = join('compositions', workspaceId, `${fallbackShotId}.html`)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}
```

- [ ] **Step 4: Write the package manifest**

Create `harness/packages/tool-visual-scene/package.json`:

```json
{
  "name": "@hrsu/dsh-tool-visual-scene",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "main": "src/index.ts",
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "vitest": "*"
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd harness && pnpm install && pnpm --filter @hrsu/dsh-tool-visual-scene test`

Expected: PASS (4/4).

- [ ] **Step 6: Commit**

```bash
cd harness
git add hyperframes_scenes_project/templates packages/tool-visual-scene package.json pnpm-lock.yaml
git commit -m "Add generic fallback template and scene write/render primitives"
```

---

## Task 4: `author_visual_scene` harness tool with the kimi-k2.7-code subagent

**Files:**
- Create: `harness/packages/tool-visual-scene/src/index.ts`
- Create: `harness/packages/tool-visual-scene/src/scene-author-persona.md`
- Modify: `harness/packages/tool-visual-scene/tests/scene-tools.spec.ts` (or a new
  `index.spec.ts` — implementer's call, keep it in the same package)

**Interfaces:**
- Consumes: `writeSceneFile`/`renderScene`/`renderFallbackScene` (Task 3), `dsh-tool-subagent`'s
  real config shape (`agentOptions: { provider, model, maxTokens }`, `toolFilter`) — verify this
  against `harness/node_modules/@deepseek-ai/dsh-tool-subagent`'s actual installed README/types
  before writing the plugin config; the shape summarized in the spec came from a web fetch, not a
  raw file read, and needs the same verification discipline every prior task in this session has
  applied to unfamiliar DSH packages.
- Produces: a registered `author_visual_scene` tool (via `@deepseek-ai/dsh-tools`' `defineTool`,
  same pattern as `@hrsu/dsh-tool-shorts-stage` from Phase 2) taking `{ shot_brief, workspace_id }`
  and returning `{ mp4_path, attempts, used_fallback }`.

- [ ] **Step 1: Read the real `dsh-tool-subagent` package before writing config**

Run: `cat harness/node_modules/@deepseek-ai/dsh-tool-subagent/README.md` (add
`"@deepseek-ai/dsh-tool-subagent": "*"` to `harness/packages/tool-visual-scene/package.json`'s
dependencies and `pnpm install` first if it isn't already resolved into `node_modules` — check
before assuming). Confirm the exact field names for: selecting a provider/model different from
the parent, restricting the child's visible tools, and whether spawning happens via a `ctx`
service call (like `ctx.tools.register`) or via the harness's own `subagent` tool being invoked
programmatically from inside another tool's `execute()`. This determines the actual shape of
Step 3 below — do not guess it from this plan's prose.

- [ ] **Step 2: Write the subagent's persona**

Create `harness/packages/tool-visual-scene/src/scene-author-persona.md` covering (as real
markdown content, not a placeholder — write the actual text):
- The HyperFrames `data-*` timing attribute contract (root composition needs
  `data-composition-id`, `data-width="1080"`, `data-height="1920"`, `data-duration`; each visible
  element needs `id`, `data-start`, `data-duration`, `data-track-index`, `class="clip"`) — copied
  from the confirmed-real schema reference this plan's spec already researched.
- The GSAP animation contract: paused timeline, registered on `window.__timelines` keyed by
  `data-composition-id`, no wall-clock state or unseeded randomness.
- This project's brand facts (read `brand_facts.yaml` and write the actual company name, domain,
  gold/navy hex colors, banned claims into this persona — not a reference to "check the file",
  the actual values, since the subagent has no filesystem read access to `brand_facts.yaml`
  itself under this plan's scoped-tools design).
- The shot brief schema it receives: `{ shot_id, beat, type, payload, duration_s, fade_in_s,
  provenance }` (matching Task 2's `shot_briefs.json` entries exactly).
- Explicit instruction: call `write_scene_file` then `render_scene`; if `render_scene` throws,
  read the error and try once more with a fix; after 2 total attempts, stop and report failure
  rather than retrying indefinitely.

- [ ] **Step 3: Write the plugin entry point**

Create `harness/packages/tool-visual-scene/src/index.ts`. The exact shape of the subagent-spawn
call depends on what Step 1 found — write it against the real API, following the same
`defineTool`/`ctx.tools.register` pattern Phase 2's `@hrsu/dsh-tool-shorts-stage` already
established (re-read `harness/packages/tool-shorts-stage/src/index.ts` for that pattern before
writing this one). The tool's `execute()` should: call `writeSceneFile`/`renderScene` through
whatever the subagent-spawn mechanism actually is, catch a first-attempt failure, retry once with
the failure reason appended to the shot brief context, and on a second failure call
`renderFallbackScene` with the shot's `payload`'s most relevant text field as the caption (pick a
sensible field per shot `type` — e.g. `payload.text` for `HEADLINE_CARD`, `payload.stat` for
`STAT_CARD`; if a shot's `type` has no obvious single text field, fall back to `beat`).

- [ ] **Step 4: Write tests for the retry/fallback logic**

Add tests (in `scene-tools.spec.ts` or a new file in the same package — your call) covering: a
mocked subagent-spawn that fails twice in a row results in `used_fallback: true` and a call to
`renderFallbackScene`; a mocked subagent-spawn that succeeds on the first attempt never touches
the fallback path. Mock at whatever boundary Step 1's real API puts the spawn call — don't invent
a boundary that doesn't match the real code you wrote in Step 3.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd harness && pnpm --filter @hrsu/dsh-tool-visual-scene test`

- [ ] **Step 6: Commit**

```bash
cd harness
git add packages/tool-visual-scene
git commit -m "Add author_visual_scene tool spawning a kimi-k2.7-code subagent"
```

---

## Task 5: Wire into cordis.yml, update the persona, mechanical regression test

**Files:**
- Modify: `harness/cordis.yml`
- Modify: `harness/system_prompt.md`
- Modify: `harness/package.json` (register `@hrsu/dsh-tool-shorts-stage`'s sibling —
  `@hrsu/dsh-tool-visual-scene` — as a workspace dependency, same sequencing lesson from Phase 1/2)
- Create: `harness/tests/visuals-stage-regression.e2e.ts`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: the composition this plan's final proof drives.

- [ ] **Step 1: Register the workspace dependency**

Add `"@hrsu/dsh-tool-visual-scene": "workspace:*"` to `harness/package.json`'s dependencies (this
must happen after Task 4 creates the package — same ordering lesson as Phase 1/2's pre-flight
findings). Run `pnpm install`.

- [ ] **Step 2: Add the plugin entry to cordis.yml**

Add an entry registering `@hrsu/dsh-tool-visual-scene`, following the exact pattern
`tool-shorts-stage`'s entry already uses in this file (read the existing entry first — same `id`/
`name`/`config` shape). Its config needs the HyperFrames project's absolute path (same
`!!js process.cwd() + '/...'` fallback pattern already proven twice in this repo, since `!!js
require(...)` is confirmed broken in this harness version) pointing at
`harness/hyperframes_scenes_project`.

- [ ] **Step 3: Replace the old `stage_visuals` tool references with the new per-shot flow**

In `harness/packages/tool-shorts-stage/src/index.ts`'s `STAGE_TOOLS` array, the existing
`stage_visuals` entry (mapping to Python's `run-stage visuals`) is now dead — Task 2 replaced that
Python code path with `visuals-prepare`/`visuals-finalize`. Add two new entries to that array
(`stage_visuals_prepare` → `stage_cli.py visuals-prepare`, `stage_visuals_finalize` →
`stage_cli.py visuals-finalize`) and remove the old `stage_visuals` entry. Update that package's
existing tests for the removed/added tool names. This is a change to an already-shipped Phase 2
package — treat it with the same care (re-run that package's full test suite, not just the new
assertions).

- [ ] **Step 4: Update the persona (both files, identically)**

Rewrite the "How to use your tools" section's stage-tool list to describe the new flow: call
`stage_visuals_prepare` once, then call `author_visual_scene` once per entry in the returned
`shot_briefs.json` (read via whatever mechanism the orchestrator already uses to inspect
JSON artifacts — same pattern as `stage_init`'s `workspace` value threading through later calls,
established in Phase 2's persona), then call `stage_visuals_finalize` once all shots are done.
Keep every other section of the persona unchanged. Edit `cordis.yml`'s embedded copy and
`system_prompt.md` identically — `persona-sync.spec.ts` enforces this.

- [ ] **Step 5: Run persona-sync and the full harness suite**

Run: `cd harness && pnpm exec vitest run tests/persona-sync.spec.ts` then
`pnpm exec vitest run` (full suite).

Expected: all pass, including Phase 1/2's existing tests (`roundtrip.e2e.ts`,
`stage-regression.e2e.ts`, adapter tests) — none of this task's changes should have touched their
behavior.

- [ ] **Step 6: Write the mechanical regression test**

Create `harness/tests/visuals-stage-regression.e2e.ts`. This test proves the plumbing (Tasks 2-3's
Python/Node wiring) without depending on a live, non-deterministic `kimi-k2.7-code` call — call
`visuals-prepare`, then directly call `writeSceneFile`/`renderScene` with a small,
deterministically-authored stub HTML composition (not through the subagent) for each shot brief,
then call `visuals-finalize`, and assert the manifest reaches `status="visuals"`. This is the
same "prove the mechanical path works deterministically, exploratory-verify the live-model path
separately" split Phase 2's Task 5 established.

Run: `cd harness && pnpm exec vitest run tests/visuals-stage-regression.e2e.ts`

- [ ] **Step 7: Manual exploratory verification (report findings, not a pass/fail gate)**

Boot the full composition (same `Loader`/`Include` pattern as `roundtrip.e2e.ts`) and call
`author_visual_scene` for real, once, with a simple `HEADLINE_CARD` shot brief. Report honestly
whether `kimi-k2.7-code` produced a valid composition, whether it rendered, and whether the
never-blank check would have passed against the real output — this is exploratory verification of
the live model path, matching the pattern this session has used for every prior live-model check.

- [ ] **Step 8: Commit**

```bash
cd harness
git add cordis.yml system_prompt.md package.json pnpm-lock.yaml packages/tool-shorts-stage tests/visuals-stage-regression.e2e.ts
git commit -m "Wire author_visual_scene into cordis.yml, replace stage_visuals with prepare/finalize split"
```
