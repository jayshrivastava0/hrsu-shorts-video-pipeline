import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const harnessRoot = resolve(here, '..')

/**
 * Extracts the literal string value of a top-level-mapping `key: |` YAML
 * block scalar from raw YAML source, without pulling in a YAML parser
 * dependency the harness doesn't otherwise need.
 *
 * This only supports exactly the shape `cordis.yml` uses: a `persona: |`
 * line at some indentation, followed by content lines indented deeper than
 * that key, ending at the first line that dedents back to (or past) the
 * key's own indentation (or end of file). Chomping follows YAML's default
 * "clip" behavior: the extracted text keeps a single trailing newline and
 * drops any extra trailing blank lines.
 */
function extractYamlLiteralBlock(yamlText: string, key: string): string {
  const lines = yamlText.split('\n')
  const keyLineRe = new RegExp(`^(\\s*)${key}:\\s*\\|\\s*$`)

  let keyIndent = -1
  let startIndex = -1
  for (let i = 0; i < lines.length; i++) {
    const match = keyLineRe.exec(lines[i])
    if (match) {
      keyIndent = match[1].length
      startIndex = i + 1
      break
    }
  }
  if (startIndex === -1) {
    throw new Error(`Could not find a "${key}: |" block scalar in the given YAML text`)
  }

  // Determine the content indentation from the first non-blank line.
  let contentIndent = -1
  for (let i = startIndex; i < lines.length; i++) {
    if (lines[i].trim() === '') continue
    const indent = lines[i].length - lines[i].trimStart().length
    if (indent <= keyIndent) break // dedented back out before any content
    contentIndent = indent
    break
  }
  if (contentIndent === -1) {
    throw new Error(`"${key}: |" block scalar appears to have no content`)
  }

  const collected: string[] = []
  for (let i = startIndex; i < lines.length; i++) {
    const line = lines[i]
    if (line.trim() === '') {
      collected.push('')
      continue
    }
    const indent = line.length - line.trimStart().length
    if (indent < contentIndent) break // dedented out of the block
    collected.push(line.slice(contentIndent))
  }

  // Clip chomping: strip trailing blank lines, keep exactly one trailing newline.
  while (collected.length > 0 && collected[collected.length - 1] === '') {
    collected.pop()
  }
  return collected.join('\n') + '\n'
}

describe('persona sync between system_prompt.md and cordis.yml', () => {
  it('keeps cordis.yml\'s embedded persona byte-for-byte identical to system_prompt.md', () => {
    const systemPromptPath = resolve(harnessRoot, 'system_prompt.md')
    const cordisPath = resolve(harnessRoot, 'cordis.yml')

    const systemPromptText = readFileSync(systemPromptPath, 'utf8')
    const cordisText = readFileSync(cordisPath, 'utf8')

    const embeddedPersona = extractYamlLiteralBlock(cordisText, 'persona')

    // system_prompt.md is the human-edited source of truth; normalize its
    // line endings and ensure exactly one trailing newline so this
    // comparison isn't defeated by incidental whitespace differences that
    // don't affect what actually gets loaded/rendered.
    const normalizedSystemPrompt = systemPromptText.replace(/\r\n/g, '\n').replace(/\n*$/, '\n')
    const normalizedEmbeddedPersona = embeddedPersona.replace(/\r\n/g, '\n')

    expect(normalizedEmbeddedPersona).toBe(normalizedSystemPrompt)
  })
})
