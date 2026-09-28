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

// Live runs showed the orchestrator agent's turn ending early — without finishing its documented
// stage sequence, and without a tool-level error — roughly 2 times in 5 real attempts (both
// phases combined). Nothing in the manifest or a raised error explains why: the agent simply
// stops. This is the same "one retry with the specific failure fed back" contract
// author_visual_scene/author_assembly_composition already use for their own per-shot/per-
// composition subagent, applied one level up, at the whole-turn granularity — 2 total attempts,
// not indefinite retries.
export const MAX_ATTEMPTS = 2

export interface TurnTrace {
  toolCalls: string[]
  finalText: string
}

/** Reads agent.session.events for everything appended since `sinceSeq` — the same event log
 * roundtrip.e2e.ts already reads for its own assertions — into a compact trace: which tools were
 * called, whether each result errored, and the agent's own final text. Diagnostic-only; never
 * used to decide success (that's always the on-disk manifest, per the existing "trust the
 * artifact, not the claim" convention this codebase follows elsewhere). */
export function readTurnTrace(agent: { session: { events: { seq: number; type: string; data: unknown }[] } }, sinceSeq: number): TurnTrace {
  const toolCalls: string[] = []
  let finalText = ''
  for (const event of agent.session.events) {
    if (event.seq < sinceSeq) continue
    if (event.type === 'tool/call') {
      const data = event.data as { name?: string }
      toolCalls.push(`call:${data.name ?? '?'}`)
    } else if (event.type === 'tool/result') {
      const data = event.data as { error?: { name: string; code: string } }
      toolCalls.push(data.error ? `result:ERROR(${data.error.name}:${data.error.code})` : 'result:ok')
    } else if (event.type === 'assistant/message') {
      const data = event.data as { message: { content: { type: string; text?: string }[] } }
      const text = data.message.content
        .filter((block) => block.type === 'text')
        .map((block) => block.text ?? '')
        .join('')
      if (text !== '') finalText = text
    }
  }
  return { toolCalls, finalText }
}

export function traceSummary(trace: TurnTrace): string {
  return `agent turn trace: ${trace.toolCalls.join(' -> ') || '(no tool calls made)'}\n` +
    `agent final text: ${trace.finalText || '(none)'}`
}

/** One real turn: send `prompt`, wait for the agent to go idle, and return what it actually did.
 * Never itself decides success/failure — the caller re-reads the manifest after every attempt. */
export async function runOneTurn(
  agent: { session: { seq: number; events: { seq: number; type: string; data: unknown }[] }; followup: (m: ReturnType<typeof createUserMessage>) => void; whenIdle: () => Promise<void> },
  prompt: string,
): Promise<TurnTrace> {
  const sinceSeq = agent.session.seq
  agent.followup(createUserMessage({ content: [{ type: 'text', text: prompt }], source: { kind: 'user' } }))
  await agent.whenIdle()
  return readTurnTrace(agent, sinceSeq)
}

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

  const readManifest = () => JSON.parse(readFileSync(join(workspace, 'run_manifest.json'), 'utf8')) as
    { last_ok_status: string; error?: string }

  let trace = await runOneTurn(agent!, buildPrompt(phase, workspace))
  let manifest = readManifest()

  for (let attempt = 2; attempt <= MAX_ATTEMPTS && manifest.last_ok_status !== EXPECTED_STATUS[phase]; attempt++) {
    const nudge = `Your previous turn on this same task did not finish. Here is exactly what ` +
      `happened:\n\n${traceSummary(trace)}\n\nThe workspace is still at ` +
      `last_ok_status="${manifest.last_ok_status}" (target: "${EXPECTED_STATUS[phase]}"). This ` +
      `is your final attempt (${attempt} of ${MAX_ATTEMPTS}) — continue from wherever you left ` +
      `off (do not repeat a tool call whose result above already shows success) and finish the ` +
      `documented sequence for this phase.`
    trace = await runOneTurn(agent!, nudge)
    manifest = readManifest()
  }

  await root.fiber.dispose()

  if (manifest.last_ok_status !== EXPECTED_STATUS[phase]) {
    console.error(
      `run-visual-authoring: expected last_ok_status="${EXPECTED_STATUS[phase]}", got ` +
      `"${manifest.last_ok_status}" after ${MAX_ATTEMPTS} attempts (error: ` +
      `${manifest.error ?? 'none recorded'})\nlast attempt's ${traceSummary(trace)}`,
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
