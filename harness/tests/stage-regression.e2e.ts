import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runStageCli } from '@hrsu/dsh-tool-shorts-stage'

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

describe('stage-bridge regression', () => {
  it('drives the harness stage tools through the same fixture the direct CLI baseline used, matching its reach and its failure point', async () => {
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
    // gap unrelated to this plan (see the comment block above). Assert the harness-tool path fails
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
