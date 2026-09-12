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
    name: ghcr.io/example-org/api
    renovate:
      repository: example-org/infrastructure
      dependencies:
        - example-org/api
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/example-org/api-worker
    renovate:
      repository: example-org/infrastructure
      dependencies:
        - example-org/api-worker
        - example-org/api
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/example-org/api-docs
    renovate:
      repository: example-org/documentation
      dependencies:
        - example-org/api-docs
  - component: api
    publisher: npm
    type: npm
    name: "@example-org/api"
    renovate:
      repository: example-org/infrastructure
      dependencies:
        - "@example-org/api"
  - component: java-client
    publisher: maven-central
    type: maven
    name: org.example:api-client
    renovate:
      repository: example-org/infrastructure
      dependencies:
        - org.example:api-client
YAML

docker=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" docker api)
jq -e '.include | length == 2' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "example-org/infrastructure") | .artifact_name == "ghcr.io/example-org/api, ghcr.io/example-org/api-worker"' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "example-org/infrastructure") | .artifact_type == "docker"' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "example-org/infrastructure") | .dependencies == ["example-org/api", "example-org/api-worker"]' <<<"$docker" >/dev/null
jq -e '.include[] | select(.target_repository == "example-org/documentation") | .dependencies == ["example-org/api-docs"]' <<<"$docker" >/dev/null

npm=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" npm api)
jq -e '.include[0].artifact_type == "npm"' <<<"$npm" >/dev/null

custom=$($root/scripts/resolve-artifact-renovate.sh "$tmpdir/artifacts.yaml" maven-central java-client)
jq -e '.include[0].artifact_type == "maven"' <<<"$custom" >/dev/null
jq -e '.include[0].dependencies == ["org.example:api-client"]' <<<"$custom" >/dev/null

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
