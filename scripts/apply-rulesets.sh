#!/usr/bin/env bash
# Reconcile org/repo rulesets to the canonical specs in rulesets/.
#
# Driven by rulesets/targets.yaml (which ruleset → which org → org-scope or
# per-repo-scope). Each ruleset's body lives in rulesets/<name>.json and is
# applied verbatim except for the org-vs-repo endpoint switch.
#
# Idempotent: if a ruleset with the same name already exists at the target,
# the script PUTs the canonical body to it (updating in place); otherwise
# POSTs a new one. Repo-level merge hygiene settings are also reconciled on
# every targeted repo:
#   - allow_auto_merge=false
#   - delete_branch_on_merge=true
#
# Usage:
#   scripts/apply-rulesets.sh [--dry-run] [--org ORG] [--repo ORG/REPO]
#                             [--ruleset NAME] [--skip-auto-merge]
#
# Requires: gh, jq, yq. Env: gh authenticated as a token with admin on the
# target orgs/repos. Token plan must support the requested scope (org-level
# rulesets need GitHub Team).

set -euo pipefail

DRY_RUN=0
ORG_FILTER=""
RULESET_FILTER=""
SKIP_AUTO_MERGE=0
declare -a REPO_FILTERS=()

usage() {
    sed -n '1,/^set -euo/p' "$0" | sed 's/^# \?//'
    cat <<'EOF'

Options:
  --dry-run            Print API calls instead of applying changes.
  --org <org>          Limit reconciliation to one org from targets.yaml.
  --ruleset <name>     Limit reconciliation to one ruleset from targets.yaml.
  --repo <org/repo>    Limit repo-scope reconciliation to one or more repos.
                       Repeat flag to target multiple repos.
  --skip-auto-merge    Skip PATCH allow_auto_merge=false enforcement.
  -h, --help           Show this help.

Examples:
  scripts/apply-rulesets.sh --org jr200-labs --dry-run
  scripts/apply-rulesets.sh --ruleset trunk-protect --repo jr200-labs/mem0-dashboard
EOF
}

repo_selected() {
    local org="$1" repo="$2"
    local fq="${org}/${repo}"
    local selected

    if [ "${#REPO_FILTERS[@]}" -eq 0 ]; then
        return 0
    fi

    for selected in "${REPO_FILTERS[@]}"; do
        if [ "$selected" = "$fq" ]; then
            return 0
        fi
    done

    return 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY_RUN=1 ;;
        --org)       shift; ORG_FILTER="$1" ;;
        --org=*)     ORG_FILTER="${1#--org=}" ;;
        --ruleset)   shift; RULESET_FILTER="$1" ;;
        --ruleset=*) RULESET_FILTER="${1#--ruleset=}" ;;
        --repo)      shift; REPO_FILTERS+=("$1") ;;
        --repo=*)    REPO_FILTERS+=("${1#--repo=}") ;;
        --skip-auto-merge) SKIP_AUTO_MERGE=1 ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -n "$ORG_FILTER" ] && [ "${#REPO_FILTERS[@]}" -gt 0 ]; then
    for selected in "${REPO_FILTERS[@]}"; do
        case "$selected" in
            "$ORG_FILTER"/*) ;;
            *)
                echo "repo filter '$selected' does not match --org '$ORG_FILTER'" >&2
                exit 2
                ;;
        esac
    done
fi

for selected in "${REPO_FILTERS[@]}"; do
    case "$selected" in
        */*) ;;
        *)
            echo "invalid --repo '$selected' (expected ORG/REPO)" >&2
            exit 2
            ;;
    esac
done

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

# Canonicalise a ruleset payload to a stable JSON string for comparison.
# Strips server-only fields (id/href/timestamps/etc.) and sorts keys.
canon_ruleset() {
    jq -Sc '{name, target, enforcement, conditions, rules, bypass_actors}'
}

# Compare canonical body to live; emit DRY: only when they differ.
diff_or_apply() {
    local label="$1" detail_endpoint="$2" body_file="$3"
    local live want
    live=$(gh api "$detail_endpoint" 2>/dev/null | jq -Sc '{name, target, enforcement, conditions, rules, bypass_actors}')
    want=$(canon_ruleset < "$body_file")
    if [ "$live" = "$want" ]; then
        echo "  $label: in sync"
        return
    fi
    echo "  $label: PUT (drift detected)"
    run gh api "$detail_endpoint" -X PUT --input "$body_file" --silent
}

# Apply one ruleset spec to one org at org scope.
apply_org() {
    local org="$1" name="$2" body_file="$3"
    local existing
    existing=$(gh api "/orgs/$org/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
    if [ -n "$existing" ]; then
        diff_or_apply "org/$org ruleset $name (id=$existing)" "/orgs/$org/rulesets/$existing" "$body_file"
    else
        echo "  org/$org: POST ruleset $name (missing)"
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
        if ! repo_selected "$org" "$repo"; then
            continue
        fi
        local existing
        existing=$(gh api "/repos/$org/$repo/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
        if [ -n "$existing" ]; then
            diff_or_apply "repo/$org/$repo ruleset $name (id=$existing)" "/repos/$org/$repo/rulesets/$existing" "$body_file"
        else
            echo "  repo/$org/$repo: POST ruleset $name (missing)"
            run gh api "/repos/$org/$repo/rulesets" -X POST --input "$body_file" --silent
        fi
    done <<<"$repos"
}

# Reconcile repo-level merge hygiene settings that don't live in rulesets.
# Rulesets cover branch protection / PR requirements; GitHub keeps a few
# adjacent behaviors as plain repository settings.
reconcile_repo_settings() {
    local org="$1"
    local repos

    if [ "$SKIP_AUTO_MERGE" = 1 ]; then
        echo "  org/$org: skipping repo merge-setting enforcement (--skip-auto-merge)"
        return
    fi

    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        if ! repo_selected "$org" "$repo"; then
            continue
        fi
        local current auto_merge delete_branch
        current=$(gh api "/repos/$org/$repo" -q '{allow_auto_merge: .allow_auto_merge, delete_branch_on_merge: .delete_branch_on_merge}' 2>/dev/null)
        auto_merge=$(jq -r '.allow_auto_merge' <<<"$current")
        delete_branch=$(jq -r '.delete_branch_on_merge' <<<"$current")

        if [ "$auto_merge" = "false" ] && [ "$delete_branch" = "true" ]; then
            continue
        fi

        echo "  repo/$org/$repo: reconcile allow_auto_merge=false, delete_branch_on_merge=true (drift detected)"
        run gh api -X PATCH "/repos/$org/$repo" \
            -F allow_auto_merge=false \
            -F delete_branch_on_merge=true \
            --silent
    done <<<"$repos"
}

# Iterate targets.yaml.
rulesets=$(yq -r 'keys | .[]' "$TARGETS")
while IFS= read -r ruleset; do
    [ -z "$ruleset" ] && continue
    [ -n "$RULESET_FILTER" ] && [ "$ruleset" != "$RULESET_FILTER" ] && continue
    body="$RULESETS_DIR/$ruleset.json"
    [ -f "$body" ] || { echo "missing body file: $body" >&2; exit 1; }

    echo "=== ruleset: $ruleset ==="
    orgs=$(yq -r ".\"$ruleset\" | keys | .[]" "$TARGETS")
    while IFS= read -r org; do
        [ -z "$org" ] && continue
        [ -n "$ORG_FILTER" ] && [ "$org" != "$ORG_FILTER" ] && continue
        scope=$(yq -r ".\"$ruleset\".\"$org\"" "$TARGETS")
        case "$scope" in
            org)  apply_org  "$org" "$ruleset" "$body" ;;
            repo) apply_repo "$org" "$ruleset" "$body" ;;
            *) echo "unknown scope '$scope' for $ruleset/$org" >&2; exit 1 ;;
        esac
        reconcile_repo_settings "$org"
    done <<<"$orgs"
done <<<"$rulesets"

echo "done."
