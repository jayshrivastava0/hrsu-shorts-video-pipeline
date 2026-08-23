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

    expect(spawnMock).toHaveBeenCalledWith(
      'npx',
      ['hyperframes', 'render', '-c', join('compositions', 'run-42', 'shot-1.html'),
       '-o', '/out/shot-1.mp4', '--resolution', 'portrait'],
      expect.objectContaining({ cwd: '/project' }),
    )
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
