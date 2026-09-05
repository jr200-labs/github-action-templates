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
grep -Fq 'PATHS_RELEASED: ${{ steps.release.outputs.paths_released }}' "$reusable"
grep -Fq 'release: ${{ fromJSON(needs.release.outputs.releases) }}' "$release_caller"
grep -Fq '"component": "${{ matrix.release.component }}"' "$release_caller"
grep -Fq 'RELEASE_COMPONENT: ${{ fromJson(needs.configure.outputs.context).component' "$docker_caller"
grep -Fq 'if [ -n "$RELEASE_COMPONENT" ] && [ "$RELEASE_COMPONENT" != "." ]; then' "$docker_caller"
grep -Fq 'select(.component == strenv(RELEASE_COMPONENT))' "$docker_caller"
grep -Fq 'success-tag-prefix: ${{ matrix.success-tag-prefix' "$docker_caller"
for caller in "${artifact_callers[@]}"; do
  grep -Fq "run-name: \${{ github.event.client_payload['release-tag']" "$caller"
done
test "$(grep -c 'include-component-in-tag' "$reusable")" -ge 2

config='{"packages":{".":{"component":"padd"},"cmd/padd-supervisor":{"component":"padd-supervisor"}}}'
paths='[".","cmd/padd-supervisor"]'
outputs='{"tag_name":"padd-v1.2.3","version":"1.2.3","sha":"aaa","cmd/padd-supervisor--tag_name":"padd-supervisor-v0.4.0","cmd/padd-supervisor--version":"0.4.0","cmd/padd-supervisor--sha":"bbb"}'

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
  and .[0] == {path: ".", component: "padd", tag_name: "padd-v1.2.3", version: "1.2.3", sha: "aaa"}
  and .[1] == {path: "cmd/padd-supervisor", component: "padd-supervisor", tag_name: "padd-supervisor-v0.4.0", version: "0.4.0", sha: "bbb"}
' <<<"$releases" >/dev/null

images=$(mktemp)
trap 'rm -f "$images"' EXIT
printf '%s\n' \
  'images:' \
  '  - name: agent-runtime' \
  '    component: runtime' \
  '  - name: openhands-standard' >"$images"

all_images=$(yq -o=json -I=0 '{"include": .images}' "$images")
runtime_images=$(RELEASE_COMPONENT=runtime yq -o=json -I=0 \
  '{"include": [.images[] | select(.component == strenv(RELEASE_COMPONENT))]}' \
  "$images")

jq -e '.include | map(.name) == ["agent-runtime", "openhands-standard"]' \
  <<<"$all_images" >/dev/null
jq -e '.include | map(.name) == ["agent-runtime"]' <<<"$runtime_images" >/dev/null

echo "test-component-releases: OK"
