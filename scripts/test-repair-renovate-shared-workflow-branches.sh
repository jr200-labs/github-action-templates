#!/usr/bin/env bash

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bare="$tmp/origin.git"
work="$tmp/work"

git init --bare "$bare" >/dev/null
git clone "$bare" "$work" >/dev/null 2>&1

cd "$work"
git config user.name tester
git config user.email tester@example.com

mkdir -p .github/workflows scripts
cat > pyproject.toml <<'PYPROJECT'
[project]
name = "demo"
version = "0.1.0"
dependencies = ["demo-dep>=1.0.0"]
PYPROJECT
cat > .github/workflows/drift-check.yaml <<'WORKFLOW'
# GENERATED/SHARED WORKFLOW: copied into consuming repos by sync-shared.
steps:
  - uses: actions/checkout@v6
WORKFLOW
cat > scripts/sync-shared <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p .github/workflows
cat > .github/workflows/drift-check.yaml <<'WORKFLOW'
# GENERATED/SHARED WORKFLOW: copied into consuming repos by sync-shared.
steps:
  - uses: actions/checkout@v6
WORKFLOW
SYNC
chmod +x scripts/sync-shared

git add .github/workflows/drift-check.yaml pyproject.toml scripts/sync-shared
git commit -m "initial" >/dev/null
git branch -M master
git push origin master >/dev/null 2>&1

git checkout -b renovate/demo >/dev/null
perl -0pi -e 's/demo-dep>=1\.0\.0/demo-dep>=1.0.1/' pyproject.toml
perl -0pi -e 's/actions\/checkout\@v6/actions\/checkout\@v7/' .github/workflows/drift-check.yaml
git add .github/workflows/drift-check.yaml pyproject.toml
git commit -m "fix(deps): update github-actions" >/dev/null
git push origin renovate/demo >/dev/null 2>&1

git checkout master >/dev/null

git checkout -b renovate/normalized-empty-amend >/dev/null
cat > .gitattributes <<'ATTRIBUTES'
*.yaml text eol=lf
ATTRIBUTES
cat > scripts/sync-shared <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p .github/workflows
printf '# GENERATED/SHARED WORKFLOW: copied into consuming repos by sync-shared.\r\nsteps:\r\n  - uses: actions/checkout@v6\r\n' > .github/workflows/drift-check.yaml
SYNC
chmod +x scripts/sync-shared
cat > .github/.shared-config.yaml <<'SHARED'
version: 2
SHARED
git add .gitattributes .github/.shared-config.yaml scripts/sync-shared
git commit -m "fix(deps): update shared config only" >/dev/null
git push origin renovate/normalized-empty-amend >/dev/null 2>&1

git checkout master >/dev/null

git checkout -b renovate/generated-workflow-only >/dev/null
perl -0pi -e 's/actions\/checkout\@v6/actions\/checkout\@v7/' .github/workflows/drift-check.yaml
git add .github/workflows/drift-check.yaml
git commit -m "fix(deps): update generated workflow only" >/dev/null
git push origin renovate/generated-workflow-only >/dev/null 2>&1

git checkout master >/dev/null

shared_root="$tmp/shared-root"
mkdir -p "$shared_root/shared-vnext/consumers/scripts" "$shared_root/shared-vnext/shared"
cat > "$shared_root/shared-vnext/consumers/scripts/sync-shared" <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--list-supporting-files" ]; then
  echo scripts/github-package-access-wizard
  echo .shared/package-macos-app.py
  exit 0
fi
mkdir -p .github/workflows .githooks .shared scripts
cat > .github/workflows/drift-check.yaml <<'WORKFLOW'
# GENERATED/SHARED WORKFLOW: copied into consuming repos by sync-shared.
steps:
  - uses: actions/checkout@v6
WORKFLOW
cat > .githooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
cog verify --file "$1"
HOOK
chmod +x .githooks/commit-msg
printf '#!/usr/bin/env bash\necho synced wizard\n' > scripts/github-package-access-wizard
chmod +x scripts/github-package-access-wizard
printf '# generated macOS packaging helper\n' > .shared/package-macos-app.py
printf '.shared/\n' >> .gitignore
rm -f commitlint.config.mjs .shared/commitlint.config.mjs .husky/commit-msg
jq 'del(.scripts.prepare | select(. == "husky"))
  | del(.scripts | select(. == {}))
  | del(.devDependencies["@commitlint/cli"], .devDependencies["@commitlint/config-conventional"], .devDependencies.husky)
  | del(.devDependencies | select(. == {}))' package.json > package.json.tmp
mv package.json.tmp package.json
SYNC
chmod +x "$shared_root/shared-vnext/consumers/scripts/sync-shared"

cat > "$shared_root/shared-vnext/shared/sync.sh" <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail
jq -n '{"separate-pull-requests": true, "packages": {".": {}}}' > release-please-config.json
SYNC
chmod +x "$shared_root/shared-vnext/shared/sync.sh"

fake_bin="$tmp/fake-bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/pnpm" <<'PNPM'
#!/usr/bin/env bash
set -euo pipefail
if [ "$1" != "install" ] || [ "$2" != "--lockfile-only" ]; then
  echo "unexpected pnpm args: $*" >&2
  exit 1
fi
printf 'lockfile refreshed\n' >> pnpm-lock.yaml
PNPM
chmod +x "$fake_bin/pnpm"

git checkout -b renovate/shared-workflow-ref >/dev/null
cat > .github/.shared-config.yaml <<'SHARED'
ref: shared-vnext
workflows:
  - hygiene
SHARED
mkdir -p .husky .shared
cat > package.json <<'JSON'
{
  "scripts": {
    "prepare": "husky"
  },
  "devDependencies": {
    "@commitlint/cli": "21.0.2",
    "@commitlint/config-conventional": "21.0.2",
    "husky": "9.1.7",
    "vitest": "3.2.4"
  }
}
JSON
cat > pnpm-lock.yaml <<'LOCK'
lockfileVersion: '9.0'
LOCK
cat > commitlint.config.mjs <<'JS'
export { default } from './.shared/commitlint.config.mjs';
JS
cat > .shared/commitlint.config.mjs <<'JS'
export default {};
JS
cat > .husky/commit-msg <<'SH'
npx --no -- commitlint --edit "$1"
SH
git add .github/.shared-config.yaml .husky/commit-msg .shared/commitlint.config.mjs commitlint.config.mjs package.json pnpm-lock.yaml
git commit -m "fix(deps): update shared workflow ref" >/dev/null
git push origin renovate/shared-workflow-ref >/dev/null 2>&1

git checkout master >/dev/null

orphan_work="$tmp/orphan"
git clone "$bare" "$orphan_work" >/dev/null 2>&1
(
  cd "$orphan_work"
  git config user.name tester
  git config user.email tester@example.com
  git checkout --orphan renovate/no-merge-base >/dev/null
  git rm -rf . >/dev/null 2>&1 || true
  mkdir -p .github
  cat > .github/.shared-config.yaml <<'SHARED'
version: 1
SHARED
  git add .github/.shared-config.yaml
  git commit -m "fix(deps): update shared workflow ref" >/dev/null
  git push origin renovate/no-merge-base >/dev/null 2>&1
)

PATH="$fake_bin:$PATH" SYNC_REPAIR_ROOT_URL="file://$shared_root" "$root/scripts/repair-renovate-shared-workflow-branches.sh" origin/master 'renovate/*'

mkdir -p .renovate-out
cat > .renovate-out/pass1.report.json <<'JSON'
{"repositories":[]}
JSON
PATH="$fake_bin:$PATH" SYNC_REPAIR_ROOT_URL="file://$shared_root" "$root/scripts/repair-renovate-shared-workflow-branches.sh" origin/master 'renovate/normalized-empty-amend'
if [ ! -f .renovate-out/pass1.report.json ]; then
  echo "expected repair cleanup to preserve Renovate report files" >&2
  exit 1
fi

git fetch origin renovate/demo >/dev/null 2>&1
updated_workflow="$(git show origin/renovate/demo:.github/workflows/drift-check.yaml)"
if grep -q 'actions/checkout@v7' <<<"$updated_workflow"; then
  echo "expected renovate/demo workflow drift to be repaired" >&2
  exit 1
fi
if ! grep -q 'actions/checkout@v6' <<<"$updated_workflow"; then
  echo "expected renovate/demo workflow to contain canonical checkout@v6" >&2
  exit 1
fi

git fetch origin renovate/shared-workflow-ref >/dev/null 2>&1
shared_files="$(git diff --name-only origin/master...origin/renovate/shared-workflow-ref)"
for expected in .github/.shared-config.yaml .githooks/commit-msg .shared/package-macos-app.py package.json pnpm-lock.yaml release-please-config.json scripts/github-package-access-wizard scripts/sync-shared; do
  if ! grep -qx "$expected" <<<"$shared_files"; then
    echo "expected shared workflow ref repair to include $expected" >&2
    echo "$shared_files" >&2
    exit 1
  fi
done
if [ "$(git show origin/renovate/shared-workflow-ref:release-please-config.json | jq -r '."separate-pull-requests"')" != true ]; then
  echo "expected shared workflow ref repair to regenerate release-please-config.json" >&2
  exit 1
fi
for removed in commitlint.config.mjs .shared/commitlint.config.mjs .husky/commit-msg; do
  if git cat-file -e "origin/renovate/shared-workflow-ref:${removed}" 2>/dev/null; then
    echo "expected shared workflow ref repair to remove $removed" >&2
    exit 1
  fi
done
shared_package="$(git show origin/renovate/shared-workflow-ref:package.json)"
if grep -Eq 'commitlint|husky|"prepare"' <<<"$shared_package"; then
  echo "expected shared workflow ref repair to remove old commitlint packages" >&2
  echo "$shared_package" >&2
  exit 1
fi
if ! git show origin/renovate/shared-workflow-ref:pnpm-lock.yaml | grep -q 'lockfile refreshed'; then
  echo "expected shared workflow ref repair to refresh pnpm lockfile" >&2
  exit 1
fi

echo "test-repair-renovate-shared-workflow-branches: ok"
