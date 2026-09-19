#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
caller="$ROOT/consumers/workflows/build-docker-image.yaml"
reusable="$ROOT/.github/workflows/build_docker_image_multiplatform.yaml"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# The generic consumer interface and synchronous notifier stay unchanged.
grep -q "DOCKER_IMAGE_PLATFORMS" "$caller"
grep -q 'build-args: \${{ github.event.inputs.build-args' "$caller"
grep -Fq 'source-sha: ${{ fromJson(needs.configure.outputs.context).sha' "$caller"
grep -Fq 'context: ${{ matrix.context || '\''.'\'' }}' "$caller"
grep -Fq 'needs: [configure, main]' "$caller"
grep -Fq 'uses: jr200-labs/github-action-templates/.github/workflows/notify_artifact_published.yaml@master' "$caller"

# Every selected platform and the final manifest are published in one job.
grep -q '^  publish:$' "$reusable"
if grep -Eq '^  (setup-matrix|build|merge):$' "$reusable"; then
    echo "docker image publication must use one publish job" >&2
    exit 1
fi
if grep -Eq 'actions/(upload|download)-artifact|digests-' "$reusable"; then
    echo "docker image publication must not transfer digest artifacts" >&2
    exit 1
fi
if [ "$(grep -c 'uses: docker/setup-buildx-action@v4' "$reusable")" -ne 2 ]; then
    echo "publish job must contain only remote and fallback Buildx setup" >&2
    exit 1
fi

grep -q '\${{ inputs.build-args }}' "$reusable"
grep -q 'enable-gha-cache-export:' "$reusable"
grep -q 'name: Publish image success tag' "$reusable"
grep -q 'docker buildx imagetools create' "$reusable"
grep -q 'docker buildx imagetools inspect' "$reusable"
grep -q 'success_tag="${SUCCESS_TAG_PREFIX:-$image_name}-${IMAGE_TAG}"' "$reusable"
grep -q "|| existing_sha=''" "$reusable"

inspect_line=$(grep -n 'docker buildx imagetools inspect' "$reusable" | cut -d: -f1)
success_tag_line=$(grep -n 'name: Publish image success tag' "$reusable" | cut -d: -f1)
if [ "$success_tag_line" -le "$inspect_line" ]; then
    echo "image success tag must be published after manifest inspection" >&2
    exit 1
fi

# Runner-owned environment and certificate mounts stay out of canonical callers.
if grep -q 'buildkit-endpoint:' "$caller"; then
    echo "canonical callers must not carry runner-specific BuildKit configuration" >&2
    exit 1
fi

# Platform selection retains input > org variable > default precedence and
# normalizes the supported universe to at most one target per architecture.
selector_script="$TMPDIR/resolve-platforms.sh"
yq -r '.jobs.publish.steps[] | select(.id == "platforms").run' "$reusable" \
    | sed -e 's/^default_universe=.*/default_universe="$DEFAULT_UNIVERSE"/' \
    > "$selector_script"
chmod +x "$selector_script"
export DEFAULT_UNIVERSE
DEFAULT_UNIVERSE=$(yq -r '.env.DEFAULT_UNIVERSE' "$reusable")

run_case() {
    local name="$1"
    local input_platforms="$2"
    local docker_image_platforms="$3"
    local runner_arch="$4"
    local expected_platforms="$5"
    local expected_qemu="$6"
    local output="$TMPDIR/$name.out"

    GITHUB_OUTPUT="$output" \
        INPUT_PLATFORMS="$input_platforms" \
        DOCKER_IMAGE_PLATFORMS="$docker_image_platforms" \
        RUNNER_ARCH="$runner_arch" \
        "$selector_script" >"$TMPDIR/$name.stdout"

    local matrix
    matrix=$(sed -n 's/^matrix=//p' "$output")
    jq -e --argjson expected "$expected_platforms" \
        'map(.platform) == $expected' <<<"$matrix" >/dev/null
    local expected_amd64=false
    local expected_arm64=false
    [[ "$expected_platforms" == *'linux/amd64'* ]] && expected_amd64=true
    [[ "$expected_platforms" == *'linux/arm64'* ]] && expected_arm64=true
    grep -qx "amd64=$expected_amd64" "$output"
    grep -qx "arm64=$expected_arm64" "$output"
    grep -qx "needs_qemu=$expected_qemu" "$output"
}

run_case default "" "" X64 '["linux/amd64","linux/arm64"]' true
run_case org_csv "" "linux/amd64" X64 '["linux/amd64"]' false
run_case org_json "" '["linux/arm64"]' ARM64 '["linux/arm64"]' false
run_case input_overrides_org '["linux/amd64","linux/arm64"]' "linux/amd64" ARM64 '["linux/amd64","linux/arm64"]' true
run_case deduplicates 'linux/amd64, linux/amd64' "" X64 '["linux/amd64"]' false

if GITHUB_OUTPUT="$TMPDIR/invalid.out" \
    INPUT_PLATFORMS="" \
    DOCKER_IMAGE_PLATFORMS="linux/s390x" \
    RUNNER_ARCH=X64 \
    "$selector_script" >"$TMPDIR/invalid-platform.stdout" 2>"$TMPDIR/invalid-platform.stderr"; then
    echo "expected unsupported platform to fail" >&2
    exit 1
fi
grep -q "unsupported docker image platform" "$TMPDIR/invalid-platform.stdout"

if GITHUB_OUTPUT="$TMPDIR/empty.out" \
    INPUT_PLATFORMS='[]' \
    DOCKER_IMAGE_PLATFORMS="" \
    RUNNER_ARCH=X64 \
    "$selector_script" >"$TMPDIR/empty-platform.stdout" 2>"$TMPDIR/empty-platform.stderr"; then
    echo "expected an empty platform selection to fail" >&2
    exit 1
fi
grep -q "platform filter resolved to an empty matrix" "$TMPDIR/empty-platform.stdout"

qemu_step=$(yq -o=json -I=0 '.jobs.publish.steps[] | select(.name == "Set up QEMU")' "$reusable")
jq -e '.if == "steps.platforms.outputs.needs_qemu == '\''true'\''"' <<<"$qemu_step" >/dev/null

# Remote BuildKit discovery and handshake failure retain the ephemeral fallback.
buildkit_selector="$TMPDIR/select-buildkit.sh"
yq -r '.jobs.publish.steps[] | select(.id == "buildkit_config").run' "$reusable" > "$buildkit_selector"
GITHUB_OUTPUT="$TMPDIR/no-remote.out" env -u BUILDKIT_ENDPOINT -u BUILDKIT_SERVER_NAME -u BUILDKIT_TLS_DIR \
    bash "$buildkit_selector"
grep -qx 'requested=false' "$TMPDIR/no-remote.out"

mkdir -p "$TMPDIR/tls"
touch "$TMPDIR/tls/ca.crt" "$TMPDIR/tls/tls.crt" "$TMPDIR/tls/tls.key"
GITHUB_OUTPUT="$TMPDIR/remote-config.out" \
    BUILDKIT_ENDPOINT=tcp://buildkit.example.svc:1234 \
    BUILDKIT_SERVER_NAME=buildkit.example.svc \
    BUILDKIT_TLS_DIR="$TMPDIR/tls" bash "$buildkit_selector"
grep -qx 'requested=true' "$TMPDIR/remote-config.out"
grep -qx 'endpoint=tcp://buildkit.example.svc:1234' "$TMPDIR/remote-config.out"
grep -qx "tls_dir=$TMPDIR/tls" "$TMPDIR/remote-config.out"

GITHUB_OUTPUT="$TMPDIR/missing-tls.out" \
    BUILDKIT_ENDPOINT=tcp://buildkit.example.svc:1234 \
    BUILDKIT_SERVER_NAME=buildkit.example.svc \
    BUILDKIT_TLS_DIR="$TMPDIR/missing-tls" bash "$buildkit_selector"
grep -qx 'requested=false' "$TMPDIR/missing-tls.out"

finalizer_script="$TMPDIR/finalize-buildkit.sh"
yq -r '.jobs.publish.steps[] | select(.id == "buildkit").run' "$reusable" > "$finalizer_script"
GITHUB_OUTPUT="$TMPDIR/remote-mode.out" REMOTE_REQUESTED=true REMOTE_OUTCOME=success bash "$finalizer_script"
grep -qx 'mode=remote' "$TMPDIR/remote-mode.out"
GITHUB_OUTPUT="$TMPDIR/failed-remote.out" REMOTE_REQUESTED=true REMOTE_OUTCOME=failure bash "$finalizer_script"
grep -qx 'mode=ephemeral' "$TMPDIR/failed-remote.out"
GITHUB_OUTPUT="$TMPDIR/local-mode.out" REMOTE_REQUESTED=false REMOTE_OUTCOME=skipped bash "$finalizer_script"
grep -qx 'mode=ephemeral' "$TMPDIR/local-mode.out"

fallback_step=$(yq -o=json -I=0 '.jobs.publish.steps[] | select(.name == "Set up per-job Docker Buildx")' "$reusable")
jq -e '.if == "steps.buildkit.outputs.mode == '\''ephemeral'\''" and (.with == null)' <<<"$fallback_step" >/dev/null
remote_step=$(yq -o=json -I=0 '.jobs.publish.steps[] | select(.name == "Connect to runner-provided BuildKit")' "$reusable")
jq -e '
  .if == "steps.buildkit_config.outputs.requested == '\''true'\''" and
  ."continue-on-error" == true and
  .with.driver == "remote" and
  .with.endpoint == "${{ steps.buildkit_config.outputs.endpoint }}" and
  (.with."driver-opts" | contains("cacert=${{ steps.buildkit_config.outputs.tls_dir }}/ca.crt")) and
  (.with."driver-opts" | contains("cert=${{ steps.buildkit_config.outputs.tls_dir }}/tls.crt")) and
  (.with."driver-opts" | contains("key=${{ steps.buildkit_config.outputs.tls_dir }}/tls.key")) and
  (.with."driver-opts" | contains("servername=${{ steps.buildkit_config.outputs.server_name }}"))
' <<<"$remote_step" >/dev/null

# Both supported platform builds preserve architecture-specific args and caches.
assert_build_step() {
    local id="$1"
    local platform="$2"
    local arch="$3"
    local step
    step=$(yq -o=json -I=0 ".jobs.publish.steps[] | select(.id == \"$id\")" "$reusable")
    jq -e --arg platform "$platform" --arg arch "$arch" '
      .if == ("steps.platforms.outputs." + $arch + " == '\''true'\''") and
      ."continue-on-error" == true and
      .with.platforms == $platform and
      (.with.outputs | contains("push-by-digest=true")) and
      (.with."build-args" | contains("BUILD_OS=linux")) and
      (.with."build-args" | contains("BUILD_ARCH=" + $arch)) and
      (.with."cache-from" | contains("buildcache-" + $arch)) and
      (.with."cache-from" | contains("scope={0}-" + $platform)) and
      (.with."cache-to" | contains("buildcache-" + $arch)) and
      (.with."cache-to" | contains("inputs.enable-gha-cache-export"))
    ' <<<"$step" >/dev/null
}
assert_build_step build-amd64 linux/amd64 amd64
assert_build_step build-arm64 linux/arm64 arm64

for id in build-amd64 build-arm64; do
    cache_from=$(yq -r ".jobs.publish.steps[] | select(.id == \"$id\") | .with.\"cache-from\"" "$reusable")
    if [ "$(grep -Fc "steps.buildkit.outputs.mode == 'ephemeral'" <<<"$cache_from")" -ne 2 ]; then
        echo "remote BuildKit must disable both external cache imports for $id" >&2
        exit 1
    fi
done
if grep -q 'scope=${{ inputs.tag }}-' "$reusable"; then
    echo "docker image cache must survive release tags" >&2
    exit 1
fi

# Both selected builds are allowed to finish, then one validation gate prevents
# publication if either failed.
validation_step=$(yq -o=json -I=0 '.jobs.publish.steps[] | select(.name == "Validate platform builds")' "$reusable")
jq -e '.if == "always()"' <<<"$validation_step" >/dev/null
validation_script="$TMPDIR/validate-builds.sh"
yq -r '.jobs.publish.steps[] | select(.name == "Validate platform builds").run' "$reusable" > "$validation_script"
AMD64_SELECTED=true AMD64_OUTCOME=success ARM64_SELECTED=true ARM64_OUTCOME=success \
    bash "$validation_script"
if AMD64_SELECTED=true AMD64_OUTCOME=failure ARM64_SELECTED=true ARM64_OUTCOME=success \
    bash "$validation_script" >"$TMPDIR/failed-build.stdout" 2>&1; then
    echo "expected a selected failed build to block publication" >&2
    exit 1
fi
grep -q 'linux/amd64 (failure)' "$TMPDIR/failed-build.stdout"

# Manifest assembly includes exactly the selected, validated platform digests.
manifest_script="$TMPDIR/create-manifest.sh"
yq -r '.jobs.publish.steps[] | select(.name == "Create manifest list and push").run' "$reusable" > "$manifest_script"
mkdir "$TMPDIR/bin"
cat > "$TMPDIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_CALLS"
EOF
chmod +x "$TMPDIR/bin/docker"
amd_digest="sha256:$(printf 'a%.0s' {1..64})"
arm_digest="sha256:$(printf 'b%.0s' {1..64})"

run_manifest_case() {
    local name="$1"
    local amd_selected="$2"
    local arm_selected="$3"
    local expected_refs="$4"
    local calls="$TMPDIR/$name-docker-calls"
    : > "$calls"
    DOCKER_CALLS="$calls" PATH="$TMPDIR/bin:$PATH" \
        REGISTRY_IMAGE=ghcr.io/example-org/runtime IMAGE_TAG=v1.2.3 \
        AMD64_SELECTED="$amd_selected" AMD64_DIGEST="$amd_digest" \
        ARM64_SELECTED="$arm_selected" ARM64_DIGEST="$arm_digest" \
        bash "$manifest_script"
    grep -Fqx "buildx imagetools create -t ghcr.io/example-org/runtime:v1.2.3 $expected_refs" "$calls"
}
run_manifest_case amd64 true false "ghcr.io/example-org/runtime@$amd_digest"
run_manifest_case arm64 false true "ghcr.io/example-org/runtime@$arm_digest"
run_manifest_case both true true "ghcr.io/example-org/runtime@$amd_digest ghcr.io/example-org/runtime@$arm_digest"

if DOCKER_CALLS="$TMPDIR/invalid-digest-calls" PATH="$TMPDIR/bin:$PATH" \
    REGISTRY_IMAGE=ghcr.io/example-org/runtime IMAGE_TAG=v1.2.3 \
    AMD64_SELECTED=true AMD64_DIGEST=bad \
    ARM64_SELECTED=false ARM64_DIGEST= \
    bash "$manifest_script" >"$TMPDIR/invalid-digest.stdout" 2>&1; then
    echo "expected an invalid selected digest to fail" >&2
    exit 1
fi
grep -q 'linux/amd64 build returned an invalid digest' "$TMPDIR/invalid-digest.stdout"

# Release success tags remain post-inspection, idempotent and conflict-safe.
success_tag_script="$TMPDIR/publish-image-success-tag.sh"
yq -r '.jobs.publish.steps[] | select(.name == "Publish image success tag").run' "$reusable" > "$success_tag_script"
cat > "$TMPDIR/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" --method POST "* ]]; then
    printf '%s\n' "$*" >> "$GH_CALLS"
    call_count=$(wc -l < "$GH_CALLS")
    if [ "$call_count" -le "${GH_POST_FAILURES:-0}" ]; then
        echo 'Resource not accessible by integration' >&2
        exit 1
    fi
    exit 0
fi
if [ -n "${GH_EXISTING_SHA:-}" ]; then
    printf '%s\n' "$GH_EXISTING_SHA"
    exit 0
fi
printf '%s\n' '{"message":"Not Found","status":"404"}'
exit 1
EOF
chmod +x "$TMPDIR/bin/gh"
cat > "$TMPDIR/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMPDIR/bin/sleep"

: > "$TMPDIR/gh-calls"
GH_CALLS="$TMPDIR/gh-calls" GH_EXISTING_SHA= PATH="$TMPDIR/bin:$PATH" \
    IMAGE_NAME=example-org/runtime IMAGE_TAG=v1.17.5 \
    SOURCE_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
    REGISTRY_IMAGE=ghcr.io/example-org/runtime GITHUB_REPOSITORY=example-org/images \
    GH_TOKEN=test-token SUCCESS_TAG_PREFIX= bash "$success_tag_script" >/dev/null
grep -q -- '--method POST' "$TMPDIR/gh-calls"
grep -q 'refs/tags/runtime-v1.17.5' "$TMPDIR/gh-calls"

: > "$TMPDIR/gh-calls"
GH_CALLS="$TMPDIR/gh-calls" GH_POST_FAILURES=1 GH_EXISTING_SHA= PATH="$TMPDIR/bin:$PATH" \
    IMAGE_NAME=example-org/runtime IMAGE_TAG=v1.17.5 \
    SOURCE_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
    REGISTRY_IMAGE=ghcr.io/example-org/runtime GITHUB_REPOSITORY=example-org/images \
    GH_TOKEN=test-token SUCCESS_TAG_PREFIX= bash "$success_tag_script" >/dev/null
if [ "$(wc -l < "$TMPDIR/gh-calls")" -ne 2 ]; then
    echo "transient success tag failure must retry once" >&2
    exit 1
fi

: > "$TMPDIR/gh-calls"
GH_CALLS="$TMPDIR/gh-calls" GH_EXISTING_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
    PATH="$TMPDIR/bin:$PATH" IMAGE_NAME=example-org/runtime IMAGE_TAG=v1.17.5 \
    SOURCE_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
    REGISTRY_IMAGE=ghcr.io/example-org/runtime GITHUB_REPOSITORY=example-org/images \
    GH_TOKEN=test-token SUCCESS_TAG_PREFIX= bash "$success_tag_script" >/dev/null
if [ -s "$TMPDIR/gh-calls" ]; then
    echo "idempotent success tag must not be recreated" >&2
    exit 1
fi

if GH_CALLS="$TMPDIR/gh-calls" GH_EXISTING_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
    PATH="$TMPDIR/bin:$PATH" IMAGE_NAME=example-org/runtime IMAGE_TAG=v1.17.5 \
    SOURCE_SHA=a2b237a239a0e65c31149eff6dc8a21722c80cc1 \
    REGISTRY_IMAGE=ghcr.io/example-org/runtime GITHUB_REPOSITORY=example-org/images \
    GH_TOKEN=test-token SUCCESS_TAG_PREFIX= bash "$success_tag_script" >"$TMPDIR/tag-conflict.stdout" 2>&1; then
    echo "expected a conflicting success tag to fail" >&2
    exit 1
fi
grep -q 'already points to' "$TMPDIR/tag-conflict.stdout"

echo "test-docker-image-platform-toggle: OK"
