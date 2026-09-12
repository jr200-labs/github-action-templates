#!/usr/bin/env bash

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bin="$tmp/bin"
capture="$tmp/capture"
mkdir -p "$bin" "$capture"

cat > "$tmp/targets.yaml" <<'YAML'
trunk-protect:
  example-enterprise: org
  jr200-labs: repo
YAML
cp "$root/rulesets/shared-workflow-ref-automerge.json" "$tmp/automerge.json"

cat > "$bin/yq" <<'YQ'
#!/usr/bin/env bash
set -euo pipefail

query="$2"
case "$query" in
  "to_entries | .[].value | keys | .[]")
    echo "example-enterprise"
    echo "jr200-labs"
    ;;
  "keys | .[]")
    echo "trunk-protect"
    ;;
  ".\"trunk-protect\" | keys | .[]")
    echo "example-enterprise"
    echo "jr200-labs"
    ;;
  ".\"trunk-protect\".\"example-enterprise\"")
    echo "org"
    ;;
  ".\"trunk-protect\".\"jr200-labs\"")
    echo "repo"
    ;;
  *)
    echo "unexpected yq query: $*" >&2
    exit 1
    ;;
esac
YQ
chmod +x "$bin/yq"

cat > "$bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail

command="$1"
shift

if [ "$command" = "pr" ]; then
  subcommand="$1"
  shift
  case "$subcommand" in
    list)
      repo=""
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --repo)
            shift
            repo="$1"
            ;;
        esac
        shift
      done
      case "$repo" in
        example-enterprise/private-repo)
          printf '%s\n' 42 43 44
          ;;
        jr200-labs/public-repo)
          echo "42"
          ;;
        *)
          echo "unexpected gh pr list repo: $repo" >&2
          exit 1
          ;;
      esac
      ;;
    view)
      number="$1"
      shift
      repo=""
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --repo)
            shift
            repo="$1"
            ;;
        esac
        shift
      done
      case "$repo" in
        example-enterprise/private-repo)
          case "$number" in
            42)
              jq -n --arg repo "$repo" '{
                title: "fix(deps): update shared workflow ref",
                headRefName: "renovate/shared-workflow-ref",
                author: {login: "app/consumer-ci-integration", is_bot: true},
                autoMergeRequest: null,
                url: ("https://github.com/" + $repo + "/pull/42"),
                files: [
                  {path: ".github/.shared-config.yaml"},
                  {path: ".github/workflows/sync-shared-drift.yaml"},
                  {path: "scripts/sync-shared"}
                ]
              }'
              ;;
            43)
              jq -n --arg repo "$repo" '{
                title: "fix(deps): update shared workflow ref",
                headRefName: "renovate/shared-workflow-ref",
                author: {login: "renovate[bot]", is_bot: true},
                autoMergeRequest: null,
                url: ("https://github.com/" + $repo + "/pull/43"),
                files: [
                  {path: ".github/.shared-config.yaml"},
                  {path: ".github/workflows/sync-shared-drift.yaml"},
                  {path: "scripts/sync-shared"}
                ]
              }'
              ;;
            44)
              jq -n --arg repo "$repo" '{
                title: "fix(deps): update shared workflow ref",
                headRefName: "renovate/shared-workflow-ref",
                author: {login: "app/consumer-ci-integration", is_bot: true},
                autoMergeRequest: null,
                url: ("https://github.com/" + $repo + "/pull/44"),
                files: [
                  {path: ".github/.shared-config.yaml"},
                  {path: "src/unexpected.ts"}
                ]
              }'
              ;;
            *)
              echo "unexpected PR number: $number" >&2
              exit 1
              ;;
          esac
          ;;
        jr200-labs/public-repo)
          jq -n --arg repo "$repo" '{
            title: "fix(deps): update shared workflow ref",
            headRefName: "renovate/shared-workflow-ref",
            author: {login: "app/jr200-labs-cicd-bot", is_bot: true},
            autoMergeRequest: null,
            url: ("https://github.com/" + $repo + "/pull/42"),
            files: [
              {path: ".github/.shared-config.yaml"},
              {path: ".github/workflows/sync-shared-drift.yaml"},
              {path: "scripts/sync-shared"}
            ]
          }'
          ;;
        *)
          echo "unexpected gh pr view repo: $repo" >&2
          exit 1
          ;;
      esac
      ;;
    merge)
      echo "shared-ref auto-merge is disabled; gh pr merge must not be called" >&2
      exit 1
      ;;
    *)
      echo "unexpected gh pr subcommand: $subcommand" >&2
      exit 1
      ;;
  esac
  exit 0
fi

if [ "$command" != "api" ]; then
  echo "unexpected gh command: $command $*" >&2
  exit 1
fi

endpoint=""
method="GET"
input_file=""
fields=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -X|--method)
      shift
      method="$1"
      ;;
    --input)
      shift
      input_file="$1"
      ;;
    -F|--field|--raw-field)
      shift
      fields="${fields}${1}"$'\n'
      ;;
    --jq|-q|--paginate|--silent)
      if [ "$1" = "--jq" ] || [ "$1" = "-q" ]; then
        shift
      fi
      ;;
    *)
      if [ -z "$endpoint" ]; then
        endpoint="$1"
      fi
      ;;
  esac
  shift
done

if [ "$endpoint" = "repos/example-enterprise/private-repo/pulls" ] || [ "$endpoint" = "repos/jr200-labs/public-repo/pulls" ]; then
  echo "unexpected direct pulls API call; use gh pr list/view/merge wrapper in this test" >&2
  exit 1
fi

case "$method:$endpoint" in
  "GET:orgs/example-enterprise/repos?per_page=100&type=all")
    echo "private-repo"
    ;;
  "GET:orgs/jr200-labs/repos?per_page=100&type=all")
    echo "public-repo"
    ;;
  "GET:/repos/example-enterprise/private-repo/rulesets")
    true
    ;;
  "GET:/orgs/example-enterprise/rulesets")
    echo "1"
    ;;
  "GET:/orgs/example-enterprise/rulesets/1")
    cat "$TEST_RULESET_BODY"
    ;;
  "PUT:/orgs/example-enterprise/rulesets/1")
    true
    ;;
  "GET:/repos/jr200-labs/public-repo/rulesets")
    echo "1"
    ;;
  "GET:/repos/example-enterprise/private-repo/rulesets/1")
    cat "$TEST_RULESET_BODY"
    ;;
  "GET:/repos/jr200-labs/public-repo/rulesets/1")
    cat "$TEST_RULESET_BODY"
    ;;
  "GET:/repos/example-enterprise/private-repo")
    printf '{"allow_auto_merge":true,"delete_branch_on_merge":true,"allow_update_branch":true}\n'
    ;;
  "PATCH:/repos/example-enterprise/private-repo")
    printf '%s' "$fields" > "$TEST_CAPTURE_DIR/enterprise-repo-settings.txt"
    true
    ;;
  "GET:/repos/jr200-labs/public-repo")
    printf '{"allow_auto_merge":true,"delete_branch_on_merge":true,"allow_update_branch":true}\n'
    ;;
  "PATCH:/repos/jr200-labs/public-repo")
    printf '%s' "$fields" > "$TEST_CAPTURE_DIR/jr200-repo-settings.txt"
    true
    ;;
  "GET:/orgs/example-enterprise/actions/permissions/fork-pr-workflows-private-repos")
    printf '{"run_workflows_from_fork_pull_requests":true,"send_write_tokens_to_workflows":true,"send_secrets_and_variables":true,"require_approval_for_fork_pr_workflows":true}\n'
    ;;
  "PUT:/orgs/example-enterprise/actions/permissions/fork-pr-workflows-private-repos")
    cp "$input_file" "$TEST_CAPTURE_DIR/org-actions-policy.json"
    ;;
  "GET:/repos/jr200-labs/public-repo/actions/permissions/fork-pr-workflows-private-repos")
    printf '{"run_workflows_from_fork_pull_requests":true,"send_write_tokens_to_workflows":true,"send_secrets_and_variables":true,"require_approval_for_fork_pr_workflows":true}\n'
    ;;
  "PUT:/repos/jr200-labs/public-repo/actions/permissions/fork-pr-workflows-private-repos")
    cp "$input_file" "$TEST_CAPTURE_DIR/actions-policy.json"
    ;;
  *":/repos/example-enterprise/private-repo/actions/permissions/fork-pr-workflows-private-repos")
    echo "organization-scoped Actions policy must be reconciled at org scope only" >&2
    exit 1
    ;;
  *":/orgs/jr200-labs/actions/permissions/fork-pr-workflows-private-repos")
    echo "jr200-labs Actions policy must be reconciled at repo scope only" >&2
    exit 1
    ;;
  *)
    echo "unexpected gh api call: $method $endpoint" >&2
    exit 1
    ;;
esac
GH
chmod +x "$bin/gh"

PATH="$bin:$PATH" \
TEST_RULESET_BODY="$root/rulesets/trunk-protect.json" \
TEST_CAPTURE_DIR="$capture" \
  "$root/scripts/apply-rulesets.sh" \
    --targets-file "$tmp/targets.yaml" \
    --automerge-config "$tmp/automerge.json" \
    --org example-enterprise \
    --repo example-enterprise/private-repo \
    --ruleset trunk-protect >"$tmp/apply-rulesets-actions-policy.out"

PATH="$bin:$PATH" \
TEST_RULESET_BODY="$root/rulesets/trunk-protect.json" \
TEST_CAPTURE_DIR="$capture" \
  "$root/scripts/apply-rulesets.sh" \
    --targets-file "$tmp/targets.yaml" \
    --automerge-config "$tmp/automerge.json" \
    --org jr200-labs \
    --repo jr200-labs/public-repo \
    --ruleset trunk-protect >"$tmp/apply-rulesets-actions-policy-jr200.out"

org_payload="$capture/org-actions-policy.json"
repo_payload="$capture/actions-policy.json"
automerge_capture="$capture/shared-ref-automerge.txt"
enterprise_settings="$capture/enterprise-repo-settings.txt"
jr200_settings="$capture/jr200-repo-settings.txt"
if [ ! -f "$org_payload" ]; then
  echo "expected org Actions private fork workflow approval payload to be written" >&2
  exit 1
fi
if [ ! -f "$repo_payload" ]; then
  echo "expected Actions private fork workflow approval payload to be written" >&2
  exit 1
fi

for payload in "$org_payload" "$repo_payload"; do
  jq -e '
  .run_workflows_from_fork_pull_requests == true and
  .send_write_tokens_to_workflows == true and
  .send_secrets_and_variables == true and
  .require_approval_for_fork_pr_workflows == false
  ' "$payload" >/dev/null
done

if [ -f "$automerge_capture" ]; then
  echo "did not expect shared workflow ref auto-merge queueing while disabled" >&2
  exit 1
fi
for settings in "$enterprise_settings" "$jr200_settings"; do
  if [ ! -f "$settings" ]; then
    echo "expected repo settings patch to be captured: $settings" >&2
    exit 1
  fi
  grep -qx "allow_auto_merge=false" "$settings"
  grep -qx "delete_branch_on_merge=true" "$settings"
  grep -qx "allow_update_branch=true" "$settings"
done

echo "test-apply-rulesets-actions-policy: ok"
