import { spawn as nodeSpawn, spawnSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

/**
 * Windows does not kill a spawned child's process tree when its parent dies — a killed/crashed
 * bridge script (this repo's own run-visual-authoring.mts among them) leaves `cmd.exe -> npx ->
 * node -> chrome-headless-shell` running as an orphan forever. Confirmed live: orphaned
 * chrome-headless-shell trees from runs whose parent no longer existed piled up over hours,
 * eventually exhausting Windows desktop-heap resources (`0x800700e8`, "insufficient system
 * resources") and causing the very hyperframes-render failures this file's stdout-drain fix
 * (above) was written to prevent. `process.on('exit', ...)` handlers may only run synchronous
 * code, so cleanup uses `spawnSync('taskkill', ['/F', '/T', '/PID', ...])`, not the async
 * `child_process.exec`. Registered per render call and removed once that call settles, so a
 * long-running process (many renders per pipeline run) doesn't accumulate listeners.
 */
function killTreeOnOurExit(childPid: number | undefined): () => void {
  if (process.platform !== 'win32' || childPid === undefined) return () => {}
  const handler = () => {
    spawnSync('taskkill', ['/F', '/T', '/PID', String(childPid)])
  }
  process.on('exit', handler)
  return () => process.off('exit', handler)
}

function compositionsRoot(projectRoot: string): string {
  return join(projectRoot, 'compositions')
}

/** Identifiers that legitimately reach this function (`workspaceId`/`shotId`) are never
 * themselves paths — reject any that contain a path separator or a drive-letter colon outright,
 * rather than only catching it indirectly via `resolve()`/`relative()` semantics. */
function containsPathSeparatorOrColon(value: string): boolean {
  return value.includes('/') || value.includes('\\') || value.includes(':')
}

function resolveScopedPath(workspaceId: string, shotId: string, projectRoot: string, ext: string): string {
  const root = compositionsRoot(projectRoot)
  const outsideError = () => new Error(
    `resolveScopedPath: resolved path is outside compositions/: ${join(root, workspaceId, `${shotId}${ext}`)}`,
  )
  // First line of defense: a workspaceId/shotId should never legitimately be an
  // absolute path. path.isAbsolute() correctly rejects drive-letter paths
  // (C:\..., D:\...) and UNC paths (\\host\share\...) on Windows, which
  // path.relative()-based checks alone do NOT catch across drives (relative()
  // between different drives returns the absolute candidate unchanged, so a
  // '..'-prefix check silently passes).
  if (isAbsolute(workspaceId) || isAbsolute(shotId)) {
    throw outsideError()
  }
  // Second line of defense: reject separators/colons outright. path.isAbsolute() alone misses
  // Windows "drive-relative" paths like `C:evil` (a drive letter with no separator) — Node
  // considers that NOT absolute, yet it still resolves per-drive-cwd semantics and can escape
  // compositions/. workspaceId/shotId are identifiers, not paths, so neither should ever
  // legitimately contain '/', '\\', or ':' at all.
  if (containsPathSeparatorOrColon(workspaceId) || containsPathSeparatorOrColon(shotId)) {
    throw outsideError()
  }
  const candidate = resolve(root, workspaceId, `${shotId}${ext}`)
  const rel = relative(root, candidate)
  // Third, redundant layer (defense in depth): catches relative traversal.
  if (rel.startsWith('..') || resolve(root, rel) !== candidate) {
    throw outsideError()
  }
  return candidate
}

export function writeSceneFile(
  workspaceId: string,
  shotId: string,
  html: string,
  projectRoot: string,
): string {
  const target = resolveScopedPath(workspaceId, shotId, projectRoot, '.html')
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, html, 'utf8')
  return target
}

export interface RenderOptions {
  /** Injectable for tests; defaults to `node:child_process`'s real `spawn`. */
  spawn?: typeof nodeSpawn
}

function runHyperframesRender(
  compositionRelPath: string,
  projectRoot: string,
  outputPath: string,
  options: RenderOptions = {},
): Promise<string> {
  const spawnFn = options.spawn ?? nodeSpawn
  // Three approaches were tried against the real installed CLI in this repo's harness (whose own
  // path contains a space, "HRSU Shorts" — a real-world case, not a hypothetical):
  //   1. Bare `spawn('npx', ...)`: Node's spawn() does no PATHEXT resolution, so this throws
  //      `ENOENT` on Windows even though `npx` (really `npx.cmd`) is on PATH.
  //   2. `spawn('npx.cmd', ..., { shell: true })`: Node's shell mode concatenates argv into one
  //      unescaped command line (see Node's own DEP0190 deprecation warning), which silently
  //      corrupted every path argument containing a space.
  //   3. `spawn('npx.cmd', ...)` directly (no shell): throws `spawn EINVAL` — Node's
  //      child_process rejects spawning a `.cmd`/`.bat` file directly without going through a
  //      shell (reproduced directly on the installed Node 24 / Windows 11 in this environment).
  // Routing through `cmd.exe /c` explicitly (not `shell: true`, which is `cmd.exe /d /s /c
  // "<concatenated-string>"`) keeps argv as a real, separately-quoted array — Node's own Windows
  // arg-escaping (`internal/child_process`) applies to each element even when the target is
  // `cmd.exe`, so a path containing a space survives as one argument. Verified directly: a probe
  // script spawning `cmd.exe -> echo <path with spaces>` this way round-trips the path unchanged.
  const [command, args] = process.platform === 'win32'
    ? ['cmd.exe', ['/c', 'npx', 'hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
    : ['npx', ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
  return new Promise((resolvePromise, reject) => {
    const child = spawnFn(
      command,
      args,
      { cwd: projectRoot },
    )
    const unregisterKillOnExit = killTreeOnOurExit(child.pid)
    let stderr = ''
    // Drain stdout — see the identical comment in tool-assembly's composition-tools.ts. Piped
    // stdio that nobody reads fills its OS buffer and blocks the child forever on its next
    // write. Short per-shot renders happen to stay under the buffer, which is why only the long
    // assembly render deadlocked in practice; the hazard is the same on this path.
    child.stdout?.on('data', () => {})
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', (err) => { unregisterKillOnExit(); reject(err) })
    child.on('close', (code: number) => {
      unregisterKillOnExit()
      if (code === 0) resolvePromise(outputPath)
      else reject(new Error(stderr.trim() || `hyperframes render exited ${code}`))
    })
  })
}

export async function renderScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  options: RenderOptions = {},
): Promise<string> {
  // Route the composition path through the same scoped-path guard `writeSceneFile` uses (Fix 3):
  // previously this built the path via a plain `join()`, so a shotId/workspaceId that slipped
  // past validation elsewhere (or a caller that skipped it) could point `hyperframes render` at
  // an arbitrary local file via `-c`. `async` (rather than a plain function returning the inner
  // promise) matters here: it turns resolveScopedPath's synchronous throw into a rejected
  // promise instead of an immediate synchronous exception, so callers can uniformly `await`/
  // `.rejects` this function regardless of which failure mode fires.
  const compositionAbsPath = resolveScopedPath(workspaceId, shotId, projectRoot, '.html')
  const compositionRelPath = relative(projectRoot, compositionAbsPath)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export async function renderFallbackScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  captionText: string,
  durationSeconds: number,
  options: RenderOptions = {},
): Promise<string> {
  const templatePath = join(projectRoot, 'templates', 'generic_fallback.html')
  const template = readFileSync(templatePath, 'utf8')
  // Caption text (and, defensively, the duration value) originates from scraped blog content
  // reaching this template as plain substitution — escape both before substituting so `<`, `&`,
  // etc. in the source text can't break or inject into the rendered composition (Fix 6).
  const html = template
    .replace(/\{\{CAPTION\}\}/g, escapeHtml(captionText))
    .replace(/\{\{DURATION\}\}/g, escapeHtml(String(durationSeconds)))
  const fallbackShotId = `${shotId}_fallback`
  writeSceneFile(workspaceId, fallbackShotId, html, projectRoot)
  // Same scoped-path guard as `renderScene` (Fix 3), for consistency — `writeSceneFile` above
  // already validated this exact path, so this call is redundant-but-safe defense in depth.
  const compositionAbsPath = resolveScopedPath(workspaceId, fallbackShotId, projectRoot, '.html')
  const compositionRelPath = relative(projectRoot, compositionAbsPath)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}
