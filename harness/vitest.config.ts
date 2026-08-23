import { defineConfig } from 'vitest/config'

// Vitest's default `include` only matches `*.test.*`/`*.spec.*`. This
// composition's own round-trip proof lives in `tests/roundtrip.e2e.ts`
// (per the task brief's exact filename), so extend the default patterns
// rather than rename the file away from that name.
export default defineConfig({
  test: {
    include: [
      '**/*.{test,spec}.?(c|m)[jt]s?(x)',
      '**/*.e2e.?(c|m)[jt]s?(x)',
    ],
  },
})
