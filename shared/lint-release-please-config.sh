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
# Rule enforced:
#   Every entry under .packages MUST set release-type explicitly
#   (or inherit from a top-level `release-type`). Marker-file
#   cross-checks were tried and dropped — repos legitimately mix
#   release-type=simple with a package.json (devDeps for commitlint
#   etc), making the cross-check more false-positive than catch.
#
# Usage: .shared/lint-release-please-config.sh [config-file]
#   config-file defaults to release-please-config.json
#
# Distributed via shared/sync.sh (common.cache → .shared/) and invoked
# by the release_please reusable workflow as a fail-fast pre-step.
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

# release-please honours a top-level `release-type` as the default for
# every entry under .packages that doesn't override it. The lint must
# match that inheritance — otherwise repos with one top-level setting
# and an empty `.` package (the canonical pattern for single-package
# repos) get false-positives.
top_level_type=$(jq -r '.["release-type"] // ""' "$config")

while IFS=$'\t' read -r pkg_dir release_type; do
  pkg_label="$pkg_dir"
  [ "$pkg_dir" = "." ] && pkg_label="<root>"

  effective_type="$release_type"
  if [ -z "$effective_type" ] || [ "$effective_type" = "null" ]; then
    effective_type="$top_level_type"
  fi

  if [ -z "$effective_type" ]; then
    echo "::error file=${config}::package '${pkg_label}' missing release-type (no per-package value, no top-level default) — release-please defaults to 'node' (expects package.json), which silently breaks Python/Go/Rust repos. Set release-type explicitly."
    fail=1
    continue
  fi
done < <(jq -r '.packages | to_entries[] | "\(.key)\t\(.value["release-type"] // "")"' "$config")

if [ "$fail" = "1" ]; then
  exit 1
fi

echo "lint-release-please-config: $config OK"
