#!/usr/bin/env node
// Run vitest bench N times and keep the best-of-N (max hz) per benchmark.
// Smooths over runner preemption, JIT warm-up, and transient noise.
//
// Usage: node bench/runBest.mjs <runs> <output.json>
//   e.g. node bench/runBest.mjs 3 bench/current.json

import { spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [, , runsArg, outputArg] = process.argv
const runs = Math.max(1, Number(runsArg ?? 3))
const output = outputArg ?? 'bench/current.json'

const tmpDir = mkdtempSync(join(tmpdir(), 'bench-'))
const runFiles = []

try {
  for (let i = 0; i < runs; i++) {
    const runPath = join(tmpDir, `run-${i}.json`)
    console.log(`\n=== bench run ${i + 1} / ${runs} ===`)
    const res = spawnSync(
      'npx',
      [
        'vitest',
        'bench',
        '--run',
        '--config',
        'vitest.bench.config.ts',
        '--outputJson',
        runPath,
      ],
      { stdio: 'inherit' },
    )
    if (res.status !== 0) {
      console.error(`bench run ${i + 1} exited with ${res.status}`)
      process.exit(res.status ?? 1)
    }
    runFiles.push(runPath)
  }

  // Merge: for each benchmark, keep the run with the highest hz.
  const reports = runFiles.map((p) => JSON.parse(readFileSync(p, 'utf8')))
  const merged = structuredClone(reports[0])
  for (let f = 0; f < (merged.files?.length ?? 0); f++) {
    for (let g = 0; g < (merged.files[f].groups?.length ?? 0); g++) {
      for (let b = 0; b < (merged.files[f].groups[g].benchmarks?.length ?? 0); b++) {
        // find the best run for this benchmark
        let best = merged.files[f].groups[g].benchmarks[b]
        for (let r = 1; r < reports.length; r++) {
          const candidate = reports[r].files?.[f]?.groups?.[g]?.benchmarks?.[b]
          if (candidate && candidate.hz > best.hz) best = candidate
        }
        merged.files[f].groups[g].benchmarks[b] = best
      }
    }
  }

  writeFileSync(output, JSON.stringify(merged, null, 2))
  console.log(`\nbest-of-${runs} merged output written to ${output}`)
} finally {
  rmSync(tmpDir, { recursive: true, force: true })
}
