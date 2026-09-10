#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output=
headers=
payload=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|--dump-header|--data)
      case "$1" in
        --output) output="$2" ;;
        --dump-header) headers="$2" ;;
        --data) payload="$2" ;;
      esac
      shift 2
      ;;
    --connect-timeout|--max-time|--request|--write-out|--url|-H) shift 2 ;;
    *) shift ;;
  esac
done
count=0
[[ ! -f "$MOCK_CURL_COUNT" ]] || count="$(cat "$MOCK_CURL_COUNT")"
count=$((count + 1))
printf '%s' "$count" >"$MOCK_CURL_COUNT"
if (( count <= ${MOCK_FAIL_UNTIL:-0} )); then
  printf '000'
  exit 7
fi
printf 'HTTP/1.1 200 OK\r\nMcp-Session-Id: session-1\r\n\r\n' >"$headers"
case "$payload" in
  *'"method":"initialize"'*)
    printf '%s\n' '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"mock-mcp","version":"1"}}}' >"$output"
    ;;
  *'"method":"tools/list"'*)
    printf '%s\n' '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"read_topic"},{"name":"attach_repository"}]}}' >"$output"
    ;;
  *) : >"$output" ;;
esac
printf '200'
EOF
chmod +x "$tmp/curl"

endpoint='http://padd.agents.svc:8080/mcp/T-1/secret-value'
run_probe() {
  PATH="$tmp:$PATH" \
    MCP_ENDPOINT="$endpoint" \
    MCP_REQUIRED_TOOLS_JSON='["read_topic","attach_repository"]' \
    MCP_RETRY_DELAY_SECONDS=0 \
    MOCK_CURL_COUNT="$tmp/curl-count" \
    "$root/scripts/probe-mcp-server.sh"
}

run_probe >"$tmp/success.out" 2>&1
grep -q '^MCP_PROBE_OK protocol=2025-06-18 server=mock-mcp tools=2 catalogue_digest=' "$tmp/success.out"

rm -f "$tmp/curl-count"
if PATH="$tmp:$PATH" MCP_ENDPOINT="$endpoint" MCP_REQUIRED_TOOLS_JSON='["missing_tool"]' \
  MCP_RETRY_DELAY_SECONDS=0 MOCK_CURL_COUNT="$tmp/curl-count" \
  "$root/scripts/probe-mcp-server.sh" >"$tmp/missing.out" 2>&1; then
  echo 'missing required tool unexpectedly passed' >&2
  exit 1
fi
grep -Fq 'MCP_PROBE_ERROR stage=tools_list reason=missing_required_tools tools=["missing_tool"]' "$tmp/missing.out"

rm -f "$tmp/curl-count"
MOCK_FAIL_UNTIL=1 run_probe >"$tmp/retry.out" 2>&1
grep -q 'MCP_PROBE_RETRY stage=initialize reason=connection_failed attempt=1 attempts=3 next_attempt=2 curl_exit=7 http_status=000' "$tmp/retry.out"

rm -f "$tmp/curl-count"
if GITHUB_ACTIONS=true MOCK_FAIL_UNTIL=3 run_probe >"$tmp/failure.out" 2>&1; then
  echo 'unreachable MCP server unexpectedly passed' >&2
  exit 1
fi
grep -q 'MCP_PROBE_ERROR stage=initialize reason=connection_failed attempt=3 attempts=3 curl_exit=7 http_status=000' "$tmp/failure.out"
grep -q '^::error title=MCP protocol probe failed::stage=initialize reason=connection_failed attempt=3 attempts=3 curl_exit=7 http_status=000$' "$tmp/failure.out"

if grep -Fq "$endpoint" "$tmp"/*.out; then
  echo 'probe output exposed the secret endpoint' >&2
  exit 1
fi
