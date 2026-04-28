#!/usr/bin/env bash
# Lint GHCR container package visibility against an expected policy.
# Catches drift before it manifests as broken Renovate tracking
# (visibility=private requires per-package ACL grants for the docker
# datasource), broken cross-repo `oras pull`, or unintended public
# leaks of closed-source images.
#
# Lives in shared/ so consumer workflows can curl + run it in their
# own org's GITHUB_TOKEN context — same-org packages:read is granted
# automatically, no App token plumbing required.
#
# Usage:
#   lint-package-visibility.sh <org> <expected-visibility> [allowlist-csv]
#
#   org                  GitHub org slug
#   expected-visibility  one of: public | internal | private
#   allowlist-csv        comma-separated package names exempt from the check
#
# Exit codes:
#   0 all packages match expected (or are allowlisted)
#   1 one or more mismatches
#   2 misuse (missing args, gh not authed)
set -euo pipefail

org="${1:?org required}"
expected="${2:?expected-visibility required}"
allowlist_csv="${3:-}"

case "$expected" in
  public|internal|private) ;;
  *) echo "::error::expected-visibility must be public|internal|private (got '$expected')" >&2; exit 2 ;;
esac

if ! command -v gh >/dev/null 2>&1; then
  echo "::error::gh CLI required" >&2; exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "::error::jq required" >&2; exit 2
fi

# Build a lookup table of allowlisted names so we can flag them in the
# OK list (audit trail) instead of silently ignoring.
declare -A allow=()
IFS=',' read -ra _items <<< "$allowlist_csv"
for n in "${_items[@]}"; do
  [ -n "$n" ] && allow["$n"]=1
done

# Collect all container packages across the org. --paginate handles
# orgs with >100 packages; --jq emits one TSV row per package.
mapfile -t rows < <(gh api "orgs/${org}/packages?package_type=container" --paginate --jq '.[] | "\(.name)\t\(.visibility)"')

if [ "${#rows[@]}" -eq 0 ]; then
  echo "lint-package-visibility: no container packages found in ${org} — nothing to lint"
  exit 0
fi

fail=0
ok=0
for row in "${rows[@]}"; do
  name="${row%%$'\t'*}"
  vis="${row#*$'\t'}"

  if [ -n "${allow[$name]:-}" ]; then
    echo "  ALLOWLIST  ${name} (visibility=${vis})"
    continue
  fi

  if [ "$vis" = "$expected" ]; then
    ok=$((ok+1))
  else
    echo "::error::${org}/${name}: visibility=${vis}, expected=${expected} — fix at https://github.com/orgs/${org}/packages/container/${name}/settings"
    fail=1
  fi
done

if [ "$fail" = "1" ]; then
  echo
  echo "lint-package-visibility: FAIL — see ::error:: lines above"
  exit 1
fi

echo "lint-package-visibility: ${org} OK (${ok} packages match expected=${expected})"
