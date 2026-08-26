import { describe, expect, it } from 'vitest'
import { buildPrompt } from '../run-visual-authoring.mts'

describe('buildPrompt', () => {
  it('visuals phase names stage_visuals_prepare/author_visual_scene/stage_visuals_finalize and forbids earlier stages', () => {
    const prompt = buildPrompt('visuals', '/abs/workspace')
    expect(prompt).toContain('/abs/workspace')
    expect(prompt).toContain('stage_visuals_prepare')
    expect(prompt).toContain('author_visual_scene')
    expect(prompt).toContain('stage_visuals_finalize')
    expect(prompt).toContain('Do not call stage_init')
  })

  // The agent has no file-read tool, so the prompt must point it at the `briefs`/`run_id`
  // stage_visuals_prepare now returns inline rather than at the shot_briefs.json filename.
  it('visuals phase points the agent at the inline briefs and run_id, not the file', () => {
    const prompt = buildPrompt('visuals', '/abs/workspace')
    expect(prompt).toContain('briefs')
    expect(prompt).toContain('run_id')
    expect(prompt).toContain('workspace_id')
  })

  it('assemble phase names stage_assemble_prepare/author_assembly_composition/stage_assemble_finalize', () => {
    const prompt = buildPrompt('assemble', '/abs/workspace')
    expect(prompt).toContain('stage_assemble_prepare')
    expect(prompt).toContain('author_assembly_composition')
    expect(prompt).toContain('stage_assemble_finalize')
  })

  it('assemble phase points the agent at the inline brief and run_id, not the file', () => {
    const prompt = buildPrompt('assemble', '/abs/workspace')
    expect(prompt).toContain('brief')
    expect(prompt).toContain('run_id')
    expect(prompt).toContain('workspace_id')
  })
})
