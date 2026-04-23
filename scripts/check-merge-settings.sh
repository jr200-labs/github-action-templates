#!/usr/bin/env bash
# Check merge settings drift across all non-archived, non-template repos
# in a GitHub org.
#
# Policy:
#   allow_merge_commit = false
#   allow_squash_merge = true
#   allow_rebase_merge = true
#
# Rationale: tools that derive changelogs from git history (e.g.
# release-please) double-count commits when PRs land as merge commits
# rather than squash or rebase. Enforcing squash/rebase-only also keeps
# history linear and easier to bisect.
#
# Usage:
#   check-merge-settings.sh <org>
#
# Auth:
#   Uses whatever token `gh` picks up from the environment (GH_TOKEN,
#   GITHUB_TOKEN, or `gh auth login`). Needs at minimum `repo` read
#   scope on the target org.
#
# Output:
#   TSV lines (repo<TAB>drift-summary) for non-compliant repos on stdout.
#   Scan summary on stderr. Exits 0 if no drift, 1 if drift, 2 on API
#   failure.
#
# Implementation note:
#   The REST list-org-repos endpoint does NOT return merge flags; they
#   only appear on GET /repos/{owner}/{repo}. To avoid N REST calls, use
#   GraphQL with a paginated repositories() selection and ask for
#   mergeCommitAllowed / squashMergeAllowed / rebaseMergeAllowed directly.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <org>" >&2
  exit 2
fi

org="$1"

# shellcheck disable=SC2016  # $org / $cursor are GraphQL variables, not shell
gql_query='
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(first: 100, after: $cursor, isArchived: false) {
      pageInfo { endCursor hasNextPage }
      nodes {
        name
        isTemplate
        mergeCommitAllowed
        squashMergeAllowed
        rebaseMergeAllowed
      }
    }
  }
}'

# gh api graphql --paginate handles the cursor automatically when the
# query includes a $cursor variable + pageInfo.endCursor. Each page
# returns a full response object; collapse the stream with jq -s.
if ! raw=$(gh api graphql \
  --paginate \
  -F org="$org" \
  -f query="$gql_query"); then
  echo "ERROR: GraphQL query failed for org=${org}" >&2
  exit 2
fi

nodes=$(echo "$raw" | jq -s '[.[].data.organization.repositories.nodes[] | select(.isTemplate == false)]')

total=$(echo "$nodes" | jq 'length')
drift_count=0

while IFS=$'\t' read -r name merge squash rebase; do
  drift=()
  [[ "$merge"  != "false" ]] && drift+=("mergeCommit=$merge (want false)")
  [[ "$squash" != "true"  ]] && drift+=("squashMerge=$squash (want true)")
  [[ "$rebase" != "true"  ]] && drift+=("rebaseMerge=$rebase (want true)")

  if (( ${#drift[@]} > 0 )); then
    printf '%s\t%s\n' "${org}/${name}" "$(IFS='; '; echo "${drift[*]}")"
    drift_count=$((drift_count + 1))
  fi
done < <(echo "$nodes" | jq -r '.[] | [.name, .mergeCommitAllowed, .squashMergeAllowed, .rebaseMergeAllowed] | @tsv')

echo "scanned org=${org}: total=${total} drift=${drift_count}" >&2

if (( drift_count > 0 )); then
  exit 1
fi
exit 0
