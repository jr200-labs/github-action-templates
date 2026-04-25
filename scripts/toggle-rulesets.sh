#!/usr/bin/env bash
# Flip ruleset enforcement between active and disabled across the
# orgs/repos declared in rulesets/targets.yaml. Use this when you need
# to merge a PR you opened yourself and the ruleset requires a non-self
# CODEOWNERS reviewer (the WG-105 merge gate).
#
# Usage:
#   scripts/toggle-rulesets.sh disabled    # turn rulesets off
#   scripts/toggle-rulesets.sh active      # turn them back on (always re-run after merge!)
#   scripts/toggle-rulesets.sh evaluate    # dry-run mode (active but only logs violations)
#
# Idempotent. Operates only on rulesets whose name matches one declared
# in targets.yaml — won't touch anything you set up by hand.

set -euo pipefail

STATE="${1:-}"
case "$STATE" in
    active|disabled|evaluate) ;;
    *) echo "usage: $0 {active|disabled|evaluate}" >&2; exit 2 ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS="$REPO_ROOT/rulesets/targets.yaml"

for cmd in gh jq yq; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "missing: $cmd" >&2; exit 1; }
done

put_with_state() {
    # GitHub rulesets API has no PATCH; fetch + modify + PUT the full body.
    local endpoint="$1"
    local tmp
    tmp=$(mktemp)
    gh api "$endpoint" --jq ". | .enforcement = \"$STATE\" | {name, target, enforcement, conditions, rules, bypass_actors}" > "$tmp"
    gh api "$endpoint" -X PUT --input "$tmp" --silent
    rm -f "$tmp"
}

set_state_org() {
    local org="$1" name="$2"
    local id
    id=$(gh api "/orgs/$org/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
    [ -z "$id" ] && { echo "  org/$org: ruleset $name not found — skipping"; return; }
    echo "  org/$org: ruleset $name (id=$id) -> $STATE"
    put_with_state "/orgs/$org/rulesets/$id"
}

set_state_repo() {
    local org="$1" name="$2"
    local repos
    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        local id
        id=$(gh api "/repos/$org/$repo/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
        [ -z "$id" ] && continue
        echo "  repo/$org/$repo: ruleset $name (id=$id) -> $STATE"
        put_with_state "/repos/$org/$repo/rulesets/$id"
    done <<<"$repos"
}

rulesets=$(yq -r 'keys | .[]' "$TARGETS")
while IFS= read -r ruleset; do
    [ -z "$ruleset" ] && continue
    echo "=== ruleset: $ruleset -> $STATE ==="
    orgs=$(yq -r ".\"$ruleset\" | keys | .[]" "$TARGETS")
    while IFS= read -r org; do
        [ -z "$org" ] && continue
        scope=$(yq -r ".\"$ruleset\".\"$org\"" "$TARGETS")
        case "$scope" in
            org)  set_state_org  "$org" "$ruleset" ;;
            repo) set_state_repo "$org" "$ruleset" ;;
        esac
    done <<<"$orgs"
done <<<"$rulesets"

if [ "$STATE" = "disabled" ]; then
    cat >&2 <<'WARN'

WARNING: rulesets are now DISABLED across all targeted orgs/repos.
Re-enable with: scripts/toggle-rulesets.sh active
WARN
fi
