#!/usr/bin/env bash
# Validate release-please-config.json so misconfigurations are caught
# before release-please runs and silently degrades to its `node` default.
#
# Background. release-please defaults release-type to `node` when a
# package omits it. On a Python repo this manifests as
#   "release-please failed: Missing required file: package.json"
# AFTER the conventional-commit PR has been merged, blocking every
# subsequent release. The default is silent — no warning at PR time, no
# warning when the config is added — so the failure mode only surfaces
# when someone tries to ship.
#
# Rules enforced:
#   1. Every entry under .packages MUST set release-type explicitly.
#   2. The declared release-type MUST match the package directory's
#      project marker file (pyproject.toml → python, package.json →
#      node, go.mod → go, Cargo.toml → rust). Mismatch fails. No marker
#      → skip cross-check (release-type still required from rule 1).
#
# Usage: scripts/lint-release-please-config.sh [config-file]
#   config-file defaults to release-please-config.json
set -euo pipefail

config="${1:-release-please-config.json}"

if [ ! -f "$config" ]; then
  echo "lint-release-please-config: $config not present, skipping"
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "::error::jq required but not installed"
  exit 1
fi

fail=0

# Map marker file → expected release-type. Order doesn't matter; first
# match wins per package dir.
declare -A marker_to_type=(
  ["pyproject.toml"]="python"
  ["package.json"]="node"
  ["go.mod"]="go"
  ["Cargo.toml"]="rust"
)

while IFS=$'\t' read -r pkg_dir release_type; do
  pkg_label="$pkg_dir"
  [ "$pkg_dir" = "." ] && pkg_label="<root>"

  if [ -z "$release_type" ] || [ "$release_type" = "null" ]; then
    echo "::error file=${config}::package '${pkg_label}' missing release-type — release-please defaults to 'node' (expects package.json), which silently breaks Python/Go/Rust repos. Set release-type explicitly."
    fail=1
    continue
  fi

  detected=""
  for marker in "${!marker_to_type[@]}"; do
    if [ -f "${pkg_dir}/${marker}" ]; then
      detected="${marker_to_type[$marker]}"
      break
    fi
  done

  if [ -n "$detected" ] && [ "$detected" != "$release_type" ]; then
    echo "::error file=${config}::package '${pkg_label}' declares release-type='${release_type}' but the directory contains a project marker for '${detected}'. Fix one or the other."
    fail=1
  fi
done < <(jq -r '.packages | to_entries[] | "\(.key)\t\(.value["release-type"] // "")"' "$config")

if [ "$fail" = "1" ]; then
  exit 1
fi

echo "lint-release-please-config: $config OK"
