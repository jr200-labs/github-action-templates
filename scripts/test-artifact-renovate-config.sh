#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

cat > "$tmpdir/artifacts.yaml" <<'YAML'
version: 1
artifacts:
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/whengas/api
    renovate:
      repository: whengas/whengas-iac
      dependencies:
        - whengas/api
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/whengas/api-worker
    renovate:
      repository: whengas/whengas-iac
      dependencies:
        - whengas/api-worker
        - whengas/api
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/whengas/api-docs
    renovate:
      repository: whengas/documentation
      dependencies:
        - whengas/api-docs
  - component: api
    publisher: npm
    type: npm
    name: "@whengas/api"
    renovate:
      repository: whengas/whengas-iac
      dependencies:
        - "@whengas/api"
  - component: java-client
    publisher: maven-central
    type: maven
    name: dev.whengas:api-client
    renovate:
      repository: whengas/whengas-iac
      dependencies:
        - dev.whengas:api-client
YAML

docker=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" docker api)
jq -e '.include | length == 2' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "whengas/whengas-iac") | .artifact_name == "ghcr.io/whengas/api, ghcr.io/whengas/api-worker"' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "whengas/whengas-iac") | .artifact_type == "docker"' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "whengas/whengas-iac") | .dependencies == ["whengas/api", "whengas/api-worker"]' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "whengas/documentation") | .dependencies == ["whengas/api-docs"]' <<<"$docker" >/dev/null

npm=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" npm api)
jq -e '.include[0].artifact_type == "npm"' <<<"$npm" >/dev/null

custom=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" maven-central java-client)
jq -e '.include[0].artifact_type == "maven"' <<<"$custom" >/dev/null
jq -e '.include[0].dependencies == ["dev.whengas:api-client"]' <<<"$custom" >/dev/null

missing=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/missing.yaml" docker api)
jq -e '.include == [{"configured":false}]' <<<"$missing" >/dev/null

unmatched=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" pypi api)
jq -e '.include == [{"configured":false}]' <<<"$unmatched" >/dev/null

echo 'version: 1' > "$tmpdir/invalid.yaml"
if $root/scripts/resolve-artifact-renovate.sh "$tmpdir/invalid.yaml" docker api >/dev/null 2>&1; then
  echo "invalid catalog was accepted" >&2
  exit 1
fi

echo "test-artifact-renovate-config: ok"
