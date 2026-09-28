import { describe, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeSceneFile, renderScene, renderFallbackScene, findDuplicateAttributeIssues } from '../src/scene-tools.ts'

function fakeSpawn() {
  return vi.fn().mockImplementation(() => {
    const { EventEmitter } = require('node:events')
    const child = new EventEmitter() as never as
      { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
    return child
  })
}

describe('writeSceneFile', () => {
  it('writes inside compositions/<workspaceId>/ and returns the absolute path', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    const result = writeSceneFile('run-42', 'shot-1', '<html></html>', projectRoot)
    expect(existsSync(result)).toBe(true)
    expect(readFileSync(result, 'utf8')).toBe('<html></html>')
    expect(result).toContain(join('compositions', 'run-42', 'shot-1.html'))
  })

  it('rejects a shotId that attempts path traversal', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', '../../../etc/passwd', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a cross-drive absolute shotId (Windows drive-letter bypass)', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', 'D:\\evil\\payload', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a UNC-path shotId (network share bypass)', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', '\\\\attacker-host\\share\\evil', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a cross-drive absolute workspaceId', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('D:\\evil\\payload', 'shot-1', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a UNC-path workspaceId', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('\\\\attacker-host\\share\\evil', 'shot-1', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  // Residual bypass found by the final whole-branch review, one notch narrower than the
  // cross-drive/UNC cases above: a Windows "drive-relative" path (a drive letter with NO
  // separator, e.g. `C:evil`) has `path.isAbsolute() === false` by Node's own definition, so it
  // slipped past the isAbsolute() guard alone, then resolved outside compositions/ via per-drive
  // cwd semantics. Reproduces the reviewer's exact three probes.
  it('rejects a drive-relative (no separator) workspaceId bypass', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('C:evilpayload', 'shot-1', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a drive-relative (no separator) shotId bypass', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', 'C:evil', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })

  it('rejects a drive-relative shotId bypass on a different drive letter', () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    expect(() => writeSceneFile('run-42', 'D:evil', '<html></html>', projectRoot))
      .toThrow(/outside/i)
  })
})

describe('findDuplicateAttributeIssues', () => {
  it('flags a duplicate class attribute on one tag (the real defect that produced a broken render)', () => {
    // Trimmed from a real authored composition (run-05a56f37/s05.html): every element repeated
    // `class="clip"` then a second `class="..."` for its real layout class on the same tag.
    // A browser keeps only the first `class` value, so `.label-1` etc. never applied and the
    // whole composition rendered as overlapping, unstyled default-flow text.
    const html = `<div id="label_1" class="clip" data-start="0.4" data-duration="2.39" data-track-index="4" class="label label-1">Substrate Mix</div>`
    const issues = findDuplicateAttributeIssues(html)
    expect(issues.length).toBe(1)
    expect(issues[0]).toContain('<div>')
    expect(issues[0]).toContain('"class"')
  })

  it('flags a duplicate id attribute the same way', () => {
    const html = `<div id="a" data-start="0" id="b">x</div>`
    expect(findDuplicateAttributeIssues(html).length).toBe(1)
  })

  it('does not flag ordinary well-formed markup (no false positives on a real good shot)', () => {
    // Trimmed from run-05a56f37/s07.html, which rendered correctly.
    const html = `
      <div id="root" data-composition-id="s07_comp" data-width="1080" data-height="1920" data-duration="3.24">
        <div id="card" class="clip stat-card" data-start="0" data-duration="3.24" data-track-index="0">
          <div id="label" class="clip label" data-start="0" data-duration="3.24" data-track-index="1">PEAK EC</div>
        </div>
      </div>`
    expect(findDuplicateAttributeIssues(html)).toEqual([])
  })

  it('does not flag two different attributes that happen to share a substring', () => {
    const html = `<div data-start="0" data-duration="1">x</div>`
    expect(findDuplicateAttributeIssues(html)).toEqual([])
  })
})

describe('renderScene', () => {
  // Same regression guarded in tool-assembly's composition-tools.spec.ts: the child is spawned
  // with piped stdio, so an unread `stdout` fills the OS pipe buffer and the render blocks
  // forever on its next write. Short per-shot renders stayed under the buffer and hid this;
  // the assembly render (same spawn strategy) deadlocked live for 3+ hours because of it.
  it('drains the child stdout so a chatty render cannot deadlock on a full pipe', async () => {
    let stdoutDrained = false
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as
        { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      child.stdout.on('newListener', (event: string) => {
        if (event === 'data') stdoutDrained = true
      })
      queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
      return child
    })

    await renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', { spawn: spawnMock as never })
    expect(stdoutDrained).toBe(true)
  })

  // A killed/crashed parent Node process leaves Windows child process trees running forever
  // (confirmed live: orphaned chrome-headless-shell trees from earlier crashed runs piled up
  // over hours and exhausted system resources). renderScene registers a process-exit cleanup
  // handler for the render's own pid, and removes it once the render settles normally, so a
  // long-running process (many renders per pipeline run) doesn't accumulate listeners.
  it('registers a process-exit cleanup handler for the child pid and removes it after a normal close', async () => {
    const originalPlatform = process.platform
    Object.defineProperty(process, 'platform', { value: 'win32' })
    const onSpy = vi.spyOn(process, 'on')
    const offSpy = vi.spyOn(process, 'off')
    try {
      const spawnMock = vi.fn().mockImplementation(() => {
        const { EventEmitter } = require('node:events')
        const child = new EventEmitter() as never as
          { pid: number; stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
        child.pid = 4242
        child.stdout = new EventEmitter()
        child.stderr = new EventEmitter()
        queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
        return child
      })
      await renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', { spawn: spawnMock as never })
      const exitHandler = onSpy.mock.calls.find((call) => call[0] === 'exit')?.[1]
      expect(exitHandler).toBeDefined()
      expect(offSpy).toHaveBeenCalledWith('exit', exitHandler)
    } finally {
      Object.defineProperty(process, 'platform', { value: originalPlatform })
      onSpy.mockRestore()
      offSpy.mockRestore()
    }
  })

  it('does not register a cleanup handler on non-Windows platforms', async () => {
    const originalPlatform = process.platform
    Object.defineProperty(process, 'platform', { value: 'linux' })
    const onSpy = vi.spyOn(process, 'on')
    try {
      const spawnMock = vi.fn().mockImplementation(() => {
        const { EventEmitter } = require('node:events')
        const child = new EventEmitter() as never as
          { pid: number; stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
        child.pid = 4242
        child.stdout = new EventEmitter()
        child.stderr = new EventEmitter()
        queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
        return child
      })
      await renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', { spawn: spawnMock as never })
      expect(onSpy.mock.calls.some((call) => call[0] === 'exit')).toBe(false)
    } finally {
      Object.defineProperty(process, 'platform', { value: originalPlatform })
      onSpy.mockRestore()
    }
  })

  it('spawns the hyperframes CLI with the expected composition/output/resolution args', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as
        { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => { (child as never as EventEmitter).emit('close', 0) })
      return child
    })

    const result = await renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', {
      spawn: spawnMock as never,
    })

    // Windows routes through `cmd.exe /c npx ...` (see scene-tools.ts's comment for why: bare
    // 'npx' throws ENOENT, and both `npx.cmd` directly and `shell: true` fail differently);
    // every other platform keeps the bare 'npx' name with no shell wrapper.
    const expectedArgs = ['hyperframes', 'render', '-c', join('compositions', 'run-42', 'shot-1.html'),
      '-o', '/out/shot-1.mp4', '--resolution', 'portrait']
    if (process.platform === 'win32') {
      expect(spawnMock).toHaveBeenCalledWith(
        'cmd.exe',
        ['/c', 'npx', ...expectedArgs],
        expect.objectContaining({ cwd: '/project' }),
      )
    } else {
      expect(spawnMock).toHaveBeenCalledWith(
        'npx',
        expectedArgs,
        expect.objectContaining({ cwd: '/project' }),
      )
    }
    expect(result).toBe('/out/shot-1.mp4')
  })

  it('throws with the CLI stderr on a non-zero exit', async () => {
    const spawnMock = vi.fn().mockImplementation(() => {
      const { EventEmitter } = require('node:events')
      const child = new EventEmitter() as never as
        { stdout: InstanceType<typeof EventEmitter>; stderr: InstanceType<typeof EventEmitter> }
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      queueMicrotask(() => {
        child.stderr.emit('data', Buffer.from('composition lint error: missing data-duration'))
        ;(child as never as EventEmitter).emit('close', 1)
      })
      return child
    })

    await expect(renderScene('run-42', 'shot-1', '/project', '/out/shot-1.mp4', {
      spawn: spawnMock as never,
    })).rejects.toThrow(/missing data-duration/)
  })

  it('rejects a composition path that escapes compositions/ via a drive-relative workspaceId', async () => {
    await expect(renderScene('C:evil', 'shot-1', '/project', '/out/shot-1.mp4', { spawn: fakeSpawn() as never }))
      .rejects.toThrow(/outside/i)
  })

  it('rejects a written composition with duplicate attributes BEFORE spawning the real render', async () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    writeSceneFile('run-42', 'shot-1',
      '<div id="x" class="clip" class="headline">broken</div>', projectRoot)
    const spawnMock = fakeSpawn()
    await expect(renderScene('run-42', 'shot-1', projectRoot, '/out/shot-1.mp4', { spawn: spawnMock as never }))
      .rejects.toThrow(/duplicate|class/i)
    expect(spawnMock).not.toHaveBeenCalled()
  })

  it('still renders a real written composition with no duplicate attributes', async () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    writeSceneFile('run-42', 'shot-1', '<div id="x" class="clip headline">fine</div>', projectRoot)
    const spawnMock = fakeSpawn()
    const result = await renderScene('run-42', 'shot-1', projectRoot, '/out/shot-1.mp4', { spawn: spawnMock as never })
    expect(result).toBe('/out/shot-1.mp4')
    expect(spawnMock).toHaveBeenCalled()
  })
})

describe('renderFallbackScene', () => {
  it('substitutes CAPTION and DURATION into the template, HTML-escaped', async () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    const templatesDir = join(projectRoot, 'templates')
    mkdirSync(templatesDir, { recursive: true })
    writeFileSync(
      join(templatesDir, 'generic_fallback.html'),
      '<div data-duration="{{DURATION}}">{{CAPTION}}</div><div data-duration="{{DURATION}}"></div>',
      'utf8',
    )

    const outputPath = join(projectRoot, 'out.mp4')
    const result = await renderFallbackScene(
      'run-42',
      'shot-1',
      projectRoot,
      outputPath,
      '<script>alert(1)</script> & "quoted"',
      2.5,
      { spawn: fakeSpawn() as never },
    )

    expect(result).toBe(outputPath)
    const written = readFileSync(join(projectRoot, 'compositions', 'run-42', 'shot-1_fallback.html'), 'utf8')
    expect(written).toContain('&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot;')
    expect(written).not.toContain('<script>alert(1)</script>')
    expect(written).toContain('data-duration="2.5"')
  })

  it('rejects a composition path that would escape compositions/ via a drive-relative shotId', async () => {
    const projectRoot = mkdtempSync(join(tmpdir(), 'hf-project-'))
    const templatesDir = join(projectRoot, 'templates')
    mkdirSync(templatesDir, { recursive: true })
    writeFileSync(join(templatesDir, 'generic_fallback.html'), '{{CAPTION}} {{DURATION}}', 'utf8')

    await expect(renderFallbackScene('run-42', 'C:evil', projectRoot, join(projectRoot, 'out.mp4'), 'caption', 3, {
      spawn: fakeSpawn() as never,
    })).rejects.toThrow(/outside/i)
  })
})
