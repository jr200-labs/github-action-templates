#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Exercise the real comparison without invoking the reconciliation loop.
source <(sed -n '/^canon_ruleset() {/,/^}/p; /^diff_or_apply() {/,/^}/p' "$root/scripts/apply-rulesets.sh")
gh() { cat "$tmp/live.json"; }
run() { echo "DRY: mutation"; }
cat > "$tmp/desired.json" <<'JSON'
{"name":"example-author-allowlist","target":"branch","enforcement":"active","conditions":{"ref_name":{"include":["~ALL"],"exclude":[]}},"bypass_actors":[],"rules":[{"type":"commit_author_email_pattern","parameters":{"operator":"regex","negate":false,"pattern":"^owner@example.test$"}}]}
JSON
jq '.rules[0].parameters.name = "" | .id = 123' "$tmp/desired.json" > "$tmp/live.json"
result=$(diff_or_apply example /example "$tmp/desired.json")
[[ "$result" == *'in sync'* ]] || { echo "FAIL: empty API display label reported as drift"; exit 1; }
for mutation in '.rules[0].parameters.negate = true' '.rules[0].parameters.pattern = "different"' '.enforcement = "disabled"' '.bypass_actors = [{"actor_id":1}]'; do
    jq "$mutation" "$tmp/desired.json" > "$tmp/live.json"
    result=$(diff_or_apply example /example "$tmp/desired.json")
    [[ "$result" == *'DRY: mutation'* ]] || { echo "FAIL: enforcement drift was ignored"; exit 1; }
done
echo 'test-ruleset-canonicalization: ok'
