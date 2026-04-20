#!/usr/bin/env bash
# Download canonical lint configs from jr200-labs/github-action-templates.
# Works both locally (via gh cli) and in CI (via GITHUB_TOKEN + curl).
#
# Usage:
#   ./sync-shared-lint.sh [python|go|node]
#
# If no language is specified, detects from project files.
# Downloads configs to .shared/ in the repo root.

set -euo pipefail

REPO="jr200-labs/github-action-templates"
BRANCH="master"
SHARED_DIR=".shared"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/shared"

die() { echo "error: $*" >&2; exit 1; }

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

download_file() {
    local remote_path="$1"
    local local_path="${SHARED_DIR}/$2"

    mkdir -p "$(dirname "$local_path")"
    curl -sfL "${BASE_URL}/${remote_path}" -o "$local_path"
    echo "synced: ${remote_path} -> ${local_path}"
}

ensure_gitignore() {
    if [ -f ".gitignore" ]; then
        if ! grep -qxF ".shared/" .gitignore 2>/dev/null; then
            echo ".shared/" >> .gitignore
            echo "added .shared/ to .gitignore"
        fi
    fi
}

LANG="${1:-$(detect_language)}"

case "$LANG" in
    python)
        download_file "ruff.toml" "ruff.toml"
        ;;
    go)
        download_file ".golangci.yml" ".golangci.yml"
        ;;
    node)
        download_file "eslint.config.mjs" "eslint.config.mjs"
        ;;
    *)
        die "unknown language: $LANG (expected python, go, or node)"
        ;;
esac

# Always download self for future syncs
download_file "sync-shared-lint.sh" "sync-shared-lint.sh"
chmod +x "${SHARED_DIR}/sync-shared-lint.sh"

ensure_gitignore

echo "done. configs cached in ${SHARED_DIR}/"
