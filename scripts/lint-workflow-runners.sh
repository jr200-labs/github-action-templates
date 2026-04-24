#!/usr/bin/env bash
# Lint GitHub Actions workflows for hardcoded runner OS in `runs-on`.
#
# Policy: every `runs-on` must resolve through the org-level
# `vars.RUNNER_PROFILE` / `vars.RUNNER_PROFILES` indirection, so the
# entire org can flip between hosted and self-hosted runners by
# toggling one org var. Hardcoding `ubuntu-latest` etc. bypasses that
# switch and silently burns hosted-runner minutes when the org is
# meant to be on self-hosted (e.g. after a billing block).
#
# Allowed forms (regex-tested against the value after `runs-on:`):
#
#   ${{ fromJSON(vars.RUNNER_PROFILES)[vars.RUNNER_PROFILE].<tier> }}
#   ${{ inputs.runner || fromJSON(vars.RUNNER_PROFILES)[vars.RUNNER_PROFILE].<tier> }}
#   ${{ needs.*.outputs.* }}        — dynamic resolution from an earlier job (rare)
#   <self-hosted-label>             — explicit self-hosted label (uncommon; require an ALLOW line)
#
# Rejected: any literal matching /^(ubuntu|macos|windows)[-_]/, with or
# without a version suffix. That's the entire hosted-runner family.
#
# Usage:
#   lint-workflow-runners.sh [<workflows-dir>]
#     default: .github/workflows
#
# Exit codes:
#   0 all runs-on values compliant (or no workflow files present)
#   1 one or more hardcoded runner OS literals found
#   2 workflows dir missing
#
# Dependencies: bash, grep. No jq/yq — regex-only by design so the lint
# script has the smallest possible dependency footprint and can run in
# any minimal CI container.

set -euo pipefail

dir="${1:-.github/workflows}"

if [[ ! -d "$dir" ]]; then
  echo "ERROR: workflows dir not found: $dir" >&2
  exit 2
fi

# Gather .yaml / .yml workflow files (either extension — we enforce
# .yaml elsewhere but don't want this script to misfire on legacy .yml
# that happens to exist).
shopt -s nullglob
files=("$dir"/*.yaml "$dir"/*.yml)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
  echo "lint-workflow-runners: no workflow files in $dir — nothing to check" >&2
  exit 0
fi

# Pattern matches the hardcoded-OS literal family at the start of a
# runs-on value. Quoted variants (runs-on: 'ubuntu-latest' or "...")
# are also caught. Leading whitespace required so we don't match
# stray occurrences inside strings/comments.
bad_pattern='^[[:space:]]*runs-on:[[:space:]]*["'"'"']?(ubuntu|macos|windows)[-_]'

fail_count=0
for f in "${files[@]}"; do
  if matches=$(grep -nE "$bad_pattern" "$f"); then
    while IFS= read -r line; do
      echo "FAIL $f:$line" >&2
    done <<<"$matches"
    fail_count=$((fail_count + 1))
  fi
done

if (( fail_count > 0 )); then
  echo "" >&2
  echo "Hardcoded runner OS found. Replace with:" >&2
  echo "  runs-on: \${{ fromJSON(vars.RUNNER_PROFILES)[vars.RUNNER_PROFILE].default }}" >&2
  echo "(or include 'inputs.runner ||' in reusable workflows)" >&2
  exit 1
fi

echo "lint-workflow-runners: ${#files[@]} file(s) checked, no hardcoded runner OS" >&2
exit 0
