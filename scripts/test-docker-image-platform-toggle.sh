#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
caller="$ROOT/consumers/workflows/build-docker-image.yaml"
reusable="$ROOT/.github/workflows/build_docker_image_multiplatform.yaml"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

grep -q "DOCKER_IMAGE_PLATFORMS" "$caller"
grep -q "DOCKER_IMAGE_PLATFORMS" "$reusable"
grep -q "docker_image_platforms" "$reusable"
grep -q 'build-args: \${{ github.event.inputs.build-args' "$caller"
grep -Fq 'source-sha: ${{ fromJson(needs.configure.outputs.context).sha' "$caller"
grep -Fq 'context: ${{ matrix.context || '\''.'\'' }}' "$caller"
grep -q '\${{ inputs.build-args }}' "$reusable"
grep -q 'enable-gha-cache-export:' "$reusable"
grep -Fq 'type=registry,ref=${{ env.REGISTRY_IMAGE }}:buildcache-${{ matrix.architecture }}' "$reusable"
grep -Fq 'type=gha,scope=${{ inputs.image_name }}-${{ matrix.platform }}' "$reusable"
grep -q 'name: Publish image success tag' "$reusable"
grep -q 'docker buildx imagetools inspect' "$reusable"
grep -Fq 'name: digests-${{ needs.setup-matrix.outputs.sanitized_image_name }}--${{ env.PLATFORM_PAIR }}' "$reusable"
grep -Fq 'pattern: digests-${{ needs.setup-matrix.outputs.sanitized_image_name }}--*' "$reusable"
grep -q 'success_tag="${SUCCESS_TAG_PREFIX:-$image_name}-${IMAGE_TAG}"' "$reusable"
grep -q "|| existing_sha=''" "$reusable"
inspect_line=$(grep -n 'docker buildx imagetools inspect' "$reusable" | cut -d: -f1)
success_tag_line=$(grep -n 'name: Publish image success tag' "$reusable" | cut -d: -f1)
if [ "$success_tag_line" -le "$inspect_line" ]; then
    echo "image success tag must be published after manifest inspection" >&2
    exit 1
fi
grep -Fq "cache-to: \${{ inputs.enable-gha-cache-export && format('type=registry,ref={0}:buildcache-{1},mode=max,image-manifest=true,oci-mediatypes=true', env.REGISTRY_IMAGE, matrix.architecture) || '' }}" "$reusable"
if grep -q 'scope=${{ inputs.tag }}-' "$reusable"; then
    echo "docker image cache must survive release tags" >&2
    exit 1
fi

# A short image name must not collect digest artifacts from another image for
# which it is a prefix (for example, app and app-worker).
shopt -s extglob
artifacts=(digests-example-org-app--linux-amd64 digests-example-org-app-worker--linux-amd64)
matched=()
for artifact in "${artifacts[@]}"; do
    if [[ "$artifact" == digests-example-org-app--* ]]; then
        matched+=("$artifact")
    fi
done
if [ "${matched[*]}" != "digests-example-org-app--linux-amd64" ]; then
    echo "docker digest artifact pattern crosses image names" >&2
    exit 1
fi

if grep -q "matrix filtering skipped" "$reusable"; then
    echo "docker image platform filtering must apply outside workflow_dispatch" >&2
    exit 1
fi

script="$TMPDIR/determine-platforms.sh"
yq -r '.jobs."setup-matrix".steps[] | select(.id == "create-matrix").run' "$reusable" \
    | sed \
        -e 's/^platforms=.*/platforms="${INPUT_PLATFORMS:-}"/' \
        -e 's/^docker_image_platforms=.*/docker_image_platforms="${DOCKER_IMAGE_PLATFORMS:-}"/' \
        -e 's/^default_universe=.*/default_universe="$DEFAULT_UNIVERSE"/' \
    > "$script"
chmod +x "$script"

export DEFAULT_UNIVERSE
DEFAULT_UNIVERSE=$(yq -r '.env.DEFAULT_UNIVERSE' "$reusable")

run_case() {
    local name="$1"
    local input_platforms="$2"
    local docker_image_platforms="$3"
    local expected_platforms="$4"
    local output="$TMPDIR/$name.out"

    GITHUB_OUTPUT="$output" \
        INPUT_PLATFORMS="$input_platforms" \
        DOCKER_IMAGE_PLATFORMS="$docker_image_platforms" \
        "$script" >/tmp/"$name".stdout

    local matrix
    matrix=$(grep '^matrix=' "$output" | sed 's/^matrix=//')
    jq -e --argjson expected "$expected_platforms" '
      map(.platform) == $expected
    ' <<<"$matrix" >/dev/null
}

run_case default "" "" '["linux/amd64","linux/arm64"]'
run_case org_csv "" "linux/amd64" '["linux/amd64"]'
run_case org_json "" '["linux/arm64"]' '["linux/arm64"]'
run_case input_overrides_org '["linux/amd64","linux/arm64"]' "linux/amd64" '["linux/amd64","linux/arm64"]'

if GITHUB_OUTPUT="$TMPDIR/invalid.out" \
    INPUT_PLATFORMS="" \
    DOCKER_IMAGE_PLATFORMS="linux/s390x" \
    "$script" >/tmp/invalid-platform.stdout 2>/tmp/invalid-platform.stderr; then
    echo "expected unsupported platform to fail" >&2
    exit 1
fi
grep -q "unsupported docker image platform" /tmp/invalid-platform.stdout

success_tag_script="$TMPDIR/publish-image-success-tag.sh"
yq -r '.jobs.merge.steps[] | select(.name == "Publish image success tag").run' "$reusable" \
    > "$success_tag_script"
chmod +x "$success_tag_script"

mkdir "$TMPDIR/bin"
cat > "$TMPDIR/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ " $* " == *" --method POST "* ]]; then
    printf '%s\n' "$*" >> "$GH_CALLS"
    exit 0
fi

printf '%s\n' '{"message":"Not Found","status":"404"}'
exit 1
EOF
chmod +x "$TMPDIR/bin/gh"

GH_CALLS="$TMPDIR/gh-calls" \
PATH="$TMPDIR/bin:$PATH" \
IMAGE_NAME=example-org/runtime \
IMAGE_TAG=v1.17.5 \
SOURCE_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
REGISTRY_IMAGE=ghcr.io/example-org/runtime \
GITHUB_REPOSITORY=example-org/images \
GH_TOKEN=test-token \
SUCCESS_TAG_PREFIX= \
    "$success_tag_script" >/dev/null

grep -q -- '--method POST' "$TMPDIR/gh-calls"
grep -q 'refs/tags/runtime-v1.17.5' "$TMPDIR/gh-calls"
grep -q 'sha=a2b237a239a0e65c31149eff6dc8a21722c80cc1' "$TMPDIR/gh-calls"

: > "$TMPDIR/gh-calls"
GH_CALLS="$TMPDIR/gh-calls" \
PATH="$TMPDIR/bin:$PATH" \
IMAGE_NAME=example-org/app-worker \
IMAGE_TAG=v0.2.0 \
SOURCE_SHA=b2b237a239a0e65c31149eff6dc8a21722c80cc2 \
SUCCESS_TAG_PREFIX=app-worker-image \
REGISTRY_IMAGE=ghcr.io/example-org/app-worker \
GITHUB_REPOSITORY=example-org/application \
GH_TOKEN=test-token \
    "$success_tag_script" >/dev/null

grep -q 'refs/tags/app-worker-image-v0.2.0' "$TMPDIR/gh-calls"
