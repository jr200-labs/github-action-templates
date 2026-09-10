#!/usr/bin/env bash
set -euo pipefail

fail() {
  local message="$*"
  [[ "${GITHUB_ACTIONS:-}" != "true" ]] || printf '::error title=MCP protocol probe failed::%s\n' "$message" >&2
  printf 'MCP_PROBE_ERROR %s\n' "$message" >&2
  exit 1
}

for command in curl jq sha256sum; do
  command -v "$command" >/dev/null 2>&1 || fail "stage=preflight reason=missing_command command=$command"
done

endpoint="${MCP_ENDPOINT:-}"
authorization="${MCP_AUTHORIZATION:-}"
required_tools="${MCP_REQUIRED_TOOLS_JSON:-[]}"
attempts="${MCP_ATTEMPTS:-3}"
timeout_seconds="${MCP_TIMEOUT_SECONDS:-20}"
retry_delay_seconds="${MCP_RETRY_DELAY_SECONDS:-2}"

[[ -n "$endpoint" ]] || fail 'stage=preflight reason=missing_endpoint'
[[ "$attempts" =~ ^[1-9]$|^10$ ]] || fail 'stage=preflight reason=invalid_attempts'
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || fail 'stage=preflight reason=invalid_timeout'
[[ "$retry_delay_seconds" =~ ^[0-9]+$ ]] || fail 'stage=preflight reason=invalid_retry_delay'
jq -e 'type == "array" and all(.[]; type == "string" and length > 0)' <<<"$required_tools" >/dev/null \
  || fail 'stage=preflight reason=invalid_required_tools_json'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
common_headers=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
[[ -z "$authorization" ]] || common_headers+=(-H "Authorization: $authorization")
protocol_headers=()

transport_reason() {
  local curl_exit="$1" http_status="$2"
  if [[ "$curl_exit" == 0 && ! "$http_status" =~ ^2[0-9][0-9]$ ]]; then
    printf 'http_error'; return
  fi
  case "$curl_exit" in
    6) printf 'dns_resolution_failed' ;;
    7) printf 'connection_failed' ;;
    28) printf 'timeout' ;;
    *) printf 'transport_failed' ;;
  esac
}

request() {
  local stage="$1" payload="$2"
  local response_file="$tmp/$stage.response" header_file="$tmp/$stage.headers" stderr_file="$tmp/$stage.stderr"
  local attempt http_status curl_exit reason
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    : >"$response_file"; : >"$header_file"; : >"$stderr_file"
    set +e
    http_status="$(curl --silent --show-error --connect-timeout "$timeout_seconds" --max-time "$timeout_seconds" \
      --request POST --output "$response_file" --dump-header "$header_file" --write-out '%{http_code}' \
      "${common_headers[@]}" "${protocol_headers[@]}" --data "$payload" --url "$endpoint" 2>"$stderr_file")"
    curl_exit=$?
    set -e
    [[ "$curl_exit" != 0 || ! "$http_status" =~ ^2[0-9][0-9]$ ]] || return 0
    reason="$(transport_reason "$curl_exit" "$http_status")"
    if (( attempt == attempts )); then
      fail "stage=$stage reason=$reason attempt=$attempt attempts=$attempts curl_exit=$curl_exit http_status=${http_status:-000}"
    fi
    printf 'MCP_PROBE_RETRY stage=%s reason=%s attempt=%d attempts=%d next_attempt=%d curl_exit=%d http_status=%s delay_seconds=%s\n' \
      "$stage" "$reason" "$attempt" "$attempts" "$((attempt + 1))" "$curl_exit" "${http_status:-000}" "$retry_delay_seconds" >&2
    sleep "$retry_delay_seconds"
  done
}

normalize_json() {
  local stage="$1" response_file="$tmp/$stage.response" normalized_file="$tmp/$stage.json"
  jq -e . "$response_file" >"$normalized_file" 2>/dev/null && return
  sed -n 's/^data:[[:space:]]*//p' "$response_file" | tail -n 1 >"$normalized_file"
  jq -e . "$normalized_file" >/dev/null 2>&1 \
    || fail "stage=$stage reason=invalid_json response_digest=$(sha256sum "$response_file" | cut -d' ' -f1)"
}

validate_rpc_result() {
  local stage="$1" normalized_file="$tmp/$stage.json"
  if jq -e '.error != null' "$normalized_file" >/dev/null; then
    fail "stage=$stage reason=jsonrpc_error code=$(jq -r '.error.code // "unknown"' "$normalized_file") response_digest=$(sha256sum "$normalized_file" | cut -d' ' -f1)"
  fi
  jq -e '.result != null' "$normalized_file" >/dev/null \
    || fail "stage=$stage reason=missing_result response_digest=$(sha256sum "$normalized_file" | cut -d' ' -f1)"
}

request initialize '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"github-actions-mcp-probe","version":"1.0.0"}}}'
normalize_json initialize
validate_rpc_result initialize
protocol="$(jq -r '.result.protocolVersion // empty' "$tmp/initialize.json")"
server_name="$(jq -r '.result.serverInfo.name // empty' "$tmp/initialize.json")"
[[ -n "$protocol" ]] || fail 'stage=initialize reason=missing_protocol_version'
[[ -n "$server_name" ]] || fail 'stage=initialize reason=missing_server_name'
session_id="$(awk -F ':[[:space:]]*' 'tolower($1) == "mcp-session-id" {sub(/\r$/, "", $2); print $2}' "$tmp/initialize.headers" | tail -n 1)"
protocol_headers=(-H "MCP-Protocol-Version: $protocol")
[[ -z "$session_id" ]] || protocol_headers+=(-H "Mcp-Session-Id: $session_id")

request initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'
request tools_list '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
normalize_json tools_list
validate_rpc_result tools_list
jq -e '.result.tools | type == "array" and all(.[]; (.name | type == "string") and (.name | length > 0))' "$tmp/tools_list.json" >/dev/null \
  || fail "stage=tools_list reason=invalid_tool_catalogue response_digest=$(sha256sum "$tmp/tools_list.json" | cut -d' ' -f1)"
missing_tools="$(jq -c --argjson required "$required_tools" \
  '([.result.tools[].name] | unique) as $available | [$required[] as $name | select($available | index($name) == null) | $name]' \
  "$tmp/tools_list.json")"
[[ "$missing_tools" == '[]' ]] || fail "stage=tools_list reason=missing_required_tools tools=$missing_tools"

tool_count="$(jq '.result.tools | length' "$tmp/tools_list.json")"
catalogue_digest="$(jq -r '.result.tools[].name' "$tmp/tools_list.json" | LC_ALL=C sort -u | sha256sum | cut -d' ' -f1)"
printf 'MCP_PROBE_OK protocol=%s server=%s tools=%s catalogue_digest=%s\n' "$protocol" "$server_name" "$tool_count" "$catalogue_digest"
