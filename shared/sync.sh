#!/usr/bin/env bash
# Sync canonical shared configs from jr200-labs/github-action-templates into this repo.
# Driven by shared/MANIFEST.json — see that file for the file lists.
#
# Usage:
#   ./sync.sh [python|go|node]
#
# If no language is specified, detects from project files.
#
# Two destination modes per file (declared in MANIFEST.json):
#   - cache     → written to .shared/<file> (gitignored; re-fetched in CI)
#   - committed → written to <file> at repo root (must be committed; drift-checked)

set -euo pipefail

REPO="jr200-labs/github-action-templates"
BRANCH="master"
SHARED_DIR=".shared"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/shared"

die() { echo "error: $*" >&2; exit 1; }

require() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

detect_language() {
    if [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
        echo "python"
    elif [ -f "go.mod" ]; then
        echo "go"
    elif [ -f "package.json" ]; then
        echo "node"
    else
        die "cannot detect project language — pass python, go, or node as argument"
    fi
}

download_to() {
    local remote_path="$1"
    local local_path="$2"
    mkdir -p "$(dirname "$local_path")"
    curl -sfL "${BASE_URL}/${remote_path}" -o "$local_path"
    echo "synced: ${remote_path} -> ${local_path}"
}

ensure_gitignore() {
    if [ -f ".gitignore" ]; then
        if ! grep -qxF "${SHARED_DIR}/" .gitignore 2>/dev/null; then
            echo "${SHARED_DIR}/" >> .gitignore
            echo "added ${SHARED_DIR}/ to .gitignore"
        fi
    fi
}

require curl
require jq

LANG="${1:-$(detect_language)}"

# Fetch manifest
MANIFEST_JSON=$(curl -sfL "${BASE_URL}/MANIFEST.json") || die "failed to fetch MANIFEST.json"

get_files() {
    # $1 = section (common | <lang>), $2 = mode (cache | committed)
    echo "$MANIFEST_JSON" | jq -r --arg s "$1" --arg m "$2" '(.[$s][$m] // []) | .[]'
}

# Sync committed files (common + language-specific) → repo root
for section in common "$LANG"; do
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        download_to "$f" "$f"
    done < <(get_files "$section" "committed")
done

# Sync cache files (common + language-specific) → .shared/
for section in common "$LANG"; do
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        download_to "$f" "${SHARED_DIR}/$f"
    done < <(get_files "$section" "cache")
done

# Always refresh self for future syncs
download_to "sync.sh" "${SHARED_DIR}/sync.sh"
chmod +x "${SHARED_DIR}/sync.sh"

ensure_gitignore

echo "done."
