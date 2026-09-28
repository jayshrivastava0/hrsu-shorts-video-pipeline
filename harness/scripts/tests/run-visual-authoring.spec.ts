import { describe, expect, it, vi } from 'vitest'
import { buildPrompt, MAX_ATTEMPTS, readTurnTrace, runOneTurn, traceSummary } from '../run-visual-authoring.mts'

/** A minimal fake agent: each `followup()` call appends the next batch of `eventsPerCall`,
 * assigning real sequential `seq` numbers as a real session would, so `session.seq` (read BEFORE
 * a turn) and each event's `seq` behave exactly like the real primitive `readTurnTrace`/
 * `runOneTurn` are built against. */
function makeFakeAgent(eventsPerCall: { type: string; data: unknown }[][]) {
  const events: { seq: number; type: string; data: unknown }[] = []
  let call = 0
  return {
    session: { get seq() { return events.length }, events },
    followup: vi.fn(() => {
      for (const e of eventsPerCall[call]) events.push({ seq: events.length, ...e })
      call += 1
    }),
    whenIdle: vi.fn(async () => {}),
  }
}

describe('buildPrompt', () => {
  it('visuals phase names stage_visuals_prepare/author_visual_scene/stage_visuals_finalize and forbids earlier stages', () => {
    const prompt = buildPrompt('visuals', '/abs/workspace')
    expect(prompt).toContain('/abs/workspace')
    expect(prompt).toContain('stage_visuals_prepare')
    expect(prompt).toContain('author_visual_scene')
    expect(prompt).toContain('stage_visuals_finalize')
    expect(prompt).toContain('Do not call stage_init')
  })

  // The agent has no file-read tool, so the prompt must point it at the `briefs`/`run_id`
  // stage_visuals_prepare now returns inline rather than at the shot_briefs.json filename.
  it('visuals phase points the agent at the inline briefs and run_id, not the file', () => {
    const prompt = buildPrompt('visuals', '/abs/workspace')
    expect(prompt).toContain('briefs')
    expect(prompt).toContain('run_id')
    expect(prompt).toContain('workspace_id')
  })

  it('assemble phase names stage_assemble_prepare/author_assembly_composition/stage_assemble_finalize', () => {
    const prompt = buildPrompt('assemble', '/abs/workspace')
    expect(prompt).toContain('stage_assemble_prepare')
    expect(prompt).toContain('author_assembly_composition')
    expect(prompt).toContain('stage_assemble_finalize')
  })

  it('assemble phase points the agent at the inline brief and run_id, not the file', () => {
    const prompt = buildPrompt('assemble', '/abs/workspace')
    expect(prompt).toContain('brief')
    expect(prompt).toContain('run_id')
    expect(prompt).toContain('workspace_id')
  })
})

describe('readTurnTrace', () => {
  it('extracts tool calls, result outcomes, and the final assistant text since a given seq', () => {
    const agent = makeFakeAgent([[
      { type: 'tool/call', data: { name: 'stage_visuals_prepare' } },
      { type: 'tool/result', data: {} },
      { type: 'tool/call', data: { name: 'author_visual_scene' } },
      { type: 'tool/result', data: { error: { name: 'ToolError', code: 'E_BAD_ARGS' } } },
      { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'stuck' }] } } },
    ]])
    const sinceSeq = agent.session.seq
    agent.followup()
    const trace = readTurnTrace(agent, sinceSeq)
    expect(trace.toolCalls).toEqual([
      'call:stage_visuals_prepare', 'result:ok',
      'call:author_visual_scene', 'result:ERROR(ToolError:E_BAD_ARGS)',
    ])
    expect(trace.finalText).toBe('stuck')
  })

  it('ignores events before sinceSeq', () => {
    const agent = makeFakeAgent([
      [{ type: 'tool/call', data: { name: 'stage_init' } }],
      [{ type: 'tool/call', data: { name: 'stage_visuals_prepare' } }],
    ])
    agent.followup() // events from a prior, unrelated turn
    const sinceSeq = agent.session.seq
    agent.followup()
    const trace = readTurnTrace(agent, sinceSeq)
    expect(trace.toolCalls).toEqual(['call:stage_visuals_prepare'])
  })
})

describe('runOneTurn', () => {
  it('sends the prompt via followup and returns the trace of what happened during that turn only', async () => {
    const agent = makeFakeAgent([[
      { type: 'tool/call', data: { name: 'stage_assemble_prepare' } },
      { type: 'tool/result', data: {} },
    ]])
    const trace = await runOneTurn(agent, 'do the assemble phase')
    expect(agent.followup).toHaveBeenCalledTimes(1)
    const sentMessage = agent.followup.mock.calls[0][0]
    expect(sentMessage.content[0].text).toBe('do the assemble phase')
    expect(trace.toolCalls).toEqual(['call:stage_assemble_prepare', 'result:ok'])
  })
})

describe('MAX_ATTEMPTS retry contract', () => {
  // main() itself boots the real harness (like buildPrompt's siblings, not unit-testable without
  // a full cordis boot — see roundtrip.e2e.ts for that level of test). This exercises the same
  // retry loop main() runs, using the exported primitives directly, so the retry CONTRACT is
  // covered without needing a real agent.
  it('retries once with a nudge naming the prior trace when the first turn does not reach the target status', async () => {
    const agent = makeFakeAgent([
      [{ type: 'tool/call', data: { name: 'stage_assemble_prepare' } }, { type: 'tool/result', data: {} }],
      [{ type: 'tool/call', data: { name: 'stage_assemble_finalize' } }, { type: 'tool/result', data: {} }],
    ])
    let status = 'visuals'
    const readStatus = () => status

    let trace = await runOneTurn(agent, 'initial prompt')
    for (let attempt = 2; attempt <= MAX_ATTEMPTS && readStatus() !== 'assembled'; attempt++) {
      const nudge = `previous attempt did not finish\n\n${traceSummary(trace)}\n\nattempt ${attempt} of ${MAX_ATTEMPTS}`
      trace = await runOneTurn(agent, nudge)
      status = 'assembled' // simulates the second turn actually finishing the sequence
    }

    expect(agent.followup).toHaveBeenCalledTimes(2)
    const nudgeSent = agent.followup.mock.calls[1][0].content[0].text as string
    expect(nudgeSent).toContain('call:stage_assemble_prepare')
    expect(nudgeSent).toContain(`attempt 2 of ${MAX_ATTEMPTS}`)
    expect(status).toBe('assembled')
  })

  it('gives up after MAX_ATTEMPTS without retrying a third time', async () => {
    const agent = makeFakeAgent([
      [{ type: 'tool/call', data: { name: 'stage_assemble_prepare' } }],
      [{ type: 'tool/call', data: { name: 'stage_assemble_prepare' } }],
    ])
    const status = 'visuals' // never reaches "assembled" in this test

    let trace = await runOneTurn(agent, 'initial prompt')
    for (let attempt = 2; attempt <= MAX_ATTEMPTS && status !== 'assembled'; attempt++) {
      trace = await runOneTurn(agent, `attempt ${attempt} of ${MAX_ATTEMPTS}\n${traceSummary(trace)}`)
    }

    expect(agent.followup).toHaveBeenCalledTimes(MAX_ATTEMPTS)
  })
})
