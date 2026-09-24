#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
reusable="$ROOT/.github/workflows/release_please.yaml"
release_caller="$ROOT/consumers/workflows/release-please.yaml"
docker_caller="$ROOT/consumers/workflows/build-docker-image.yaml"
artifact_callers=(
  "$docker_caller"
  "$ROOT/consumers/workflows/build-helm-chart.yaml"
  "$ROOT/consumers/workflows/publish-crate.yaml"
  "$ROOT/consumers/workflows/publish-npm-package.yaml"
  "$ROOT/consumers/workflows/publish-oci-artifact.yaml"
  "$ROOT/consumers/workflows/publish-wheel.yaml"
)

grep -Fq 'releases: ${{ steps.release.outputs.releases_created' "$reusable" || \
    grep -Fq 'releases: ${{ steps.normalize-releases.outputs.releases' "$reusable"
grep -Fq 'id: release-retry' "$reusable"
grep -Fq "if: steps.release.outcome == 'failure'" "$reusable"
grep -Fq 'Snapshot releases before release-please' "$reusable"
grep -Fq 'BEFORE_RELEASES="$before_releases" AFTER_RELEASES="$after_releases"' "$reusable"
grep -Fq 'release: ${{ fromJSON(needs.release.outputs.releases) }}' "$release_caller"
grep -Fq '"component": "${{ matrix.release.component }}"' "$release_caller"
grep -Fq '"release-id": ${{ matrix.release.release_id }}' "$release_caller"
grep -Fq 'RELEASE_COMPONENT: ${{ fromJson(needs.configure.outputs.context).component' "$docker_caller"
grep -Fq 'if [ -n "$RELEASE_COMPONENT" ] && [ "$RELEASE_COMPONENT" != "." ]; then' "$docker_caller"
grep -Fq 'select(.component == strenv(RELEASE_COMPONENT))' "$docker_caller"
grep -Fq 'success-tag-prefix: ${{ matrix.success-tag-prefix' "$docker_caller"
for caller in "${artifact_callers[@]}"; do
  grep -Fq "run-name: \${{ github.event.client_payload['release-tag']" "$caller"
done
test "$(grep -c 'include-component-in-tag' "$reusable")" -ge 2

config='{"packages":{".":{"component":"application"},"cmd/worker":{"component":"worker"}}}'
paths='[".","cmd/worker"]'
outputs='{"tag_name":"application-v1.2.3","version":"1.2.3","sha":"aaa","cmd/worker--tag_name":"worker-v0.4.0","cmd/worker--version":"0.4.0","cmd/worker--sha":"bbb"}'

releases=$(jq -cn \
    --argjson paths "$paths" \
    --argjson outputs "$outputs" \
    --argjson config "$config" '
      [
        $paths[] as $path
        | (if $path == "." then "" else ($path + "--") end) as $prefix
        | {
            path: $path,
            component: ($config.packages[$path].component // $path),
            tag_name: $outputs[$prefix + "tag_name"],
            version: $outputs[$prefix + "version"],
            sha: $outputs[$prefix + "sha"]
          }
      ]
    ')

jq -e '
  length == 2
  and .[0] == {path: ".", component: "application", tag_name: "application-v1.2.3", version: "1.2.3", sha: "aaa"}
  and .[1] == {path: "cmd/worker", component: "worker", tag_name: "worker-v0.4.0", version: "0.4.0", sha: "bbb"}
' <<<"$releases" >/dev/null

reconciler="$ROOT/scripts/reconcile-release-please.sh"
manifest=$(mktemp)
config_file=$(mktemp)
trap 'rm -f "${images:-}" "$manifest" "$config_file"' EXIT
printf '%s\n' '{".":"1.2.3","cmd/worker":"0.4.0"}' >"$manifest"
printf '%s\n' \
  '{"include-component-in-tag":false,"include-v-in-tag":true,"packages":{".":{"component":"application"},"cmd/worker":{"component":"worker","include-component-in-tag":true}}}' \
  >"$config_file"

recovered=$(
  CONFIG_FILE="$config_file" \
  MANIFEST_FILE="$manifest" \
  BEFORE_RELEASES='[{"id":1,"tag_name":"v1.2.2","draft":false},{"id":2,"tag_name":"worker-v0.3.0","draft":false}]' \
  AFTER_RELEASES='[{"id":1,"tag_name":"v1.2.2","draft":false},{"id":2,"tag_name":"worker-v0.3.0","draft":false},{"id":3,"tag_name":"v1.2.3","draft":true},{"id":4,"tag_name":"worker-v0.4.0","draft":false}]' \
  RELEASE_SHA=abc123 \
  "$reconciler"
)
jq -e '
  length == 2
  and .[0] == {path: ".", component: "application", tag_name: "v1.2.3", version: "1.2.3", sha: "abc123", release_id: 3}
  and .[1] == {path: "cmd/worker", component: "worker", tag_name: "worker-v0.4.0", version: "0.4.0", sha: "abc123", release_id: 4}
' <<<"$recovered" >/dev/null

combined=$(
  CONFIG_FILE="$config_file" \
  MANIFEST_FILE="$manifest" \
  FIRST_PATHS='["."]' \
  FIRST_OUTPUTS='{"tag_name":"v1.2.3","version":"1.2.3","sha":"abc123"}' \
  RETRY_PATHS='["cmd/worker"]' \
  RETRY_OUTPUTS='{"cmd/worker--tag_name":"worker-v0.4.0","cmd/worker--version":"0.4.0","cmd/worker--sha":"abc123"}' \
  BEFORE_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":true},{"id":2,"tag_name":"worker-v0.3.0","draft":false}]' \
  AFTER_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":true},{"id":2,"tag_name":"worker-v0.3.0","draft":false},{"id":4,"tag_name":"worker-v0.4.0","draft":false}]' \
  RELEASE_SHA=abc123 \
  "$reconciler"
)
jq -e 'map({tag_name, release_id}) == [{tag_name:"v1.2.3",release_id:3},{tag_name:"worker-v0.4.0",release_id:4}]' <<<"$combined" >/dev/null

# A queued component release can observe a newer target branch and create a
# second component release whose version is newer than the run's checked-out
# manifest. Release Please reports both releases explicitly; reconciliation
# must retain those outputs instead of orphaning their publication events.
concurrent=$(
  CONFIG_FILE="$config_file" \
  MANIFEST_FILE="$manifest" \
  FIRST_PATHS='["cmd/worker","."]' \
  FIRST_OUTPUTS='{"releases_created":"true","cmd/worker--tag_name":"worker-v0.4.0","cmd/worker--version":"0.4.0","cmd/worker--sha":"worker-sha","tag_name":"v1.2.4","version":"1.2.4","sha":"application-sha"}' \
  BEFORE_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":false},{"id":2,"tag_name":"worker-v0.3.0","draft":false}]' \
  AFTER_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":false},{"id":2,"tag_name":"worker-v0.3.0","draft":false},{"id":5,"tag_name":"v1.2.4","draft":false},{"id":4,"tag_name":"worker-v0.4.0","draft":false}]' \
  RELEASE_SHA=queued-run-sha \
  "$reconciler"
)
jq -e '
  length == 2
  and .[0] == {path: ".", component: "application", tag_name: "v1.2.4", version: "1.2.4", sha: "application-sha", release_id: 5}
  and .[1] == {path: "cmd/worker", component: "worker", tag_name: "worker-v0.4.0", version: "0.4.0", sha: "worker-sha", release_id: 4}
' <<<"$concurrent" >/dev/null

replayed=$(
  CONFIG_FILE="$config_file" \
  MANIFEST_FILE="$manifest" \
  BEFORE_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":true},{"id":4,"tag_name":"worker-v0.4.0","draft":false}]' \
  AFTER_RELEASES='[{"id":3,"tag_name":"v1.2.3","draft":true},{"id":4,"tag_name":"worker-v0.4.0","draft":false}]' \
  RELEASE_SHA=abc123 \
  REPLAY_TAG=v1.2.3 \
  "$reconciler"
)
jq -e '. == [{path: ".", component: "application", tag_name: "v1.2.3", version: "1.2.3", sha: "abc123", release_id: 3}]' \
  <<<"$replayed" >/dev/null

if CONFIG_FILE="$config_file" \
  MANIFEST_FILE="$manifest" \
  BEFORE_RELEASES='[]' \
  AFTER_RELEASES='[{"id":9,"tag_name":"unrelated-v9.9.9","draft":false}]' \
  RELEASE_SHA=abc123 \
  "$reconciler" >/dev/null 2>&1; then
  echo "reconciler accepted an unconfigured release tag" >&2
  exit 1
fi

images=$(mktemp)
printf '%s\n' \
  'images:' \
  '  - name: runtime' \
  '    component: runtime' \
  '  - name: application' >"$images"

all_images=$(yq -o=json -I=0 '{"include": .images}' "$images")
runtime_images=$(RELEASE_COMPONENT=runtime yq -o=json -I=0 \
  '{"include": [.images[] | select(.component == strenv(RELEASE_COMPONENT))]}' \
  "$images")

jq -e '.include | map(.name) == ["runtime", "application"]' \
  <<<"$all_images" >/dev/null
jq -e '.include | map(.name) == ["runtime"]' <<<"$runtime_images" >/dev/null

echo "test-component-releases: OK"
