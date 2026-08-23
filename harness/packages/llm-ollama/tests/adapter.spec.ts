import { describe, expect, it, vi } from 'vitest'
import { OllamaAdapter } from '../src/adapter.ts'

function ndjsonResponse(lines: object[]): Response {
  const body = lines.map(line => JSON.stringify(line)).join('\n') + '\n'
  return new Response(body, { status: 200 })
}

describe('OllamaAdapter', () => {
  it('streams text deltas then a stop finish', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
      { message: { role: 'assistant', content: 'Hel' }, done: false },
      { message: { role: 'assistant', content: 'lo' }, done: false },
      { message: { role: 'assistant', content: '' }, done: true },
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const adapter = new OllamaAdapter({ baseURL: () => 'http://localhost:11434' })
    const chunks = []
    for await (const chunk of adapter.stream({
      provider: 'ollama-local',
      model: 'gemma3:4b',
      messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'hi' }], source: { kind: 'user' } }],
    } as never)) {
      chunks.push(chunk)
    }

    expect(chunks[0]).toEqual({ type: 'block-start', index: 0, blockType: 'text' })
    expect(chunks.filter(c => c.type === 'text-delta').map(c => (c as never as { text: string }).text)).toEqual(['Hel', 'lo'])
    expect(chunks.at(-1)).toEqual({ type: 'finish', reason: { kind: 'stop' } })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:11434/api/chat',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('emits an error finish on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('server error', { status: 500 })))
    const adapter = new OllamaAdapter({ baseURL: () => 'http://localhost:11434' })
    const chunks = []
    for await (const chunk of adapter.stream({
      provider: 'ollama-local',
      model: 'gemma3:4b',
      messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'hi' }], source: { kind: 'user' } }],
    } as never)) {
      chunks.push(chunk)
    }
    expect(chunks).toHaveLength(1)
    const finish = chunks[0] as never as { type: string; reason: { kind: string; failure: { code: string } } }
    expect(finish.type).toBe('finish')
    expect(finish.reason.kind).toBe('error')
    expect(finish.reason.failure.code).toBe('HTTP_500')
  })
})
