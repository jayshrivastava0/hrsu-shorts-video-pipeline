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

  it('sends the tools array in the request body when options.tools is set', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
      { message: { role: 'assistant', content: '' }, done: true },
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const adapter = new OllamaAdapter({ baseURL: () => 'http://localhost:11434' })
    const chunks = []
    for await (const chunk of adapter.stream({
      provider: 'ollama-local',
      model: 'gemma4:31b-cloud',
      messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'run stage_init' }], source: { kind: 'user' } }],
      tools: [{ name: 'stage_init', description: 'Start a new run.', parameters: { type: 'object', properties: { blog_url: { type: 'string' } }, required: ['blog_url'] } }],
    } as never)) {
      chunks.push(chunk)
    }

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(options.body as string)
    expect(body.tools).toEqual([{
      type: 'function',
      function: {
        name: 'stage_init',
        description: 'Start a new run.',
        parameters: { type: 'object', properties: { blog_url: { type: 'string' } }, required: ['blog_url'] },
      },
    }])
  })

  it('parses a tool_calls response into tool-call-delta chunks and a tool-calls finish', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
      {
        message: {
          role: 'assistant',
          content: '',
          tool_calls: [{ function: { name: 'stage_init', arguments: { blog_url: 'https://example.com/post' } } }],
        },
        done: true,
      },
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const adapter = new OllamaAdapter({ baseURL: () => 'http://localhost:11434' })
    const chunks = []
    for await (const chunk of adapter.stream({
      provider: 'ollama-local',
      model: 'gemma4:31b-cloud',
      messages: [{ id: 'm1', role: 'user', content: [{ type: 'text', text: 'start a run' }], source: { kind: 'user' } }],
      tools: [{ name: 'stage_init', description: 'Start a new run.', parameters: {} }],
    } as never)) {
      chunks.push(chunk)
    }

    const toolCallDelta = chunks.find(c => c.type === 'tool-call-delta') as never as
      { type: string; name?: string; argumentsDelta: string }
    expect(toolCallDelta).toBeDefined()
    expect(toolCallDelta.name).toBe('stage_init')
    expect(JSON.parse(toolCallDelta.argumentsDelta)).toEqual({ blog_url: 'https://example.com/post' })

    const finish = chunks.at(-1) as never as { type: string; reason: { kind: string } }
    expect(finish.type).toBe('finish')
    expect(finish.reason.kind).toBe('tool-calls')
  })

  it('round-trips a tool-call/tool-result history so the real data reaches the wire request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
      { message: { role: 'assistant', content: 'done' }, done: true },
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const adapter = new OllamaAdapter({ baseURL: () => 'http://localhost:11434' })
    const chunks = []
    for await (const chunk of adapter.stream({
      provider: 'ollama-local',
      model: 'gemma4:31b-cloud',
      messages: [
        { id: 'm1', role: 'user', content: [{ type: 'text', text: 'start a run' }], source: { kind: 'user' } },
        {
          id: 'm2',
          role: 'assistant',
          content: [{ type: 'tool-call', id: 'call-1', name: 'stage_init', arguments: JSON.stringify({ blog_url: 'https://example.com/post' }) }],
          source: { kind: 'model', provider: 'ollama-local', model: 'gemma4:31b-cloud' },
        },
        {
          id: 'm3',
          role: 'user',
          content: [{
            type: 'tool-result',
            toolCallId: 'call-1',
            content: [{ type: 'text', text: JSON.stringify({ workspace: '/ws/run-42' }) }],
          }],
          source: { kind: 'tool', callId: 'call-1' },
        },
      ],
    } as never)) {
      chunks.push(chunk)
    }

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(options.body as string) as { messages: { role: string; content: string; tool_calls?: unknown[] }[] }

    const assistantMessage = body.messages.find(m => m.role === 'assistant')
    expect(assistantMessage?.tool_calls).toEqual([
      { function: { name: 'stage_init', arguments: { blog_url: 'https://example.com/post' } } },
    ])

    const toolMessage = body.messages.find(m => m.role === 'tool')
    expect(toolMessage).toBeDefined()
    expect(toolMessage?.content).toContain('/ws/run-42')
    expect(JSON.parse(toolMessage!.content)).toEqual({ workspace: '/ws/run-42' })
  })

  it('reports a max-tokens finish when Ollama truncates the response (done_reason: length)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
      { message: { role: 'assistant', content: 'partial output that got cut off' }, done: true, done_reason: 'length' },
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

    const finish = chunks.at(-1) as never as { type: string; reason: { kind: string } }
    expect(finish.type).toBe('finish')
    expect(finish.reason.kind).toBe('max-tokens')
  })
})
