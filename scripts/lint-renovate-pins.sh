#!/usr/bin/env bash
# Lint packageRule pins in default.json to enforce periodic review.
#
# Policy:
#   Any packageRule with `matchPackageNames` set AND `enabled: false`
#   must include `review-by: YYYY-MM-DD` in its description. The date:
#     * must parse as an ISO calendar date (YYYY-MM-DD)
#     * must NOT be in the past (expired pin = fail; forces reconsider)
#     * must NOT be more than 31 days in the future (no long pins)
#
# Rationale: we never want a forever pin. Every version-scoped disable
# is a bet that the upstream bug will be fixed or we'll revisit the
# decision; the date is the commitment to actually revisit.
#
# Structural disables that only set `matchManagers` (e.g. disabling a
# duplicate manager that collides with a custom regex manager) are NOT
# pins and are exempt — they are permanent config choices, not version
# decisions.
#
# Usage:
#   lint-renovate-pins.sh [<path-to-default.json>]
#     default path: ./default.json (repo root)
#
# Exit codes:
#   0  all pins compliant
#   1  one or more pins missing/expired/too-far-out
#   2  config file missing or unparseable
#
# Dependencies: jq, date (GNU or BSD — both handled via python fallback
# for portable arithmetic).

set -euo pipefail

file="${1:-default.json}"

if [[ ! -f "$file" ]]; then
  echo "ERROR: config file not found: $file" >&2
  exit 2
fi

if ! jq -e . "$file" >/dev/null 2>&1; then
  echo "ERROR: $file is not valid JSON" >&2
  exit 2
fi

# today / max_future as ISO dates. Use python for cross-platform date
# arithmetic (GNU `date -d` and BSD `date -v` have incompatible syntax).
today=$(python3 -c 'import datetime; print(datetime.date.today().isoformat())')
max_future=$(python3 -c 'import datetime; print((datetime.date.today() + datetime.timedelta(days=31)).isoformat())')

echo "lint-renovate-pins: today=${today} max_review_by=${max_future}" >&2

# Emit TSV rows for every pin rule: index, packageNames, description.
# Only rules with matchPackageNames are in scope (structural
# matchManagers-only disables are exempt).
mapfile -t rows < <(jq -r '
  .packageRules // []
  | to_entries[]
  | select(.value.enabled == false)
  | select((.value.matchPackageNames // []) | length > 0)
  | [
      .key,
      ((.value.matchPackageNames // []) | join(",")),
      (.value.description // "")
    ]
  | @tsv
' "$file")

fail_count=0
pin_count=${#rows[@]}

for row in "${rows[@]}"; do
  IFS=$'\t' read -r idx pkgs desc <<<"$row"
  label="packageRules[${idx}] (${pkgs})"

  # Extract review-by: YYYY-MM-DD (first occurrence wins).
  if [[ "$desc" =~ review-by:\ ([0-9]{4}-[0-9]{2}-[0-9]{2}) ]]; then
    date="${BASH_REMATCH[1]}"
  else
    echo "FAIL ${label}: missing 'review-by: YYYY-MM-DD' in description" >&2
    fail_count=$((fail_count + 1))
    continue
  fi

  # Validate date parses + not past + not > 31d out.
  if ! python3 -c "import datetime; datetime.date.fromisoformat('${date}')" >/dev/null 2>&1; then
    echo "FAIL ${label}: review-by '${date}' is not a valid ISO date" >&2
    fail_count=$((fail_count + 1))
    continue
  fi

  if [[ "$date" < "$today" ]]; then
    echo "FAIL ${label}: review-by ${date} is in the past (today=${today}) — reconsider the pin" >&2
    fail_count=$((fail_count + 1))
    continue
  fi

  if [[ "$date" > "$max_future" ]]; then
    echo "FAIL ${label}: review-by ${date} is more than 31 days out (max=${max_future}) — no long pins" >&2
    fail_count=$((fail_count + 1))
    continue
  fi

  echo "ok   ${label}: review-by ${date}" >&2
done

echo "lint-renovate-pins: pins=${pin_count} fails=${fail_count}" >&2

if (( fail_count > 0 )); then
  exit 1
fi
exit 0
