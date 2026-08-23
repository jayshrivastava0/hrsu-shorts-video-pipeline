import { spawn as nodeSpawn } from 'node:child_process'

export interface RunStageCliOptions {
  /** Working directory for the subprocess — the `_shorts_engine_impl/` package root. */
  cwd: string
  /** Injectable for tests; defaults to `node:child_process`'s real `spawn`. */
  spawn?: typeof nodeSpawn
}

/**
 * Invoke `python -m shorts_engine.stage_cli <args>` and parse its JSON stdout/stderr line.
 * The bridge CLI (Task 2) always prints exactly one JSON line on success (stdout) or failure
 * (stderr) — see `shorts_engine/stage_cli.py`'s `cmd_init`/`cmd_run_stage`.
 */
export function runStageCli(
  args: string[],
  options: RunStageCliOptions,
): Promise<Record<string, unknown>> {
  const spawnFn = options.spawn ?? nodeSpawn
  return new Promise((resolve, reject) => {
    const child = spawnFn('python', ['-m', 'shorts_engine.stage_cli', ...args], { cwd: options.cwd })
    let stdout = ''
    let stderr = ''
    child.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) {
        resolve(JSON.parse(stdout.trim().split('\n').at(-1) ?? '{}'))
        return
      }
      let message = stderr.trim()
      try {
        const parsed = JSON.parse(stderr.trim().split('\n').at(-1) ?? '{}') as { message?: string }
        if (parsed.message !== undefined) message = parsed.message
      } catch {
        // stderr wasn't JSON (e.g. a Python traceback) — surface it raw.
      }
      reject(new Error(message))
    })
  })
}
