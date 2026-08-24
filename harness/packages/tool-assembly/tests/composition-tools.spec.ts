import { describe, expect, test, vi } from 'vitest'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  writeCompositionFile, renderComposition, buildDeterministicAssemblyHtml,
  groupWordsIntoCues, renderFallbackComposition, type AssemblyBrief,
} from '../src/composition-tools.ts'

function tempProject(): string {
  const root = mkdtempSync(join(tmpdir(), 'tool-assembly-test-'))
  return root
}

describe('writeCompositionFile', () => {
  test('writes to compositions/<workspaceId>/assembly.html', () => {
    const projectRoot = tempProject()
    const target = writeCompositionFile('run-123', '<html></html>', projectRoot)
    expect(target).toBe(join(projectRoot, 'compositions', 'run-123', 'assembly.html'))
    expect(readFileSync(target, 'utf8')).toBe('<html></html>')
  })

  test('rejects a workspaceId that is an absolute path', () => {
    const projectRoot = tempProject()
    expect(() => writeCompositionFile('C:\\evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
  })

  test('rejects a workspaceId containing a path separator or colon', () => {
    const projectRoot = tempProject()
    expect(() => writeCompositionFile('../evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
    expect(() => writeCompositionFile('C:evil', '<html></html>', projectRoot)).toThrow(/outside compositions/)
    expect(() => writeCompositionFile('a/b', '<html></html>', projectRoot)).toThrow(/outside compositions/)
  })

  test('rejects a bare ".." workspaceId (caught only by the layer-3 relative-traversal check, not layers 1/2)', () => {
    // ".." has no separator and no colon, so it passes isAbsolute and
    // containsPathSeparatorOrColon untouched — this is the one value that proves the
    // relative(root, candidate)/resolve(root, rel) !== candidate check is load-bearing.
    const projectRoot = tempProject()
    expect(() => writeCompositionFile('..', '<html></html>', projectRoot)).toThrow(/outside compositions/)
  })
})

describe('renderComposition', () => {
  test('resolves the scoped composition path and shells hyperframes render via cmd.exe on win32', async () => {
    const projectRoot = tempProject()
    writeCompositionFile('run-123', '<html></html>', projectRoot)
    const calls: unknown[] = []
    const fakeSpawn = vi.fn((command: string, args: string[], _opts: unknown) => {
      calls.push([command, args])
      const listeners: Record<string, (...a: unknown[]) => void> = {}
      return {
        stderr: { on: () => {} },
        on: (event: string, cb: (...a: unknown[]) => void) => {
          listeners[event] = cb
          if (event === 'close') setTimeout(() => cb(0), 0)
        },
      } as unknown as ReturnType<typeof import('node:child_process').spawn>
    })
    const outputPath = join(projectRoot, 'out.mp4')
    const result = await renderComposition('run-123', projectRoot, outputPath, { spawn: fakeSpawn as never })
    expect(result).toBe(outputPath)
    expect(calls.length).toBe(1)
    const [command, args] = calls[0] as [string, string[]]
    if (process.platform === 'win32') {
      expect(command).toBe('cmd.exe')
      expect(args).toContain('run-123'.length > 0 ? join('compositions', 'run-123', 'assembly.html') : '')
    }
  })
})

describe('groupWordsIntoCues', () => {
  test('groups up to 3 words or 1.5s per cue, matching the retired Python group_words_into_cues policy', () => {
    const words = [
      { word: 'Cold', start: 0.0, end: 0.3 },
      { word: 'weather', start: 0.3, end: 0.7 },
      { word: 'pours', start: 0.7, end: 1.0 },
      { word: 'wait', start: 1.1, end: 1.4 },
    ]
    const cues = groupWordsIntoCues(words)
    expect(cues).toEqual([
      { start: 0.0, end: 1.0, text: 'COLD WEATHER POURS' },
      { start: 1.1, end: 1.4, text: 'WAIT' },
    ])
  })
})

describe('buildDeterministicAssemblyHtml', () => {
  const brief: AssemblyBrief = {
    shots: [
      { id: '1', video_path: 'E:\\ws\\shots\\shot_1.mp4', start_s: 0, duration_s: 2.5, beat: 'hook' },
      { id: '2', video_path: 'E:\\ws\\shots\\shot_2.mp4', start_s: 2.5, duration_s: 3.0, beat: 'cta' },
    ],
    word_timings: [{ word: 'Test', start: 0.1, end: 0.4 }],
    audio_path: 'E:\\ws\\music_mix.mp3', logo_path: 'E:\\assets\\Logo.png',
    voice_total_s: 4.0, target_duration_s: 5.5,
  }

  test('embeds one <video> clip per shot at its start_s/duration_s', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('src="E:\\ws\\shots\\shot_1.mp4"')
    expect(html).toContain('data-start="0"')
    expect(html).toContain('data-duration="2.5"')
    expect(html).toContain('src="E:\\ws\\shots\\shot_2.mp4"')
    expect(html).toContain('data-start="2.5"')
  })

  test('embeds an <audio> clip for the mixed track', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('<audio')
    expect(html).toContain('src="E:\\ws\\music_mix.mp3"')
  })

  test('root data-duration matches target_duration_s', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain('data-duration="5.5"')
  })

  test('escapes caption text and does not reference logo_path as an image asset', () => {
    // groupWordsIntoCues uppercases word text, so a payload relying on lowercase '<script>' would
    // be defeated by the case change alone rather than by real escaping. Use a payload whose
    // breakout characters (<, >, /) stay meaningful regardless of case.
    const withUnsafeCaption: AssemblyBrief = {
      ...brief,
      word_timings: [{ word: '</SCRIPT><img onerror=x src=y>', start: 0, end: 1 }],
    }
    const html = buildDeterministicAssemblyHtml(withUnsafeCaption)
    expect(html).not.toContain('</SCRIPT><img onerror=x src=y>')
    expect(html).not.toContain('</SCRIPT><IMG ONERROR=X SRC=Y>')
    expect(html).toContain('&lt;/SCRIPT&gt;&lt;IMG ONERROR=X SRC=Y&gt;')
    // The fallback deliberately uses the proven text-based brand mark, not an unverified
    // <img>/background-image asset load (see Global Constraints item 6).
    expect(html).not.toContain(brief.logo_path)
    expect(html).toContain('HRSU INDORE')
  })

  test('registers a paused GSAP timeline keyed to the composition id', () => {
    const html = buildDeterministicAssemblyHtml(brief)
    expect(html).toContain("gsap.timeline({ paused: true })")
    expect(html).toContain("window.__timelines['assembly-fallback']")
  })
})

describe('renderFallbackComposition', () => {
  test('writes the generated HTML then renders it', async () => {
    const projectRoot = tempProject()
    const fakeSpawn = vi.fn((_command: string, _args: string[], _opts: unknown) => ({
      stderr: { on: () => {} },
      on: (event: string, cb: (...a: unknown[]) => void) => { if (event === 'close') setTimeout(() => cb(0), 0) },
    } as unknown as ReturnType<typeof import('node:child_process').spawn>))
    const brief: AssemblyBrief = {
      shots: [{ id: '1', video_path: 'E:\\ws\\shots\\shot_1.mp4', start_s: 0, duration_s: 2, beat: 'hook' }],
      word_timings: [], audio_path: 'E:\\ws\\voice.mp3', logo_path: 'E:\\Logo.png',
      voice_total_s: 2, target_duration_s: 3.5,
    }
    const outputPath = join(projectRoot, 'video_short.mp4')
    const result = await renderFallbackComposition('run-1', projectRoot, outputPath, brief, { spawn: fakeSpawn as never })
    expect(result).toBe(outputPath)
    const written = readFileSync(join(projectRoot, 'compositions', 'run-1', 'assembly.html'), 'utf8')
    expect(written).toContain('shot_1.mp4')
  })
})
