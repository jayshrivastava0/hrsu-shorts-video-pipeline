import { describe, expect, it, vi } from 'vitest'
import { join } from 'node:path'
import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
import { authorVisualScene, pickFallbackCaption, apply, type ShotBrief } from '../src/index.ts'

/**
 * Minimal fake Cordis `Context` sufficient for exercising `apply()`'s tool registration: a
 * `ctx.tools.register` that records definitions in a map, and a `getRegistered` test helper (not
 * part of the real `ToolRuntime` API) that resolves a registered tool's `execute()` with a
 * throwaway `ToolRunContext`. `apply()` itself only touches `ctx.tools` (and, inside
 * `author_visual_scene`'s own `execute()`, `ctx.subagents` — not exercised by this test), so
 * nothing else needs stubbing.
 */
function makeFakeCtx() {
  const registry = new Map<string, ToolDefinition>()
  return {
    tools: {
      register(def: ToolDefinition) {
        registry.set(def.name, def)
        return () => registry.delete(def.name)
      },
      getRegistered(name: string) {
        const def = registry.get(name)
        if (def === undefined) throw new Error(`tool not registered: ${name}`)
        return {
          execute: (args: Record<string, unknown>) =>
            def.execute(args, {
              callId: 'test-call' as never,
              rootCallId: 'test-call' as never,
              name,
              arguments: args,
              signal: new AbortController().signal,
              token: Symbol('test-token') as never,
              deferContext: () => {},
              concludeTurn: () => {},
            }),
        }
      },
    },
  } as never
}

const HEADLINE_BRIEF: ShotBrief = {
  shot_id: '3',
  beat: 'hook',
  type: 'HEADLINE_CARD',
  payload: { text: 'Cold weather pours do not have to wait for spring.' },
  duration_s: 2.5,
  fade_in_s: 0.4,
}

const STAT_BRIEF: ShotBrief = {
  shot_id: '4',
  beat: 'body',
  type: 'STAT_CARD',
  payload: { stat: '38%' },
  duration_s: 3,
  fade_in_s: 0,
}

const NO_TEXT_FIELD_BRIEF: ShotBrief = {
  shot_id: '5',
  beat: 'transition',
  type: 'B_ROLL',
  payload: {},
  duration_s: 1.5,
  fade_in_s: 0,
}

function fakeAgent(): never {
  // A `SubagentStartRequest.parent` is typed `Agent`, not `Agent | undefined` — the real API
  // requires it. Tests only need a distinct object identity (never actually dereferenced by the
  // mocked `subagents.start`), so an opaque cast stands in for a real `Agent`.
  return {} as never
}

function completedResult(): { output: never[]; stopReason: 'completed' } {
  return { output: [], stopReason: 'completed' }
}

function erroredResult(diagnostic: string): { output: never[]; stopReason: 'error'; diagnostic: string } {
  return { output: [], stopReason: 'error', diagnostic }
}

describe('pickFallbackCaption', () => {
  it('uses payload.text for HEADLINE_CARD', () => {
    expect(pickFallbackCaption(HEADLINE_BRIEF)).toBe('Cold weather pours do not have to wait for spring.')
  })

  it('uses payload.stat for STAT_CARD', () => {
    expect(pickFallbackCaption(STAT_BRIEF)).toBe('38%')
  })

  it('falls back to beat when the type has no obvious single text field', () => {
    expect(pickFallbackCaption(NO_TEXT_FIELD_BRIEF)).toBe('transition')
  })
})

describe('authorVisualScene', () => {
  it('never touches the fallback path when the subagent-spawn succeeds on the first attempt', async () => {
    const dispose = vi.fn().mockResolvedValue(undefined)
    const start = vi.fn().mockResolvedValue({
      id: 'child-1',
      localAgent: undefined,
      result: Promise.resolve(completedResult()),
      dispose,
    })
    const renderFallbackScene = vi.fn()
    const existsSyncMock = vi.fn().mockReturnValue(true)
    const registerOutputPath = vi.fn()

    const result = await authorVisualScene(
      { shot_brief: HEADLINE_BRIEF, workspace_id: 'run-42', workspace: '/workspace' },
      {
        subagents: { start },
        subagentProviderName: 'spawn',
        agentOptions: { provider: 'ollama-local', model: 'kimi-k2.7-code', maxTokens: 8192 },
        agent: fakeAgent(),
        signal: new AbortController().signal,
        projectRoot: '/project',
        existsSync: existsSyncMock,
        renderFallbackScene,
        registerOutputPath,
      },
    )

    expect(start).toHaveBeenCalledTimes(1)
    expect(start).toHaveBeenCalledWith('spawn', expect.objectContaining({
      agentOptions: { provider: 'ollama-local', model: 'kimi-k2.7-code', maxTokens: 8192 },
      toolFilter: { allow: ['write_scene_file', 'render_scene', 'request_broll'] },
    }))
    expect(dispose).toHaveBeenCalledTimes(1)
    expect(renderFallbackScene).not.toHaveBeenCalled()
    // The mp4 must land under the Python run WORKSPACE's shots/ dir (matching what
    // cmd_visuals_finalize expects), never under projectRoot/output/ (Fix 1).
    const expectedMp4Path = join('/workspace', 'shots', 'shot_3.mp4')
    expect(registerOutputPath).toHaveBeenCalledWith('run-42', '3', expectedMp4Path)
    expect(result).toEqual({
      mp4_path: expectedMp4Path,
      attempts: 1,
      used_fallback: false,
    })
  })

  it('falls back after two failed subagent-spawn attempts and calls renderFallbackScene with the right caption and duration', async () => {
    const dispose = vi.fn().mockResolvedValue(undefined)
    const start = vi.fn().mockResolvedValue({
      id: 'child-1',
      localAgent: undefined,
      result: Promise.resolve(erroredResult('render_scene: missing data-duration')),
      dispose,
    })
    const renderFallbackScene = vi.fn().mockResolvedValue('/workspace/shots/shot_4.mp4')
    const existsSyncMock = vi.fn().mockReturnValue(false)
    const registerOutputPath = vi.fn()

    const result = await authorVisualScene(
      { shot_brief: STAT_BRIEF, workspace_id: 'run-42', workspace: '/workspace' },
      {
        subagents: { start },
        subagentProviderName: 'spawn',
        agentOptions: { provider: 'ollama-local', model: 'kimi-k2.7-code', maxTokens: 8192 },
        agent: fakeAgent(),
        signal: new AbortController().signal,
        projectRoot: '/project',
        existsSync: existsSyncMock,
        renderFallbackScene,
        registerOutputPath,
      },
    )

    expect(start).toHaveBeenCalledTimes(2)
    expect(dispose).toHaveBeenCalledTimes(2)
    // Second attempt's prompt carries the first attempt's failure reason forward.
    const secondCallPrompt = start.mock.calls[1][1].prompt[0].text as string
    expect(secondCallPrompt).toContain('render_scene: missing data-duration')
    const expectedMp4Path = join('/workspace', 'shots', 'shot_4.mp4')
    expect(renderFallbackScene).toHaveBeenCalledWith(
      'run-42',
      '4',
      '/project',
      expectedMp4Path,
      '38%',
      STAT_BRIEF.duration_s,
    )
    expect(result).toEqual({
      mp4_path: expectedMp4Path,
      attempts: 2,
      used_fallback: true,
    })
  })

  it('treats a completed run whose output file never materialized as a failure (does not trust claimed success)', async () => {
    const dispose = vi.fn().mockResolvedValue(undefined)
    const start = vi.fn().mockResolvedValue({
      id: 'child-1',
      localAgent: undefined,
      result: Promise.resolve(completedResult()),
      dispose,
    })
    const renderFallbackScene = vi.fn().mockResolvedValue('/workspace/shots/shot_3.mp4')
    // The child claims a normal stop, but the mp4 never actually landed on disk.
    const existsSyncMock = vi.fn().mockReturnValue(false)

    const result = await authorVisualScene(
      { shot_brief: HEADLINE_BRIEF, workspace_id: 'run-42', workspace: '/workspace' },
      {
        subagents: { start },
        subagentProviderName: 'spawn',
        agentOptions: { provider: 'ollama-local', model: 'kimi-k2.7-code', maxTokens: 8192 },
        agent: fakeAgent(),
        signal: new AbortController().signal,
        projectRoot: '/project',
        existsSync: existsSyncMock,
        renderFallbackScene,
        registerOutputPath: vi.fn(),
      },
    )

    expect(start).toHaveBeenCalledTimes(2)
    expect(result.used_fallback).toBe(true)
    expect(result.attempts).toBe(2)
  })

  it('throws when no parent agent is available to spawn from', async () => {
    const start = vi.fn()
    await expect(authorVisualScene(
      { shot_brief: HEADLINE_BRIEF, workspace_id: 'run-42', workspace: '/workspace' },
      {
        subagents: { start },
        subagentProviderName: 'spawn',
        agentOptions: { provider: 'ollama-local', model: 'kimi-k2.7-code', maxTokens: 8192 },
        agent: undefined,
        signal: new AbortController().signal,
        projectRoot: '/project',
        existsSync: vi.fn(),
        renderFallbackScene: vi.fn(),
        registerOutputPath: vi.fn(),
      },
    )).rejects.toThrow(/no parent agent/i)
    expect(start).not.toHaveBeenCalled()
  })
})

describe('request_broll tool', () => {
  it('invokes broll-request via runStageCli with the exact wish/narration_span/workspace', async () => {
    const runStageCli = vi.fn().mockResolvedValue({ image_path: null, focal_hint: 'center', provenance: {} })
    const ctx = makeFakeCtx()
    apply(ctx, {
      projectRoot: '/tmp/project', shortsEngineCwd: '/tmp/engine',
      agentOptions: { model: 'gemma4:31b-cloud' }, runStageCli,
    })
    const tool = ctx.tools.getRegistered('request_broll')
    await tool.execute({ workspace: '/tmp/ws', wish: 'white powder', narration_span: 'It dissolves.' })
    expect(runStageCli).toHaveBeenCalledWith(
      ['broll-request', '--workspace', '/tmp/ws', '--wish', 'white powder', '--narration-span', 'It dissolves.'],
      { cwd: '/tmp/engine' },
    )
  })
})
