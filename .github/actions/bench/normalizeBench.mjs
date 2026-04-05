#!/usr/bin/env node
// Normalize a vitest benchmark JSON file for committing as a baseline:
// strip absolute paths + volatile fields, keep hz/mean/rme/sampleCount.
//
// Usage: node bench/normalizeBench.mjs <in.json> <out.json>

import { readFileSync, writeFileSync } from 'node:fs'

const [, , inPath, outPath] = process.argv
if (!inPath || !outPath) {
  console.error('usage: normalizeBench.mjs <in.json> <out.json>')
  process.exit(2)
}

const raw = JSON.parse(readFileSync(inPath, 'utf8'))
const KEEP_BENCH_FIELDS = ['name', 'rank', 'rme', 'hz', 'mean', 'sampleCount']

const out = {
  version: 1,
  files: (raw.files ?? []).map((file) => ({
    // drop absolute filepath; keep only the tail (last two path components)
    filepath: (file.filepath ?? '').split('/').slice(-2).join('/'),
    groups: (file.groups ?? []).map((group) => ({
      fullName: (group.fullName ?? '').split(' > ').slice(-Math.min(2, (group.fullName ?? '').split(' > ').length)).join(' > '),
      benchmarks: (group.benchmarks ?? []).map((b) => {
        const o = {}
        for (const k of KEEP_BENCH_FIELDS) if (k in b) o[k] = b[k]
        return o
      }),
    })),
  })),
}

writeFileSync(outPath, JSON.stringify(out, null, 2) + '\n')
console.log(`normalized benchmark written to ${outPath}`)
