#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

if grep -Fq 'github-action-templates/master/shared/lint-release-please-config.sh' \
    "$ROOT/.github/workflows/release_please.yaml"; then
    echo "release workflow bypasses the consumer's pinned shared ref for config validation" >&2
    exit 1
fi

invalid_release_config="$TMPDIR/invalid-release-please-config.json"
jq '
  .["release-type"] = "go"
  | .["group-pull-request-title-pattern"] = "chore(${branch}): release ${component} ${version}"
' "$ROOT/shared/release-please-config.base.json" > "$invalid_release_config"
if "$ROOT/shared/lint-release-please-config.sh" "$invalid_release_config" >"$TMPDIR/invalid-release-config.log" 2>&1; then
    echo "release config lint accepted a title pattern without ${scope}" >&2
    exit 1
fi
grep -Fq 'must include ${scope}, ${component}, and ${version}' "$TMPDIR/invalid-release-config.log"

grouped_release_config="$TMPDIR/grouped-release-please-config.json"
jq '.["release-type"] = "go" | .["separate-pull-requests"] = false' \
  "$ROOT/shared/release-please-config.base.json" > "$grouped_release_config"
if "$ROOT/shared/lint-release-please-config.sh" "$grouped_release_config" >"$TMPDIR/grouped-release-config.log" 2>&1; then
    echo "release config lint accepted grouped release PRs" >&2
    exit 1
fi
grep -Fq 'separate-pull-requests must be true' "$TMPDIR/grouped-release-config.log"

make_consumer_repo() {
    local repo_dir="$1"
    mkdir -p "$repo_dir/.github" "$repo_dir/scripts" "$repo_dir/.husky" "$repo_dir/.shared"
    cp "$ROOT/consumers/scripts/sync-shared" "$repo_dir/scripts/sync-shared"
    cat > "$repo_dir/.github/.shared-config.yaml" <<'YAML'
ref: shared-v0.1.0
workflows:
  - hygiene
YAML
    mkdir -p "$repo_dir/.github/workflows"
    cat > "$repo_dir/.github/workflows/ci.yaml" <<'YAML'
name: ci

on:
  pull_request:

jobs:
  ci:
    uses: jr200-labs/github-action-templates/.github/workflows/ci_npmjs.yaml@master
  commitlint:
    uses: jr200-labs/github-action-templates/.github/workflows/lint_commits.yaml@master
  lint-pr-metadata:
    uses: jr200-labs/github-action-templates/.github/workflows/lint_pr_metadata.yaml@master
YAML
    cat > "$repo_dir/package.json" <<'JSON'
{
  "scripts": {
    "prepare": "husky",
    "test": "node --test"
  },
  "devDependencies": {
    "@commitlint/cli": "19.8.1",
    "@commitlint/config-conventional": "19.8.1",
    "husky": "9.1.7",
    "vitest": "3.2.4"
  }
}
JSON
    cat > "$repo_dir/commitlint.config.mjs" <<'JS'
export { default } from './.shared/commitlint.config.mjs';
JS
    cat > "$repo_dir/.shared/commitlint.config.mjs" <<'JS'
export default {};
JS
    cat > "$repo_dir/.husky/commit-msg" <<'SH'
npx --no -- commitlint --edit "$1"
SH
}

consumer_repo="$TMPDIR/consumer"
make_consumer_repo "$consumer_repo"
(
    cd "$consumer_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    test -f .github/workflows/commitlint.yaml
    test -f .github/workflows/sync-shared-drift.yaml
    test -f .github/workflows/ci.yaml
    ! yq -e '.jobs.commitlint' .github/workflows/ci.yaml >/dev/null 2>&1
    ! yq -e '.jobs."lint-pr-metadata"' .github/workflows/ci.yaml >/dev/null 2>&1
    ! grep -q 'lint_pr_metadata.yaml@master' .github/workflows/ci.yaml
    test -x .githooks/commit-msg
    test -x .githooks/lint-message-text.sh
    test -f cog.toml
    test ! -f .shared/commitlint.config.mjs
    test ! -f commitlint.config.mjs
    test ! -f .husky/commit-msg
    test "$(git config --get core.hooksPath)" = ".githooks"
    ! grep -q '"prepare": "husky"' package.json
    ! grep -q '"@commitlint/cli"' package.json
    ! grep -q '"@commitlint/config-conventional"' package.json
    ! grep -q '"husky"' package.json
    grep -q '"vitest": "3.2.4"' package.json
    mv .github/workflows/ci.yaml .github/workflows/bespoke_ci.yaml
    touch .github/workflows/unexpected.yaml
    if STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check; then
        echo "expected sync-shared --check to reject an unexpected workflow" >&2
        exit 1
    fi
    rm .github/workflows/unexpected.yaml
    STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check
)

artifact_repo="$TMPDIR/artifact-consumer"
make_consumer_repo "$artifact_repo"
cat > "$artifact_repo/.github/.shared-config.yaml" <<'YAML'
ref: shared-v0.1.0
workflows:
  - hygiene
  - artifact-renovate
YAML
(
    cd "$artifact_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    workflow=.github/workflows/renovate-artifact-published.yaml
    test -f "$workflow"
    yq -o=json '.' "$workflow" | jq -e '.on.repository_dispatch.types == ["artifact-published"]' >/dev/null
    yq -o=json '.' "$workflow" | jq -e '.concurrency.group == "renovate-artifact-${{ github.repository }}"' >/dev/null
    yq -o=json '.' "$workflow" | jq -e '.concurrency."cancel-in-progress" == false' >/dev/null
    yq -o=json '.' "$workflow" | jq -e '.jobs.renovate.with."dependency-names" == "${{ needs.validate.outputs.dependencies }}"' >/dev/null
    mv .github/workflows/ci.yaml .github/workflows/bespoke_ci.yaml
    STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check
)

package_access_repo="$TMPDIR/package-access-consumer"
make_consumer_repo "$package_access_repo"
cat > "$package_access_repo/.github/.shared-config.yaml" <<'YAML'
ref: shared-v0.1.0
workflows:
  - hygiene
  - package-access
YAML
(
    cd "$package_access_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    workflow=.github/workflows/package-access-drift.yaml
    test -f "$workflow"
    test -x scripts/github-package-access-wizard
    cmp "$ROOT/consumers/files/scripts/github-package-access-wizard" scripts/github-package-access-wizard
    test "$(SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --list-supporting-files)" = "scripts/github-package-access-wizard"
    yq -e '.permissions.packages == "read"' "$workflow" >/dev/null
    yq -e '.jobs."package-access-drift".uses == "jr200-labs/github-action-templates/.github/workflows/check_package_access.yaml@master"' "$workflow" >/dev/null
    mv .github/workflows/ci.yaml .github/workflows/bespoke_ci.yaml
    printf '\n# drift\n' >> scripts/github-package-access-wizard
    if STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check >wizard-drift.log 2>&1; then
        echo "expected sync-shared --check to reject package wizard drift" >&2
        exit 1
    fi
    grep -q 'drift: scripts/github-package-access-wizard differs from canonical' wizard-drift.log
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check
)

size_limit_repo="$TMPDIR/size-limit-consumer"
make_consumer_repo "$size_limit_repo"
cat > "$size_limit_repo/.github/.shared-config.yaml" <<'YAML'
ref: shared-v0.1.0
workflows:
  - node-size-limit
YAML
(
    cd "$size_limit_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    test -f .github/workflows/ci-node-size-limit.yaml
    yq -e '.jobs.ci.with."run-size-limit" == true' .github/workflows/ci-node-size-limit.yaml >/dev/null
    ! yq -e '.jobs.commitlint' .github/workflows/ci-node-size-limit.yaml >/dev/null 2>&1
    mv .github/workflows/ci.yaml .github/workflows/bespoke_ci.yaml
    STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check
)

metadata_repo="$TMPDIR/metadata-consumer"
make_consumer_repo "$metadata_repo"
# Bad consumer input: a repository-specific ci.yaml must not keep a duplicate
# lint-pr-metadata job when the shared hygiene workflow owns it.
cat > "$metadata_repo/.github/workflows/ci.yaml" <<'YAML'
name: ci

on:
  pull_request:

jobs:
  ci:
    uses: jr200-labs/github-action-templates/.github/workflows/ci_npmjs.yaml@master
  lint-pr-metadata:
    uses: jr200-labs/github-action-templates/.github/workflows/lint_pr_metadata.yaml@master
YAML
(
    cd "$metadata_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared
    test -f .github/workflows/lint-pr-metadata.yaml
    test -f .github/workflows/ci.yaml
    ! grep -q 'lint_pr_metadata.yaml@master' .github/workflows/ci.yaml
    mv .github/workflows/ci.yaml .github/workflows/bespoke_ci.yaml
    STRICT=1 SYNC_BASE_URL="file://$ROOT/consumers" ./scripts/sync-shared --check
)

lint_repo="$TMPDIR/lint-consumer"
mkdir -p "$lint_repo/.github"
cp "$ROOT/shared/sync.sh" "$lint_repo/sync.sh"
cat > "$lint_repo/.github/.shared-config.yaml" <<'YAML'
ref: shared-v0.1.0
workflows:
  - hygiene
YAML
touch "$lint_repo/package.json"
cat > "$lint_repo/package.json" <<'JSON'
{
  "scripts": {
    "prepare": "husky",
    "test": "node --test"
  },
  "devDependencies": {
    "@commitlint/cli": "19.8.1",
    "@commitlint/config-conventional": "19.8.1",
    "husky": "9.1.7",
    "vitest": "3.2.4"
  }
}
JSON
mkdir -p "$lint_repo/.husky" "$lint_repo/.shared"
cat > "$lint_repo/commitlint.config.mjs" <<'JS'
export { default } from './.shared/commitlint.config.mjs';
JS
cat > "$lint_repo/.shared/commitlint.config.mjs" <<'JS'
export default {};
JS
cat > "$lint_repo/.husky/commit-msg" <<'SH'
npx --no -- commitlint --edit "$1"
SH
cat > "$lint_repo/pnpm-workspace.yaml" <<'YAML'
packages:
  - "."
YAML
(
    cd "$lint_repo"
    git init -q
    SYNC_BASE_URL="file://$ROOT/shared" ./sync.sh node
    test -f .shared/eslint.config.mjs
    test -f .shared/commitlint.config.mjs
    test -f commitlint.config.mjs
    test -f .husky/commit-msg
    test -x .githooks/commit-msg
    test -x .githooks/lint-message-text.sh
    test -f cog.toml
    test -f release-please-config.json
    test "$(jq -r '.["separate-pull-requests"]' release-please-config.json)" = 'true'
    test "$(jq -r '.["group-pull-request-title-pattern"]' release-please-config.json)" = 'chore${scope}: release${component} ${version}'
    test "$(jq -r '.["pull-request-title-pattern"]' release-please-config.json)" = 'chore${scope}: release${component} ${version}'
    test -f .syncpackrc.yaml
    test "$(git config --get core.hooksPath)" = ".githooks"
    grep -q '"prepare": "husky"' package.json
    grep -q '"@commitlint/cli"' package.json
    grep -q '"@commitlint/config-conventional"' package.json
    grep -q '"husky": "9.1.7"' package.json
    grep -q '"vitest": "3.2.4"' package.json
    grep -qx "packages:" pnpm-workspace.yaml
    grep -qx "minimumReleaseAge: 0" pnpm-workspace.yaml
)
