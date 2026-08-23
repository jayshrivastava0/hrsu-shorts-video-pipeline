import type { Context } from '@deepseek-ai/cordis'
import { OllamaAdapter } from './adapter.ts'

export { OllamaAdapter } from './adapter.ts'
export type { OllamaAdapterOptions } from './adapter.ts'

export const name = 'llm-ollama'
export const inject = ['llm']

/** Plugin config: which provider route to register and where Ollama is listening. */
export interface Config {
  /** Provider route this adapter serves; cordis.yml's agent `provider` field must match. */
  provider?: string
  /** Ollama base URL; falls back to $OLLAMA_HOST, then the local default. */
  baseURL?: string
}

const DEFAULT_PROVIDER = 'ollama-local'
const DEFAULT_BASE_URL = 'http://localhost:11434'

export function apply(ctx: Context, config: Config): void {
  const provider = config.provider ?? DEFAULT_PROVIDER
  const baseURL = (): string => config.baseURL ?? process.env.OLLAMA_HOST ?? DEFAULT_BASE_URL
  const adapter = new OllamaAdapter({ baseURL })
  ctx.llm.registerAdapter([provider], adapter)
}
