import { CallId, LlmAdapter, LlmError, attributionHeaders } from '@deepseek-ai/dsh-llm'
import type { GenerateOptions, Message, StreamChunk } from '@deepseek-ai/dsh-llm'

export interface OllamaAdapterOptions {
  /** Resolved once per stream call, so a config change reaches the next request. */
  baseURL: () => string
}

interface OllamaWireMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

function textOf(message: Message): string {
  return message.content
    .filter((block): block is { type: 'text'; text: string } => block.type === 'text')
    .map(block => block.text)
    .join('')
}

function toWireMessages(options: GenerateOptions): OllamaWireMessage[] {
  const wire: OllamaWireMessage[] = []
  if (options.system !== undefined) wire.push({ role: 'system', content: options.system })
  for (const message of options.messages) {
    // `Message.role` is `'system' | 'user' | 'assistant'` in this installed version of
    // @deepseek-ai/dsh-llm — there is no `'tool'` role. Tool results arrive as role
    // `'user'` messages with a `ToolMessageSource`, so no separate branch is needed.
    const role = message.role === 'assistant' ? 'assistant' : message.role === 'system' ? 'system' : 'user'
    wire.push({ role, content: textOf(message) })
  }
  return wire
}

/**
 * `LlmAdapter` for a local Ollama server's native `/api/chat` endpoint
 * (NDJSON streaming, one JSON object per line, no SSE framing). Text-only:
 * image content blocks are dropped by {@link textOf} rather than sent, since
 * this first version only needs to prove a text round-trip.
 */
export class OllamaAdapter extends LlmAdapter {
  // Not a TS constructor-parameter-property shorthand: Cordis's plugin loader
  // dynamically `import()`s this package's raw `src/index.ts` at runtime (its
  // own README's usage example loads plugins by module specifier, not from a
  // prebuilt `lib/`), and under Node's native type-stripping loader (no
  // bundler/transform involved) a parameter property throws
  // `TypeScript parameter property is not supported in strip-only mode` —
  // confirmed empirically while getting Task 4's round-trip test to boot.
  // Plain field assignment strips cleanly with no behavior change.
  private readonly config: OllamaAdapterOptions

  constructor(config: OllamaAdapterOptions) {
    super()
    this.config = config
  }

  override providerInfo(provider: string) {
    return { id: provider, name: 'Ollama (local)' }
  }

  async * stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    let response: Response
    try {
      response = await fetch(`${this.config.baseURL()}/api/chat`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...attributionHeaders() },
        body: JSON.stringify({
          model: options.model,
          stream: true,
          messages: toWireMessages(options),
          ...options.temperature === undefined ? {} : { options: { temperature: options.temperature } },
          ...options.tools === undefined ? {} : {
            tools: options.tools.map(tool => ({
              type: 'function' as const,
              function: { name: tool.name, description: tool.description, parameters: tool.parameters },
            })),
          },
        }),
        signal: options.signal,
      })
    } catch (error: unknown) {
      if (options.signal?.aborted) {
        yield { type: 'finish', reason: { kind: 'aborted', failure: { code: 'ABORTED', message: 'Ollama request aborted' } } }
        return
      }
      yield {
        type: 'finish',
        reason: { kind: 'error', failure: { code: 'TRANSPORT', message: `Ollama request to ${this.config.baseURL()} failed: ${String(error)}` } },
      }
      return
    }

    if (!response.ok || !response.body) {
      const body = await response.text().catch(() => '')
      yield {
        type: 'finish',
        reason: {
          kind: 'error',
          failure: { code: `HTTP_${response.status}`, message: `Ollama API error (HTTP ${response.status}): ${body}`, status: response.status },
        },
      }
      return
    }

    yield { type: 'block-start', index: 0, blockType: 'text' }
    let text = ''
    let nextIndex = 1
    let sawToolCalls = false
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let newlineIndex: number
        while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newlineIndex).trim()
          buffer = buffer.slice(newlineIndex + 1)
          if (line.length === 0) continue
          const parsed = JSON.parse(line) as {
            message?: {
              content?: string
              tool_calls?: { function: { name: string; arguments: Record<string, unknown> } }[]
            }
            done?: boolean
          }
          const delta = parsed.message?.content ?? ''
          if (delta.length > 0) {
            text += delta
            yield { type: 'text-delta', index: 0, text: delta }
          }
          const toolCalls = parsed.message?.tool_calls
          if (toolCalls !== undefined && toolCalls.length > 0) {
            sawToolCalls = true
            for (const call of toolCalls) {
              const index = nextIndex++
              const id = CallId(`ollama-${index}`)
              const argumentsJson = JSON.stringify(call.function.arguments)
              yield { type: 'block-start', index, blockType: 'tool-call' }
              yield { type: 'tool-call-delta', index, id, name: call.function.name, argumentsDelta: argumentsJson }
              yield {
                type: 'block-end',
                index,
                block: { type: 'tool-call', id, name: call.function.name, arguments: argumentsJson },
              }
            }
          }
        }
      }
    } catch (error: unknown) {
      throw new LlmError(`Ollama stream read failed: ${String(error)}`, 'TRANSPORT', { cause: error })
    }
    yield { type: 'block-end', index: 0, block: { type: 'text', text } }
    yield { type: 'finish', reason: sawToolCalls ? { kind: 'tool-calls' } : { kind: 'stop' } }
  }
}
