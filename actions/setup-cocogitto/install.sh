#!/usr/bin/env bash
set -euo pipefail

version="${COCOGITTO_VERSION:?Cocogitto version is required}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Unsupported Cocogitto version: $version" >&2
  exit 1
fi

case "${RUNNER_OS:?}-${RUNNER_ARCH:?}" in
  Linux-X64) target=x86_64-unknown-linux-musl ;;
  Linux-ARM64) target=aarch64-unknown-linux-gnu ;;
  macOS-X64) target=x86_64-apple-darwin ;;
  macOS-ARM64) target=aarch64-apple-darwin ;;
  *) echo "Unsupported Cocogitto runner: $RUNNER_OS-$RUNNER_ARCH" >&2; exit 1 ;;
esac

work=$(mktemp -d "${RUNNER_TEMP:?}/cocogitto.XXXXXX")
trap 'rm -rf "$work"' EXIT
archive="cocogitto-$version-$target.tar.gz"
# Download to a file: HTTP errors must fail before tar sees a gateway response.
# Bound retries and timeouts so an unavailable release cannot stall the job.
curl --fail --silent --show-error --location \
  --retry 5 --retry-delay 2 --retry-all-errors --retry-max-time 180 \
  --connect-timeout 10 --max-time 30 \
  --output "$work/release.tar.gz" \
  "https://github.com/cocogitto/cocogitto/releases/download/$version/$archive"
tar -xzf "$work/release.tar.gz" -C "$work" "$target/cog"
"$work/$target/cog" --version

bin="${RUNNER_TEMP}/cocogitto/bin"
mkdir -p "$bin"
install -m 0755 "$work/$target/cog" "$bin/cog"
printf '%s\n' "$bin" >> "${GITHUB_PATH:?}"
