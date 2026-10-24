#!/usr/bin/env bash
# Exercise the distributed verifier with a real Docker build and COPY failure.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/.github"
cat > "$fixture/Dockerfile" <<'DOCKER'
FROM scratch
COPY marker /marker
DOCKER
printf 'image-ci-fixture\n' > "$fixture/marker"
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
(
  cd "$fixture"
  python3 "$root/consumers/files/.shared/check-docker-images.py"
  rm marker
  if python3 "$root/consumers/files/.shared/check-docker-images.py" >failure.log 2>&1; then
    echo 'missing Docker COPY input must fail shared CI' >&2
    exit 1
  fi
  grep -q 'CalledProcessError' failure.log
)
echo 'shared Docker CI builds, smoke-tests, and rejects missing COPY inputs'
