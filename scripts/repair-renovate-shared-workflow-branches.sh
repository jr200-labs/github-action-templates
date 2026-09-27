#!/usr/bin/env bash
# Repair Renovate branches where generated shared workflows drift from the
# canonical sync-shared output. Renovate can update action refs inside copied
# consumer workflows, but those files are generated and drift-check rejects
# direct edits.

set -euo pipefail

base_ref="${1:-origin/${GITHUB_REF_NAME:-master}}"
branch_glob="${2:-renovate/*}"

if [[ -n "${RENOVATE_REPAIR_TOKEN:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  git remote set-url origin "https://x-access-token:${RENOVATE_REPAIR_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
fi

git config user.name "${GIT_AUTHOR_NAME:-${GITHUB_ACTOR:-renovate}[bot]}"
git config user.email "${GIT_AUTHOR_EMAIL:-${GITHUB_ACTOR_ID:-41898282}+${GITHUB_ACTOR:-renovate}[bot]@users.noreply.github.com}"

git fetch --no-tags origin "+refs/heads/${branch_glob}:refs/remotes/origin/${branch_glob}"

base_sha="$(git rev-parse "$base_ref")"
repaired=0
checked=0
repair_root_url="${SYNC_REPAIR_ROOT_URL:-https://raw.githubusercontent.com/jr200-labs/github-action-templates}"

restore_base() {
  git reset --hard HEAD >/dev/null 2>&1 || true
  git clean -fd -e .renovate-out/ -e _gha_templates/ >/dev/null 2>&1 || true
  git checkout --detach "$base_sha" >/dev/null 2>&1 || true
}

shared_config_ref() {
  [ -f .github/.shared-config.yaml ] || return 0
  sed -nE "s/^[[:space:]]*ref:[[:space:]]*['\"]?([^'\"[:space:]]+).*$/\1/p" .github/.shared-config.yaml | head -n1
}

refresh_sync_shared() {
  local ref
  ref="$(shared_config_ref)"
  [ -n "$ref" ] || return 0

  mkdir -p scripts
  if curl -sfL --max-time 10 "${repair_root_url}/${ref}/consumers/scripts/sync-shared" -o scripts/sync-shared.tmp; then
    mv scripts/sync-shared.tmp scripts/sync-shared
    chmod +x scripts/sync-shared
    echo "repair-renovate-shared-workflow: refreshed scripts/sync-shared from ${ref}" >&2
  else
    rm -f scripts/sync-shared.tmp
    echo "repair-renovate-shared-workflow: failed to refresh scripts/sync-shared from ${ref}; using branch copy" >&2
  fi
}

refresh_shared_config_sync() {
  local ref
  ref="$(shared_config_ref)"
  [ -n "$ref" ] || return 0

  mkdir -p .shared
  if curl -sfL --max-time 10 "${repair_root_url}/${ref}/shared/sync.sh" -o .shared/sync.sh.tmp; then
    mv .shared/sync.sh.tmp .shared/sync.sh
    chmod +x .shared/sync.sh
    echo "repair-renovate-shared-workflow: refreshed .shared/sync.sh from ${ref}" >&2
  else
    rm -f .shared/sync.sh.tmp
    echo "repair-renovate-shared-workflow: failed to refresh .shared/sync.sh from ${ref}; using branch copy" >&2
  fi
}

stage_path_if_present() {
  local path="$1"
  if [ -e "$path" ] || git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
    git add -A -- "$path"
  fi
}

while IFS= read -r remote_ref; do
  branch="${remote_ref#refs/remotes/origin/}"
  [[ "$branch" == renovate/* ]] || continue

  if ! git merge-base "$base_sha" "$remote_ref" >/dev/null; then
    echo "repair-renovate-shared-workflow: ${branch} has no merge base with ${base_ref}; skipping" >&2
    continue
  fi

  changed_files="$(git diff --name-only "${base_sha}...${remote_ref}")"
  if ! grep -Eq '^\.github/(workflows/|\.shared-config\.ya?ml$)' <<<"$changed_files"; then
    continue
  fi

  checked=$((checked + 1))
  echo "repair-renovate-shared-workflow: checking ${branch}" >&2

  git checkout -B "$branch" "$remote_ref"

  if [ ! -x scripts/sync-shared ]; then
    echo "repair-renovate-shared-workflow: ${branch} has no executable scripts/sync-shared; skipping" >&2
    restore_base
    continue
  fi

  before_tree="$(git rev-parse HEAD^{tree})"
  refresh_sync_shared
  ./scripts/sync-shared
  refresh_shared_config_sync
  if [ -x .shared/sync.sh ]; then
    ./.shared/sync.sh
  fi

  if [ -f package.json ] && [ -f pnpm-lock.yaml ] && ! git diff --quiet -- package.json; then
    pnpm install --lockfile-only
  fi

  stage_path_if_present .github/.shared-config.yaml
  stage_path_if_present .github/workflows
  stage_path_if_present .gitignore
  stage_path_if_present .githooks
  stage_path_if_present .pre-commit-config.yaml
  stage_path_if_present .release-please.local.json
  stage_path_if_present .syncpackrc.yaml
  stage_path_if_present cog.toml
  stage_path_if_present package.json
  stage_path_if_present pnpm-lock.yaml
  stage_path_if_present pnpm-workspace.yaml
  stage_path_if_present release-please-config.json
  stage_path_if_present scripts/sync-shared
  stage_path_if_present scripts/sync-shared-drift-check
  while IFS= read -r managed_file; do
    [ -z "$managed_file" ] || stage_path_if_present "$managed_file"
  done < <(./scripts/sync-shared --list-supporting-files)
  stage_path_if_present .husky/commit-msg
  stage_path_if_present .shared/commitlint.config.mjs
  stage_path_if_present commitlint.config.js
  stage_path_if_present commitlint.config.cjs
  stage_path_if_present commitlint.config.mjs

  if git diff --cached --quiet; then
    echo "repair-renovate-shared-workflow: ${branch} did not need shared rollout changes" >&2
    restore_base
    continue
  fi

  git commit --amend --no-edit --allow-empty

  after_tree="$(git rev-parse HEAD^{tree})"
  if [[ "$before_tree" == "$after_tree" ]]; then
    echo "repair-renovate-shared-workflow: ${branch} tree unchanged after amend" >&2
    restore_base
    continue
  fi

  git push --force-with-lease origin "HEAD:${branch}"
  repaired=$((repaired + 1))
  restore_base
done < <(git for-each-ref --format='%(refname)' 'refs/remotes/origin/renovate')

restore_base
echo "repair-renovate-shared-workflow: checked=${checked} repaired=${repaired}" >&2
