#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

policy="$ROOT/shared/.githooks/lint-message-text.sh"

good="$TMPDIR/good-message"
bad="$TMPDIR/bad-message"
custom="$TMPDIR/custom-message"
identifier="$TMPDIR/identifier-message"

printf '%s\n' "fix: update shared workflow sync" > "$good"
"$policy" "commit message" "$good"

printf '%s\n' "fix: update generated text from codex" > "$bad"
if "$policy" "commit message" "$bad" >/tmp/message-policy.out 2>/tmp/message-policy.err; then
    echo "expected default blocked term to fail" >&2
    exit 1
fi
grep -q "blocked attribution term" /tmp/message-policy.err

printf '%s\n' "fix: update forbidden marker" > "$custom"
if BANNED_COMMIT_WORDS=forbidden "$policy" "commit message" "$custom" >/tmp/message-policy-custom.out 2>/tmp/message-policy-custom.err; then
    echo "expected custom blocked term to fail" >&2
    exit 1
fi
grep -q "blocked attribution term" /tmp/message-policy-custom.err

for message in \
    "fix(openhands-standard): refresh codex-acp patch" \
    "fix(deps): update @openai/codex" \
    "fix: update codex_adapter"; do
    printf '%s\n' "$message" > "$identifier"
    "$policy" "commit message" "$identifier"
done

printf '%s\n' "fix: update generated text from codex." > "$bad"
if "$policy" "commit message" "$bad" >/tmp/message-policy.out 2>/tmp/message-policy.err; then
    echo "expected punctuated standalone blocked term to fail" >&2
    exit 1
fi

metadata_workflow="$ROOT/.github/workflows/lint_pr_metadata.yaml"
commit_workflow="$ROOT/.github/workflows/lint_commits.yaml"

# Renovate derives generated metadata and commit subjects from dependency
# names. Both workflows must recognize the same trusted generated-PR shape and
# must retain Conventional Commit validation after bypassing attribution scans.
for workflow in "$metadata_workflow" "$commit_workflow"; do
    grep -Fq '"${PR_BRANCH:-}" == renovate/*' "$workflow"
    grep -Fq "\"\${PR_AUTHOR:-}\" == *'[bot]'" "$workflow"
    grep -Fq '"${PR_BODY:-}" == *'"'"'<!--renovate-debug:'"'"'*' "$workflow"
done

grep -Fq 'PR metadata attribution checks: skipped (Renovate)' "$metadata_workflow"
grep -Fq 'PR title attribution check: skipped (Renovate)' "$commit_workflow"
grep -Fq 'Commit attribution checks: skipped (Renovate)' "$commit_workflow"
grep -Fq 'cog verify --file "${title_file}"' "$commit_workflow"
grep -Fq 'cog check "${range}"' "$commit_workflow"
