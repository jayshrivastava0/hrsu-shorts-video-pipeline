import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { Agent, AgentOptions } from '@deepseek-ai/dsh-agent'
import type { SubagentResult, SubagentRuntime } from '@deepseek-ai/dsh-subagent'
import {
  writeCompositionFile, renderComposition, renderFallbackComposition, type AssemblyBrief,
} from './composition-tools.ts'

export {
  writeCompositionFile, renderComposition, renderFallbackComposition,
} from './composition-tools.ts'

export const name = 'tool-assembly'
export const inject = ['tools', 'subagents']

export interface Config {
  /** Absolute path to hyperframes_scenes_project/ — same project root tool-visual-scene uses. */
  projectRoot: string
  /** The assembly-authoring child's model route — see ASSEMBLY_AUTHOR_MODEL in cordis.yml. */
  agentOptions: AgentOptions
  subagentProviderName?: string
}

export interface AuthorAssemblyCompositionArgs {
  assembly_brief: AssemblyBrief
  workspace_id: string
  /** Absolute path to the Python run's workspace directory (stage_init's `workspace` output).
   * The rendered mp4 lands at `<workspace>/video_short.mp4`, matching what
   * cmd_assemble_finalize expects. */
  workspace: string
}

export interface AuthorAssemblyCompositionOutput {
  mp4_path: string
  attempts: number
  used_fallback: boolean
}

const PERSONA_PATH = fileURLToPath(new URL('./assembly-author-persona.md', import.meta.url))
const PERSONA_TEXT = readFileSync(PERSONA_PATH, 'utf8')

const MAX_ATTEMPTS = 2
const CHILD_TOOL_NAMES = ['write_composition_file', 'render_composition'] as const

function resolveOutputPath(workspace: string): string {
  return join(workspace, 'video_short.mp4')
}

function buildPrompt(
  brief: AssemblyBrief, workspaceId: string, previousFailureReason: string | undefined,
): string {
  const briefJson = JSON.stringify(brief, null, 2)
  const retrySection = previousFailureReason === undefined
    ? ''
    : `\n\n## Your previous attempt failed\n\nYour first attempt's \`render_composition\` call failed with this error:\n\n\`\`\`\n${previousFailureReason}\n\`\`\`\n\nThis is your final attempt (2 of ${MAX_ATTEMPTS}). Fix the specific problem named above and try once more.`
  return `${PERSONA_TEXT}

## This run's assembly brief

\`\`\`json
${briefJson}
\`\`\`

## Exact value to use in your tool calls

- \`workspace_id\`: \`${workspaceId}\`

Note: \`render_composition\` takes only \`workspace_id\` — it never takes an output path. Where the
rendered mp4 lands is decided by the pipeline, not by you.
${retrySection}`
}

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

export interface AuthorAssemblyCompositionDeps {
  subagents: Pick<SubagentRuntime, 'start'>
  subagentProviderName: string
  agentOptions: AgentOptions
  agent: Agent | undefined
  signal: AbortSignal
  projectRoot: string
  existsSync: (path: string) => boolean
  renderFallbackComposition: typeof renderFallbackComposition
  /** Records the trusted output path, keyed by workspace_id, before spawning the child — same
   * anti-prompt-injection discipline as tool-visual-scene's registerOutputPath. */
  registerOutputPath: (workspaceId: string, outputPath: string) => void
}

export async function authorAssemblyComposition(
  args: AuthorAssemblyCompositionArgs, deps: AuthorAssemblyCompositionDeps,
): Promise<AuthorAssemblyCompositionOutput> {
  const { assembly_brief: brief, workspace_id: workspaceId, workspace } = args
  const outputPath = resolveOutputPath(workspace)
  deps.registerOutputPath(workspaceId, outputPath)

  let previousFailureReason: string | undefined
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (!deps.agent) {
      throw new Error(
        'author_assembly_composition: no parent agent available to spawn the assembly-authoring subagent from (exec.agent is undefined)',
      )
    }
    const run = await deps.subagents.start(deps.subagentProviderName, {
      label: `author_assembly_composition:attempt${attempt}`,
      prompt: [{ type: 'text', text: buildPrompt(brief, workspaceId, previousFailureReason) }],
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

    if (result.stopReason === 'completed' && deps.existsSync(outputPath)) {
      return { mp4_path: outputPath, attempts: attempt, used_fallback: false }
    }
    previousFailureReason = describeFailure(result)
  }

  await deps.renderFallbackComposition(workspaceId, deps.projectRoot, outputPath, brief)
  return { mp4_path: outputPath, attempts: MAX_ATTEMPTS, used_fallback: true }
}

export function apply(ctx: Context, config: Config): void {
  const outputPathsByWorkspace = new Map<string, string>()

  ctx.tools.register(defineTool({
    name: 'write_composition_file',
    description: 'Write the one top-level HyperFrames assembly composition (HTML/CSS/GSAP) to disk for later rendering.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
      html: { type: 'string', required: true, description: 'Complete self-contained HTML document for the composition.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const path = writeCompositionFile(args.workspace_id, args.html, config.projectRoot)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'render_composition',
    description: 'Render the previously written assembly composition to an MP4.',
    parameters: {
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (matches the composition subdirectory).' },
    },
    output: {
      schema: { type: 'object', additionalProperties: false, properties: { path: { type: 'string', required: true } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const outputPath = outputPathsByWorkspace.get(args.workspace_id)
      if (outputPath === undefined) {
        throw new Error(
          `render_composition: no registered output path for workspace_id=${JSON.stringify(args.workspace_id)} ` +
          '— author_assembly_composition must be the one starting this run\'s subagent.',
        )
      }
      const path = await renderComposition(args.workspace_id, config.projectRoot, outputPath)
      return { path }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'author_assembly_composition',
    description:
      'Author, render, and (on repeated failure) fall back to a safety-net composition for the whole video\'s ' +
      'final assembly, by delegating composition to a creative assembly-authoring subagent.',
    parameters: {
      assembly_brief: { type: 'object', additionalProperties: true, required: true, description: 'The run\'s assembly_brief.json contents.' },
      workspace_id: { type: 'string', required: true, description: 'Run workspace id (namespaces this run\'s HyperFrames composition file).' },
      workspace: {
        type: 'string', required: true,
        description: 'Absolute path to the run workspace, from stage_init\'s `workspace` output. The rendered mp4 lands at `<workspace>/video_short.mp4`, matching what stage_assemble_finalize expects.',
      },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          mp4_path: { type: 'string', required: true },
          attempts: { type: 'integer', required: true },
          used_fallback: { type: 'boolean', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return authorAssemblyComposition(args as unknown as AuthorAssemblyCompositionArgs, {
        subagents: ctx.subagents,
        subagentProviderName: config.subagentProviderName ?? 'spawn',
        agentOptions: config.agentOptions,
        agent: exec.agent,
        signal: exec.signal,
        projectRoot: config.projectRoot,
        existsSync,
        renderFallbackComposition,
        registerOutputPath: (workspaceId, outputPath) => {
          outputPathsByWorkspace.set(workspaceId, outputPath)
        },
      })
    },
  }))
}
