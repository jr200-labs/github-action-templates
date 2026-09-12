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
#   - allow_auto_merge=false by default; when shared-ref automerge is enabled
#     in config, this becomes true as GitHub's prerequisite for queueing vetted
#     shared-ref PRs
#   - delete_branch_on_merge=true
#   - allow_update_branch=true
#   - require_approval_for_fork_pr_workflows=false
#
# Usage:
#   scripts/apply-rulesets.sh [--dry-run] [--org ORG] [--repo ORG/REPO]
#                             [--ruleset NAME] [--targets-file PATH]
#                             [--automerge-config PATH] [--skip-auto-merge]
#
# Requires: gh, jq, yq. Env: gh authenticated as a token with admin on the
# target orgs/repos. Token plan must support the requested scope (org-level
# rulesets need GitHub Team).

set -euo pipefail

DRY_RUN=0
ORG_FILTER=""
RULESET_FILTER=""
SKIP_AUTO_MERGE=0
INTERACTIVE_ORG_SELECTION=0
APPLY_ORG_SCOPE=1
declare -a REPO_FILTERS=()
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS="$REPO_ROOT/rulesets/targets.yaml"
RULESETS_DIR="$REPO_ROOT/rulesets"
SHARED_REF_AUTOMERGE_CONFIG="$RULESETS_DIR/shared-workflow-ref-automerge.json"

usage() {
    sed -n '1,/^set -euo/p' "$0" | sed 's/^# \?//'
    cat <<'EOF'

Options:
  --dry-run            Print API calls instead of applying changes.
  --org <org>          Limit reconciliation to one supported org and prompt
                       Y/n (default Y) for each repo before applying repo-
                       scoped changes. For org-scoped rulesets, the script
                       prompts once before applying the org-wide rule.
  --ruleset <name>     Limit reconciliation to one ruleset from targets.yaml.
  --repo <org/repo>    Limit repo-scope reconciliation to one or more repos.
                       Repeat flag to target multiple repos.
  --targets-file <p>   Read ruleset targets from this YAML file.
  --automerge-config <p>
                       Read shared-ref auto-merge policy from this JSON file.
  --skip-auto-merge    Skip PATCH allow_auto_merge enforcement and shared
                       workflow ref PR auto-merge queueing.
  -h, --help           Show this help.

Examples:
  scripts/apply-rulesets.sh --org jr200-labs --dry-run
  scripts/apply-rulesets.sh --repo jr200-labs/example-repository --ruleset trunk-protect
  scripts/apply-rulesets.sh --targets-file ../consumer/ruleset-targets.yaml --org example-org
EOF
}

configured_orgs() {
    yq -r 'to_entries | .[].value | keys | .[]' "$TARGETS" | sort -u
}

org_supported() {
    local candidate="$1" supported
    while IFS= read -r supported; do
        [ "$supported" = "$candidate" ] && return 0
    done < <(configured_orgs)
    return 1
}

confirm_default_yes() {
    local prompt="$1"
    local reply

    if [ ! -t 0 ]; then
        return 0
    fi

    read -r -p "$prompt [Y/n] " reply || return 1
    case "$reply" in
        ""|[Yy]|[Yy][Ee][Ss]) return 0 ;;
        [Nn]|[Nn][Oo]) return 1 ;;
        *)
            echo "please answer Y or n" >&2
            confirm_default_yes "$prompt"
            ;;
    esac
}

interactive_select_repos() {
    local org="$1"
    local repos repo

    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        if confirm_default_yes "apply to $org/$repo?"; then
            REPO_FILTERS+=("$org/$repo")
        fi
    done <<<"$repos"
}

repo_selected() {
    local org="$1" repo="$2"
    local fq="${org}/${repo}"
    local selected

    if [ "$INTERACTIVE_ORG_SELECTION" = 1 ] && [ "${#REPO_FILTERS[@]}" -eq 0 ]; then
        return 1
    fi

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
        --targets-file) shift; TARGETS="$1" ;;
        --targets-file=*) TARGETS="${1#--targets-file=}" ;;
        --automerge-config) shift; SHARED_REF_AUTOMERGE_CONFIG="$1" ;;
        --automerge-config=*) SHARED_REF_AUTOMERGE_CONFIG="${1#--automerge-config=}" ;;
        --skip-auto-merge) SKIP_AUTO_MERGE=1 ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

for cmd in gh jq yq; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "missing: $cmd" >&2; exit 1; }
done

[ -f "$TARGETS" ] || { echo "missing targets file: $TARGETS" >&2; exit 1; }
[ -f "$SHARED_REF_AUTOMERGE_CONFIG" ] || { echo "missing config: $SHARED_REF_AUTOMERGE_CONFIG" >&2; exit 1; }

if [ -n "$ORG_FILTER" ] && ! org_supported "$ORG_FILTER"; then
    supported_orgs=$(configured_orgs | paste -sd ' ' -)
    echo "unsupported --org '$ORG_FILTER' (configured: $supported_orgs)" >&2
    exit 2
fi

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

if [ -n "$ORG_FILTER" ] && [ "${#REPO_FILTERS[@]}" -eq 0 ]; then
    INTERACTIVE_ORG_SELECTION=1
    interactive_select_repos "$ORG_FILTER"
fi

shared_ref_automerge_enabled() {
    jq -e '.enabled == true' "$SHARED_REF_AUTOMERGE_CONFIG" >/dev/null
}

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
    live=$(gh api "$detail_endpoint" | jq -Sc '{name, target, enforcement, conditions, rules, bypass_actors}')
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

    if [ "$INTERACTIVE_ORG_SELECTION" = 1 ] && [ "$APPLY_ORG_SCOPE" = 0 ]; then
        echo "  org/$org ruleset $name: skipped by prompt"
        return
    fi

    local tmp_body
    tmp_body=$(mktemp)
    # Organization rulesets require a repository targeting condition
    jq '.conditions += {"repository_name": {"include": ["~ALL"], "exclude": []}}' "$body_file" > "$tmp_body"

    existing=$(gh api "/orgs/$org/rulesets" --jq ".[] | select(.name==\"$name\") | .id" | head -1)
    if [ -n "$existing" ]; then
        diff_or_apply "org/$org ruleset $name (id=$existing)" "/orgs/$org/rulesets/$existing" "$tmp_body"
    else
        echo "  org/$org: POST ruleset $name (missing)"
        run gh api "/orgs/$org/rulesets" -X POST --input "$tmp_body" --silent
    fi
    rm -f "$tmp_body"
}

# If a ruleset is canonical at org scope, repo-level rulesets with the same
# name become accidental additive restrictions. Remove those duplicates so the
# org-level body is the single source of truth.
cleanup_repo_duplicates_for_org_ruleset() {
    local org="$1" name="$2"
    local repos

    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        if ! repo_selected "$org" "$repo"; then
            continue
        fi
        local ids
        ids=$(gh api "/repos/$org/$repo/rulesets" --jq ".[] | select(.name==\"$name\" and .source_type==\"Repository\") | .id")
        while IFS= read -r id; do
            [ -z "$id" ] && continue
            echo "  repo/$org/$repo: DELETE duplicate repo-level ruleset $name (id=$id)"
            run gh api "/repos/$org/$repo/rulesets/$id" -X DELETE --silent
        done <<<"$ids"
    done <<<"$repos"
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
        existing=$(gh api "/repos/$org/$repo/rulesets" --jq ".[] | select(.name==\"$name\") | .id" | head -1)
        if [ -n "$existing" ]; then
            diff_or_apply "repo/$org/$repo ruleset $name (id=$existing)" "/repos/$org/$repo/rulesets/$existing" "$body_file"
        else
            echo "  repo/$org/$repo: POST ruleset $name (missing)"
            run gh api "/repos/$org/$repo/rulesets" -X POST --input "$body_file" --silent
        fi
    done <<<"$repos"
}

# Reconcile org/repo-level Actions settings that don't live in rulesets.
# This keeps bot-repaired Renovate PRs from getting stuck behind the
# "approve workflow run" gate after github-actions[bot] amends a branch.
reconcile_actions_private_fork_workflow_policy() {
    local label="$1" endpoint="$2"
    local current approval tmp_body error_summary

    if ! current=$(gh api "$endpoint" 2>&1); then
        error_summary="${current//$'\n'/ }"
        echo "  $label actions private fork workflow policy: skipped ($error_summary)"
        return
    fi

    approval=$(jq -r '.require_approval_for_fork_pr_workflows' <<<"$current")
    if [ "$approval" = "false" ]; then
        return
    fi

    tmp_body=$(mktemp)
    jq -S '
      {
        run_workflows_from_fork_pull_requests,
        send_write_tokens_to_workflows,
        send_secrets_and_variables,
        require_approval_for_fork_pr_workflows: false
      }
    ' <<<"$current" > "$tmp_body"

    echo "  $label: reconcile require_approval_for_fork_pr_workflows=false (drift detected)"
    run gh api "$endpoint" -X PUT --input "$tmp_body" --silent
    rm -f "$tmp_body"
}

is_allowed_shared_ref_author() {
    local login="$1"

    jq -e --arg login "$login" '.allowedAuthorLogins | index($login) != null' "$SHARED_REF_AUTOMERGE_CONFIG" >/dev/null
}

is_allowed_shared_ref_path() {
    local path="$1" glob

    while IFS= read -r glob; do
        [ -z "$glob" ] && continue
        if [[ "$path" == $glob ]]; then
            return 0
        fi
    done < <(jq -r '.allowedPathGlobs[]' "$SHARED_REF_AUTOMERGE_CONFIG")

    return 1
}

queue_shared_workflow_ref_automerge() {
    local org="$1" repo="$2" fq pr_numbers pr_number pr_json title branch expected_title expected_branch url author_login author_is_bot auto_merge bad_paths path
    fq="${org}/${repo}"
    if ! shared_ref_automerge_enabled; then
        return
    fi
    expected_title=$(jq -r '.title' "$SHARED_REF_AUTOMERGE_CONFIG")
    expected_branch=$(jq -r '.headRefName' "$SHARED_REF_AUTOMERGE_CONFIG")

    pr_numbers=$(gh pr list \
        --repo "$fq" \
        --state open \
        --head "$expected_branch" \
        --json number \
        --jq '.[].number')

    while IFS= read -r pr_number; do
        [ -z "$pr_number" ] && continue

        pr_json=$(gh pr view "$pr_number" \
            --repo "$fq" \
            --json title,headRefName,author,autoMergeRequest,files,url)

        title=$(jq -r '.title' <<<"$pr_json")
        branch=$(jq -r '.headRefName' <<<"$pr_json")
        url=$(jq -r '.url' <<<"$pr_json")
        author_login=$(jq -r '.author.login' <<<"$pr_json")
        author_is_bot=$(jq -r '.author.is_bot' <<<"$pr_json")
        auto_merge=$(jq -r '.autoMergeRequest != null' <<<"$pr_json")

        if [ "$title" != "$expected_title" ] || [ "$branch" != "$expected_branch" ]; then
            echo "  repo/$fq PR #$pr_number: skip shared-ref auto-merge (unexpected title/branch)"
            continue
        fi
        if [ "$author_is_bot" != "true" ] || ! is_allowed_shared_ref_author "$author_login"; then
            echo "  repo/$fq PR #$pr_number: skip shared-ref auto-merge (author is not an allowed app: $author_login)"
            continue
        fi
        if [ "$auto_merge" = "true" ]; then
            echo "  repo/$fq PR #$pr_number: shared-ref auto-merge already queued"
            continue
        fi

        bad_paths=""
        while IFS= read -r path; do
            [ -z "$path" ] && continue
            if ! is_allowed_shared_ref_path "$path"; then
                bad_paths="${bad_paths}${path}"$'\n'
            fi
        done < <(jq -r '.files[].path' <<<"$pr_json")

        if [ -n "$bad_paths" ]; then
            echo "  repo/$fq PR #$pr_number: skip shared-ref auto-merge (unexpected paths)"
            printf '%s' "$bad_paths" | sed 's/^/    - /'
            continue
        fi

        echo "  repo/$fq PR #$pr_number: queue shared-ref auto-merge ($url)"
        run gh pr merge "$pr_number" --repo "$fq" --squash --auto --delete-branch
    done <<<"$pr_numbers"
}

# Reconcile repo-level merge hygiene settings that don't live in rulesets.
# Rulesets cover branch protection / PR requirements; GitHub keeps a few
# adjacent behaviors as plain repository settings.
reconcile_repo_settings() {
    local org="$1"
    local scope="$2"
    local repos

    if [ "$scope" = "org" ]; then
        reconcile_actions_private_fork_workflow_policy \
            "org/$org" \
            "/orgs/$org/actions/permissions/fork-pr-workflows-private-repos"
    fi

    if [ "$SKIP_AUTO_MERGE" = 1 ]; then
        echo "  org/$org: skipping repo merge-setting enforcement (--skip-auto-merge)"
    fi

    repos=$(gh api "orgs/$org/repos?per_page=100&type=all" --paginate --jq '.[] | select(.archived==false) | .name')
    while IFS= read -r repo; do
        [ -z "$repo" ] && continue
        if ! repo_selected "$org" "$repo"; then
            continue
        fi

        if [ "$SKIP_AUTO_MERGE" != 1 ]; then
            local current auto_merge delete_branch update_branch
            current=$(gh api "/repos/$org/$repo" -q '{allow_auto_merge: .allow_auto_merge, delete_branch_on_merge: .delete_branch_on_merge, allow_update_branch: .allow_update_branch}')
            auto_merge=$(jq -r '.allow_auto_merge' <<<"$current")
            delete_branch=$(jq -r '.delete_branch_on_merge' <<<"$current")
            update_branch=$(jq -r '.allow_update_branch' <<<"$current")

            local desired_auto_merge
            if shared_ref_automerge_enabled; then
                desired_auto_merge=true
            else
                desired_auto_merge=false
            fi

            if [ "$auto_merge" != "$desired_auto_merge" ] || [ "$delete_branch" != "true" ] || [ "$update_branch" != "true" ]; then
                echo "  repo/$org/$repo: reconcile allow_auto_merge=${desired_auto_merge}, delete_branch_on_merge=true, allow_update_branch=true (drift detected)"
                run gh api -X PATCH "/repos/$org/$repo" \
                    -F allow_auto_merge="$desired_auto_merge" \
                    -F delete_branch_on_merge=true \
                    -F allow_update_branch=true \
                    --silent
            fi

            queue_shared_workflow_ref_automerge "$org" "$repo"
        fi

        if [ "$scope" = "repo" ]; then
            reconcile_actions_private_fork_workflow_policy \
                "repo/$org/$repo" \
                "/repos/$org/$repo/actions/permissions/fork-pr-workflows-private-repos"
        fi
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

        if [ "$INTERACTIVE_ORG_SELECTION" = 1 ] && [ "$scope" = "org" ]; then
            if [ "${#REPO_FILTERS[@]}" -eq 0 ]; then
                echo "  org/$org: no repos selected; skipping org-scoped ruleset $ruleset"
                APPLY_ORG_SCOPE=0
            elif confirm_default_yes "apply org-scoped ruleset '$ruleset' to all repos in $org?"; then
                APPLY_ORG_SCOPE=1
            else
                APPLY_ORG_SCOPE=0
            fi
        fi

        case "$scope" in
            org)
                apply_org  "$org" "$ruleset" "$body"
                cleanup_repo_duplicates_for_org_ruleset "$org" "$ruleset"
                ;;
            repo) apply_repo "$org" "$ruleset" "$body" ;;
            *) echo "unknown scope '$scope' for $ruleset/$org" >&2; exit 1 ;;
        esac
        reconcile_repo_settings "$org" "$scope"
    done <<<"$orgs"
done <<<"$rulesets"

echo "done."
