#!/usr/bin/env bash
# Sync canonical shared configs from jr200-labs/github-action-templates into this repo.
# Driven by shared/MANIFEST.json — see that file for the file lists.
#
# Usage:
#   ./sync.sh [python|go|node]        # single-language override
#   ./sync.sh "python node"           # explicit multi-language override
#   ./sync.sh all                     # sync every language in MANIFEST
#   ./sync.sh                         # auto-detect from present marker files
#
# Auto-detect iterates every marker file (pyproject.toml, go.mod,
# package.json) and syncs each language's configs — polyglot-safe.
# Use `all` when you want every language regardless of markers (e.g. a
# tooling repo that doesn't check in any marker file).
#
# Two destination modes per file (declared in MANIFEST.json):
#   - cache     → written to .shared/<file> (gitignored; re-fetched in CI)
#   - committed → written to <file> at repo root (must be committed; drift-checked)
#
# Offline tolerance:
#   - SYNC_OFFLINE=1       → skip entirely, exit 0 (explicit opt-out)
#   - network failures     → warn to stderr, skip that file, exit 0 (never block commit)
#   - missing jq/curl      → warn + exit 0 (local dev without deps still commits)

set -uo pipefail

REPO="jr200-labs/github-action-templates"
BRANCH="master"
SHARED_DIR=".shared"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/shared"

warn() { echo "sync: $*" >&2; }

if [ "${SYNC_OFFLINE:-0}" = "1" ]; then
    warn "SYNC_OFFLINE=1 — skipping shared config sync"
    exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found — skipping shared config sync"
    exit 0
fi
if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found — skipping shared config sync"
    exit 0
fi

detect_languages() {
    # Emit every language whose marker file is present. Polyglot repos
    # (e.g. Go backend + Node frontend at root) need all their configs.
    local langs=()
    [ -f "pyproject.toml" ] || [ -f "setup.py" ] && langs+=("python")
    [ -f "go.mod" ] && langs+=("go")
    [ -f "package.json" ] && langs+=("node")
    echo "${langs[*]}"
}

download_to() {
    local remote_path="$1"
    local local_path="$2"
    mkdir -p "$(dirname "$local_path")"
    if curl -sfL --max-time 10 "${BASE_URL}/${remote_path}" -o "${local_path}.tmp"; then
        mv "${local_path}.tmp" "$local_path"
        echo "synced: ${remote_path} -> ${local_path}"
    else
        rm -f "${local_path}.tmp"
        warn "offline or fetch failed — skipped ${remote_path}"
        return 1
    fi
}

ensure_gitignore() {
    if [ -f ".gitignore" ]; then
        if ! grep -qxF "${SHARED_DIR}/" .gitignore 2>/dev/null; then
            echo "${SHARED_DIR}/" >> .gitignore
            echo "added ${SHARED_DIR}/ to .gitignore"
        fi
    fi
}

# Fetch manifest — if network down, skip the whole sync cleanly.
MANIFEST_JSON=$(curl -sfL --max-time 10 "${BASE_URL}/MANIFEST.json" 2>/dev/null) || {
    warn "cannot fetch MANIFEST.json (offline?) — skipping sync"
    exit 0
}

LANGS="${1:-$(detect_languages)}"
if [ "$LANGS" = "all" ]; then
    # Expand to every language section declared in the manifest (excluding
    # 'common' and any non-language top-level keys like $schema / _comment).
    LANGS=$(echo "$MANIFEST_JSON" | jq -r 'keys[] | select(. != "common" and (startswith("$") or startswith("_") | not))' | tr '\n' ' ')
fi
if [ -z "$LANGS" ]; then
    warn "cannot detect project language(s) — pass python, go, or node as argument; skipping"
    exit 0
fi

get_files() {
    # $1 = section (common | <lang>), $2 = mode (cache | committed)
    echo "$MANIFEST_JSON" | jq -r --arg s "$1" --arg m "$2" '(.[$s][$m] // []) | .[]'
}

# Sync committed files (common + language-specific) → repo root
for section in common $LANGS; do
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        download_to "$f" "$f" || true
    done < <(get_files "$section" "committed")
done

# Sync cache files (common + language-specific) → .shared/
for section in common $LANGS; do
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        download_to "$f" "${SHARED_DIR}/$f" || true
    done < <(get_files "$section" "cache")
done

# Refresh self for future syncs (best-effort)
download_to "sync.sh" "${SHARED_DIR}/sync.sh" || true
[ -f "${SHARED_DIR}/sync.sh" ] && chmod +x "${SHARED_DIR}/sync.sh"

ensure_gitignore

echo "done."
