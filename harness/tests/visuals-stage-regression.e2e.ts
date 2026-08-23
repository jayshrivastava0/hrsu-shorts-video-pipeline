import { describe, expect, it } from 'vitest'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { runStageCli } from '@hrsu/dsh-tool-shorts-stage'
import { writeSceneFile, renderScene } from '@hrsu/dsh-tool-visual-scene'

// This test proves the mechanical plumbing Tasks 2-3 built (the Python `visuals-prepare`/
// `visuals-finalize` bridge, and the Node `writeSceneFile`/`renderScene` primitives) works
// end-to-end, WITHOUT depending on a live, non-deterministic `kimi-k2.7-code` subagent call —
// the same "prove the mechanical path deterministically, exploratory-verify the live-model path
// separately" split Phase 2's Task 5 established (see `stage-regression.e2e.ts`'s own two-part
// structure). Step 7 of this task (reported in task-5-report.md, not asserted here) exercises the
// real `author_visual_scene` subagent path once, manually.
//
// `visuals-prepare` requires the manifest to be sitting at `last_ok_status: "audio"` (see
// `stage_cli.py`'s `_stage_order_error`) and reads `shotlist.json`/`post.json` from the
// workspace. Rather than drive `ingest` -> `facts` -> `script` -> `shotlist` -> `audio` for real
// (slow, and non-deterministic once it reaches an LLM-backed stage), this test starts a real
// workspace via `stage_init`, then hand-writes the manifest/shotlist/post fixtures the
// `visuals-prepare` stage actually reads — the same "construct the exact precondition state"
// approach `stage-regression.e2e.ts` uses via `--html-override` for the `ingest` stage. Both
// shots use DESIGNED card types (`HEADLINE_CARD`/`STAT_CARD`), which `resolve_shot()`
// (`_shorts_engine_impl/shorts_engine/stages/visuals.py`) returns directly from the shot's own
// `payload` with no network acquisition ladder — unlike `BROLL`/`PAPER_CARD` — keeping this
// deterministic.
const SHORTS_ENGINE_CWD = join(import.meta.dirname, '../../_shorts_engine_impl')
const PROJECT_ROOT = join(import.meta.dirname, '../hyperframes_scenes_project')

/**
 * A minimal, valid HyperFrames composition: one `data-duration`-timed `class="clip"` element
 * with visible white text on a black background, a registered (paused) `window.__timelines`
 * entry, and no non-deterministic JS (`Date.now()`/`Math.random()`/network fetches) — satisfying
 * `hyperframes_scenes_project/AGENTS.md`'s "Key Rules" and `visuals-finalize`'s never-blank
 * bright-pixel check (`MIN_CONTENT_PIXELS = 500` bright px at `LUMA_CONTENT_THRESHOLD = 140`,
 * `_shorts_engine_impl/shorts_engine/config.py`) via large white caption text on black.
 */
function stubComposition(compositionId: string, caption: string, durationS: number): string {
  return `<!doctype html>
<html lang="en" data-resolution="portrait">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body { margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #000000; }
      .caption {
        position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        text-align: center; font-family: Arial, "Segoe UI", sans-serif; font-weight: 700;
        font-size: 88px; line-height: 1.3; color: #ffffff; padding: 100px;
      }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="${compositionId}" data-start="0" data-duration="${durationS}"
         data-width="1080" data-height="1920">
      <div id="caption" class="clip" data-start="0" data-duration="${durationS}" data-track-index="0">
        <div class="caption">${caption}</div>
      </div>
    </div>
    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      tl.from("#caption .caption", { opacity: 0, duration: 0.4 }, 0);
      window.__timelines["${compositionId}"] = tl;
    </script>
  </body>
</html>`
}

interface ShotBrief {
  shot_id: string
  beat: string
  type: string
  payload: Record<string, unknown>
  duration_s: number
}

function captionFor(brief: ShotBrief): string {
  const text = brief.payload.text
  const stat = brief.payload.stat
  if (typeof text === 'string') return text
  if (typeof stat === 'string') return stat
  return brief.beat
}

describe('visuals-stage mechanical regression (prepare -> deterministic authoring -> finalize)', () => {
  it('reaches manifest status="visuals" without calling the kimi-k2.7-code subagent', async (testCtx) => {
    // Boots the real `harness/cordis.yml` composition, same pattern as `roundtrip.e2e.ts` and
    // `stage-regression.e2e.ts`'s "harness tool layer" test — resolves `tool-shorts-stage`'s and
    // `tool-visual-scene`'s `inject: ['tools']` (and `tool-visual-scene`'s `inject: ['subagents']`)
    // dependencies from the composed bundle, without needing a reachable Ollama server, since this
    // test never sends the top-level agent a message and never calls `author_visual_scene`.
    const root = new Context()
    root.baseUrl = import.meta.url
    await root.plugin(Loader, { baseUrl: import.meta.url })
    await root.plugin(Include, { path: '../cordis.yml', initial: [] })
    await root.get('loader')?.await()

    try {
      const tools = root.get('tools')
      expect(tools).toBeDefined()
      expect(tools!.get('stage_visuals_prepare')).toBeDefined()
      expect(tools!.get('stage_visuals_finalize')).toBeDefined()
      expect(tools!.get('author_visual_scene')).toBeDefined()
      expect(tools!.get('stage_visuals')).toBeUndefined()

      const workspaceRoot = join(SHORTS_ENGINE_CWD, '..', 'output', `visuals-mech-test-${Date.now()}`)
      const init = await runStageCli(
        ['init', 'https://blog.hrsuindore.com/fixture-post', '--workspace-root', workspaceRoot],
        { cwd: SHORTS_ENGINE_CWD },
      ) as { workspace: string; run_id: string }
      const workspace = init.workspace
      expect(workspace).toBeTruthy()

      // Hand-construct the precondition `visuals-prepare` requires: manifest sitting at
      // `last_ok_status: "audio"`, plus the `shotlist.json`/`post.json` it reads.
      const manifestPath = join(workspace, 'run_manifest.json')
      const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Record<string, unknown>
      manifest.status = 'audio'
      manifest.last_ok_status = 'audio'
      writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8')

      const shots = [
        { id: '1', beat: 'hook', type: 'HEADLINE_CARD', payload: { text: 'Cold pours, warm results.' }, duration_s: 2.5 },
        { id: '2', beat: 'body', type: 'STAT_CARD', payload: { stat: '38%' }, duration_s: 3 },
      ]
      writeFileSync(join(workspace, 'shotlist.json'), JSON.stringify({ shots }, null, 2), 'utf8')
      writeFileSync(join(workspace, 'post.json'), JSON.stringify({ images: [] }, null, 2), 'utf8')

      const controller = new AbortController()
      const prepareResult = await tools!.execute({
        callId: 'test-visuals-prepare' as never,
        name: 'stage_visuals_prepare',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(prepareResult.isError).toBe(false)

      const briefsPath = join(workspace, 'shot_briefs.json')
      expect(existsSync(briefsPath)).toBe(true)
      const briefs = JSON.parse(readFileSync(briefsPath, 'utf8')) as ShotBrief[]
      expect(briefs.map((b) => b.shot_id).sort()).toEqual(['1', '2'])

      // Author + render each shot deterministically via the raw `writeSceneFile`/`renderScene`
      // primitives -- NOT through `author_visual_scene`'s subagent (that path is exploratory-
      // verified once, manually, in Step 7 / task-5-report.md). `render_scene`'s output_path is
      // caller-supplied, so it's pointed directly at the real Python workspace's `shots/`
      // directory, matching `visuals-finalize`'s expectation (`shots_dir / f"shot_{shot_id}.mp4"`,
      // `_shorts_engine_impl/shorts_engine/stage_cli.py`'s `cmd_visuals_finalize`).
      const shotsDir = join(workspace, 'shots')
      mkdirSync(shotsDir, { recursive: true })
      const workspaceId = `mech-test-${init.run_id}`
      for (const brief of briefs) {
        writeSceneFile(workspaceId, brief.shot_id, stubComposition(`shot-${brief.shot_id}`, captionFor(brief), 3), PROJECT_ROOT)
        const outputPath = join(shotsDir, `shot_${brief.shot_id}.mp4`)
        await renderScene(workspaceId, brief.shot_id, PROJECT_ROOT, outputPath)
        expect(existsSync(outputPath)).toBe(true)
      }

      const finalizeResult = await tools!.execute({
        callId: 'test-visuals-finalize' as never,
        name: 'stage_visuals_finalize',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(finalizeResult.isError).toBe(false)

      const finalManifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { status: string; last_ok_status: string }
      expect(finalManifest.status).toBe('visuals')
      expect(finalManifest.last_ok_status).toBe('visuals')

      const reportPath = join(workspace, 'visuals_report.json')
      expect(existsSync(reportPath)).toBe(true)
      const report = JSON.parse(readFileSync(reportPath, 'utf8')) as { shots: Array<{ shot_id: string; content_pixels: number }> }
      expect(report.shots.length).toBe(2)
      for (const shot of report.shots) {
        expect(shot.content_pixels).toBeGreaterThan(0)
      }
    } finally {
      await root.fiber.dispose()
    }
  }, 120_000)
})
