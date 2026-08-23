import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { runStageCli } from '@hrsu/dsh-tool-shorts-stage'

// NOTE on scope (final whole-branch review, Fix 5): the test below drives
// `runStageCli` directly, bypassing the harness tool plugin's registered
// `defineTool` parameter schemas and `execute()` functions entirely — it
// proves the Python bridge CLI works end-to-end (init → ingest → the known
// pre-existing `facts`-stage gap), NOT that the harness tool layer
// (`stage_init`/`stage_ingest`/... as the model would actually call them) is
// wired correctly. Renamed accordingly to avoid overclaiming "harness tool
// path parity."
//
// A full rewrite of *this* scenario to go through the real registered tools
// turns out to be blocked by a genuine schema gap, not just test-harness
// friction: `stage_ingest`'s registered tool parameters
// (`harness/packages/tool-shorts-stage/src/index.ts`) only expose `workspace`
// (+ the new `local_only`) — there is no `html_override` parameter, because
// the persona is never supposed to fabricate fixture HTML in production. The
// CLI-level `--html-override` flag exists purely for deterministic testing.
// Calling the real `stage_ingest` tool as the model would therefore cannot
// reach the deterministic fixture path used below — it would perform a live
// network fetch of a fake blog URL, which is neither deterministic nor
// appropriate for this test suite. Adding an `html_override` tool parameter
// just to make this test possible would leak a test-only affordance into the
// model-facing contract, which is out of scope for this fix wave.
//
// So: the CLI-bypass scenario below stays as "Python bridge CLI parity"
// evidence (renamed, with this comment), and a second test underneath it
// exercises the actual harness tool layer end-to-end for the one stage that
// doesn't need network or fixture data (`stage_init`) by booting the real
// `cordis.yml` composition and calling `ctx.tools.execute()` the way the
// model's tool calls actually dispatch — proving the `defineTool`
// registration, parameter schema, and `runStageCli` wiring all function
// together, not just the underlying CLI in isolation.

const SHORTS_ENGINE_CWD = join(import.meta.dirname, '../../_shorts_engine_impl')
const FIXTURE_HTML = join(SHORTS_ENGINE_CWD, 'tests/shorts_engine/fixtures/nitrate_post.html')

// The direct-CLI baseline (Step 1) was established two different ways, both against the
// *untouched* `shorts_engine` code (no changes from this plan's Tasks 1-4 touch anything under
// `_shorts_engine_impl/`):
//
//  1. `python -m shorts_engine <url> --html-override <fixture> --workspace-root ... --local-only`
//     (the full pipeline entry point named in this task's brief) fails immediately inside the
//     `ingest` stage with "No post containers found in HTML". Root cause: `cli.py`'s
//     `--html-override` handling passes the override *path string* itself as literal HTML instead
//     of reading the file (`flags["html_override"] = str(args.html_override)`), which
//     `ingest.py`'s `run(ctx)` then hands straight to BeautifulSoup. This is a pre-existing bug in
//     `shorts_engine/cli.py`, present since before this plan and untouched by its diff --
//     `shorts_engine/stage_cli.py` (Task 2's per-stage bridge, and what the harness tools actually
//     drive) already works around it correctly by reading the file itself
//     (`Path(args.html_override).read_text(...)`), so this bug does not affect the harness path.
//
//  2. Driving stages individually through `stage_cli.py` (bypassing bug #1) reaches `ingested`,
//     then fails deterministically inside the `facts` stage:
//       Could not import from video_agent.config: No module named 'config'
//       Could not import smart_client from video_agent.ollama_client: No module named 'config'
//       {"status": "error", "message": "No module named 'config'"}
//     Root cause: `video_agent/config.py` (a sibling package this worktree's `shorts_engine`
//     depends on for shared brand/model config, at line 29: `from config import (...)`) hard-imports
//     a top-level `config` module that does not exist anywhere in this repo or worktree (confirmed:
//     no `config.py` at the HRSU Shorts repo root, in this worktree, or anywhere in this repo's git
//     history). Every LLM-backed stage (`facts`, `script`, `shotlist`, ...) routes through
//     `shorts_engine/llm/text_llm.py`'s `_get_smart_client()`, which imports
//     `video_agent.ollama_client`, which unconditionally imports `video_agent.config` at module
//     load time -- so this failure is 100% deterministic and reproducible on a clean checkout of
//     this worktree, verified by reproducing it directly via `stage_cli.py` outside the harness
//     entirely. This is a pre-existing environment/dependency gap in `video_agent/` (not part of
//     this plan's File Structure for any task, and not modified by Tasks 1-4's diff), not something
//     introduced by this plan -- see task-5-report.md for the full investigation, including why an
//     earlier in-progress run (a workspace with a locally-copied `config.py` shim) is not a valid
//     baseline: that shim is not part of this repo, is not something this task should introduce or
//     commit, and was declined by the sandbox's permission system when attempted here.
//
// Because the *existing, untouched* pipeline cannot get past `facts` in this environment, "same
// result as the baseline" for the harness-tool path means: reach the same `ingested` checkpoint,
// then fail on the same stage with the same underlying error -- not a fabricated `verified` pass.
const BASELINE_OK_STAGES = ['ingest'] as const
const BASELINE_FAILING_STAGE = 'facts'
const BASELINE_ARTIFACT_KEYS = ['canonical', 'post'].sort()

describe('stage-bridge regression (Python bridge CLI parity)', () => {
  it('drives the Python bridge CLI directly through the same fixture the baseline used, matching its reach and its failure point (NOT the harness tool layer — see file-header note)', async () => {
    const init = await runStageCli(
      ['init', 'https://blog.hrsuindore.com/fixture-post', '--workspace-root', '/tmp/harness_regression_run'],
      { cwd: SHORTS_ENGINE_CWD },
    ) as { workspace: string }
    expect(init.workspace).toBeTruthy()

    for (const stage of BASELINE_OK_STAGES) {
      const result = await runStageCli(
        ['run-stage', stage, '--workspace', init.workspace, '--html-override', FIXTURE_HTML, '--local-only'],
        { cwd: SHORTS_ENGINE_CWD },
      ) as { status: string; status_after: string }
      expect(result.status).toBe('ok')
    }

    // Read the manifest at the point the baseline last succeeded ("ingested") and compare
    // artifact keys before attempting the stage the baseline failed on.
    const midManifest = JSON.parse(readFileSync(join(init.workspace, 'run_manifest.json'), 'utf8')) as
      { status: string; artifacts: Record<string, string> }
    expect(midManifest.status).toBe('ingested')
    expect(Object.keys(midManifest.artifacts).sort()).toEqual(BASELINE_ARTIFACT_KEYS)

    // The baseline's next stage (`facts`) fails deterministically on a pre-existing environment
    // gap unrelated to this plan (see the comment block above). Assert the Python bridge CLI fails
    // the *same* way here -- that is the correct definition of "same result" when the baseline
    // itself doesn't reach `verified`: parity includes matching failure points, not a fabricated
    // pass.
    await expect(
      runStageCli(
        ['run-stage', BASELINE_FAILING_STAGE, '--workspace', init.workspace, '--html-override', FIXTURE_HTML, '--local-only'],
        { cwd: SHORTS_ENGINE_CWD },
      ),
    ).rejects.toThrow(/No module named 'config'/)

    const finalManifest = JSON.parse(readFileSync(join(init.workspace, 'run_manifest.json'), 'utf8')) as
      { status: string; last_ok_status: string; artifacts: Record<string, string> }
    expect(finalManifest.status).toBe('failed')
    expect(finalManifest.last_ok_status).toBe('ingested')
    expect(Object.keys(finalManifest.artifacts).sort()).toEqual(BASELINE_ARTIFACT_KEYS)
  }, 300_000)
})

describe('stage-bridge regression (harness tool layer)', () => {
  it('calls the real registered stage_init tool through ctx.tools.execute() — the same dispatch path a model tool call takes — and gets back a real workspace', async (testCtx) => {
    // Boots `harness/cordis.yml` for real, same pattern as `roundtrip.e2e.ts` — this
    // resolves `tool-shorts-stage`'s `inject: ['tools']` dependency (the ToolRuntime
    // service comes from the composed bundle, not from mounting the plugin in
    // isolation) without needing a reachable Ollama server, since this test never
    // sends the agent a message.
    const root = new Context()
    root.baseUrl = import.meta.url
    await root.plugin(Loader, { baseUrl: import.meta.url })
    await root.plugin(Include, { path: '../cordis.yml', initial: [] })
    await root.get('loader')?.await()

    try {
      const tools = root.get('tools')
      expect(tools).toBeDefined()

      const definition = tools!.get('stage_init')
      expect(definition).toBeDefined()
      // Prove the registered parameter schema is what Fix 3 shipped: `stage_init`
      // takes blog_url/workspace_root and (unlike every other stage tool) no
      // `local_only`, since it makes no model-tier decision.
      expect(Object.keys(definition!.parameters.properties ?? {}).sort()).toEqual(['blog_url', 'workspace_root'])

      const workspaceRoot = join(SHORTS_ENGINE_CWD, '..', 'output', `harness-tool-layer-test-${Date.now()}`)
      const controller = new AbortController()
      const result = await tools!.execute({
        callId: 'test-call-1' as never,
        name: 'stage_init',
        arguments: {
          blog_url: 'https://blog.hrsuindore.com/fixture-post',
          workspace_root: workspaceRoot,
        },
        signal: controller.signal,
      })

      expect(result.isError).toBe(false)
      const value = (result as { value: { workspace: string; run_id: string } }).value
      expect(value.workspace).toBeTruthy()
      expect(value.run_id).toBeTruthy()

      const manifest = JSON.parse(readFileSync(join(value.workspace, 'run_manifest.json'), 'utf8')) as
        { status: string; blog_url: string }
      expect(manifest.status).toBe('init')
      expect(manifest.blog_url).toBe('https://blog.hrsuindore.com/fixture-post')
    } finally {
      await root.fiber.dispose()
    }
  }, 60_000)
})
