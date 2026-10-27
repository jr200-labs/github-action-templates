#!/usr/bin/env bash
# Parse YAML and enforce the configured runner profile, including forwarding.
set -euo pipefail
# These repository-policy checks use the runner's Python, not the caller's app pin.
exec uv run --no-project --python python3 --script "$(dirname "$0")/lint-workflow-runners.py" "$@"
