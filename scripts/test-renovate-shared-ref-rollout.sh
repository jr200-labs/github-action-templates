#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

config="$ROOT/default.json"
report_linter="$ROOT/scripts/lint-renovate-report-problems.sh"
renovate_workflow="$ROOT/.github/workflows/renovate.yaml"
consumer_workflow="$ROOT/consumers/workflows/renovate.yaml"

for input in repair-lockfiles rebase-branches; do
    if ! grep -q "${input}:" "$renovate_workflow" || ! grep -q "${input}:" "$consumer_workflow"; then
        echo "renovate repair controls must be exposed by both reusable and shared workflows" >&2
        exit 1
    fi
done
if ! grep -q 'repair-renovate-rebase-branches.sh' "$renovate_workflow" || [ ! -x "$ROOT/scripts/repair-renovate-rebase-branches.sh" ]; then
    echo "rebase control must run the executable Renovate branch rebase helper" >&2
    exit 1
fi
if ! grep -q 'repair-renovate-pnpm-lock-branches.sh' "$renovate_workflow" || [ ! -x "$ROOT/scripts/repair-renovate-pnpm-lock-branches.sh" ]; then
    echo "lockfile repair control must run the executable pnpm branch repair helper" >&2
    exit 1
fi

if jq -e '
  .packageRules[]?
  | select((.matchPackageNames // []) | index("jr200-labs/github-action-templates"))
  | select((.groupSlug // "") == "shared-workflow-ref")
  | has("postUpgradeTasks")
' "$config" >/dev/null; then
    echo "shared-ref rollout must use the reusable workflow repair step, not Renovate post-upgrade tasks" >&2
    exit 1
fi

if grep -q 'consumers/scripts/sync-shared.*pnpm install --lockfile-only' "$renovate_workflow"; then
    echo "renovate workflow must not allow the removed shared-ref post-upgrade command" >&2
    exit 1
fi

allowed_count=$(grep -c 'RENOVATE_ALLOWED_COMMANDS: .*\\./scripts/sync-shared' "$renovate_workflow" || true)
if [ "$allowed_count" -ne 2 ]; then
    echo "renovate workflow must allow the local sync-shared command in both Renovate passes" >&2
    exit 1
fi

if ! grep -q 'config-file:' "$renovate_workflow"; then
    echo "renovate workflow must expose a config-file input for scoped lanes" >&2
    exit 1
fi
config_file_count=$(grep -F -c 'RENOVATE_CONFIG_FILE: ${{ steps.targeted-config.outputs.config-file || inputs.config-file }}' "$renovate_workflow" || true)
if [ "$config_file_count" -ne 2 ]; then
    echo "renovate workflow must pass config-file through to both Renovate passes" >&2
    exit 1
fi
if ! grep -Fq 'echo "config-file=.renovate-out/targeted-config.json"' "$renovate_workflow"; then
    echo "targeted Renovate config must use a runner-visible relative path" >&2
    exit 1
fi
if grep -Fq 'echo "config-file=/out/targeted-config.json"' "$renovate_workflow"; then
    echo "targeted Renovate config must not use the container-only /out path" >&2
    exit 1
fi
if ! grep -Fq '.pruneStaleBranches = false' "$renovate_workflow"; then
    echo "targeted Renovate must not prune unrelated stale branches" >&2
    exit 1
fi
if grep -Fq '"matchPackageNames": $dependencies' "$renovate_workflow"; then
    echo "targeted Renovate must not enable dependencies through shared lookup package names" >&2
    exit 1
fi
if ! grep -Fq '"matchDepNames": $dependencies' "$renovate_workflow"; then
    echo "targeted Renovate must enable exact dependency names" >&2
    exit 1
fi

if ! grep -Eq 'uses: actions/cache@v[0-9]+' "$renovate_workflow"; then
    echo "renovate workflow must restore a persisted Renovate cache" >&2
    exit 1
fi
if ! grep -A6 -F 'name: Restore Renovate cache' "$renovate_workflow" \
    | grep -q "if: github.event_name != 'workflow_dispatch'"; then
    echo "manual Renovate runs must bypass the persisted datasource cache" >&2
    exit 1
fi
if ! grep -q 'key: renovate-${{ runner.os }}-${{ github.repository_id }}-${{ github.run_id }}' "$renovate_workflow"; then
    echo "renovate cache key must be run-specific so updated caches are saved" >&2
    exit 1
fi
if ! grep -q 'renovate-${{ runner.os }}-${{ github.repository_id }}-' "$renovate_workflow"; then
    echo "renovate cache must restore from a stable per-repository prefix" >&2
    exit 1
fi
cache_dir_count=$(grep -c 'RENOVATE_CACHE_DIR: /out/cache' "$renovate_workflow" || true)
repository_cache_count=$(grep -c "RENOVATE_REPOSITORY_CACHE: 'enabled'" "$renovate_workflow" || true)
cache_path_count=$(grep -c 'path: .renovate-out/cache' "$renovate_workflow" || true)
out_mount_count=$(grep -c '.renovate-out:/out' "$renovate_workflow" || true)
if [ "$cache_dir_count" -ne 2 ] || [ "$repository_cache_count" -ne 2 ] || [ "$cache_path_count" -ne 1 ] || [ "$out_mount_count" -ne 2 ]; then
    echo "renovate workflow must wire the persisted cache under the mounted /out dir" >&2
    exit 1
fi
if grep -q '.renovate-cache:/tmp/renovate/cache' "$renovate_workflow"; then
    echo "renovate workflow must not use a second docker-volumes entry for cache" >&2
    exit 1
fi

if ! grep -q 'id: renovate-pass2' "$renovate_workflow"; then
    echo "renovate workflow must gate the second Renovate pass" >&2
    exit 1
fi
if ! grep -q 'select(.result? == "repository-changed")' "$renovate_workflow"; then
    echo "renovate pass 2 gate must be driven by the pass 1 repository-changed report result" >&2
    exit 1
fi
if ! grep -q "steps.renovate-pass2.outputs.run == 'true'" "$renovate_workflow"; then
    echo "renovate pass 2 must only run when the pass 1 gate says it is needed" >&2
    exit 1
fi
if ! grep -q "steps.renovate-pass2.outputs.run || 'false'" "$renovate_workflow"; then
    echo "renovate report validation must allow pass 2 to be skipped by the gate" >&2
    exit 1
fi

uv_setup_line=$(grep -n 'uses: astral-sh/setup-uv@' "$renovate_workflow" | head -n1 | cut -d: -f1 || true)
uv_repair_line=$(grep -n 'repair-renovate-uv-lock-branches.sh' "$renovate_workflow" | head -n1 | cut -d: -f1 || true)
if [ -z "$uv_setup_line" ] || [ -z "$uv_repair_line" ] || [ "$uv_setup_line" -ge "$uv_repair_line" ]; then
    echo "renovate workflow must install uv before repairing Renovate Python branches" >&2
    exit 1
fi
if ! sed -n "$((uv_setup_line - 2)),$((uv_setup_line + 2))p" "$renovate_workflow" \
    | grep -q "inputs.dry-run == '' && inputs.repair-branches && hashFiles('pyproject.toml') != ''"; then
    echo "renovate workflow must only install uv for Python repair runs" >&2
    exit 1
fi

node_setup_line=$(grep -n 'uses: actions/setup-node@' "$renovate_workflow" | tail -n1 | cut -d: -f1 || true)
node_resolve_line=$(grep -n 'id: repair-node-version' "$renovate_workflow" | head -n1 | cut -d: -f1 || true)
pnpm_setup_line=$(grep -n 'uses: pnpm/action-setup@' "$renovate_workflow" | tail -n1 | cut -d: -f1 || true)
cog_setup_line=$(grep -n 'uses: cocogitto/cocogitto-action@' "$renovate_workflow" | head -n1 | cut -d: -f1 || true)
repair_line=$(grep -n 'repair-renovate-shared-workflow-branches.sh' "$renovate_workflow" | head -n1 | cut -d: -f1 || true)
if [ -z "$node_setup_line" ] || [ -z "$node_resolve_line" ] || [ -z "$pnpm_setup_line" ] || [ -z "$cog_setup_line" ] || [ -z "$repair_line" ] \
    || [ "$pnpm_setup_line" -ge "$node_resolve_line" ] || [ "$node_resolve_line" -ge "$node_setup_line" ] || [ "$node_setup_line" -ge "$cog_setup_line" ] || [ "$cog_setup_line" -ge "$repair_line" ]; then
    echo "renovate workflow must install pnpm, node, and cocogitto before repairing shared-ref branches" >&2
    exit 1
fi
if ! grep -Eq 'uses: actions/setup-node@v[0-9]+' "$renovate_workflow"; then
    echo "renovate workflow must use a versioned setup-node action" >&2
    exit 1
fi
if ! grep -Eq 'uses: pnpm/action-setup@v[0-9]+' "$renovate_workflow"; then
    echo "renovate workflow must use a versioned pnpm setup action" >&2
    exit 1
fi
if ! grep -A4 'uses: cocogitto/cocogitto-action@' "$renovate_workflow" | grep -q 'install-only: true'; then
    echo "renovate workflow cocogitto setup must be install-only" >&2
    exit 1
fi
if ! grep -A4 'uses: pnpm/action-setup@' "$renovate_workflow" | grep -q 'run_install: false'; then
    echo "renovate workflow pnpm setup must not install dependencies" >&2
    exit 1
fi

if [ ! -x "$report_linter" ]; then
    echo "missing executable Renovate report linter: $report_linter" >&2
    exit 1
fi

allowed_warning="$TMPDIR/allowed-warning.json"
cat > "$allowed_warning" <<'JSON'
{
  "repositories": [
    {
      "repository": "example/repo",
      "problems": [
        {
          "warnings": [
            "Cannot access vulnerability alerts. Please ensure permissions have been granted."
          ]
        }
      ]
    }
  ]
}
JSON
"$report_linter" "$allowed_warning"

allowed_pep440="$TMPDIR/allowed-pep440.json"
cat > "$allowed_pep440" <<'JSON'
{
  "repositories": [
    {
      "repository": "example/repo",
      "problems": [
        {
          "warnings": [
            "pep440: failed to calculate newValue"
          ]
        }
      ]
    }
  ]
}
JSON
"$report_linter" "$allowed_pep440"

allowed_ref_cleanup="$TMPDIR/allowed-ref-cleanup.json"
cat > "$allowed_ref_cleanup" <<'JSON'
{
  "repositories": [
    {
      "repository": "example/repo",
      "problems": [
        {
          "warnings": [
            "Error deleting \"refs/renovate/branches/*\""
          ]
        }
      ]
    }
  ]
}
JSON
"$report_linter" "$allowed_ref_cleanup"

real_error="$TMPDIR/real-error.json"
cat > "$real_error" <<'JSON'
{
  "repositories": [
    {
      "repository": "example/repo",
      "result": "lockfile-error",
      "problems": [
        {
          "msg": "artifact update failed"
        }
      ]
    }
  ]
}
JSON
if "$report_linter" "$real_error" >/tmp/renovate-report-linter.out 2>/tmp/renovate-report-linter.err; then
    echo "expected real Renovate error to fail" >&2
    exit 1
fi
grep -q "artifact update failed" /tmp/renovate-report-linter.err
