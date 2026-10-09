import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type JsonValue } from '@deepseek-ai/dsh-tools'
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
  /**
   * Bridge-CLI subcommand to invoke instead of `run-stage <stageName>`. Used by the two
   * `visuals-prepare`/`visuals-finalize` entries, which `stage_cli.py` exposes as their own
   * top-level subcommands (see `_shorts_engine_impl/shorts_engine/stage_cli.py`'s `main()`),
   * not as `run-stage`-dispatched stage names.
   */
  cliVerb?: string
  /**
   * `visuals-finalize`'s argparse subparser (`stage_cli.py`) does not accept `--local-only` at
   * all (it does no model-tier work — it only verifies already-rendered mp4s), unlike every
   * other per-stage tool. Set `true` to omit the `local_only` parameter for that one tool.
   */
  noLocalOnly?: boolean
}

const STAGE_TOOLS: StageToolSpec[] = [
  { toolName: 'stage_init', description: 'Start a new shorts-video run for one blog URL.' },
  { toolName: 'stage_ingest', description: 'Isolate the target post and extract canonical text.', stageName: 'ingest' },
  { toolName: 'stage_facts', description: 'Build the grounded factsheet from canonical text.', stageName: 'facts' },
  { toolName: 'stage_explain', description: 'Plan the explanation (question, causal steps, tagged claims) from the full article.', stageName: 'explain' },
  { toolName: 'stage_verify_claims', description: 'Verify every claim against the article or retrieved sources; drop or repair what fails.', stageName: 'verify_claims' },
  { toolName: 'stage_script', description: 'Narrate the verified explanation plan as the script.', stageName: 'script' },
  { toolName: 'stage_shotlist', description: 'Break the script into a shot-by-shot list.', stageName: 'shotlist' },
  { toolName: 'stage_audio', description: 'Synthesize per-beat voiceover audio.', stageName: 'audio' },
  {
    toolName: 'stage_visuals_prepare',
    description: 'Break the shotlist into per-shot visual authoring briefs (shot_briefs.json).',
    stageName: 'visuals-prepare',
    cliVerb: 'visuals-prepare',
  },
  {
    toolName: 'stage_visuals_finalize',
    description: 'Verify every shot\'s rendered visual (never-blank check) and advance the workspace to visuals.',
    stageName: 'visuals-finalize',
    cliVerb: 'visuals-finalize',
    noLocalOnly: true,
  },
  {
    toolName: 'stage_assemble_prepare',
    description: 'Reflow shot durations onto real audio timing, mix music under the voiceover, and write assembly_brief.json.',
    stageName: 'assemble-prepare',
    cliVerb: 'assemble-prepare',
    // Unlike `visuals-prepare`, `assemble-prepare`'s argparse subparser (`stage_cli.py`) does
    // not accept `--local-only` at all — it does no model-tier work, only deterministic
    // reflow/mix — so passing the flag would be an unrecognized-argument error.
    noLocalOnly: true,
  },
  {
    toolName: 'stage_assemble_finalize',
    description: 'Verify the duration law and every shot\'s presence in the assembled video, and advance the workspace to assembled.',
    stageName: 'assemble-finalize',
    cliVerb: 'assemble-finalize',
    noLocalOnly: true,
  },
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
/**
 * `local_only` is a real flag `stage_cli.py`'s `run-stage` subcommand already accepts
 * (read by `facts.py`/`script.py` to pick local vs. cloud model tier). It's exposed on
 * every per-stage tool except `stage_init` (which has no model-tier decision to make)
 * and `publish` (publishing isn't a model call either).
 */
function supportsLocalOnly(spec: StageToolSpec): boolean {
  return spec.stageName !== undefined && spec.stageName !== 'publish' && spec.noLocalOnly !== true
}

export function apply(ctx: Context, config: Config): void {
  for (const spec of STAGE_TOOLS) {
    const localOnlyParam = supportsLocalOnly(spec)
      ? {
          local_only: {
            type: 'boolean' as const,
            // `ParameterPropertySpec.required` is `true | undefined` — the installed
            // dsh-tools schema compiler rejects `required: false` outright
            // ("required must be true when present"), so an optional parameter omits
            // the field entirely rather than setting it falsy.
            description: 'Force the local model tier instead of cloud for this stage.',
          },
        }
      : {}
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
            ...localOnlyParam,
          },
      output: {
        schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
      },
      async execute(args) {
        const cliArgs = spec.stageName === undefined
          ? ['init', (args as { blog_url: string }).blog_url, '--workspace-root', (args as { workspace_root: string }).workspace_root]
          : spec.cliVerb !== undefined
            ? [spec.cliVerb, '--workspace', (args as { workspace: string }).workspace]
            : ['run-stage', spec.stageName, '--workspace', (args as { workspace: string }).workspace]
        if (supportsLocalOnly(spec) && (args as { local_only?: boolean }).local_only === true) {
          cliArgs.push('--local-only')
        }
        // runStageCli's return type is `Record<string, unknown>` (it's a JSON.parse of the
        // bridge CLI's arbitrary per-stage output shape); the tool's declared output schema is
        // an open `additionalProperties: true` object, so this is a safe narrowing, not a real
        // type hole — the registry re-validates the returned value against `output.schema`
        // before it reaches the model. `Record<string, JsonValue>` is the type `execute`'s
        // signature actually infers for this schema (see `InferValue` in dsh-tools'
        // `schema.d.ts`), so narrow to that instead of `never`.
        return (await runStageCli(cliArgs, { cwd: config.shortsEngineCwd })) as Record<string, JsonValue>
      },
    }))
  }
}
