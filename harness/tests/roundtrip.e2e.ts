import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

async function ollamaReachable(): Promise<boolean> {
  try {
    const response = await fetch(`${process.env.OLLAMA_HOST ?? 'http://localhost:11434'}/api/tags`)
    return response.ok
  } catch {
    return false
  }
}

/**
 * Boot `harness/cordis.yml` for real, the same way the `dsh` launcher boots any
 * profile: mount `@deepseek-ai/cordis-plugin-loader`'s `Loader` service, then
 * mount `@deepseek-ai/cordis-plugin-include`'s `Include` plugin pointed at the
 * composition file (`@deepseek-ai/cordis-plugin-include`'s own README is the
 * "Usage" example this mirrors). There is no documented single
 * `bootHeadlessTurn`-style helper exported by `@deepseek-ai/dsh-agent-spine-demo`
 * or `@deepseek-ai/dsh` for booting a bare `cordis.yml` outside the `dsh`
 * profile/launcher machinery — `dsh`'s own one-shot driver
 * (`@deepseek-ai/dsh-headless`'s `lib/index.js`) is the authoritative reference
 * for "create one Agent, drive it to quiescence, read back the assistant text
 * from session events," and this test reproduces that exact pattern directly
 * against our own already-created `main` agent (from `agent-spine`'s `agents`
 * config in `cordis.yml`) instead of going through the `dsh --profile headless`
 * CLI machinery, which assumes a different bundle (`dsh-base` + `dsh-headless`)
 * than the one this composition builds.
 */
describe('harness round trip', () => {
  it('gets a real, non-empty response from the local model through the harness', async (ctx) => {
    if (!(await ollamaReachable())) {
      // `ctx.skip()` throws to abort the test and mark it "skipped" in the
      // Vitest report. A bare `return` here would instead report a silent
      // PASS with zero assertions run, hiding the fact that nothing was
      // actually verified on a machine without a reachable Ollama server.
      ctx.skip('Ollama not reachable at OLLAMA_HOST/localhost:11434 — skipping round-trip test')
    }

    const root = new Context()
    // Set directly on the root context (not only via `Loader`'s config) —
    // `Include` mounts as a sibling of `Loader`, and a plain field written
    // inside a plugin's own scoped/extended context does not become visible
    // to a sibling plugin's context through Cordis's prototype-chain scoping.
    root.baseUrl = import.meta.url
    await root.plugin(Loader, { baseUrl: import.meta.url })
    await root.plugin(Include, {
      path: '../cordis.yml',
      initial: [],
    })
    // `Include`'s own entry creation is async (module imports for each plugin
    // row); wait for the whole tree — including the config-created `main`
    // agent that `agent-spine`/`dsh-agent-loop` starts automatically — to
    // settle before touching any service it registers.
    await root.get('loader')?.await()

    const agents = root.get('agents')
    expect(agents).toBeDefined()

    const agent = agents!.list()[0]
    expect(agent).toBeDefined()

    await agent!.whenIdle()
    const firstSeq = agent!.session.seq

    agent!.followup(
      createUserMessage({
        content: [{ type: 'text', text: 'Reply with exactly the word: pong' }],
        source: { kind: 'user' },
      }),
    )
    await agent!.whenIdle()

    let text = ''
    for (const event of agent!.session.events) {
      if (event.seq < firstSeq) continue
      if (event.type === 'assistant/message') {
        const joined = event.data.message.content
          .filter((block: { type: string }) => block.type === 'text')
          .map((block: { text: string }) => block.text)
          .join('')
        if (joined !== '') text = joined
      }
    }

    await root.fiber.dispose()

    expect(text.toLowerCase()).toContain('pong')
  }, 120_000)
})
