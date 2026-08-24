import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const PROJECT_ROOT = join(import.meta.dirname, '..', 'hyperframes_scenes_project')
const SCRATCH_DIR = join(PROJECT_ROOT, 'compositions', '_video_retime_check')
const OUT_DIR = join(import.meta.dirname, '..', '_video_retime_check_out')

function ffmpeg(args: string[]): void {
  const res = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', ...args])
  if (res.status !== 0) {
    throw new Error(`ffmpeg ${args.join(' ')} failed: ${res.stderr?.toString()}`)
  }
}

function averageColorAt(mp4Path: string, atSeconds: number): [number, number, number] {
  const res = spawnSync('ffmpeg', [
    '-y', '-loglevel', 'error', '-ss', atSeconds.toFixed(3), '-i', mp4Path,
    '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-',
  ])
  if (res.status !== 0 || res.stdout.length < 3) {
    throw new Error(`averageColorAt(${mp4Path}, ${atSeconds}) failed: ${res.stderr?.toString()}`)
  }
  return [res.stdout[0], res.stdout[1], res.stdout[2]]
}

function runHyperframesRenderSync(compositionRelPath: string, outputPath: string): void {
  // Same spawn strategy as harness/packages/tool-visual-scene/src/scene-tools.ts's
  // runHyperframesRender — see that file's comment for why cmd.exe /c is required on Windows.
  const [command, args] = process.platform === 'win32'
    ? ['cmd.exe', ['/c', 'npx', 'hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
    : ['npx', ['hyperframes', 'render', '-c', compositionRelPath, '-o', outputPath, '--resolution', 'portrait']]
  const res = spawnSync(command, args, { cwd: PROJECT_ROOT })
  if (res.status !== 0) {
    throw new Error(`hyperframes render failed: ${res.stderr?.toString() ?? res.stdout?.toString()}`)
  }
}

function compositionHtml(compositionDuration: number, clipDataDuration: number): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>body,html{margin:0;padding:0;width:1080px;height:1920px;overflow:hidden;background:#000;}video{display:block;width:100%;height:100%;object-fit:cover;}</style>
</head><body>
<div id="root" data-composition-id="retime-check" data-start="0" data-duration="${compositionDuration}" data-width="1080" data-height="1920">
<video id="clip" class="clip" src="compositions/_video_retime_check/red_green.mp4" muted playsinline autoplay
       data-start="0" data-duration="${clipDataDuration}" data-track-index="0"></video>
<script>
window.__timelines = window.__timelines || {};
const clip = document.getElementById('clip');
if (clip) {
  clip.play().catch(() => {});
}
window.__timelines['retime-check'] = gsap.timeline({ paused: true });
</script>
</div></body></html>`
}

describe('HyperFrames <video> clip retiming (data-duration vs native length)', () => {
  const redGreenFixture = join(SCRATCH_DIR, 'red_green.mp4')

  beforeAll(() => {
    mkdirSync(SCRATCH_DIR, { recursive: true })
    mkdirSync(OUT_DIR, { recursive: true })
    const red = join(SCRATCH_DIR, 'red.mp4')
    const green = join(SCRATCH_DIR, 'green.mp4')
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=1:r=10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', red])
    ffmpeg(['-f', 'lavfi', '-i', 'color=c=green:s=64x64:d=1:r=10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', green])
    ffmpeg(['-i', red, '-i', green, '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-pix_fmt', 'yuv420p', redGreenFixture])
  }, 30_000)

  afterAll(() => {
    rmSync(SCRATCH_DIR, { recursive: true, force: true })
    rmSync(OUT_DIR, { recursive: true, force: true })
  })

  test('data-duration exceeding native length holds the last frame — no loop, no blank', () => {
    writeFileSync(join(SCRATCH_DIR, 'longer.html'), compositionHtml(4, 4), 'utf8')
    const outPath = join(OUT_DIR, 'longer.mp4')
    runHyperframesRenderSync('compositions/_video_retime_check/longer.html', outPath)

    const duringRed = averageColorAt(outPath, 0.5)
    const duringGreen = averageColorAt(outPath, 1.5)
    const afterNativeEnd = averageColorAt(outPath, 3.5)

    expect(duringRed[0]).toBeGreaterThan(150)
    expect(duringGreen[1]).toBeGreaterThan(80)
    // Still green 1.5s past the source's native 2s end — held, not looped back to red and not blank.
    expect(afterNativeEnd[1]).toBeGreaterThan(80)
    expect(afterNativeEnd[0]).toBeLessThan(80)
  }, 60_000)

  test('data-duration shorter than native length trims cleanly — no distortion, no error', () => {
    writeFileSync(join(SCRATCH_DIR, 'shorter.html'), compositionHtml(1.5, 1.5), 'utf8')
    const outPath = join(OUT_DIR, 'shorter.mp4')
    runHyperframesRenderSync('compositions/_video_retime_check/shorter.html', outPath)

    expect(averageColorAt(outPath, 0.3)[0]).toBeGreaterThan(150)
    expect(averageColorAt(outPath, 1.4)[1]).toBeGreaterThan(80)
  }, 60_000)
})
