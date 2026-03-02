#!/usr/bin/env bash
set -euo pipefail

USER_ID="phase2-source-followup-user"
TEST_URL="https://example.com"
AGENT_ID="main"
BASE_URL="http://127.0.0.1:18789"

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_source_followup_check.sh [options]

Options:
  --user <id>       Stable OpenClaw API user id (default: phase2-source-followup-user)
  --url <url>       URL used in round-1 message (default: https://example.com)
  --agent <id>      Agent id (default: main)
  --base-url <url>  Gateway base URL (default: http://127.0.0.1:18789)
  -h, --help        Show this help

This check sends two API turns with the same user id:
1) ask to summarize a URL
2) ask for source link
Then verifies round-2 reply contains an http(s) URL.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      USER_ID="${2:-}"
      shift 2
      ;;
    --url)
      TEST_URL="${2:-}"
      shift 2
      ;;
    --agent)
      AGENT_ID="${2:-}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd curl
require_cmd jq
require_cmd rg

TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
if [[ -z "$TOKEN" && -f "$HOME/.openclaw/.env" ]]; then
  TOKEN="$(awk -F= '/^OPENCLAW_GATEWAY_TOKEN=/{print $2}' "$HOME/.openclaw/.env" | tail -n1)"
fi
if [[ -z "$TOKEN" ]]; then
  echo "OPENCLAW_GATEWAY_TOKEN not found in env or ~/.openclaw/.env" >&2
  exit 1
fi

call_api() {
  local message="$1"
  curl -sS "${BASE_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-openclaw-agent-id: ${AGENT_ID}" \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"openclaw\",
      \"user\": \"${USER_ID}\",
      \"messages\": [{\"role\":\"user\",\"content\": $(jq -Rn --arg v "$message" '$v')}],
      \"max_tokens\": 280,
      \"temperature\": 0.2
    }"
}

round1="帮我总结这个链接：${TEST_URL}"
round2="把刚刚的来源链接给我"

resp1="$(call_api "$round1")"
text1="$(printf '%s\n' "$resp1" | jq -r '.choices[0].message.content // ""')"
if [[ -z "$text1" ]]; then
  echo "[FAIL] round1 empty response"
  exit 1
fi

resp2="$(call_api "$round2")"
text2="$(printf '%s\n' "$resp2" | jq -r '.choices[0].message.content // ""')"
if [[ -z "$text2" ]]; then
  echo "[FAIL] round2 empty response"
  exit 1
fi

echo "== Round 1 =="
printf '%s\n' "$text1" | sed -n '1,6p'
echo
echo "== Round 2 =="
printf '%s\n' "$text2" | sed -n '1,8p'
echo

if printf '%s\n' "$text2" | rg -q 'https?://'; then
  echo "[PASS] source follow-up returned url"
  exit 0
fi

echo "[FAIL] source follow-up did not return url"
exit 1
