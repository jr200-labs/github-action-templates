#!/usr/bin/env bash
# Renovate may update generated .github/workflows/* files after post-upgrade
# sync. The GitHub App token used for Renovate branches must include
# workflows:write or GitHub rejects those pushes.

set -euo pipefail

workflow="${1:-.github/workflows/renovate.yaml}"

if [ ! -f "$workflow" ]; then
  echo "lint-renovate-workflow-token: workflow not found: $workflow" >&2
  exit 2
fi

if ! grep -q 'uses: actions/create-github-app-token@' "$workflow"; then
  echo "lint-renovate-workflow-token: Renovate workflow must mint a GitHub App token" >&2
  exit 1
fi

if ! grep -q 'permission-contents: write' "$workflow"; then
  echo "lint-renovate-workflow-token: app token must request contents:write" >&2
  exit 1
fi

if ! grep -q 'permission-pull-requests: write' "$workflow"; then
  echo "lint-renovate-workflow-token: app token must request pull-requests:write" >&2
  exit 1
fi

if ! grep -q 'permission-issues: write' "$workflow"; then
  echo "lint-renovate-workflow-token: app token must request issues:write" >&2
  exit 1
fi

if ! grep -q 'permission-workflows: write' "$workflow"; then
  echo "lint-renovate-workflow-token: app token must request workflows:write" >&2
  exit 1
fi

if grep -q '^    permissions:' "$workflow"; then
  echo "lint-renovate-workflow-token: reusable Renovate jobs must inherit permissions from pinned callers" >&2
  exit 1
fi

if [ "$(grep -c 'RENOVATE_HOST_RULES:.*github.token' "$workflow")" -ne 2 ]; then
  echo "lint-renovate-workflow-token: both Renovate passes must authenticate ghcr.io with github.token" >&2
  exit 1
fi

hosted_image_guard="runner.environment == 'self-hosted' && inputs.renovate-image || 'ghcr.io/renovatebot/renovate'"
if [ "$(grep -Fc "$hosted_image_guard" "$workflow")" -ne 3 ]; then
  echo "lint-renovate-workflow-token: image override must be limited to self-hosted runners for pre-pull and both passes" >&2
  exit 1
fi

while IFS= read -r caller; do
  if ! grep -q '^  packages: read$' "$caller"; then
    echo "lint-renovate-workflow-token: Renovate caller must grant packages:read: $caller" >&2
    exit 1
  fi
done < <(grep -rlF \
  'uses: jr200-labs/github-action-templates/.github/workflows/renovate.yaml@master' \
  consumers/workflows)

mint_line="$(grep -n 'name: Mint App installation token' "$workflow" | head -n1 | cut -d: -f1 || true)"
checkout_line="$(grep -n 'name: Checkout$' "$workflow" | head -n1 | cut -d: -f1 || true)"
checkout_token_line="$(grep -n 'token: \${{ steps.app-token.outputs.token }}' "$workflow" | head -n1 | cut -d: -f1 || true)"

if [ -z "$mint_line" ] || [ -z "$checkout_line" ]; then
  echo "lint-renovate-workflow-token: expected both app token mint and initial checkout steps" >&2
  exit 1
fi

if [ "$mint_line" -ge "$checkout_line" ]; then
  echo "lint-renovate-workflow-token: app token must be minted before checkout so persisted git credentials can push workflow changes" >&2
  exit 1
fi

if [ -z "$checkout_token_line" ] || [ "$checkout_token_line" -le "$checkout_line" ]; then
  echo "lint-renovate-workflow-token: initial checkout must use steps.app-token.outputs.token" >&2
  exit 1
fi

echo "lint-renovate-workflow-token: $workflow OK"
