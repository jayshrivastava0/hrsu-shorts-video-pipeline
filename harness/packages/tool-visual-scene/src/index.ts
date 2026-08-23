import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { Agent, AgentOptions } from '@deepseek-ai/dsh-agent'
import type { SubagentResult, SubagentRuntime } from '@deepseek-ai/dsh-subagent'
import { writeSceneFile, renderScene, renderFallbackScene } from './scene-tools.ts'

export { writeSceneFile, renderScene, renderFallbackScene } from './scene-tools.ts'

export const name = 'tool-visual-scene'
// `subagents` is `ctx.subagents` (the `SubagentRuntime` service from `@deepseek-ai/dsh-subagent`)
// — see the long comment above `apply()` for why this, and not `@deepseek-ai/dsh-tool-subagent`,
// is the correct injection target for a tool that spawns a subagent programmatically from inside
// its own `execute()`.
export const inject = ['tools', 'subagents']

export interface Config {
  /** Absolute path to `hyperframes_scenes_project/` — the HyperFrames project root Task 3's
   * `writeSceneFile`/`renderScene`/`renderFallbackScene` operate against (same root the
   * `compositions/` and `templates/` directories live under). */
  projectRoot: string
  /**
   * The child subagent's model route, distinct from the parent orchestrator
   * (`gemma4:31b-cloud`, configured in `cordis.yml`'s `agent-spine` block). Both are reached
   * through the same `ollama-local` provider/adapter (`@hrsu/dsh-llm-ollama`); only the `model`
   * string differs. Shape confirmed against the real installed
   * `@deepseek-ai/dsh-agent@0.1.1-rc.2` `AgentOptions` (`{ provider?, model?, maxTokens? }`) —
   * see the file-level comment below for the verification trail.
   */
  agentOptions: AgentOptions
  /**
   * Name of the registered `SubagentProvider` to delegate through (e.g. `spawn` for
   * `@deepseek-ai/dsh-subagent-spawn-in-process`). Registering that provider in `cordis.yml` is
   * Task 5's job; this plugin only needs to know the name it will be registered under.
   * Defaults to `'spawn'`, the in-process provider's conventional registry name.
   */
  subagentProviderName?: string
}

/**
 * Loose shape of one entry from the pipeline's `shot_briefs.json` (see Task 2's
 * `cmd_visuals_prepare`), matching the schema documented in `scene-author-persona.md`. `payload`
 * varies by `type` so it stays an open record rather than a fixed union.
 */
export interface ShotBrief {
  shot_id: string
  beat: string
  type: string
  payload: Record<string, unknown>
  duration_s: number
  fade_in_s: number
  provenance?: unknown
}

export interface AuthorVisualSceneArgs {
  shot_brief: ShotBrief
  workspace_id: string
}

export interface AuthorVisualSceneOutput {
  mp4_path: string
  attempts: number
  used_fallback: boolean
}

const PERSONA_PATH = fileURLToPath(new URL('./scene-author-persona.md', import.meta.url))
const PERSONA_TEXT = readFileSync(PERSONA_PATH, 'utf8')

const MAX_ATTEMPTS = 2

/** The two tools the child subagent is scoped to via `toolFilter.allow`. */
const CHILD_TOOL_NAMES = ['write_scene_file', 'render_scene'] as const

/**
 * Convention for a shot's rendered output: `<projectRoot>/output/<workspaceId>/shots/shot_<shot_id>.mp4`.
 * The `shots/shot_<id>.mp4` suffix deliberately mirrors the Python `shorts_engine`
 * `visuals-finalize` stage's own expectation (`shots_dir / f"shot_{shot_id}.mp4"`, confirmed in
 * Task 2's `cmd_visuals_finalize`) so a later wiring task can point this tool's output straight
 * at the run workspace's `shots/` directory without a rename step. The `output/<workspaceId>/`
 * prefix keeps it inside the HyperFrames project root, alongside `compositions/<workspaceId>/`,
 * rather than writing outside `projectRoot` (full cross-project wiring between this harness and
 * the Python workspace root is out of this task's scope).
 */
function resolveOutputPath(projectRoot: string, workspaceId: string, shotId: string): string {
  return join(projectRoot, 'output', workspaceId, 'shots', `shot_${shotId}.mp4`)
}

/**
 * Pick the shot's single most relevant text field to caption the safety-net fallback with, per
 * the brief's Step 3 guidance: `payload.text` for `HEADLINE_CARD`, `payload.stat` for
 * `STAT_CARD`, and `beat` when the shot's `type` has no obvious single text field.
 */
export function pickFallbackCaption(shotBrief: ShotBrief): string {
  const payload = shotBrief.payload ?? {}
  const candidate = ((): unknown => {
    switch (shotBrief.type) {
      case 'HEADLINE_CARD':
        return payload.text
      case 'STAT_CARD':
        return payload.stat
      default:
        return payload.text ?? payload.stat
    }
  })()
  return typeof candidate === 'string' && candidate.trim().length > 0 ? candidate : shotBrief.beat
}

function buildPrompt(
  shotBrief: ShotBrief,
  workspaceId: string,
  outputPath: string,
  previousFailureReason: string | undefined,
): string {
  const briefJson = JSON.stringify(shotBrief, null, 2)
  const retrySection = previousFailureReason === undefined
    ? ''
    : `\n\n## Your previous attempt failed\n\nYour first attempt's \`render_scene\` call failed with this error:\n\n\`\`\`\n${previousFailureReason}\n\`\`\`\n\nThis is your final attempt (2 of ${MAX_ATTEMPTS}). Fix the specific problem named above and try once more.`
  return `${PERSONA_TEXT}

## This shot's brief

\`\`\`json
${briefJson}
\`\`\`

## Exact values to use in your tool calls

- \`workspace_id\`: \`${workspaceId}\`
- \`shot_id\`: \`${shotBrief.shot_id}\`
- \`output_path\` (pass this exact string to \`render_scene\`): \`${outputPath}\`
${retrySection}`
}

/** Provider-authored diagnostic, or the child's own closing text, or the bare stop reason — in
 * that preference order — used both as the retry-context failure reason and (implicitly, via
 * that same text reaching the model) as the record of what went wrong. */
function describeFailure(result: SubagentResult): string {
  if (result.diagnostic) return result.diagnostic
  const text = result.output
    .map((block) => (block.type === 'text' ? block.text : ''))
    .filter((chunk) => chunk.length > 0)
    .join('\n')
    .trim()
  if (text.length > 0) return text
  return `subagent stopped with reason "${result.stopReason}" and produced no output`
}

/** Everything `authorVisualScene` needs, factored out so tests can inject fakes at the real
 * `ctx.subagents.start` boundary instead of a boundary invented for testability. */
export interface AuthorVisualSceneDeps {
  /** `ctx.subagents` itself (or a test double implementing just `start`). */
  subagents: Pick<SubagentRuntime, 'start'>
  subagentProviderName: string
  agentOptions: AgentOptions
  /** The calling agent (`exec.agent`); required because `SubagentStartRequest.parent` is
   * non-optional in the real API — a tool call with no parent agent cannot spawn a subagent. */
  agent: Agent | undefined
  signal: AbortSignal
  projectRoot: string
  existsSync: (path: string) => boolean
  renderFallbackScene: typeof renderFallbackScene
}

export async function authorVisualScene(
  args: AuthorVisualSceneArgs,
  deps: AuthorVisualSceneDeps,
): Promise<AuthorVisualSceneOutput> {
  const { shot_brief: shotBrief, workspace_id: workspaceId } = args
  const outputPath = resolveOutputPath(deps.projectRoot, workspaceId, shotBrief.shot_id)

  let previousFailureReason: string | undefined
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (!deps.agent) {
      throw new Error(
        'author_visual_scene: no parent agent available to spawn the scene-author subagent from (exec.agent is undefined)',
      )
    }
    const run = await deps.subagents.start(deps.subagentProviderName, {
      label: `author_visual_scene:${shotBrief.shot_id}:attempt${attempt}`,
      prompt: [{ type: 'text', text: buildPrompt(shotBrief, workspaceId, outputPath, previousFailureReason) }],
      parent: deps.agent,
      signal: deps.signal,
      agentOptions: deps.agentOptions,
      toolFilter: { allow: [...CHILD_TOOL_NAMES] },
    })
    let result: SubagentResult
    try {
      result = await run.result
    } finally {
      await run.dispose()
    }

    // Trust the actual rendered artifact, not the child's claimed success: `stopReason ===
    // 'completed'` only means the child's own turn finished normally, not that render_scene
    // actually produced a file.
    if (result.stopReason === 'completed' && deps.existsSync(outputPath)) {
      return { mp4_path: outputPath, attempts: attempt, used_fallback: false }
    }
    previousFailureReason = describeFailure(result)
  }

  const captionText = pickFallbackCaption(shotBrief)
  await deps.renderFallbackScene(workspaceId, shotBrief.shot_id, deps.projectRoot, outputPath, captionText)
  return { mp4_path: outputPath, attempts: MAX_ATTEMPTS, used_fallback: true }
}

/**
 * `@deepseek-ai/dsh-tool-subagent`'s installed README (`@deepseek-ai/dsh-tool-subagent@0.1.1-rc.2`,
 * verified by reading `harness/packages/tool-visual-scene/node_modules/@deepseek-ai/dsh-tool-subagent/README.md`
 * directly, not a web fetch) describes a package whose whole job is registering ONE MODEL-FACING
 * TOOL (default name `subagent`, package description: "Model-facing subagent delegation tool over
 * the ctx.subagents seam") — i.e. it is itself a `defineTool`-style plugin the ORCHESTRATOR model
 * calls directly, the same way it calls `stage_init`/`stage_ingest`/etc. It is NOT a library
 * function this tool's own `execute()` can import and call to spawn a child programmatically.
 *
 * The brief's Step 1 asked specifically: does spawning happen via a `ctx` service call, or via
 * the harness's own `subagent` tool being invoked programmatically from inside another tool's
 * `execute()`? Per `dsh-tool-subagent`'s own README ("Callers use one service API
 * (`ctx.subagents`)") and `@deepseek-ai/dsh-subagent@0.1.1-rc.2`'s installed
 * `lib/types/index.d.ts`, the answer is the former: the real programmatic seam is the
 * `SubagentRuntime` service injected as `ctx.subagents` (from `@deepseek-ai/dsh-subagent`, a
 * `dsh-tool-subagent` PEER dependency, not `dsh-tool-subagent` itself). Its `start(name, request)`
 * returns `Promise<SubagentRun>`; `run.result` resolves to a `SubagentResult`
 * (`{ output, structured?, diagnostic?, stopReason }`); `run.dispose()` must always be awaited.
 * `SubagentStartRequest` requires `prompt: ContentBlock[]`, `parent: Agent`, `signal: AbortSignal`,
 * and accepts optional `agentOptions?: AgentOptions` (`{ provider?, model?, maxTokens? }` — this
 * part of the brief's summarized sketch was accurate) and `toolFilter?: ToolRestriction`
 * (`{ allow?: string[]; deny?: string[] }`) — also accurate in field name, but the brief's prose
 * implied these were a plugin `config:` block for a registered `dsh-tool-subagent` instance in
 * `cordis.yml`; they are actually fields on the per-call `SubagentStartRequest` object this tool's
 * own `execute()` builds and passes to `ctx.subagents.start()` directly.
 *
 * `write_scene_file`/`render_scene` are registered here as ordinary GLOBAL tools (via
 * `ctx.tools.register`, same pattern as `@hrsu/dsh-tool-shorts-stage`) precisely so that
 * `toolFilter.allow` — which restricts the child's view of the GLOBAL tool registry, per
 * `ToolRestriction`'s doc comment ("Global tool names that stay visible") — has real registered
 * tools to allow-list. `renderFallbackScene` is deliberately NOT registered as a tool: the brief's
 * Step 3 has the retry/fallback orchestration itself (not the child) call it directly on a second
 * failure, so it is invoked as a plain function from `authorVisualScene` above.
 */
export function apply(ctx: Context, config: Config): void {
  ctx.tools.register(defineTool({
    name: 'write_scene_file',
    description: 'Write one HyperFrames scene composition (HTML/CSS/GSAP) to disk for later rendering.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
      shot_id: { type: 'string', required: true, description: 'This shot\'s id within the run.' },
      html: { type: 'string', required: true, description: 'Complete self-contained HTML document for the composition.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const path = writeSceneFile(args.workspace_id, args.shot_id, args.html, config.projectRoot)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'render_scene',
    description: 'Render a previously written HyperFrames scene composition to an MP4.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
      shot_id: { type: 'string', required: true, description: 'This shot\'s id within the run.' },
      output_path: { type: 'string', required: true, description: 'Absolute path the rendered MP4 should be written to.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const path = await renderScene(args.workspace_id, args.shot_id, config.projectRoot, args.output_path)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'author_visual_scene',
    description:
      'Author, render, and (on repeated failure) fall back to a safety-net template for one shot\'s visual, ' +
      'by delegating composition to a kimi-k2.7-code scene-authoring subagent.',
    parameters: {
      shot_brief: { type: 'object', additionalProperties: true, required: true, description: 'One entry from shot_briefs.json.' },
      workspace_id: { type: 'string', required: true, description: 'Run workspace id.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          mp4_path: { type: 'string', required: true },
          attempts: { type: 'integer', required: true },
          used_fallback: { type: 'boolean', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      const result = await authorVisualScene(args as unknown as AuthorVisualSceneArgs, {
        subagents: ctx.subagents,
        subagentProviderName: config.subagentProviderName ?? 'spawn',
        agentOptions: config.agentOptions,
        agent: exec.agent,
        signal: exec.signal,
        projectRoot: config.projectRoot,
        existsSync,
        renderFallbackScene,
      })
      // Unlike `@hrsu/dsh-tool-shorts-stage` (whose output schema is an open
      // `additionalProperties: true` object, inferring `Record<string, JsonValue>`), this tool's
      // output schema is closed (`additionalProperties: false` with three fixed properties), so
      // `InferValue` infers the exact `{ mp4_path, attempts, used_fallback }` shape —
      // `AuthorVisualSceneOutput` already matches it structurally, so no cast is needed.
      return result
    },
  }))
}
