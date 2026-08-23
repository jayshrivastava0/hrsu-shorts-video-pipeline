# DeepSeek Harness Bootstrap + Ollama Model Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a minimal, working DeepSeek Harness app inside `HRSU Shorts/harness/`, with a
custom local-model plugin that routes the harness's `llm` service through Ollama, and prove one
real prompt round-trips end to end.

**Architecture:** DeepSeek Harness ships as independently published `@deepseek-ai/dsh-*` npm
packages (confirmed on the npm registry: `@deepseek-ai/dsh` is the CLI, `@deepseek-ai/dsh-llm` is
the provider-neutral LLM seam, `@deepseek-ai/dsh-agent-spine-demo` provides the minimal
executor-less agent loop). Consumers do not vendor the monorepo — they `npm install` the packages
they need and supply their own `cordis.yml` plugin composition, the same way
`examples/headless-agent/cordis.yml` in the upstream repo does. `harness/` becomes exactly such a
consumer app: a small `package.json` depending on the published packages plus one new local
package, `@hrsu/dsh-llm-ollama`, implementing `LlmAdapter.stream()` against Ollama's native
`/api/chat` endpoint (NDJSON streaming, no SSE parsing needed).

**Tech Stack:** Node.js (ESM/TypeScript, `type: module`), pnpm workspace, `@deepseek-ai/dsh-llm`
(`LlmAdapter` base class, `GenerateOptions`/`StreamChunk` types), `@deepseek-ai/schemastery` (`z`)
for plugin config schemas, Vitest for tests, a local Ollama server (`ollama serve`, default
`http://localhost:11434`).

**Spec:** `docs/superpowers/specs/2026-08-23-deepseek-harness-hyperframes-migration-design.md`

## Global Constraints

- Do not vendor or clone the `deepseek-ai/deepseek-harness` monorepo into this repo — consume its
  published `@deepseek-ai/dsh-*` packages instead (per spec's "Architecture" section: the harness
  is the orchestrator, not code we fork).
- `harness/` is a pnpm workspace of its own, sibling to `_shorts_engine_impl/` inside
  `HRSU Shorts` — not nested inside `_shorts_engine_impl/`.
- No `--publish` or real Blogger/social calls happen anywhere in this plan; this plan only proves
  the harness boots and can call a local model.
- Every new package is ESM (`"type": "module"`) and TypeScript, matching the upstream packages'
  own convention (`.ts` source, compiled `lib/` output is not required for our own app code since
  we run it directly with `tsx`, matching how the upstream repo's own `dsh` bin and demos run:
  `node --import tsx/esm ...`).

---

## File Structure

```
harness/
  package.json                 pnpm workspace root; depends on @deepseek-ai/dsh + plugin packages
  pnpm-workspace.yaml           declares packages/* as workspace members
  cordis.yml                    plugin composition: settings, credentials, our ollama adapter,
                                 agent-spine (persona = system prompt), a bash/fs tool pair so the
                                 trivial round-trip test has something concrete to ask the agent to do
  system_prompt.md              the drafted HRSU Shorts pipeline persona (from the spec), loaded
                                 into cordis.yml's `persona` field
  packages/
    llm-ollama/
      package.json               name: "@hrsu/dsh-llm-ollama"
      src/
        adapter.ts                OllamaAdapter (extends LlmAdapter), stream() only
        index.ts                  plugin entry: name, inject, Config schema, apply(ctx, config)
      tests/
        adapter.spec.ts           unit test: fake fetch returning NDJSON, asserts chunk sequence
  tests/
    roundtrip.e2e.ts              starts the harness against a real local Ollama, sends one
                                   prompt, asserts a non-empty text response (skipped if
                                   OLLAMA_HOST is unreachable)
```

---

## Task 1: Confirm local prerequisites (Ollama reachable, packages resolvable)

**Files:**
- Create: `harness/package.json`
- Create: `harness/pnpm-workspace.yaml`
- Test: manual verification steps below (no test file — this task is environment verification)

**Interfaces:**
- Produces: a `harness/` directory pnpm recognizes as a workspace root, ready for Task 2 to add
  the `llm-ollama` package under `packages/`.

- [ ] **Step 1: Verify Ollama is installed and serving**

Run: `ollama list`

Expected: a table of installed models (no error). If this fails, stop and tell the user — Ollama
must be running before any later task's tests can pass. Per project memory, `gemma3:4b` should be
present; that is the model this plan's tests target.

- [ ] **Step 2: Create the harness workspace root**

Create `harness/package.json`:

```json
{
  "name": "hrsu-shorts-harness",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dsh": "node --import tsx/esm node_modules/@deepseek-ai/dsh/lib/bin.js"
  },
  "dependencies": {
    "@deepseek-ai/dsh": "0.1.1-rc.2",
    "@deepseek-ai/dsh-llm": "0.1.1-rc.2",
    "@deepseek-ai/dsh-settings-file": "0.0.1-rc.3",
    "@deepseek-ai/dsh-credentials-local": "0.0.1-rc.3",
    "@deepseek-ai/dsh-agent-spine-demo": "0.0.1-rc.1",
    "@deepseek-ai/dsh-session-persistence-jsonl": "0.0.1-rc.3"
  },
  "devDependencies": {
    "tsx": "^4",
    "typescript": "^5",
    "vitest": "^3"
  }
}
```

Note: exact `0.x.x-rc.N` versions drift as the harness ships new release candidates. Before
running `pnpm install` in Step 4, run `npm view @deepseek-ai/dsh versions --json` (and the same
for each `@deepseek-ai/dsh-*` package above) and replace every version in this file with each
package's actual latest published version — the harness is explicitly a fast-moving developer
preview, so pinning to a stale version here would fail install.

Create `harness/pnpm-workspace.yaml`:

```yaml
packages:
  - packages/*
```

Note: this app's own code never imports `@deepseek-ai/schemastery` — Task 2's plugin config is a
plain TypeScript interface, not a validated schema — so it is deliberately absent from these
dependencies. Do not add it.

Note: `@hrsu/dsh-llm-ollama` is deliberately absent from these dependencies too, even though
Task 2 creates that package inside this same workspace. Declaring a `workspace:*` dependency on a
package that does not exist yet fails `pnpm install` in Step 4 below — Task 2 adds this line (and
reinstalls) once the package actually exists.

- [ ] **Step 3: Install**

Run: `cd harness && pnpm install`

Expected: install succeeds with no `E404`. If any `@deepseek-ai/dsh-*` package 404s, re-check its
exact published name with `npm search dsh-<guessed-name>` before proceeding — package names in
this developer-preview harness may have shifted between when this plan was written and when it's
executed.

- [ ] **Step 4: Commit**

```bash
cd harness
git add package.json pnpm-workspace.yaml pnpm-lock.yaml
git commit -m "Add DeepSeek Harness consumer app workspace"
```

---

## Task 2: `@hrsu/dsh-llm-ollama` — Ollama model adapter plugin

**Files:**
- Create: `harness/packages/llm-ollama/package.json`
- Create: `harness/packages/llm-ollama/src/adapter.ts`
- Create: `harness/packages/llm-ollama/src/index.ts`
- Test: `harness/packages/llm-ollama/tests/adapter.spec.ts`

**Interfaces:**
- Consumes: `LlmAdapter` (abstract base, `stream()` is the only required method),
  `GenerateOptions`, `StreamChunk`, `LlmError` from `@deepseek-ai/dsh-llm`; `Context` from
  `@deepseek-ai/cordis` is NOT a direct dependency of `adapter.ts` (only `index.ts` touches it).
- Produces: `OllamaAdapter` class (constructor takes `{ baseURL: () => string }`) and a Cordis
  plugin (`export const name = 'llm-ollama'`, `export const inject = ['llm']`,
  `export function apply(ctx, config): void`) that Task 3 references from `cordis.yml` as
  `name: '@hrsu/dsh-llm-ollama'`.

- [ ] **Step 1: Write the failing adapter test**

Create `harness/packages/llm-ollama/tests/adapter.spec.ts`:

```typescript
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd harness && pnpm --filter @hrsu/dsh-llm-ollama test`

Expected: FAIL — `../src/adapter.ts` does not exist yet.

- [ ] **Step 3: Write the adapter**

Create `harness/packages/llm-ollama/src/adapter.ts`:

```typescript
import { LlmAdapter, LlmError } from '@deepseek-ai/dsh-llm'
import type { GenerateOptions, Message, StreamChunk } from '@deepseek-ai/dsh-llm'

export interface OllamaAdapterOptions {
  /** Resolved once per stream call, so a config change reaches the next request. */
  baseURL: () => string
}

interface OllamaWireMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
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
    const role = message.role === 'assistant' ? 'assistant' : message.role === 'tool' ? 'tool' : 'user'
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
  constructor(private readonly config: OllamaAdapterOptions) {
    super()
  }

  override providerInfo(provider: string) {
    return { id: provider, name: 'Ollama (local)' }
  }

  async * stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    let response: Response
    try {
      response = await fetch(`${this.config.baseURL()}/api/chat`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          model: options.model,
          stream: true,
          messages: toWireMessages(options),
          ...options.temperature === undefined ? {} : { options: { temperature: options.temperature } },
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
          const parsed = JSON.parse(line) as { message?: { content?: string }; done?: boolean }
          const delta = parsed.message?.content ?? ''
          if (delta.length > 0) {
            text += delta
            yield { type: 'text-delta', index: 0, text: delta }
          }
        }
      }
    } catch (error: unknown) {
      throw new LlmError(`Ollama stream read failed: ${String(error)}`, 'TRANSPORT', { cause: error })
    }
    yield { type: 'block-end', index: 0, block: { type: 'text', text } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd harness && pnpm --filter @hrsu/dsh-llm-ollama test`

Expected: PASS (both tests).

- [ ] **Step 5: Write the plugin entry point**

Create `harness/packages/llm-ollama/src/index.ts`:

```typescript
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
```

If Task 1 Step 3 found `@deepseek-ai/schemastery` unavailable standalone, this file needs no
change — it already uses a plain `interface Config` with no schema export, which is valid: the
upstream `LlmAdapter` contract does not require a schemastery `Config` export, only `name`,
`inject`, and `apply`.

- [ ] **Step 6: Create the package manifest**

Create `harness/packages/llm-ollama/package.json`:

```json
{
  "name": "@hrsu/dsh-llm-ollama",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "main": "src/index.ts",
  "dependencies": {
    "@deepseek-ai/dsh-llm": "*"
  },
  "devDependencies": {
    "@deepseek-ai/cordis": "*",
    "vitest": "*"
  }
}
```

- [ ] **Step 7: Register the new package as a workspace dependency**

Edit `harness/package.json` (created in Task 1): add `"@hrsu/dsh-llm-ollama": "workspace:*"` to
its `"dependencies"` object. This must happen after Task 2 Step 6 creates
`harness/packages/llm-ollama/package.json` — declaring it any earlier makes `pnpm install` fail
because pnpm's `workspace:*` protocol requires the named package to already exist in the
workspace.

- [ ] **Step 8: Reinstall so the workspace links the new package, then run the full test suite**

Run: `cd harness && pnpm install && pnpm --filter @hrsu/dsh-llm-ollama test`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd harness
git add packages/llm-ollama package.json pnpm-lock.yaml
git commit -m "Add Ollama LlmAdapter plugin for DeepSeek Harness"
```

---

## Task 3: Wire the plugin into a runnable `cordis.yml` with the pipeline system prompt

**Files:**
- Create: `harness/system_prompt.md`
- Create: `harness/cordis.yml`

**Interfaces:**
- Consumes: `@hrsu/dsh-llm-ollama` (Task 2), `@deepseek-ai/dsh-agent-spine-demo`'s `persona`
  config field (confirmed in the upstream `examples/headless-agent/cordis.yml` composition).
- Produces: a bootable harness composition Task 4's round-trip test drives via the `dsh` CLI.

- [ ] **Step 1: Save the drafted system prompt**

Create `harness/system_prompt.md` with exactly the persona content already approved in the spec's
"System prompt for the harness agent" section (copy it verbatim from
`docs/superpowers/specs/2026-08-23-deepseek-harness-hyperframes-migration-design.md`) — the
`# HRSU Shorts Pipeline Agent` heading through the `## What not to do` list.

- [ ] **Step 2: Write cordis.yml**

Create `harness/cordis.yml`:

```yaml
# HRSU Shorts pipeline harness composition. Model: local Ollama via our own
# llm-ollama plugin, not a DeepSeek/pi-ai cloud route.

- id: settings
  name: '@deepseek-ai/dsh-settings-file'

- id: credentials
  name: '@deepseek-ai/dsh-credentials-local'

- id: llm-ollama
  name: '@hrsu/dsh-llm-ollama'
  config:
    provider: ollama-local
    baseURL: !!js "process.env.OLLAMA_HOST ?? 'http://localhost:11434'"

- id: agent-spine
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    agents:
      - id: main
        provider: ollama-local
        model: gemma3:4b
        cwd: !!js process.cwd()
    persona: !!js "require('node:fs').readFileSync(require('node:path').join(__dirname, 'system_prompt.md'), 'utf8')"

- id: persistence
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config:
    root: './.sessions'
```

Note: if `!!js` inside `cordis.yml` cannot call `require()` in this harness version (module
resolution inside `!!js` blocks may be restricted to already-imported globals — this was not
directly verified against the installed package version), fall back to reading the file in a tiny
wrapper script instead: create `harness/load-persona.mjs` exporting the file contents as a plain
string, and reference it however the installed `dsh-agent-spine-demo` version's README documents
loading `persona` from a file (check
`node_modules/@deepseek-ai/dsh-agent-spine-demo/README.md` for the current option — do not guess
further than what that file documents).

- [ ] **Step 3: Commit**

```bash
cd harness
git add system_prompt.md cordis.yml
git commit -m "Wire Ollama plugin and pipeline persona into a runnable cordis.yml"
```

---

## Task 4: End-to-end round-trip test

**Files:**
- Create: `harness/tests/roundtrip.e2e.ts`

**Interfaces:**
- Consumes: the `harness/cordis.yml` composition from Task 3, a running local Ollama server.
- Produces: proof (a passing or explicitly-skipped test) that the harness boots this composition
  and gets a real, non-empty response from the local model — the deliverable the spec's Migration
  Phase 1 asks for ("verify a trivial tool call round-trip").

- [ ] **Step 1: Write the round-trip test**

Create `harness/tests/roundtrip.e2e.ts`:

```typescript
import { describe, expect, it } from 'vitest'

async function ollamaReachable(): Promise<boolean> {
  try {
    const response = await fetch(`${process.env.OLLAMA_HOST ?? 'http://localhost:11434'}/api/tags`)
    return response.ok
  } catch {
    return false
  }
}

describe('harness round trip', () => {
  it('gets a real, non-empty response from the local model through the harness', async () => {
    if (!(await ollamaReachable())) {
      console.warn('Ollama not reachable at OLLAMA_HOST/localhost:11434 — skipping round-trip test')
      return
    }

    // Import path depends on the exact bootable export the installed
    // @deepseek-ai/dsh-agent-spine-demo / @deepseek-ai/dsh versions expose for
    // headless single-turn use (the upstream examples/headless-agent/tests
    // harness is the authoritative reference — check
    // node_modules/@deepseek-ai/dsh/README.md and
    // node_modules/@deepseek-ai/dsh-agent-spine-demo/README.md for the current
    // API before filling this in; do not invent a boot function name here).
    const { bootHeadlessTurn } = await import('@deepseek-ai/dsh-agent-spine-demo')
    const result = await bootHeadlessTurn({
      cordisConfigPath: new URL('../cordis.yml', import.meta.url).pathname,
      prompt: 'Reply with exactly the word: pong',
    })

    expect(result.text.toLowerCase()).toContain('pong')
  }, 60_000)
})
```

- [ ] **Step 2: Resolve the real boot API before running**

Before running this test, read `node_modules/@deepseek-ai/dsh-agent-spine-demo/README.md` and
`node_modules/@deepseek-ai/dsh/README.md` (installed in Task 1) for the actual documented way to
boot one headless turn against a `cordis.yml` file — the upstream repo's own
`examples/headless-agent/tests/harness.ts` is the reference implementation of this pattern.
Replace the `bootHeadlessTurn` import and call in Step 1 with whatever that documentation and
reference file actually show (exact function name, exact options shape). This step exists because
that exact API was not read as part of writing this plan (only the plugin/adapter surface was
verified) — do not guess further than what the installed package's own README/tests show.

- [ ] **Step 3: Run it**

Run: `cd harness && pnpm vitest run tests/roundtrip.e2e.ts`

Expected: PASS with the model's reply containing "pong" (or a printed skip message if Ollama
isn't running locally).

- [ ] **Step 4: Commit**

```bash
cd harness
git add tests/roundtrip.e2e.ts
git commit -m "Add harness round-trip e2e test against local Ollama"
```
