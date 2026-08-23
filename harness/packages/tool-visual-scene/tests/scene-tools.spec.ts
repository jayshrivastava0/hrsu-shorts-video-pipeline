import { describe, expect, it, vi } from 'vitest'
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeSceneFile, renderScene, renderFallbackScene } from '../src/scene-tools.ts'

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
})

describe('renderScene', () => {
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
})
