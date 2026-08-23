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
  /** `undefined` for the `init` operation (blog_url/workspace_root replace the `--workspace` arg). */
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

/**
 * `defineTool`'s `parameters` and `output.schema` shapes come from the installed
 * `@deepseek-ai/dsh-tools@0.1.1-rc.2` `schema.d.ts` (`ParameterSchemaSpec` /
 * `ObjectValueSchemaSpec`), not the plan's own shorthand:
 *   - `output.schema` for an object root MUST declare `additionalProperties`
 *     (it's a required field on `ObjectValueSchemaSpec`, not optional) — the bridge
 *     CLI's JSON shape varies per stage, so this declares `additionalProperties: true`
 *     rather than an enumerated `properties` list.
 *   - `output.render`'s `ContentBlock` (`{ type: 'text', text: string }`) matches the
 *     plan's sketch exactly (confirmed against `@deepseek-ai/dsh-llm`'s `types.d.ts`).
 */
export function apply(ctx: Context, config: Config): void {
  for (const spec of STAGE_TOOLS) {
    ctx.tools.register(defineTool({
      name: spec.toolName,
      description: spec.description,
      parameters: spec.stageName === undefined
        ? {
            blog_url: { type: 'string', required: true, description: 'Blog post URL to process.' },
            workspace_root: { type: 'string', required: true, description: 'Parent directory for the run workspace.' },
          }
        : {
            workspace: { type: 'string', required: true, description: 'Run workspace directory (from stage_init).' },
          },
      output: {
        schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
      },
      async execute(args) {
        const cliArgs = spec.stageName === undefined
          ? ['init', (args as { blog_url: string }).blog_url, '--workspace-root', (args as { workspace_root: string }).workspace_root]
          : ['run-stage', spec.stageName, '--workspace', (args as { workspace: string }).workspace]
        // runStageCli's return type is `Record<string, unknown>` (it's a JSON.parse of the
        // bridge CLI's arbitrary per-stage output shape); the tool's declared output schema is
        // an open `additionalProperties: true` object, so this is a safe narrowing, not a real
        // type hole — the registry re-validates the returned value against `output.schema`
        // before it reaches the model.
        return (await runStageCli(cliArgs, { cwd: config.shortsEngineCwd })) as never
      },
    }))
  }
}
