import { spawn as nodeSpawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

function compositionsRoot(projectRoot: string): string {
  return join(projectRoot, 'compositions')
}

/** Same discipline as tool-visual-scene/src/scene-tools.ts's resolveScopedPath (including the
 * cross-drive/UNC/drive-relative hardening from that package's final review) — identifiers that
 * reach this function are never themselves paths. Simplified for one composition per workspace
 * (`assembly.html`, not `<shotId>.html`) since assembly has no shot-level fan-out. */
function containsPathSeparatorOrColon(value: string): boolean {
  return value.includes('/') || value.includes('\\') || value.includes(':')
}

function resolveScopedAssemblyPath(workspaceId: string, projectRoot: string): string {
  const root = compositionsRoot(projectRoot)
  const outsideError = () => new Error(
    `resolveScopedAssemblyPath: resolved path is outside compositions/: ${join(root, workspaceId, 'assembly.html')}`,
  )
  if (isAbsolute(workspaceId)) throw outsideError()
  if (containsPathSeparatorOrColon(workspaceId)) throw outsideError()
  const candidate = resolve(root, workspaceId, 'assembly.html')
  const rel = relative(root, candidate)
  if (rel.startsWith('..') || resolve(root, rel) !== candidate) throw outsideError()
  return candidate
}

export function writeCompositionFile(workspaceId: string, html: string, projectRoot: string): string {
  const target = resolveScopedAssemblyPath(workspaceId, projectRoot)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, html, 'utf8')
  return target
}

export interface RenderOptions {
  spawn?: typeof nodeSpawn
}

function runHyperframesRender(
  compositionRelPath: string, projectRoot: string, outputPath: string, options: RenderOptions = {},
): Promise<string> {
  // Identical spawn strategy to tool-visual-scene's scene-tools.ts — see that file's comment for
  // the full investigation of why cmd.exe /c (not bare npx, not shell:true) is required on
  // Windows when the harness's own path contains a space ("HRSU Shorts").
  const spawnFn = options.spawn ?? nodeSpawn
  const [command, args] = process.platform === 'win32'
    ? ['cmd.exe', ['/c', 'npx', 'hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
    : ['npx', ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
  return new Promise((resolvePromise, reject) => {
    const child = spawnFn(command, args, { cwd: projectRoot })
    let stderr = ''
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', reject)
    child.on('close', (code: number) => {
      if (code === 0) resolvePromise(outputPath)
      else reject(new Error(stderr.trim() || `hyperframes render exited ${code}`))
    })
  })
}

export async function renderComposition(
  workspaceId: string, projectRoot: string, outputPath: string, options: RenderOptions = {},
): Promise<string> {
  const compositionAbsPath = resolveScopedAssemblyPath(workspaceId, projectRoot)
  const compositionRelPath = relative(projectRoot, compositionAbsPath)
  return runHyperframesRender(compositionRelPath, projectRoot, outputPath, options)
}

// ---- Deterministic fallback composition (the never-blank safety net) ----

export interface AssemblyShot {
  id: string
  video_path: string
  start_s: number
  duration_s: number
  beat: string
}

export interface WordTiming {
  word: string
  start: number
  end: number
}

export interface AssemblyBrief {
  shots: AssemblyShot[]
  word_timings: WordTiming[]
  audio_path: string
  logo_path: string
  voice_total_s: number
  target_duration_s: number
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** Groups word-level timings into short caption cues — same policy (max 3 words, max 1.5s per
 * cue) as the retired shorts_engine/stages/assemble.py's group_words_into_cues, ported to TS
 * since this generator has no Python in its call path. Used only by the deterministic fallback —
 * the creative subagent (Task 4) is free to caption however it wants. */
export function groupWordsIntoCues(
  words: WordTiming[], maxWords = 3, maxDurS = 1.5,
): { start: number; end: number; text: string }[] {
  const cues: { start: number; end: number; text: string }[] = []
  let buf: WordTiming[] = []
  const flush = () => {
    if (buf.length > 0) {
      cues.push({
        start: buf[0].start, end: buf[buf.length - 1].end,
        text: buf.map((w) => w.word.trim().toUpperCase()).join(' '),
      })
      buf = []
    }
  }
  for (const w of words) {
    if (buf.length > 0 && (buf.length >= maxWords || w.end - buf[0].start > maxDurS)) flush()
    buf.push(w)
  }
  flush()
  return cues
}

/** Builds a complete, self-contained HyperFrames composition HTML string from an AssemblyBrief:
 * one <video> clip per shot (plain absolute path — confirmed to work directly, without copying
 * into the project tree, per Global Constraints item 3), a caption track built from
 * word_timings via groupWordsIntoCues, a text-based brand mark (the same proven technique
 * generic_fallback.html uses — NOT an <img>/background-image asset load, which is unverified,
 * see Global Constraints item 6), and a simple GSAP-animated progress bar. This is the
 * never-blank safety net for the `assembled` stage — deliberately plain, since it only ships
 * after the creative subagent has failed twice. */
export function buildDeterministicAssemblyHtml(brief: AssemblyBrief): string {
  const videoClips = brief.shots.map((shot) => `
      <video id="shot-${escapeHtml(shot.id)}" class="clip" src="${escapeHtml(shot.video_path)}" muted playsinline
             data-start="${shot.start_s}" data-duration="${shot.duration_s}" data-track-index="0"></video>`).join('')

  const cues = groupWordsIntoCues(brief.word_timings)
  const captionTimelineCalls = cues.map((cue) => `
        tl.set(box, { visibility: 'visible' }, ${cue.start});
        tl.to(box, { opacity: 1, duration: 0.1, onStart: () => { textEl.textContent = ${JSON.stringify(cue.text)}; } }, ${cue.start});
        tl.to(box, { opacity: 0, duration: 0.1 }, ${cue.end});
        tl.set(box, { opacity: 0, visibility: 'hidden' }, ${cue.end + 0.1});`).join('')

  const totalDuration = brief.target_duration_s

  return `<!doctype html>
<html><head>
  <meta charset="UTF-8" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    body, html { margin: 0; padding: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0a192f; }
    .brand-mark { position: absolute; top: 60px; left: 0; width: 1080px; text-align: center;
                  font-family: Georgia, "Times New Roman", serif; font-weight: 700; font-size: 44px;
                  letter-spacing: 2px; color: #d4af37; }
    .caption-box { position: absolute; left: 90px; width: 900px; bottom: 260px; display: flex; justify-content: center;
                   background: rgba(10,25,47,0.85); border-radius: 24px; padding: 16px 28px; opacity: 0; visibility: hidden; }
    .caption-text { color: #ccd6f6; font-family: Arial, "Segoe UI", sans-serif; font-weight: 700; font-size: 44px; text-align: center; }
    .progress-track { position: absolute; left: 0; bottom: 6px; width: 1080px; height: 6px; background: rgba(255,255,255,0.15); }
    .progress-bar { position: absolute; left: 0; bottom: 6px; width: 0px; height: 6px; background: #d4af37; }
  </style>
</head><body>
  <div id="root" data-composition-id="assembly-fallback" data-width="1080" data-height="1920" data-duration="${totalDuration}">
    ${videoClips}
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="1">
      <div class="brand-mark">HRSU INDORE</div>
    </div>
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="2">
      <div class="progress-track"></div>
      <div id="progress-bar" class="progress-bar"></div>
    </div>
    <div class="clip" data-start="0" data-duration="${totalDuration}" data-track-index="3">
      <div id="caption-box" class="caption-box"><span id="caption-text" class="caption-text"></span></div>
    </div>
    <audio id="mix" src="${escapeHtml(brief.audio_path)}" data-start="0" data-duration="${totalDuration}" data-track-index="4" data-volume="1"></audio>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    const box = document.getElementById('caption-box');
    const textEl = document.getElementById('caption-text');
    tl.to('#progress-bar', { width: 1080, duration: ${totalDuration}, ease: 'none' }, 0);
    ${captionTimelineCalls}
    window.__timelines['assembly-fallback'] = tl;
  </script>
</body></html>`
}

export async function renderFallbackComposition(
  workspaceId: string, projectRoot: string, outputPath: string, brief: AssemblyBrief,
  options: RenderOptions = {},
): Promise<string> {
  const html = buildDeterministicAssemblyHtml(brief)
  writeCompositionFile(workspaceId, html, projectRoot)
  return renderComposition(workspaceId, projectRoot, outputPath, options)
}
