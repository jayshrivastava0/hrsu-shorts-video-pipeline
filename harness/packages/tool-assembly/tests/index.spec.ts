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
    registerOutputPath: vi.fn(),
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
    expect(deps.subagents.start).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ toolFilter: { allow: ['write_composition_file', 'render_composition'] } }),
    )
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

  test('recovers on attempt 2 after attempt 1 fails, threading the failure reason into the retry prompt', async () => {
    const workspace = mkdtempSync(join(tmpdir(), 'assemble-ws-'))
    const outputPath = join(workspace, 'video_short.mp4')
    const start = vi.fn()
      .mockResolvedValueOnce({
        result: Promise.resolve({ output: [], stopReason: 'error', diagnostic: 'render failed: bad html' } as SubagentResult),
        dispose: vi.fn(async () => {}),
      })
      .mockImplementationOnce(async () => {
        // Simulate the child's second-attempt tool calls actually producing the output file.
        writeFileSync(outputPath, 'fake mp4 bytes')
        return {
          result: Promise.resolve({ output: [], stopReason: 'completed' } as SubagentResult),
          dispose: vi.fn(async () => {}),
        }
      })
    const deps = makeDeps({ subagents: { start } })
    const result = await authorAssemblyComposition(baseArgs(workspace), deps)

    expect(result.used_fallback).toBe(false)
    expect(result.attempts).toBe(2)
    expect(start).toHaveBeenCalledTimes(2)

    const secondCallRequest = start.mock.calls[1][1] as { prompt: Array<{ type: string; text: string }> }
    const secondPromptText = secondCallRequest.prompt.map((block) => block.text).join('\n')
    expect(secondPromptText).toContain('render failed: bad html')
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
