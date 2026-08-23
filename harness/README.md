# HRSU Shorts pipeline harness

Cordis composition that runs the HRSU Shorts pipeline agent against a **local Ollama** model
through our own `@hrsu/dsh-llm-ollama` plugin — not a DeepSeek/pi-ai cloud route. See
`cordis.yml` for the full composition and `system_prompt.md` for the agent persona (the two must
stay in sync by hand — see "Persona sync" below).

## Requirements

- **Node >= 22.15.0** (pinned in `.nvmrc` and enforced by `package.json`'s `engines.node` plus
  `.npmrc`'s `engine-strict=true`). If you use nvm: `nvm use`. This version floor matches what the
  installed `@deepseek-ai/*` packages were built/tested against; older Node has produced subtle
  ESM/module-resolution failures in this harness that are not worth debugging.
- **pnpm** (version pinned via `packageManager` in `package.json`, currently `pnpm@9.15.9`).
- A running **Ollama** server reachable at `OLLAMA_HOST` (default `http://localhost:11434`) if you
  want to run the e2e round-trip test or actually drive the agent.

## Install

```bash
pnpm install
```

`.npmrc` sets `ignore-scripts=true`, so no dependency's install/postinstall scripts run — this is
a deliberate supply-chain guard, not an oversight. If a future dependency genuinely needs a build
step, allow it explicitly via pnpm's `onlyBuiltDependencies` rather than removing this line. See
the inline comments in `.npmrc` for the full rationale on both settings.

## Running the test suites

There are two independent test suites in this workspace:

```bash
# Unit tests for the local Ollama plugin package
pnpm --filter @hrsu/dsh-llm-ollama test

# Harness-level tests (vitest picks up *.test.*, *.spec.*, and *.e2e.* files
# per this workspace's vitest.config.ts), run from harness/
pnpm exec vitest run
```

The harness-level suite includes `tests/roundtrip.e2e.ts`, which drives a real request through
the composition to a local Ollama server. If Ollama isn't reachable at `OLLAMA_HOST`/
`localhost:11434`, that test calls `ctx.skip()` and is reported as **skipped**, not passed — it
does not silently no-op.

## Known-untested script

`package.json` defines a `dsh` script (`node --import tsx/esm node_modules/@deepseek-ai/dsh/lib/bin.js`)
for driving the `dsh` CLI directly against this composition. It has not been exercised as part of
this harness's bootstrap work and is likely broken — treat it as unverified until someone actually
runs `pnpm dsh` end-to-end and fixes what doesn't work.

## Persona sync

`cordis.yml`'s `agent-spine` plugin config embeds the agent persona as literal text under
`agents[].persona` because the installed `@deepseek-ai/dsh-agent-spine-demo@0.1.1-rc.2` /
`dsh-system-prompt@0.1.1-rc.2` packages only document `persona` as inline config text — there is
no documented file-path/import option, and `!!js require(...)` doesn't work in this ESM cordis
loader (see the comment block at the top of `cordis.yml` for the full investigation). That means
`system_prompt.md` (source of truth for humans editing the prompt) and the persona block in
`cordis.yml` (what actually ships to the agent) are two copies of the same text that must be kept
byte-for-byte identical by hand. `tests/persona-sync.spec.ts` asserts this — if you edit one, edit
the other and re-run that test before committing.
