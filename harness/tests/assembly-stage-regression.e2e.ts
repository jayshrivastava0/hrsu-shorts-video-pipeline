import { describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { runStageCli } from '@hrsu/dsh-tool-shorts-stage'
import { buildDeterministicAssemblyHtml, type AssemblyBrief } from '../packages/tool-assembly/src/composition-tools.ts'

// This test proves the mechanical plumbing Tasks 2-4 built (the Python `assemble-prepare`/
// `assemble-finalize` bridge, and the Node `write_composition_file`/`render_composition`
// primitives + `author_assembly_composition` tool) works end-to-end, WITHOUT depending on a live,
// non-deterministic assembly-authoring subagent call — the same "prove the mechanical path
// deterministically, exploratory-verify the live-model path separately" split
// `visuals-stage-regression.e2e.ts` established for the sibling `visuals` stage.
//
// `assemble-prepare` requires the manifest to be sitting at `last_ok_status: "visuals"` (see
// `stage_cli.py`'s `_stage_order_error`) and reads `shotlist.json`/`beats_audio.json`/
// `word_timings.json`/`post.json`/`voiceover.mp3`/`shots/shot_<id>.mp4` from the workspace.
// Rather than drive `ingest` -> ... -> `visuals` for real, this test starts a real workspace via
// `stage_init`, then hand-writes the exact fixtures `_shorts_engine_impl/tests/shorts_engine/
// test_stage_cli.py`'s `_write_assemble_prepare_fixtures` already uses for the Python-side unit
// tests (Task 2) — the same "construct the exact precondition state" approach
// `visuals-stage-regression.e2e.ts` uses.
const SHORTS_ENGINE_CWD = join(import.meta.dirname, '../../_shorts_engine_impl')
const PROJECT_ROOT = join(import.meta.dirname, '../hyperframes_scenes_project')

function runFfmpeg(args: string[]): void {
  execFileSync('ffmpeg', args, { stdio: 'pipe' })
}

/**
 * Hand-writes the exact precondition fixtures `cmd_assemble_prepare`
 * (`_shorts_engine_impl/shorts_engine/stage_cli.py`) reads, mirroring
 * `test_stage_cli.py`'s `_write_assemble_prepare_fixtures` byte-for-byte in intent: two shots
 * (`HEADLINE_CARD`/`LOGO_CTA`), a 3.4s silent voiceover, and two pre-rendered white shot mp4s.
 */
function writeAssemblePrepareFixtures(workspace: string): void {
  writeFileSync(join(workspace, 'shotlist.json'), JSON.stringify({
    shots: [
      { id: '1', beat: 'hook', type: 'HEADLINE_CARD', payload: { text: 'Cold weather pours' }, duration_s: 2.5 },
      { id: '2', beat: 'cta', type: 'LOGO_CTA', payload: { text: 'Visit hrsuindore.com' }, duration_s: 3.0 },
    ],
  }), 'utf8')
  writeFileSync(join(workspace, 'beats_audio.json'), JSON.stringify([
    { beat: 'hook', start_s: 0.0 },
    { beat: 'cta', start_s: 2.4 },
  ]), 'utf8')
  writeFileSync(join(workspace, 'word_timings.json'), JSON.stringify([
    { word: 'Cold', start: 0.1, end: 0.4 },
    { word: 'pours', start: 0.5, end: 0.9 },
  ]), 'utf8')
  writeFileSync(join(workspace, 'post.json'), JSON.stringify({ region: 'usa', images: [] }), 'utf8')

  runFfmpeg(['-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '3.4', join(workspace, 'voiceover.mp3')])

  const shotsDir = join(workspace, 'shots')
  mkdirSync(shotsDir, { recursive: true })
  for (const shotId of ['1', '2']) {
    runFfmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=white:s=64x64:d=2.5', join(shotsDir, `shot_${shotId}.mp4`)])
  }
}

async function bootHarness(): Promise<Context> {
  const root = new Context()
  root.baseUrl = import.meta.url
  await root.plugin(Loader, { baseUrl: import.meta.url })
  await root.plugin(Include, { path: '../cordis.yml', initial: [] })
  await root.get('loader')?.await()
  return root
}

async function initWorkspaceAtVisuals(tools: NonNullable<ReturnType<Context['get']>>, label: string): Promise<{ workspace: string; run_id: string }> {
  const workspaceRoot = join(SHORTS_ENGINE_CWD, '..', 'output', `${label}-${Date.now()}`)
  const init = await runStageCli(
    ['init', 'https://blog.hrsuindore.com/fixture-post', '--workspace-root', workspaceRoot],
    { cwd: SHORTS_ENGINE_CWD },
  ) as { workspace: string; run_id: string }
  expect(init.workspace).toBeTruthy()

  const manifestPath = join(init.workspace, 'run_manifest.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Record<string, unknown>
  manifest.status = 'visuals'
  manifest.last_ok_status = 'visuals'
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8')

  writeAssemblePrepareFixtures(init.workspace)
  return init
}

describe('assembly-stage mechanical regression (prepare -> real author_assembly_composition -> finalize)', () => {
  it('reaches manifest status="assembled" via a stubbed subagent that calls the real write_composition_file/render_composition tools', async () => {
    const root = await bootHarness()
    try {
      const tools = root.get('tools')
      expect(tools).toBeDefined()
      expect(tools!.get('stage_assemble_prepare')).toBeDefined()
      expect(tools!.get('stage_assemble_finalize')).toBeDefined()
      expect(tools!.get('author_assembly_composition')).toBeDefined()
      expect(tools!.get('stage_assemble')).toBeUndefined()

      const init = await initWorkspaceAtVisuals(tools!, 'assembly-mech-test')
      const workspace = init.workspace
      const manifestPath = join(workspace, 'run_manifest.json')

      const controller = new AbortController()
      const prepareResult = await tools!.execute({
        callId: 'asm-test-prepare' as never,
        name: 'stage_assemble_prepare',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(prepareResult.isError).toBe(false)

      const briefPath = join(workspace, 'assembly_brief.json')
      expect(existsSync(briefPath)).toBe(true)
      const brief = JSON.parse(readFileSync(briefPath, 'utf8')) as AssemblyBrief
      expect(brief.shots.map((s) => s.id).sort()).toEqual(['1', '2'])

      // Stub the subagent SERVICE (`ctx.subagents.start`), not the `write_composition_file`/
      // `render_composition` TOOLS — same discipline `visuals-stage-regression.e2e.ts` uses for
      // `author_visual_scene`. This "fake child" calls the real, registered
      // `write_composition_file`/`render_composition` tools through the real tool registry, using
      // the deterministic composition generator (Task 3's `buildDeterministicAssemblyHtml`)
      // directly against the real `assembly_brief.json`, so the actual mp4-path plumbing inside
      // `author_assembly_composition`'s execute() runs for real; only the model turn that would
      // normally choose what HTML to author is faked.
      const workspaceId = init.run_id
      const realSubagents = root.get('subagents')
      expect(realSubagents).toBeDefined()
      const originalStart = realSubagents!.start.bind(realSubagents)
      realSubagents!.start = (async (_name: string, request: { signal: AbortSignal }) => {
        const html = buildDeterministicAssemblyHtml(brief)

        const writeResult = await tools!.execute({
          callId: 'asm-test-write' as never,
          name: 'write_composition_file',
          arguments: { workspace_id: workspaceId, html },
          signal: request.signal,
        })
        if (writeResult.isError) throw new Error(`test stub: write_composition_file failed: ${JSON.stringify(writeResult)}`)

        const renderResult = await tools!.execute({
          callId: 'asm-test-render' as never,
          name: 'render_composition',
          arguments: { workspace_id: workspaceId },
          signal: request.signal,
        })
        if (renderResult.isError) throw new Error(`test stub: render_composition failed: ${JSON.stringify(renderResult)}`)

        return {
          id: 'fake-child-assembly' as never,
          localAgent: undefined,
          result: Promise.resolve({ output: [], stopReason: 'completed' as const }),
          dispose: async () => {},
        }
      }) as typeof realSubagents.start

      try {
        const authorResult = await tools!.execute({
          callId: 'asm-test-author' as never,
          name: 'author_assembly_composition',
          arguments: { assembly_brief: brief, workspace_id: workspaceId, workspace },
          // `author_assembly_composition`'s own execute() requires `exec.agent` (becomes
          // `SubagentStartRequest.parent`) — our stubbed `subagents.start` above never
          // dereferences it, so an opaque object stands in, same trick the visuals test uses.
          agent: {} as never,
          signal: controller.signal,
        })
        expect(authorResult.isError).toBe(false)
        const authorValue = (authorResult as { value: { mp4_path: string; attempts: number; used_fallback: boolean } }).value
        expect(authorValue.used_fallback).toBe(false)
        expect(authorValue.attempts).toBe(1)
        expect(existsSync(authorValue.mp4_path)).toBe(true)
      } finally {
        realSubagents!.start = originalStart
      }

      const finalizeResult = await tools!.execute({
        callId: 'asm-test-finalize' as never,
        name: 'stage_assemble_finalize',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(finalizeResult.isError).toBe(false)

      const finalManifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { status: string; last_ok_status: string }
      expect(finalManifest.status).toBe('assembled')
      expect(finalManifest.last_ok_status).toBe('assembled')

      const reportPath = join(workspace, 'assemble_report.json')
      expect(existsSync(reportPath)).toBe(true)
      const report = JSON.parse(readFileSync(reportPath, 'utf8')) as { shots: Array<{ id: string; content_pixels: number }> }
      expect(report.shots.length).toBe(2)
      for (const shot of report.shots) {
        expect(shot.content_pixels).toBeGreaterThan(0)
      }
    } finally {
      await root.fiber.dispose()
    }
  }, 180_000)
})

describe('assembly-stage mechanical regression (fallback path)', () => {
  it('falls back to the safety-net composition when the subagent fails twice, and stage_assemble_finalize still succeeds', async () => {
    const root = await bootHarness()
    try {
      const tools = root.get('tools')
      expect(tools).toBeDefined()

      const init = await initWorkspaceAtVisuals(tools!, 'assembly-fallback-test')
      const workspace = init.workspace
      const manifestPath = join(workspace, 'run_manifest.json')

      const controller = new AbortController()
      const prepareResult = await tools!.execute({
        callId: 'asm-fb-prepare' as never,
        name: 'stage_assemble_prepare',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(prepareResult.isError).toBe(false)

      const briefPath = join(workspace, 'assembly_brief.json')
      const brief = JSON.parse(readFileSync(briefPath, 'utf8')) as AssemblyBrief

      const workspaceId = init.run_id
      const realSubagents = root.get('subagents')
      expect(realSubagents).toBeDefined()
      const originalStart = realSubagents!.start.bind(realSubagents)
      // Always fails — never touches write_composition_file/render_composition — proving the
      // deterministic safety net (`renderFallbackComposition`) alone satisfies the duration law
      // and never-blank check.
      realSubagents!.start = (async () => ({
        id: 'fake-child-assembly-fail' as never,
        localAgent: undefined,
        result: Promise.resolve({
          output: [], stopReason: 'error' as const, diagnostic: 'test stub: forced failure',
        }),
        dispose: async () => {},
      })) as typeof realSubagents.start

      let authorValue: { mp4_path: string; attempts: number; used_fallback: boolean }
      try {
        const authorResult = await tools!.execute({
          callId: 'asm-fb-author' as never,
          name: 'author_assembly_composition',
          arguments: { assembly_brief: brief, workspace_id: workspaceId, workspace },
          agent: {} as never,
          signal: controller.signal,
        })
        expect(authorResult.isError).toBe(false)
        authorValue = (authorResult as { value: typeof authorValue }).value
        expect(authorValue.used_fallback).toBe(true)
        expect(authorValue.attempts).toBe(2)
        expect(existsSync(authorValue.mp4_path)).toBe(true)
      } finally {
        realSubagents!.start = originalStart
      }

      const finalizeResult = await tools!.execute({
        callId: 'asm-fb-finalize' as never,
        name: 'stage_assemble_finalize',
        arguments: { workspace },
        signal: controller.signal,
      })
      expect(finalizeResult.isError).toBe(false)

      const finalManifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { status: string; last_ok_status: string }
      expect(finalManifest.status).toBe('assembled')
      expect(finalManifest.last_ok_status).toBe('assembled')
    } finally {
      await root.fiber.dispose()
    }
  }, 180_000)
})
