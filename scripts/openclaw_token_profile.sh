#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18789}"
AGENT_ID="${AGENT_ID:-main}"
MODEL="${MODEL:-openclaw}"
OUT_FILE="${OUT_FILE:-/tmp/openclaw_token_profile.csv}"
USER_PREFIX="${USER_PREFIX:-token-profile}"
MAX_TOKENS="${MAX_TOKENS:-360}"
TEMPERATURE="${TEMPERATURE:-0.2}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_token_profile.sh [options]

Options:
  --out <path>         Output CSV path (default: /tmp/openclaw_token_profile.csv)
  --base-url <url>     Gateway base URL (default: http://127.0.0.1:18789)
  --agent <id>         Agent id (default: main)
  --model <id>         Model id for gateway (default: openclaw)
  --max-tokens <n>     Max output tokens (default: 360)
  --temperature <v>    Temperature (default: 0.2)
  -h, --help           Show help

This script records both provider-reported usage and estimated usage.
If provider usage is zero/missing, it falls back to estimated tokens by chars.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_FILE="${2:-}"; shift 2 ;;
    --base-url) BASE_URL="${2:-}"; shift 2 ;;
    --agent) AGENT_ID="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --max-tokens) MAX_TOKENS="${2:-}"; shift 2 ;;
    --temperature) TEMPERATURE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }
}

require_cmd curl
require_cmd jq
require_cmd wc
require_cmd awk

TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"
if [[ -z "$TOKEN" && -f "$HOME/.openclaw/.env" ]]; then
  TOKEN="$(awk -F= '/^OPENCLAW_GATEWAY_TOKEN=/{print $2}' "$HOME/.openclaw/.env" | tail -n1)"
fi
if [[ -z "$TOKEN" ]]; then
  echo "OPENCLAW_GATEWAY_TOKEN not found in env or ~/.openclaw/.env" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT_FILE")"
printf '%s\n' "case_id,input_chars,output_chars,latency_ms,prompt_tokens,completion_tokens,total_tokens,est_prompt_tokens,est_completion_tokens,est_total_tokens,usage_source,result,summary" > "$OUT_FILE"

estimate_tokens() {
  local chars="$1"
  # Practical heuristic for CJK + mixed text.
  awk -v c="$chars" 'BEGIN { if (c <= 0) print 0; else printf("%d\n", int((c/1.8)+0.9999)); }'
}

call_case() {
  local case_id="$1"
  local msg="$2"
  local user="${USER_PREFIX}-${case_id}-$(date +%s)-$RANDOM"

  local payload
  payload="$(jq -n --arg model "$MODEL" --arg user "$user" --arg msg "$msg" --argjson mt "$MAX_TOKENS" --argjson temp "$TEMPERATURE" \
    '{model:$model,user:$user,messages:[{role:"user",content:$msg}],max_tokens:$mt,temperature:$temp}')"

  local t0 t1 latency resp out pt ct tt in_chars out_chars est_pt est_ct est_tt usage_source summary
  t0="$(date +%s%3N)"
  resp="$(curl -sS "${BASE_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-openclaw-agent-id: ${AGENT_ID}" \
    -H "Content-Type: application/json" \
    -d "$payload")"
  t1="$(date +%s%3N)"
  latency="$((t1 - t0))"

  out="$(printf '%s' "$resp" | jq -r '.choices[0].message.content // ""')"
  pt="$(printf '%s' "$resp" | jq -r '.usage.prompt_tokens // 0')"
  ct="$(printf '%s' "$resp" | jq -r '.usage.completion_tokens // 0')"
  tt="$(printf '%s' "$resp" | jq -r '.usage.total_tokens // 0')"

  in_chars="$(printf '%s' "$msg" | wc -m | awk '{print $1}')"
  out_chars="$(printf '%s' "$out" | wc -m | awk '{print $1}')"
  est_pt="$(estimate_tokens "$in_chars")"
  est_ct="$(estimate_tokens "$out_chars")"
  est_tt="$((est_pt + est_ct))"

  usage_source="provider_usage"
  if [[ "${tt}" -le 0 ]]; then
    usage_source="estimated_chars"
  fi

  summary="$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-180 | tr ',' ';')"
  local result="PASS"
  if [[ -z "$out" || "$out" == *"403 status code"* ]]; then
    result="FAIL"
    if [[ -z "$summary" ]]; then
      summary="$(printf '%s' "$resp" | tr '\n' ' ' | cut -c1-180 | tr ',' ';')"
    fi
  fi

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$case_id" "$in_chars" "$out_chars" "$latency" "$pt" "$ct" "$tt" \
    "$est_pt" "$est_ct" "$est_tt" "$usage_source" "$result" "$summary" >> "$OUT_FILE"
}

# Representative command/tool prompts.
call_case "CMD_HEALTH" "/xiao-health"
call_case "CMD_MEMORY" "/xiao-memory list"
call_case "CMD_WEATHER" "/xiao-weather 上海"
call_case "TOOL_WEATHER" "请调用 xiao_weather_openmeteo，city=Shanghai"
call_case "TOOL_STOCK" "请调用 xiao_stock_quote，symbol=600519"
call_case "TOOL_URL" "请调用 xiao_url_digest，总结这个链接：https://example.com"
call_case "TOOL_VISION" "请调用 xiao_vision_analyze，imageUrl=https://avatars.githubusercontent.com/u/9919?v=4"
call_case "TOOL_TTS" "请调用 xiao_tts_synthesize，text=你好"

echo "saved: $OUT_FILE"
echo "summary:"
awk -F',' 'NR>1{t++; if($12=="PASS")p++; if($11=="estimated_chars")e++; est+=$10}
END{printf("cases=%d pass=%d estimated_usage=%d avg_est_total_tokens=%.2f\n",t,p,e,(t?est/t:0))}' "$OUT_FILE"

