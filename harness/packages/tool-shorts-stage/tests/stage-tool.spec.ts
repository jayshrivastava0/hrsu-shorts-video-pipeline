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
