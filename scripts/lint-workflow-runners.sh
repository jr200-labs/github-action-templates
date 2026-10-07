#!/usr/bin/env bash
# Parse YAML and enforce the configured runner profile, including forwarding.
set -euo pipefail
exec uv run --script "$(dirname "$0")/lint-workflow-runners.py" "$@"
