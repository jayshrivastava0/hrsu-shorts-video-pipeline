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

/**
 * HyperFrames' render runtime applies its own default styling to every `class="clip"` element
 * that the composition's own CSS doesn't otherwise position -- confirmed empirically (not from
 * docs, which don't cover this): a composition with several top-level sibling `.clip` elements
 * (a headline, several diagram nodes, a stat callout) renders with ALL of them piled on top of
 * each other at the same spot, even though the identical markup opened as a plain static HTML
 * file (no HyperFrames involved) lays out perfectly via normal flex/flow. Adding
 * `.clip { position: static !important; }` to the composition's own stylesheet (verified via a
 * real render, both with and without this rule, on the exact composition that produced the
 * overlap) makes every `.clip` element participate in normal CSS layout instead, and eliminates
 * the pile-up entirely. This is the root cause behind the overlapping/garbled compositions seen
 * across three separate real runs on the same blog post -- distinct from the two earlier fixes
 * (duplicate attributes, the broken GSAP CDN URL), which were necessary but not sufficient.
 *
 * Mechanical, not a persona instruction: the persona already failed to prevent two earlier
 * defects (a duplicate `class` attribute, and adding a `<script>` tag it was told not to add),
 * so this is injected unconditionally into every subagent-authored composition rather than left
 * to a prompt the model could omit or contradict.
 */
export function injectClipPositionReset(html: string): string {
  const RESET = '<style>.clip{position:static!important}</style>'
  const styleOpenIndex = html.search(/<style[^>]*>/i)
  if (styleOpenIndex === -1) {
    // No <style> tag to anchor against -- insert before </head> (or, failing that, before
    // </html>) so the reset still applies rather than silently doing nothing.
    if (/<\/head>/i.test(html)) return html.replace(/<\/head>/i, `${RESET}</head>`)
    if (/<\/html>/i.test(html)) return html.replace(/<\/html>/i, `${RESET}</html>`)
    return html + RESET
  }
  // Insert immediately BEFORE the composition's own <style> block (not !important-free inside
  // it) so any element-specific rule the author writes later in their own stylesheet -- e.g. a
  // deliberately positioned element -- still needs its own !important to win; this keeps the
  // reset as the default floor, not an unconditional override no author rule can beat.
  return html.slice(0, styleOpenIndex) + RESET + html.slice(styleOpenIndex)
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

/**
 * Detects an HTML anti-pattern `hyperframes lint` does not check for and that has already
 * produced a real broken render: the same attribute name (most damagingly `class`) written
 * twice on one opening tag, e.g. `<div class="clip" ... class="headline">`. A browser silently
 * keeps only the FIRST occurrence and drops every later one -- the element loses whatever
 * layout/styling the dropped `class` value carried, with no error anywhere in the render
 * pipeline. Confirmed live: a scene-authoring subagent wrote this pattern on every element of a
 * DIAGRAM shot (each element had `class="clip"` then a second `class="..."` for its real layout
 * class), collapsing the whole composition into unstyled, overlapping default-flow text.
 * Deliberately a plain regex scan, not a full HTML parser -- authored compositions are simple,
 * single-file, LLM-generated markup, and a full parser dependency is not worth it for one
 * well-defined, zero-legitimate-use defect.
 */
export function findDuplicateAttributeIssues(html: string): string[] {
  const issues: string[] = []
  const tagPattern = /<([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*?)?)\s*\/?>/g
  let tagMatch: RegExpExecArray | null
  while ((tagMatch = tagPattern.exec(html)) !== null) {
    const tagName = tagMatch[1]
    const attrsBlob = tagMatch[2]
    const attrNamePattern = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"[^"]*"|'[^']*')/g
    const counts = new Map<string, number>()
    let attrMatch: RegExpExecArray | null
    while ((attrMatch = attrNamePattern.exec(attrsBlob)) !== null) {
      const name = attrMatch[1].toLowerCase()
      counts.set(name, (counts.get(name) ?? 0) + 1)
    }
    for (const [name, count] of counts) {
      if (count > 1) {
        issues.push(
          `<${tagName}> has ${count} "${name}" attributes -- a browser only honors the FIRST ` +
          `one and silently drops the rest, so any styling/layout the later ${name} value ` +
          `carried never applies. Merge them into a single ${name} attribute instead (e.g. ` +
          `class="clip headline"), never repeat the same attribute name on one tag.`,
        )
      }
    }
  }
  return issues
}

/**
 * Detects the exact composition-wide failure mode that produced overlapping/garbled shots
 * across two separate real runs: a `<script src>` tag for GSAP pointing at a 404 URL. The real
 * npm package publishes its minified bundle under `dist/` (`gsap@3.12.2/dist/gsap.min.js`), not
 * at the package root -- every shot in a real run wrote `gsap@3.12.2/gsap.min.js` (no `dist/`),
 * which 404s. Confirmed directly against HyperFrames' own render log for one of those shots:
 * `[Browser:PAGEERROR] Cannot read properties of null (reading 'timeline')` followed by
 * `sub_timeline_script_failure` ("script resource(s) failed to load ... the timeline
 * registration they carry can never arrive ... the render proceeds without those animations").
 * Without a registered timeline the renderer has no idea when/where anything belongs, which is
 * what collapsed every element toward the same overlapping region. A prompt instruction alone
 * (scene-author-persona.md) is not enough -- the model wrote the wrong URL despite the persona
 * telling it not to write one at all, so this is caught mechanically the same way
 * `findDuplicateAttributeIssues` catches its defect, before a render is ever attempted.
 */
export function findGsapScriptIssues(html: string): string[] {
  const scriptSrcPattern = /<script\b[^>]*\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>/gi
  const gsapScripts: string[] = []
  let match: RegExpExecArray | null
  while ((match = scriptSrcPattern.exec(html)) !== null) {
    const src = match[1] ?? match[2] ?? ''
    if (/\bgsap\b/i.test(src)) gsapScripts.push(src)
  }
  if (gsapScripts.length === 0) {
    return ['no <script src="..."> tag for GSAP found -- window.__timelines will never be ' +
      'registered and the renderer will render this composition with no animation/layout ' +
      'information at all. Add <script src="https://cdn.jsdelivr.net/npm/gsap@3.12.2/dist/' +
      'gsap.min.js"></script> before your own animation <script> block.']
  }
  const issues: string[] = []
  for (const src of gsapScripts) {
    // The confirmed-broken shape: a gsap CDN/package URL with no `dist/` segment before the
    // final .js filename. Matches jsdelivr (`cdn.jsdelivr.net/npm/gsap@...`) and the equivalent
    // unpkg shape, not just the one CDN seen in the real failure.
    if (/gsap@[^/]+\/(?!dist\/)[^/]*\.m?js/i.test(src) && !/\/dist\//i.test(src)) {
      issues.push(
        `GSAP script src "${src}" is missing the "dist/" path segment and will 404 (the real ` +
        `npm package publishes its bundle at dist/gsap.min.js, not at the package root) -- ` +
        `this exact URL shape already produced a real broken render (renderer log: ` +
        `sub_timeline_script_failure, "script resource(s) failed to load"). Use ` +
        `"https://cdn.jsdelivr.net/npm/gsap@3.12.2/dist/gsap.min.js" instead.`,
      )
    }
  }
  return issues
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
  // Fail BEFORE spending a real render on a composition that is already known to render wrong —
  // this throws into the same retry path `authorVisualScene` already has for any other
  // `render_scene` failure (previousFailureReason carries this exact message into attempt 2), so
  // a broken composition gets one real chance to be fixed by the authoring subagent before the
  // never-blank fallback takes over. No new retry machinery needed. Reading the file can only
  // fail if `write_scene_file` was never actually called for this shot (every real caller calls
  // it first, at this exact resolved path) -- that case is left to `runHyperframesRender`'s own
  // real CLI error below rather than duplicated here, so a missing-file failure still surfaces,
  // just through the existing path instead of a second one.
  let html: string | undefined
  try {
    html = readFileSync(compositionAbsPath, 'utf8')
  } catch {
    html = undefined
  }
  if (html !== undefined) {
    const issues = [...findDuplicateAttributeIssues(html), ...findGsapScriptIssues(html)]
    if (issues.length > 0) {
      throw new Error(`render_scene: composition has broken markup:\n- ${issues.join('\n- ')}`)
    }
  }
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
