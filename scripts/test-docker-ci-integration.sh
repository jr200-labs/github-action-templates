#!/usr/bin/env bash
# Exercise the distributed verifier with a real Docker build and COPY failure.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
fixture=$(mktemp -d)
builders=()
cleanup() {
  for builder in "${builders[@]}"; do
    docker buildx rm "$builder" >/dev/null || true
  done
  rm -rf "$fixture"
}
trap cleanup EXIT
mkdir -p "$fixture/.github"
cat > "$fixture/Dockerfile" <<'DOCKER'
FROM scratch
COPY marker /marker
DOCKER
printf 'image-ci-fixture\n' > "$fixture/marker"
printf 'cache/\n' > "$fixture/.dockerignore"
cat > "$fixture/.github/images.yaml" <<'YAML'
images:
  - dockerfile: Dockerfile
    smoke-test: [bash, smoke.sh]
YAML
cat > "$fixture/smoke.sh" <<'SMOKE'
set -euo pipefail
container=$(docker create --entrypoint /unused "$1")
trap 'docker rm "$container" >/dev/null' EXIT
docker cp "$container:/marker" copied-marker
cmp marker copied-marker
SMOKE
builder=$(docker buildx create --driver docker-container)
builders+=("$builder")
builder=$(docker buildx create --driver docker-container)
builders+=("$builder")
(
  cd "$fixture"
  export GAT_DOCKER_CACHE_DIR="$fixture/cache"
  export GAT_DOCKER_BUILDER="${builders[0]}" GAT_DOCKER_CACHE_EXPORT=true
  python3 "$root/consumers/files/.shared/check-docker-images.py"
  test -f "$GAT_DOCKER_CACHE_DIR/export/0/index.json"
  mv "$GAT_DOCKER_CACHE_DIR/export" "$GAT_DOCKER_CACHE_DIR/import"

  export GAT_DOCKER_BUILDER="${builders[1]}" GAT_DOCKER_CACHE_EXPORT=false
  python3 "$root/consumers/files/.shared/check-docker-images.py" >warm.log 2>&1 || { cat warm.log; exit 1; }
  grep -q 'CACHED' warm.log
  rm marker
  if python3 "$root/consumers/files/.shared/check-docker-images.py" >failure.log 2>&1; then
    echo 'missing Docker COPY input must fail shared CI' >&2
    exit 1
  fi
  grep -q 'CalledProcessError' failure.log
)
echo 'shared Docker CI restores layers on a fresh builder, smoke-tests, and rejects missing COPY inputs'
