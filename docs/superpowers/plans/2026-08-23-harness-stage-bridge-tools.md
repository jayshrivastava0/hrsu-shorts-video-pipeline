# Harness Stage-Bridge Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the DeepSeek Harness agent tool-calling ability, wrap each existing `shorts_engine`
pipeline stage as a callable tool, and prove the harness can drive the *existing* Python
pipeline end-to-end (orchestration swap only — no visual/rendering changes) with a real
regression check against a known-good direct run of the same fixture.

**Architecture:** `@hrsu/dsh-llm-ollama`'s adapter gains tool-call support (Ollama's native
`/api/chat` `tools`/`tool_calls` wire format) and the orchestrator model moves from `gemma3:4b`
(no tool support) to `gemma4:31b-cloud` (tool-calling capable, already pulled, cloud-hosted so it
doesn't depend on the local GPU). A new Python module, `shorts_engine/stage_cli.py`, exposes each
pipeline stage as a standalone JSON-in/JSON-out subcommand — a thin factoring of one loop
iteration out of `runner.run()`, with zero change to `RunManifest`'s on-disk state model. A new
harness tool plugin, `@hrsu/dsh-tool-shorts-stage`, registers one tool per pipeline stage (plus
`stage_init`), each shelling out to that subcommand. `harness/cordis.yml` wires the new plugin in
and swaps the model. A deterministic regression test drives every tool in sequence against the
repo's existing HTML fixture and diffs the resulting artifacts against the same fixture run
through the *existing* `shorts_engine` CLI directly — proving the swap changed only *who* calls
the stages, not *what* they produce.

**Tech Stack:** TypeScript/Node (harness side, same as Phase 1), Python 3.12 (shorts_engine side,
already the project's existing stack), Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-deepseek-harness-hyperframes-migration-design.md`
(this plan implements Migration Phase 2)

## Global Constraints

- No visual/rendering changes in this plan — `visuals`/`assembled` stage *logic* is untouched;
  only *who calls it* changes (per spec Phase 2: "orchestration swap only, no visual changes
  yet").
- No `--publish` or real YouTube/social calls — the regression test stops at `verified`, matching
  the existing CLI's default `hold_for_review` behavior; the `publish` tool must exist (the
  persona already references it) but this plan never calls it for real.
- `RunManifest`'s on-disk JSON schema and `STATUS_ORDER` (`init, ingested, facts, scripted,
  shotlisted, audio, visuals, assembled, verified, packaged, published`) do not change — the
  bridge CLI must produce byte-for-byte-equivalent manifests to the existing `runner.run()` path
  for the same inputs.
- Every new TypeScript file is ESM (`"type": "module"`), matching Phase 1's convention.
- The orchestrator model for this plan is `gemma4:31b-cloud` — do not silently fall back to
  `gemma3:4b` if the cloud model has trouble; if it's unreachable, stop and report rather than
  substitute a model that can't call tools.

---

## File Structure

```
_shorts_engine_impl/
  shorts_engine/
    stage_cli.py                NEW — `init` and `run-stage` subcommands, JSON in/out
  tests/shorts_engine/
    test_stage_cli.py           NEW — unit tests for stage_cli.py (mirrors test_cli.py's style)

harness/
  packages/
    llm-ollama/
      src/
        adapter.ts                 MODIFIED — tool-call request/response support
      tests/
        adapter.spec.ts            MODIFIED — new tool-call test cases
    tool-shorts-stage/
      package.json                 NEW — "@hrsu/dsh-tool-shorts-stage"
      src/
        index.ts                   NEW — plugin entry: registers 11 stage tools
        stage-tool.ts               NEW — makeStageTool() factory, subprocess bridge
      tests/
        stage-tool.spec.ts          NEW — unit tests (mocked subprocess)
  cordis.yml                       MODIFIED — register tool-shorts-stage, switch model
  tests/
    stage-regression.e2e.ts        NEW — drives all tools against the fixture, diffs vs direct CLI
```

---

## Task 1: Tool-call support in the Ollama adapter

**Files:**
- Modify: `harness/packages/llm-ollama/src/adapter.ts`
- Modify: `harness/packages/llm-ollama/tests/adapter.spec.ts`

**Interfaces:**
- Consumes: `GenerateOptions.tools?: ToolSchema[]` where `ToolSchema = { name: string; description:
  string; parameters: Record<string, unknown> }` (confirmed in
  `@deepseek-ai/dsh-llm`'s `lib/types/types.d.ts`, read directly during Phase 1 — this type has
  not changed since `@hrsu/dsh-llm-ollama` still pins `0.1.1-rc.2`). `CallId` — a branded string
  type; check `@deepseek-ai/dsh-llm`'s exports for its constructor function (likely `CallId(...)`,
  matching the pattern already used for `ReasoningEffortId` in this same package — grep
  `harness/node_modules/@deepseek-ai/dsh-llm/lib/types/*.d.ts` for `CallId` before writing this
  code; do not invent the import path).
- Produces: `stream()` now honors `options.tools`, and on a tool-calling response yields
  `tool-call-delta` chunks and a `finish` with `reason: { kind: 'tool-calls' }` instead of always
  assuming plain text. Task 3's harness tool plugin depends on this working correctly — a tool
  registered there is inert if the model can never actually see/call it.

- [ ] **Step 1: Write the failing tests**

Add to `harness/packages/llm-ollama/tests/adapter.spec.ts` (alongside the existing two tests):

```typescript
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && pnpm --filter @hrsu/dsh-llm-ollama test`

Expected: FAIL — `adapter.ts` doesn't send `tools` or parse `tool_calls` yet.

- [ ] **Step 3: Check the real `CallId` export before writing the fix**

Run (from `harness/`): search `node_modules/@deepseek-ai/dsh-llm/lib/types/*.d.ts` for `CallId` to
confirm its exact export name and whether it's a plain brand function (e.g. `CallId(value:
string): CallId`) or something else. Use whatever you find — do not assume the sketch below is
exact; adjust the import and the one line that constructs a call id to match reality.

- [ ] **Step 4: Modify the adapter**

In `harness/packages/llm-ollama/src/adapter.ts`:

1. Add `tools` to the Ollama request body when `options.tools` is set:

```typescript
...options.tools === undefined ? {} : {
  tools: options.tools.map(tool => ({
    type: 'function' as const,
    function: { name: tool.name, description: tool.description, parameters: tool.parameters },
  })),
},
```

2. In the NDJSON parsing loop, detect `parsed.message?.tool_calls` on the line that carries
   `done: true` (Ollama's non-streaming-shaped tool-call responses arrive as a single terminal
   line even when `stream: true` — confirm this empirically in Task 5's live regression test; if a
   given Ollama version instead streams `tool_calls` incrementally across multiple lines, adapt
   the parsing to accumulate them the same way `text` deltas already accumulate, but do not add
   that complexity speculatively — start with the single-terminal-line case since that's what
   Ollama's own docs example shows). For each entry in `tool_calls`, emit a `block-start` (blockType
   `'tool-call'`), a `tool-call-delta` chunk with `id` (a synthetic id — Ollama's native API does
   not provide one; construct one deterministically, e.g. from a per-stream counter formatted
   through whatever `CallId` constructor Step 3 found), `name`, and `argumentsDelta:
   JSON.stringify(call.function.arguments)` (Ollama gives a parsed object; the harness's chunk
   type wants a raw JSON string — restringify it), then a `block-end` with the assembled
   `tool-call` content block.
3. When any `tool_calls` were seen in the response, yield `{ type: 'finish', reason: { kind:
   'tool-calls' } }` instead of the current unconditional `{ kind: 'stop' }`.
4. When no `tool_calls` were seen (the existing plain-text case), keep current behavior
   unchanged — `{ kind: 'stop' }`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd harness && pnpm --filter @hrsu/dsh-llm-ollama test`

Expected: PASS (all 4 tests: the original 2 plus these 2 new ones).

- [ ] **Step 6: Commit**

```bash
cd harness
git add packages/llm-ollama
git commit -m "Add tool-call support to the Ollama adapter"
```

---

## Task 2: Python stage-bridge CLI

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/stage_cli.py`
- Create: `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`

**Interfaces:**
- Consumes: `shorts_engine.manifest.RunManifest` (`.create()`, `.load()`, `.checkpoint()`),
  `shorts_engine.runner.StageContext`, and each stage module's `run(ctx) -> dict[str, str]`
  (all read directly from source in this same session — signatures are exact, not guessed).
- Produces: two CLI operations Task 3's Node tool plugin shells out to:
  - `python -m shorts_engine.stage_cli init <blog_url> --workspace-root PATH [--local-only]
    [--html-override PATH]` → creates a `RunManifest`, prints `{"workspace": "...", "run_id":
    "..."}` as one JSON line on stdout, exit 0. On failure: JSON `{"error": "..."}` on stderr,
    exit 1.
  - `python -m shorts_engine.stage_cli run-stage <stage_name> --workspace PATH [--local-only]
    [--html-override PATH] [--torture] [--publish]` → loads the manifest at
    `PATH/run_manifest.json`, builds a `StageContext`, runs that one stage's `run(ctx)`,
    checkpoints on success. Prints `{"status": "ok", "status_after": "...", "artifacts": {...}}`
    on stdout, exit 0. On stage failure: sets `manifest.status = "failed"`, saves, prints
    `{"status": "error", "message": "..."}` on stderr, exit 1 — mirrors exactly what
    `runner.run()`'s `except` branch already does (see `runner.py:170-183`), just for one stage
    instead of a loop.

- [ ] **Step 1: Write the failing tests**

Create `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`:

```python
"""Tests for shorts_engine.stage_cli — the harness-facing per-stage subcommand bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_HTML = Path(__file__).parent / "fixtures" / "nitrate_post.html"


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shorts_engine.stage_cli", *args],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent,  # _shorts_engine_impl/
    )


def test_init_creates_manifest_and_prints_workspace(tmp_path):
    result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "workspace" in payload
    assert "run_id" in payload
    manifest_file = Path(payload["workspace"]) / "run_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text())
    assert manifest["status"] == "init"
    assert manifest["blog_url"] == "https://blog.hrsuindore.com/test-post"


def test_run_stage_ingest_advances_manifest_status(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]

    result = run_cli([
        "run-stage", "ingest",
        "--workspace", workspace,
        "--html-override", str(FIXTURE_HTML),
    ])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["status_after"] == "ingested"
    assert "artifacts" in payload

    manifest = json.loads((Path(workspace) / "run_manifest.json").read_text())
    assert manifest["status"] == "ingested"
    assert manifest["last_ok_status"] == "ingested"


def test_run_stage_on_missing_manifest_fails_loud(tmp_path):
    result = run_cli([
        "run-stage", "ingest",
        "--workspace", str(tmp_path / "nonexistent"),
    ])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"


def test_run_stage_on_unknown_stage_name_fails_loud(tmp_path):
    init_result = run_cli([
        "init", "https://blog.hrsuindore.com/test-post",
        "--workspace-root", str(tmp_path),
    ])
    workspace = json.loads(init_result.stdout.strip().splitlines()[-1])["workspace"]

    result = run_cli(["run-stage", "not_a_real_stage", "--workspace", workspace])
    assert result.returncode == 1
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "error"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -v`

Expected: FAIL — `shorts_engine.stage_cli` doesn't exist yet (`ModuleNotFoundError`).

- [ ] **Step 3: Write `stage_cli.py`**

Create `_shorts_engine_impl/shorts_engine/stage_cli.py`:

```python
"""
Per-stage CLI bridge for shorts_engine, driven by the DeepSeek Harness instead of
runner.run()'s own loop.

This factors ONE iteration of runner.run()'s stage-execution body (see runner.py:139-183) out
into a standalone, subprocess-invokable operation, with zero change to RunManifest's on-disk
schema or checkpoint/resume semantics — the harness's agent loop takes over sequencing what
runner.run()'s `for` loop used to do, calling one stage per invocation of this module instead.

Usage:
  python -m shorts_engine.stage_cli init <blog_url> --workspace-root PATH [--local-only]
      [--html-override PATH]
  python -m shorts_engine.stage_cli run-stage <stage_name> --workspace PATH [--local-only]
      [--html-override PATH] [--torture] [--publish]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shorts_engine.cli import build_stages
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext

STAGE_FUNCTIONS = {name: fn for name, _status_after, fn in build_stages()}
STAGE_STATUS_AFTER = {name: status_after for name, status_after, _fn in build_stages()}


def _flags_from_args(args: argparse.Namespace) -> dict:
    flags: dict = {}
    if getattr(args, "local_only", False):
        flags["local_only"] = True
    if getattr(args, "html_override", None):
        flags["html_override"] = str(args.html_override)
    if getattr(args, "torture", False):
        flags["torture"] = True
    if getattr(args, "publish", False):
        flags["publish"] = True
    return flags


def cmd_init(args: argparse.Namespace) -> int:
    manifest = RunManifest.create(blog_url=args.blog_url, workspace_root=args.workspace_root)
    print(json.dumps({"workspace": manifest.workspace, "run_id": manifest.run_id}))
    return 0


def cmd_run_stage(args: argparse.Namespace) -> int:
    stage_fn = STAGE_FUNCTIONS.get(args.stage_name)
    if stage_fn is None:
        print(json.dumps({
            "status": "error",
            "message": f"Unknown stage '{args.stage_name}'. Known stages: {sorted(STAGE_FUNCTIONS)}",
        }), file=sys.stderr)
        return 1

    workspace = Path(args.workspace)
    try:
        manifest = RunManifest.load(workspace)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    ctx = StageContext(manifest=manifest, workspace=workspace, flags=_flags_from_args(args))
    status_after = STAGE_STATUS_AFTER[args.stage_name]

    try:
        artifacts = stage_fn(ctx)
    except Exception as exc:  # mirrors runner.py's own except branch, for one stage
        manifest.status = "failed"
        manifest.error = f"{args.stage_name}: {exc}"
        manifest.save()
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1

    manifest.checkpoint(status=status_after, **artifacts)
    print(json.dumps({"status": "ok", "status_after": status_after, "artifacts": artifacts}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shorts_engine.stage_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("blog_url")
    init_parser.add_argument("--workspace-root", type=Path, required=True)
    init_parser.add_argument("--local-only", action="store_true")
    init_parser.add_argument("--html-override", type=Path, default=None)
    init_parser.set_defaults(func=cmd_init)

    run_stage_parser = subparsers.add_parser("run-stage")
    run_stage_parser.add_argument("stage_name")
    run_stage_parser.add_argument("--workspace", required=True)
    run_stage_parser.add_argument("--local-only", action="store_true")
    run_stage_parser.add_argument("--html-override", type=Path, default=None)
    run_stage_parser.add_argument("--torture", action="store_true")
    run_stage_parser.add_argument("--publish", action="store_true")
    run_stage_parser.set_defaults(func=cmd_run_stage)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_stage_cli.py -v`

Expected: PASS (4/4). If `test_run_stage_ingest_advances_manifest_status` fails because `ingest`
needs network access despite `--html-override` (check `ingest.py`'s actual use of
`ctx.flags.get("html_override")` — read the file if the test fails for a reason other than a
simple bug in the CLI wiring above, since this fixture-driven pattern already works for the
existing `test_cli.py`/`test_integration.py` suite and this task should not need to modify
`ingest.py` itself).

- [ ] **Step 5: Run the full existing shorts_engine suite to confirm no regression**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine -q`

Expected: all previously-passing tests still pass (this task only adds a new file; it does not
modify `runner.py`, `manifest.py`, or any stage module).

- [ ] **Step 6: Commit**

```bash
cd _shorts_engine_impl
git add shorts_engine/stage_cli.py tests/shorts_engine/test_stage_cli.py
git commit -m "Add per-stage CLI bridge for harness-driven orchestration"
```

---

## Task 3: Harness tool plugin wrapping the Python bridge

**Files:**
- Create: `harness/packages/tool-shorts-stage/package.json`
- Create: `harness/packages/tool-shorts-stage/src/stage-tool.ts`
- Create: `harness/packages/tool-shorts-stage/src/index.ts`
- Create: `harness/packages/tool-shorts-stage/tests/stage-tool.spec.ts`

**Interfaces:**
- Consumes: Task 2's `python -m shorts_engine.stage_cli` (invoked via Node's `child_process`),
  `defineTool`/`ctx.tools.register` from `@deepseek-ai/dsh-tools` (per the official cookbook doc
  at `docs/cookbook/adding-a-tool.md` in the upstream repo — the exact `parameters` schema field
  format was read via a summarized fetch, not the raw file; **before writing this task's code,
  read the installed `harness/node_modules/@deepseek-ai/dsh-tools/README.md` and/or its `.d.ts`
  files directly and adjust `parameters`'s shape to match exactly what's there — do not trust the
  shorthand shown in this plan's own description above without that direct check**, the same
  discipline Phase 1 used for `@deepseek-ai/dsh-llm`).
- Produces: 11 registered tools — `stage_init`, `stage_ingest`, `stage_facts`, `stage_script`,
  `stage_shotlist`, `stage_audio`, `stage_visuals`, `stage_assemble`, `stage_verify`,
  `stage_package`, `publish` (name matches the persona's existing prose exactly: `harness/cordis.yml`
  already says "Never call the `publish` tool," not "`stage_publish`" — keep that name literal).
  Task 4's `cordis.yml` registers this package by its `name`/`id`.

- [ ] **Step 1: Write the failing test**

Create `harness/packages/tool-shorts-stage/tests/stage-tool.spec.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { runStageCli } from '../src/stage-tool.ts'

describe('runStageCli', () => {
  it('spawns the Python bridge CLI with the given args and parses its stdout as JSON', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as {
        stdout: InstanceType<typeof EventEmitter>
        stderr: InstanceType<typeof EventEmitter>
      }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => {
        child.stdout.emit('data', Buffer.from('{"status":"ok","status_after":"ingested","artifacts":{}}\n'))
        ;(child as never as EventEmitter).emit('close', 0)
      })
      return child
    })

    const result = await runStageCli(['run-stage', 'ingest', '--workspace', '/tmp/ws'], {
      cwd: '/repo/_shorts_engine_impl',
      spawn: spawnMock as never,
    })

    expect(spawnMock).toHaveBeenCalledWith(
      'python',
      ['-m', 'shorts_engine.stage_cli', 'run-stage', 'ingest', '--workspace', '/tmp/ws'],
      expect.objectContaining({ cwd: '/repo/_shorts_engine_impl' }),
    )
    expect(result).toEqual({ status: 'ok', status_after: 'ingested', artifacts: {} })
  })

  it('throws with the stderr JSON message when the subprocess exits non-zero', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as {
        stdout: InstanceType<typeof EventEmitter>
        stderr: InstanceType<typeof EventEmitter>
      }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => {
        child.stderr.emit('data', Buffer.from('{"status":"error","message":"boom"}\n'))
        ;(child as never as EventEmitter).emit('close', 1)
      })
      return child
    })

    await expect(runStageCli(['run-stage', 'ingest', '--workspace', '/tmp/ws'], {
      cwd: '/repo/_shorts_engine_impl',
      spawn: spawnMock as never,
    })).rejects.toThrow('boom')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd harness && pnpm --filter @hrsu/dsh-tool-shorts-stage test`

Expected: FAIL — `../src/stage-tool.ts` doesn't exist yet. (This `pnpm --filter` invocation will
itself fail until Step 5 creates `package.json` with a `test` script — if you hit that first,
create a minimal `package.json` per Step 5 before writing the test, then come back and confirm
the test fails for the right reason: missing module, not missing script.)

- [ ] **Step 3: Write `stage-tool.ts`**

Create `harness/packages/tool-shorts-stage/src/stage-tool.ts`:

```typescript
import { spawn as nodeSpawn } from 'node:child_process'

export interface RunStageCliOptions {
  /** Working directory for the subprocess — the `_shorts_engine_impl/` package root. */
  cwd: string
  /** Injectable for tests; defaults to `node:child_process`'s real `spawn`. */
  spawn?: typeof nodeSpawn
}

/**
 * Invoke `python -m shorts_engine.stage_cli <args>` and parse its JSON stdout/stderr line.
 * The bridge CLI (Task 2) always prints exactly one JSON line on success (stdout) or failure
 * (stderr) — see `shorts_engine/stage_cli.py`'s `cmd_init`/`cmd_run_stage`.
 */
export function runStageCli(
  args: string[],
  options: RunStageCliOptions,
): Promise<Record<string, unknown>> {
  const spawnFn = options.spawn ?? nodeSpawn
  return new Promise((resolve, reject) => {
    const child = spawnFn('python', ['-m', 'shorts_engine.stage_cli', ...args], { cwd: options.cwd })
    let stdout = ''
    let stderr = ''
    child.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) {
        resolve(JSON.parse(stdout.trim().split('\n').at(-1) ?? '{}'))
        return
      }
      let message = stderr.trim()
      try {
        const parsed = JSON.parse(stderr.trim().split('\n').at(-1) ?? '{}') as { message?: string }
        if (parsed.message !== undefined) message = parsed.message
      } catch {
        // stderr wasn't JSON (e.g. a Python traceback) — surface it raw.
      }
      reject(new Error(message))
    })
  })
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd harness && pnpm --filter @hrsu/dsh-tool-shorts-stage test`

Expected: PASS (2/2).

- [ ] **Step 5: Write the package manifest**

Create `harness/packages/tool-shorts-stage/package.json`:

```json
{
  "name": "@hrsu/dsh-tool-shorts-stage",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "main": "src/index.ts",
  "scripts": {
    "test": "vitest run"
  },
  "dependencies": {
    "@deepseek-ai/dsh-tools": "*"
  },
  "devDependencies": {
    "@deepseek-ai/cordis": "*",
    "vitest": "*"
  }
}
```

- [ ] **Step 6: Write the plugin entry point**

First, re-read `harness/node_modules/@deepseek-ai/dsh-tools`'s actual `README.md`/type
declarations (per this task's Interfaces note) and confirm the exact shape of `parameters` in
`defineTool`'s config and of `output.schema`/`output.render`. Then create
`harness/packages/tool-shorts-stage/src/index.ts` implementing 11 tools, each built from a small
shared factory so the 11 registrations aren't hand-duplicated:

```typescript
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { runStageCli } from './stage-tool.ts'

export { runStageCli } from './stage-tool.ts'
export type { RunStageCliOptions } from './stage-tool.ts'

export const name = 'tool-shorts-stage'
export const inject = ['tools']

export interface Config {
  /** Absolute path to `_shorts_engine_impl/` — the Python package root the bridge CLI runs from. */
  shortsEngineCwd: string
}

interface StageToolSpec {
  toolName: string
  description: string
  /** `undefined` for the `init` operation (blog_url replaces stage-name args). */
  stageName?: string
}

const STAGE_TOOLS: StageToolSpec[] = [
  { toolName: 'stage_init', description: 'Start a new shorts-video run for one blog URL.' },
  { toolName: 'stage_ingest', description: 'Isolate the target post and extract canonical text.', stageName: 'ingest' },
  { toolName: 'stage_facts', description: 'Build the grounded factsheet from canonical text.', stageName: 'facts' },
  { toolName: 'stage_script', description: 'Generate the narration script from the factsheet.', stageName: 'script' },
  { toolName: 'stage_shotlist', description: 'Break the script into a shot-by-shot list.', stageName: 'shotlist' },
  { toolName: 'stage_audio', description: 'Synthesize per-beat voiceover audio.', stageName: 'audio' },
  { toolName: 'stage_visuals', description: 'Acquire or render each shot\'s visual.', stageName: 'visuals' },
  { toolName: 'stage_assemble', description: 'Assemble shots, audio, and captions into one video.', stageName: 'assemble' },
  { toolName: 'stage_verify', description: 'Run the vision-judge/grounding verification gates.', stageName: 'verify' },
  { toolName: 'stage_package', description: 'Package the verified video for publishing review.', stageName: 'package' },
  { toolName: 'publish', description: 'Publish the packaged video for real. Only after 3 human-approved dry runs.', stageName: 'publish' },
]

export function apply(ctx: Context, config: Config): void {
  for (const spec of STAGE_TOOLS) {
    ctx.tools.register(defineTool({
      name: spec.toolName,
      description: spec.description,
      parameters: spec.stageName === undefined
        ? { blog_url: { type: 'string', required: true, description: 'Blog post URL to process.' }, workspace_root: { type: 'string', required: true, description: 'Parent directory for the run workspace.' } }
        : { workspace: { type: 'string', required: true, description: 'Run workspace directory (from stage_init).' } },
      output: {
        schema: { type: 'object' },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
      },
      async execute(args: Record<string, string>) {
        const cliArgs = spec.stageName === undefined
          ? ['init', args.blog_url, '--workspace-root', args.workspace_root]
          : ['run-stage', spec.stageName, '--workspace', args.workspace]
        return runStageCli(cliArgs, { cwd: config.shortsEngineCwd })
      },
    }))
  }
}
```

Adjust the `parameters`/`output.schema` shape here to match whatever Step 6's real README check
found, if it differs from the shorthand above.

- [ ] **Step 7: Install and run the package's tests**

Run: `cd harness && pnpm install && pnpm --filter @hrsu/dsh-tool-shorts-stage test`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd harness
git add packages/tool-shorts-stage
git commit -m "Add harness tool plugin wrapping the shorts_engine stage bridge"
```

---

## Task 4: Wire the tool plugin into cordis.yml and switch the orchestrator model

**Files:**
- Modify: `harness/cordis.yml`
- Modify: `harness/system_prompt.md`

**Interfaces:**
- Consumes: `@hrsu/dsh-tool-shorts-stage` (Task 3), `@hrsu/dsh-llm-ollama`'s tool-call support
  (Task 1).
- Produces: the composition Task 5's regression test drives.

- [ ] **Step 1: Register the new package as a root workspace dependency**

`harness/cordis.yml`'s `Include`/`Loader` resolves a plugin's `name` (e.g.
`'@hrsu/dsh-tool-shorts-stage'`) from `harness/node_modules` — pnpm only symlinks a workspace
package into another package's `node_modules` if something actually declares it as a dependency
(this is the exact bug Phase 1's pre-flight scan caught for `@hrsu/dsh-llm-ollama` — see this
plan's own spec/ledger history). Task 3 created `packages/tool-shorts-stage/` but nothing depends
on it yet. Edit `harness/package.json`: add `"@hrsu/dsh-tool-shorts-stage": "workspace:*"` to its
`"dependencies"`. Task 5's e2e test also imports directly from this package name, so this step is
required before Task 5, not optional cleanup.

Run: `cd harness && pnpm install` and confirm it resolves cleanly.

- [ ] **Step 2: Add the tool plugin entry**

In `harness/cordis.yml`, add (after the `llm-ollama` entry, before `agent-spine`):

```yaml
- id: tool-shorts-stage
  name: '@hrsu/dsh-tool-shorts-stage'
  config:
    shortsEngineCwd: !!js "require('node:path').resolve(process.cwd(), '../_shorts_engine_impl')"
```

If the `!!js require(...)` pattern fails here the same way it failed for `persona` in Phase 1
(Task 3's ledger entry explains why: `cordis-plugin-loader`'s `evaluate()` has no `require` in
scope), use `node:path`'s already-proven-working `process.cwd()` pattern instead — e.g.
`shortsEngineCwd: !!js "process.cwd() + '/../_shorts_engine_impl'"` — and verify the resulting
path is actually correct by having `cmd_init`/the tool's `execute()` print/log it during Task 5's
manual verification pass, since simple string concatenation won't normalize path separators on
Windows the way `path.resolve` would.

- [ ] **Step 3: Switch the orchestrator model**

In `harness/cordis.yml`'s `agent-spine` config, change:

```yaml
model: gemma3:4b
```

to:

```yaml
model: gemma4:31b-cloud
```

- [ ] **Step 4: Update the persona to mention `stage_init`**

The current persona (in both `harness/cordis.yml` and `harness/system_prompt.md`) lists 9 stage
tools by name under "How to use your tools" but never mentions how a run starts. Add one sentence
immediately before the existing "Stage tools (...)" bullet, in BOTH files identically (the
persona-sync test from Phase 1 checks they stay byte-identical):

```
- Call `stage_init` once, first, with the blog URL to process — it creates the run workspace
  every other tool needs. Pass its returned `workspace` value to every subsequent stage tool call.
```

- [ ] **Step 5: Run the persona sync-check test**

Run: `cd harness && pnpm exec vitest run tests/persona-sync.spec.ts`

Expected: PASS (both files were edited identically in Step 4).

- [ ] **Step 6: Commit**

```bash
cd harness
git add cordis.yml system_prompt.md package.json pnpm-lock.yaml
git commit -m "Wire stage-bridge tools into cordis.yml, switch orchestrator to gemma4:31b-cloud"
```

---

## Task 5: Regression test — harness-driven run vs. direct CLI run, same fixture

**Files:**
- Create: `harness/tests/stage-regression.e2e.ts`

**Interfaces:**
- Consumes: Task 3's 11 registered tools (called directly through their `execute()` functions,
  bypassing the LLM — this keeps the regression test deterministic and fast; proving the *agent*
  can autonomously sequence these same tools via `gemma4:31b-cloud` is a follow-up manual
  verification pass, not this automated test, since LLM tool-choice is inherently non-deterministic
  turn-to-turn).
- Produces: the proof the spec's Phase 2 asks for — "get the harness driving the *existing*
  renderer/assembly end-to-end ... regression-check against a known-good prior dry run."

- [ ] **Step 1: Establish the known-good baseline**

Run the *existing*, untouched CLI directly against the repo's fixture, through `verified` (the
default stop point, matching `hold_for_review`):

```bash
cd _shorts_engine_impl
python -m shorts_engine "https://blog.hrsuindore.com/fixture-post" \
  --html-override tests/shorts_engine/fixtures/nitrate_post.html \
  --workspace-root /tmp/baseline_run --local-only
```

Note the resulting `run_manifest.json`'s `artifacts` keys and the `status` value reached — this is
the baseline Step 3 compares against. If this command fails for reasons unrelated to this plan
(missing API keys, network calls the fixture is supposed to avoid, etc.), that is a pre-existing
condition of the repo, not something this task introduces — investigate only far enough to
distinguish "pre-existing gap in the pipeline" from "something this plan's Task 2 changed"; do not
fix pre-existing shorts_engine bugs as part of this plan.

- [ ] **Step 2: Write the regression test**

Create `harness/tests/stage-regression.e2e.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runStageCli } from '@hrsu/dsh-tool-shorts-stage'

const SHORTS_ENGINE_CWD = join(import.meta.dirname, '../../_shorts_engine_impl')
const FIXTURE_HTML = join(SHORTS_ENGINE_CWD, 'tests/shorts_engine/fixtures/nitrate_post.html')
const STAGE_ORDER = ['ingest', 'facts', 'script', 'shotlist', 'audio', 'visuals', 'assemble', 'verify']

describe('stage-bridge regression', () => {
  it('drives every stage tool through the same fixture the direct CLI run used, reaching "verified"', async () => {
    const init = await runStageCli(
      ['init', 'https://blog.hrsuindore.com/fixture-post', '--workspace-root', '/tmp/harness_regression_run'],
      { cwd: SHORTS_ENGINE_CWD },
    ) as { workspace: string }
    expect(init.workspace).toBeTruthy()

    let lastResult: { status: string; status_after: string } | undefined
    for (const stage of STAGE_ORDER) {
      lastResult = await runStageCli(
        ['run-stage', stage, '--workspace', init.workspace, '--html-override', FIXTURE_HTML, '--local-only'],
        { cwd: SHORTS_ENGINE_CWD },
      ) as { status: string; status_after: string }
      expect(lastResult.status).toBe('ok')
    }

    expect(lastResult?.status_after).toBe('verified')

    const manifest = JSON.parse(readFileSync(join(init.workspace, 'run_manifest.json'), 'utf8')) as
      { status: string; artifacts: Record<string, string> }
    expect(manifest.status).toBe('verified')
    // Compare artifact KEYS (not exact paths, which differ run-to-run by design — each run gets
    // its own workspace) against Step 1's baseline run — fill in the actual key list Step 1
    // produced before finalizing this assertion; do not leave this as a guess.
    expect(Object.keys(manifest.artifacts).sort()).toEqual(/* baseline artifact keys from Step 1, sorted */)
  }, 300_000)
})
```

- [ ] **Step 3: Fill in the real baseline artifact keys and run**

Replace the `/* baseline artifact keys from Step 1, sorted */` placeholder with the actual sorted
key list Step 1's run produced (read `/tmp/baseline_run/<run-id>/run_manifest.json`'s
`artifacts` object directly — do not guess the key names).

Run: `cd harness && pnpm exec vitest run tests/stage-regression.e2e.ts`

Expected: PASS — the harness-tool-driven run reaches `verified` with the same artifact keys as
the direct CLI run.

- [ ] **Step 4: Manual verification — agent-driven sequencing (not automated, report findings)**

Boot the full composition (same pattern as Phase 1's `roundtrip.e2e.ts`, or via `pnpm dsh` if that
now works with the tool plugin registered) and give the agent a real prompt: "Process
https://blog.hrsuindore.com/fixture-post through verification." Observe whether `gemma4:31b-cloud`
actually calls `stage_init` then each stage tool in order, unprompted, using only the persona's
instructions. Report what happened (success, wrong order, refused to call tools, etc.) in this
task's completion notes — this is exploratory verification of the *agent's* behavior, not a
pass/fail gate for this task's automated tests, since one non-deterministic run proves nothing on
its own either way.

- [ ] **Step 5: Commit**

```bash
cd harness
git add tests/stage-regression.e2e.ts
git commit -m "Add stage-bridge regression test against the existing direct-CLI baseline"
```
