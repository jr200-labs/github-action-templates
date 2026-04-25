#!/usr/bin/env bash
# Reconcile org/repo rulesets to the canonical specs in rulesets/.
#
# Driven by rulesets/targets.yaml (which ruleset → which org → org-scope or
# per-repo-scope). Each ruleset's body lives in rulesets/<name>.json and is
# applied verbatim except for the org-vs-repo endpoint switch.
#
# Idempotent: if a ruleset with the same name already exists at the target,
# the script PUTs the canonical body to it (updating in place); otherwise
# POSTs a new one. Auto-merge is patched to false on every targeted repo.
#
# Usage:
#   scripts/apply-rulesets.sh [--dry-run]
#
# Requires: gh, jq, yq. Env: gh authenticated as a token with admin on the
# target orgs/repos. Token plan must support the requested scope (org-level
# rulesets need GitHub Team).

set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS="$REPO_ROOT/rulesets/targets.yaml"
RULESETS_DIR="$REPO_ROOT/rulesets"

for cmd in gh jq yq; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "missing: $cmd" >&2; exit 1; }
done

run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "DRY: $*"
    else
        "$@"
    fi
}

# Apply one ruleset spec to one org at org scope.
apply_org() {
    local org="$1" name="$2" body_file="$3"
    local existing
    existing=$(gh api "/orgs/$org/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
    if [ -n "$existing" ]; then
        echo "  org/$org: PUT ruleset $name (id=$existing)"
        run gh api "/orgs/$org/rulesets/$existing" -X PUT --input "$body_file" --silent
    else
        echo "  org/$org: POST ruleset $name"
        run gh api "/orgs/$org/rulesets" -X POST --input "$body_file" --silent
    fi
}

# Apply one ruleset spec to every non-archived repo in an org at repo scope.
apply_repo() {
    local org="$1" name="$2" body_file="$3"
    local repos
    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        local existing
        existing=$(gh api "/repos/$org/$repo/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
        if [ -n "$existing" ]; then
            echo "  repo/$org/$repo: PUT ruleset $name (id=$existing)"
            run gh api "/repos/$org/$repo/rulesets/$existing" -X PUT --input "$body_file" --silent
        else
            echo "  repo/$org/$repo: POST ruleset $name"
            run gh api "/repos/$org/$repo/rulesets" -X POST --input "$body_file" --silent
        fi
    done <<<"$repos"
}

# Disable auto-merge on every non-archived repo in an org.
disable_auto_merge() {
    local org="$1"
    local repos
    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        echo "  repo/$org/$repo: allow_auto_merge=false"
        run gh api -X PATCH "/repos/$org/$repo" -F allow_auto_merge=false --silent
    done <<<"$repos"
}

# Iterate targets.yaml.
rulesets=$(yq -r 'keys | .[]' "$TARGETS")
while IFS= read -r ruleset; do
    [ -z "$ruleset" ] && continue
    body="$RULESETS_DIR/$ruleset.json"
    [ -f "$body" ] || { echo "missing body file: $body" >&2; exit 1; }

    echo "=== ruleset: $ruleset ==="
    orgs=$(yq -r ".\"$ruleset\" | keys | .[]" "$TARGETS")
    while IFS= read -r org; do
        [ -z "$org" ] && continue
        scope=$(yq -r ".\"$ruleset\".\"$org\"" "$TARGETS")
        case "$scope" in
            org)  apply_org  "$org" "$ruleset" "$body" ;;
            repo) apply_repo "$org" "$ruleset" "$body" ;;
            *) echo "unknown scope '$scope' for $ruleset/$org" >&2; exit 1 ;;
        esac
        disable_auto_merge "$org"
    done <<<"$orgs"
done <<<"$rulesets"

echo "done."
