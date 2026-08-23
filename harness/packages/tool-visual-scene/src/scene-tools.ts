import { spawn as nodeSpawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

function compositionsRoot(projectRoot: string): string {
  return join(projectRoot, 'compositions')
}

function resolveScopedPath(workspaceId: string, shotId: string, projectRoot: string, ext: string): string {
  const root = compositionsRoot(projectRoot)
  // First line of defense: a workspaceId/shotId should never legitimately be an
  // absolute path. path.isAbsolute() correctly rejects drive-letter paths
  // (C:\..., D:\...) and UNC paths (\\host\share\...) on Windows, which
  // path.relative()-based checks alone do NOT catch across drives (relative()
  // between different drives returns the absolute candidate unchanged, so a
  // '..'-prefix check silently passes).
  if (isAbsolute(workspaceId) || isAbsolute(shotId)) {
    throw new Error(`writeSceneFile: resolved path is outside compositions/: ${join(root, workspaceId, `${shotId}${ext}`)}`)
  }
  const candidate = resolve(root, workspaceId, `${shotId}${ext}`)
  const rel = relative(root, candidate)
  // Second, redundant layer (defense in depth): catches relative traversal.
  if (rel.startsWith('..') || resolve(root, rel) !== candidate) {
    throw new Error(`writeSceneFile: resolved path is outside compositions/: ${candidate}`)
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
  return new Promise((resolvePromise, reject) => {
    const child = spawnFn(
      'npx',
      ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait'],
      { cwd: projectRoot },
    )
    let stderr = ''
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) resolvePromise(outputPath)
      else reject(new Error(stderr.trim() || `hyperframes render exited ${code}`))
    })
  })
}

export function renderScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  options: RenderOptions = {},
): Promise<string> {
  const compositionRelPath = join('compositions', workspaceId, `${shotId}.html`)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}

export function renderFallbackScene(
  workspaceId: string,
  shotId: string,
  projectRoot: string,
  outputPath: string,
  captionText: string,
  options: RenderOptions = {},
): Promise<string> {
  const templatePath = join(projectRoot, 'templates', 'generic_fallback.html')
  const template = readFileSync(templatePath, 'utf8')
  const html = template.replace('{{CAPTION}}', captionText)
  const fallbackShotId = `${shotId}_fallback`
  writeSceneFile(workspaceId, fallbackShotId, html, projectRoot)
  const compositionRelPath = join('compositions', workspaceId, `${fallbackShotId}.html`)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}
