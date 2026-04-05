#!/usr/bin/env node
// Compare a benchmark JSON run against the committed baseline.
// Usage:
//   node bench/compareBench.mjs <current.json> [baseline.json]
//
// Thresholds (on hz, higher = better):
//   > WARN_THRESHOLD (default 10%) slower  -> warn
//   > FAIL_THRESHOLD (default 50%) slower  -> fail (exit 1)
//
// Statistical noise handling:
//   For each benchmark, combined RME = sqrt(base.rme^2 + cur.rme^2). A
//   regression is only reported if it exceeds the combined RME (i.e. we're
//   outside the noise floor).
//
// Environment variables: WARN_THRESHOLD, FAIL_THRESHOLD (percentages).
//
// NOTE: benchmark absolute numbers are environment-dependent. Regenerate the
// committed baseline whenever the CI runner changes (`pnpm bench:update`),
// or delete it to bootstrap from the next CI run.

import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const WARN_THRESHOLD = Number(process.env.WARN_THRESHOLD ?? 10)
const FAIL_THRESHOLD = Number(process.env.FAIL_THRESHOLD ?? 50)
const FAIL_ON_REGRESSION = (process.env.FAIL_ON_REGRESSION ?? 'false') === 'true'

const [, , currentArg, baselineArg] = process.argv
if (!currentArg) {
  console.error('usage: compareBench.mjs <current.json> [baseline.json]')
  process.exit(2)
}
const currentPath = resolve(currentArg)
const baselinePath = resolve(baselineArg ?? `${HERE}/baseline.json`)

if (!existsSync(currentPath)) {
  console.error(`current benchmark file not found: ${currentPath}`)
  process.exit(2)
}
if (!existsSync(baselinePath)) {
  console.log(`::notice title=No baseline::Baseline ${baselinePath} not found — thresholds will not be enforced. Commit the current output as the baseline (pnpm bench:update) to start comparisons.`)
  process.exit(0)
}

const current = JSON.parse(readFileSync(currentPath, 'utf8'))
const baseline = JSON.parse(readFileSync(baselinePath, 'utf8'))

function flatten(report) {
  const out = new Map()
  for (const file of report.files ?? []) {
    for (const group of file.groups ?? []) {
      const fn = group.fullName ?? ''
      // Keep the last two segments of fullName; matches normalized baselines too.
      const parts = fn.split(' > ')
      const groupName = parts.slice(-Math.min(2, parts.length)).join(' > ')
      for (const b of group.benchmarks ?? []) {
        const key = `${groupName} :: ${b.name}`
        out.set(key, { hz: b.hz, mean: b.mean, rme: b.rme ?? 0 })
      }
    }
  }
  return out
}

const baseFlat = flatten(baseline)
const curFlat = flatten(current)

const rows = []
for (const [key, b] of baseFlat) {
  const c = curFlat.get(key)
  if (!c) { rows.push({ key, missing: 'current', base: b }); continue }
  const regression = ((b.hz - c.hz) / b.hz) * 100  // >0 = slower
  const noise = Math.sqrt(b.rme ** 2 + c.rme ** 2)
  rows.push({ key, base: b, cur: c, regression, noise })
}
for (const [key, c] of curFlat) {
  if (!baseFlat.has(key)) rows.push({ key, missing: 'baseline', cur: c })
}

function fmt(n) { return n == null ? '—' : n >= 1000 ? Math.round(n).toLocaleString() : n.toFixed(2) }
function pctStr(p) { return p == null ? '—' : `${p >= 0 ? '+' : ''}${p.toFixed(1)}%` }

let fails = 0, warns = 0, noisy = 0
const lines = [
  '| benchmark | baseline hz | current hz | Δ | noise |',
  '|---|---:|---:|---:|---:|',
]
for (const r of rows) {
  if (r.missing === 'baseline') {
    lines.push(`| ${r.key} | _new_ | ${fmt(r.cur.hz)} | — | — |`)
    continue
  }
  if (r.missing === 'current') {
    lines.push(`| ${r.key} | ${fmt(r.base.hz)} | _removed_ | — | — |`)
    continue
  }
  let status = ''
  const exceedsNoise = Math.abs(r.regression) > r.noise
  if (exceedsNoise) {
    if (r.regression > FAIL_THRESHOLD) { status = ' ❌'; fails++ }
    else if (r.regression > WARN_THRESHOLD) { status = ' ⚠️'; warns++ }
  } else if (Math.abs(r.regression) > WARN_THRESHOLD) {
    status = ' ≈'; noisy++
  }
  lines.push(
    `| ${r.key} | ${fmt(r.base.hz)} | ${fmt(r.cur.hz)} | ${pctStr(-r.regression)}${status} | ±${r.noise.toFixed(1)}% |`,
  )
}

const summary = `${fails} fail, ${warns} warn, ${noisy} noisy (thresholds: warn>${WARN_THRESHOLD}%, fail>${FAIL_THRESHOLD}%; regressions below combined RME are flagged ≈)`
console.log(lines.join('\n'))
console.log()
console.log(summary)

if (process.env.GITHUB_ACTIONS === 'true') {
  // In report-only mode, downgrade ::error to ::warning so regressions still
  // surface as annotations but don't turn the PR check red.
  const failDirective = FAIL_ON_REGRESSION ? 'error' : 'warning'
  for (const r of rows) {
    if (r.missing || r.regression == null) continue
    if (Math.abs(r.regression) <= r.noise) continue
    if (r.regression > FAIL_THRESHOLD) {
      console.log(`::${failDirective} title=Benchmark regression::${r.key}: slowed ${r.regression.toFixed(1)}% (baseline ${fmt(r.base.hz)} → current ${fmt(r.cur.hz)} hz)`)
    } else if (r.regression > WARN_THRESHOLD) {
      console.log(`::warning title=Benchmark regression::${r.key}: slowed ${r.regression.toFixed(1)}% (baseline ${fmt(r.base.hz)} → current ${fmt(r.cur.hz)} hz)`)
    }
  }
  const summaryFile = process.env.GITHUB_STEP_SUMMARY
  if (summaryFile) {
    const { appendFileSync } = await import('node:fs')
    const mode = FAIL_ON_REGRESSION ? '' : ' _(report-only)_'
    appendFileSync(summaryFile, `## Benchmarks${mode}\n\n${lines.join('\n')}\n\n_${summary}_\n`)
  }
}

if (fails > 0 && !FAIL_ON_REGRESSION) {
  console.log(`(report-only mode: ${fails} regression(s) would have failed the job)`)
}

process.exit(fails > 0 && FAIL_ON_REGRESSION ? 1 : 0)
