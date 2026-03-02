#!/usr/bin/env bash
set -euo pipefail

DEEP=0
JOB_PREFIX="${JOB_PREFIX:-xiao}"
OBS_FILE="${XIAO_OBS_FILE:-$HOME/.openclaw/xiao-core/observability.jsonl}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_regression_smoke.sh [--deep]

Options:
  --deep   Trigger a probe turn to validate observability line generation
  -h,--help Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep)
      DEEP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 1
  }
}

require_cmd openclaw
require_cmd jq
require_cmd rg
require_cmd systemctl

pass=0
fail=0

check() {
  local name="$1"
  local cmd="$2"
  if bash -lc "$cmd" >/tmp/xiao_check.out 2>/tmp/xiao_check.err; then
    echo "[PASS] $name"
    pass=$((pass + 1))
  else
    echo "[FAIL] $name"
    sed -n '1,6p' /tmp/xiao_check.err || true
    sed -n '1,6p' /tmp/xiao_check.out || true
    fail=$((fail + 1))
  fi
}

check "gateway service active" "systemctl --user is-active openclaw-gateway.service | rg -q '^active$'"
check "gateway reachable" "openclaw status --json | sed -n '/^[[:space:]]*{/,\$p' | jq -e '.gateway.reachable == true' >/dev/null"
check "plugins loaded" "openclaw --no-color plugins list 2>&1 | rg -q '\\[plugins\\] xiao-core: loaded' && openclaw --no-color plugins list 2>&1 | rg -q '\\[plugins\\] xiao-services: loaded'"
check "cron baseline jobs" "openclaw cron list --json | sed -n '/^[[:space:]]*{/,\$p' | jq -r '.jobs[].name' | rg -c '^${JOB_PREFIX}-' | awk '\$1>=4{ok=1} END{exit ok?0:1}'"

if [[ "$DEEP" -eq 1 ]]; then
  echo "[INFO] running deep probe to generate observability metric..."
  jid="$(openclaw cron add --name xiao-regression-probe --agent main --at 30m --message '请调用 xiao_service_probe 并简短回复。' --no-deliver --keep-after-run --json | sed -n '/^[[:space:]]*{/,$p' | jq -r '.id')"
  openclaw cron run "$jid" --expect-final >/dev/null || true
  openclaw cron rm "$jid" >/dev/null || true

  check "observability file exists" "test -f '$OBS_FILE'"
  check "observability fields" "tail -n 20 '$OBS_FILE' | jq -e 'has(\"request_id\") and has(\"user_key\") and has(\"tool_name\") and has(\"latency_ms\") and has(\"error_code\")' >/dev/null"
fi

echo
echo "summary: pass=$pass fail=$fail"
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
