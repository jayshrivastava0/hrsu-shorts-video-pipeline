import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

export type Phase = 'visuals' | 'assemble'

/**
 * One real, headless turn of the top-level harness agent, scoped to exactly the sub-sequence
 * `system_prompt.md` already documents for this phase — the agent is told explicitly not to
 * touch any earlier stage, since a workspace this script is invoked against has already had
 * ingest/facts/script/shotlist/audio (or, for the assemble phase, visuals too) run for real by
 * `python -m shorts_engine`'s own STAGE_FUNCS before it shells out to this script.
 */
export function buildPrompt(phase: Phase, workspace: string): string {
  if (phase === 'visuals') {
    return `Continue the shorts-video pipeline in the existing workspace at ${workspace}. ` +
      'It is currently at last_ok_status="audio". Call stage_visuals_prepare with this ' +
      'workspace. Its result contains "run_id" and "briefs" (the full array of shot briefs) ' +
      'inline — use those directly; you have no file-read tool, so do not try to open ' +
      'shot_briefs.json. Then call author_visual_scene once for EVERY entry in "briefs", ' +
      'passing that entry as shot_brief, the result\'s "run_id" as workspace_id, and this ' +
      `workspace path (${workspace}) as workspace. Do not call stage_visuals_finalize until ` +
      'you have called author_visual_scene for every brief; then call stage_visuals_finalize ' +
      'once. Do not call stage_init or any stage before stage_visuals_prepare. Report DONE ' +
      'when stage_visuals_finalize succeeds, or report the exact error if any step fails.'
  }
  return `Continue the shorts-video pipeline in the existing workspace at ${workspace}. ` +
    'It is currently at last_ok_status="visuals". Call stage_assemble_prepare with this ' +
    'workspace. Its result contains "run_id" and "brief" (the full assembly brief object) ' +
    'inline — use those directly; you have no file-read tool, so do not try to open ' +
    'assembly_brief.json. Then call author_assembly_composition ONCE, passing the result\'s ' +
    '"brief" as assembly_brief, its "run_id" as workspace_id, and this workspace path ' +
    `(${workspace}) as workspace. Then call stage_assemble_finalize once. Do not call ` +
    'stage_init or any stage before stage_assemble_prepare. Report DONE when ' +
    'stage_assemble_finalize succeeds, or report the exact error if any step fails.'
}

const EXPECTED_STATUS: Record<Phase, string> = { visuals: 'visuals', assemble: 'assembled' }

async function main(): Promise<void> {
  const [, , phaseArg, workspace] = process.argv
  if ((phaseArg !== 'visuals' && phaseArg !== 'assemble') || !workspace) {
    console.error('usage: run-visual-authoring.mts <visuals|assemble> <absolute-workspace-path>')
    process.exit(1)
  }
  const phase = phaseArg as Phase

  const root = new Context()
  root.baseUrl = import.meta.url
  await root.plugin(Loader, { baseUrl: import.meta.url })
  await root.plugin(Include, { path: '../cordis.yml', initial: [] })
  await root.get('loader')?.await()

  const agents = root.get('agents')
  const agent = agents!.list()[0]
  await agent!.whenIdle()

  agent!.followup(createUserMessage({
    content: [{ type: 'text', text: buildPrompt(phase, workspace) }],
    source: { kind: 'user' },
  }))
  await agent!.whenIdle()

  await root.fiber.dispose()

  const manifest = JSON.parse(readFileSync(join(workspace, 'run_manifest.json'), 'utf8')) as
    { last_ok_status: string; error?: string }
  if (manifest.last_ok_status !== EXPECTED_STATUS[phase]) {
    console.error(
      `run-visual-authoring: expected last_ok_status="${EXPECTED_STATUS[phase]}", got ` +
      `"${manifest.last_ok_status}" (error: ${manifest.error ?? 'none recorded'})`,
    )
    process.exit(1)
  }
  process.exit(0)
}

// Only boot the harness when this module is the process entry point. Without this guard the unit
// test's `import { buildPrompt }` would boot cordis on import and then `process.exit(1)` on the
// missing CLI args, killing the Vitest worker instead of running the assertions.
if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main().catch((err) => {
    console.error(err)
    process.exit(1)
  })
}
