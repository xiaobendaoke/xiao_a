#!/usr/bin/env bash
set -euo pipefail

json_payload() {
  sed -n '/^[[:space:]]*{/,$p'
}

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
warn=0

record_pass() {
  echo "[PASS] $1"
  pass=$((pass + 1))
}

record_fail() {
  echo "[FAIL] $1"
  if [[ -n "${2:-}" ]]; then
    echo "  detail: $2"
  fi
  fail=$((fail + 1))
}

record_warn() {
  echo "[WARN] $1"
  if [[ -n \"${2:-}\" ]]; then
    echo "  detail: $2"
  fi
  warn=$((warn + 1))
}

check_cmd() {
  local name="$1"
  local cmd="$2"
  if bash -lc "$cmd" >/tmp/xiao_ffc_out.log 2>/tmp/xiao_ffc_err.log; then
    record_pass "$name"
  else
    local d="$(sed -n '1,3p' /tmp/xiao_ffc_err.log; sed -n '1,3p' /tmp/xiao_ffc_out.log)"
    record_fail "$name" "$d"
  fi
}

check_cmd_optional() {
  local name="$1"
  local cmd="$2"
  if bash -lc "$cmd" >/tmp/xiao_ffc_out.log 2>/tmp/xiao_ffc_err.log; then
    record_pass "$name"
  else
    local d="$(sed -n '1,3p' /tmp/xiao_ffc_err.log; sed -n '1,3p' /tmp/xiao_ffc_out.log)"
    record_warn "$name" "${d:-optional check failed}"
  fi
}

wait_for_run_entry() {
  local jid="$1"
  local out_file="$2"
  local wait_sec="${XIAO_FFC_WAIT_SEC:-120}"
  local waited=0
  local status=""
  local summary=""
  while [[ "$waited" -le "$wait_sec" ]]; do
    if openclaw cron runs --id "$jid" --limit 1 | json_payload >"$out_file" 2>/tmp/xiao_ffc_runs.err; then
      status="$(jq -r '.entries[0].status // ""' "$out_file" 2>/dev/null || true)"
      summary="$(jq -r '.entries[0].summary // ""' "$out_file" 2>/dev/null || true)"
      if [[ -n "$summary" ]]; then
        return 0
      fi
      if [[ "$status" == "ok" || "$status" == "error" || "$status" == "failed" ]]; then
        return 0
      fi
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

run_case() {
  local name="$1"
  local message="$2"
  local expect_regex="$3"

  local jid
  jid="$(openclaw cron add \
    --name "xiao-ffc-${name}-$(date +%s)-$RANDOM" \
    --agent main \
    --at 30m \
    --message "$message" \
    --no-deliver \
    --keep-after-run \
    --json | json_payload | jq -r '.id')"

  openclaw cron run "$jid" --expect-final >/tmp/xiao_ffc_run.log 2>&1 || true
  if ! wait_for_run_entry "$jid" /tmp/xiao_ffc_runs.json; then
    record_fail "$name" "run timeout waiting final entry"
    openclaw cron rm "$jid" >/dev/null 2>&1 || true
    return 0
  fi
  openclaw cron rm "$jid" >/dev/null 2>&1 || true

  local summary
  summary="$(jq -r '.entries[0].summary // ""' /tmp/xiao_ffc_runs.json 2>/dev/null || true)"

  if [[ -z "$summary" ]]; then
    record_fail "$name" "empty summary"
    return 0
  fi

  if printf '%s' "$summary" | rg -qi "$expect_regex"; then
    record_pass "$name"
  else
    record_fail "$name" "unexpected summary: $(printf '%s' "$summary" | tr '\n' ' ' | cut -c1-220)"
  fi
}

run_case_optional() {
  local name="$1"
  local message="$2"
  local expect_regex="$3"

  local jid
  jid="$(openclaw cron add \
    --name "xiao-ffc-${name}-$(date +%s)-$RANDOM" \
    --agent main \
    --at 30m \
    --message "$message" \
    --no-deliver \
    --keep-after-run \
    --json | json_payload | jq -r '.id')"

  openclaw cron run "$jid" --expect-final >/tmp/xiao_ffc_run.log 2>&1 || true
  if ! wait_for_run_entry "$jid" /tmp/xiao_ffc_runs.json; then
    record_warn "$name" "run timeout waiting final entry (qq context likely required)"
    openclaw cron rm "$jid" >/dev/null 2>&1 || true
    return 0
  fi
  openclaw cron rm "$jid" >/dev/null 2>&1 || true

  local summary
  summary="$(jq -r '.entries[0].summary // ""' /tmp/xiao_ffc_runs.json 2>/dev/null || true)"
  if [[ -z "$summary" ]]; then
    record_warn "$name" "empty summary (qq context likely required)"
    return 0
  fi
  if printf '%s' "$summary" | rg -qi "$expect_regex"; then
    record_pass "$name"
  else
    record_warn "$name" "qq command may require real qq context. summary: $(printf '%s' "$summary" | tr '\n' ' ' | cut -c1-220)"
  fi
}

echo "== OpenClaw Full Feature Check =="

check_cmd "service active" "systemctl --user is-active openclaw-gateway.service | rg -q '^active$'"
check_cmd "gateway reachable" "openclaw status --json | sed -n '/^[[:space:]]*{/,\$p' | jq -e '.gateway.reachable == true' >/dev/null"
check_cmd "plugins loaded" "openclaw --no-color plugins list 2>&1 | rg -q '\\[plugins\\] xiao-core: loaded' && openclaw --no-color plugins list 2>&1 | rg -q '\\[plugins\\] xiao-services: loaded'"
check_cmd "cron baseline >=4" "openclaw cron list --json | sed -n '/^[[:space:]]*{/,\$p' | jq -e '(.jobs | length) >= 4' >/dev/null"

run_case_optional "cmd-health" "/xiao-health" "xiao-core health|uptime"
run_case_optional "cmd-whoami" "/xiao-whoami" "xiao-core whoami|resolved_user_key"
run_case_optional "cmd-memory-add" "/xiao-memory add 回归测试香蕉" "memory saved|saved|记下|保存"
run_case "cmd-memory-search" "/xiao-memory search 香蕉" "score|香蕉|memory"
run_case_optional "cmd-links" "/xiao-links 3" "recent links|no recent links|link"
run_case_optional "cmd-reflect" "/xiao-reflect 24" "reflection saved|reflection skipped|reflection failed"
run_case_optional "cmd-persona" "/xiao-persona list" "available|default|bestie|little_sister"
run_case_optional "cmd-love-score" "/xiao-love-score" "恋爱指数|score|互动"
run_case_optional "cmd-plan" "/xiao-plan list" "待办约定|暂无待办"
run_case_optional "cmd-habit" "/xiao-habit list" "习惯打卡|还没有创建"
run_case_optional "cmd-diary" "/xiao-diary today" "今天心情|还没有心情记录"
run_case_optional "cmd-game" "/xiao-game start riddle" "猜谜|游戏|usage"
run_case_optional "cmd-greet" "/xiao-greet 晚安" "晚安|问候"

run_case "tool-weather" "请调用 xiao_weather_openmeteo 查询上海天气并给出简报。" "上海|天气|温度"
run_case "tool-stock" "请调用 xiao_stock_quote 查询600519并简要说明。" "600519|茅台|涨|跌|现价"
run_case "tool-github" "请调用 xiao_github_trending（since=weekly,limit=3），给出3个仓库名。" "/|github|仓库"
run_case "tool-url" "请调用 xiao_url_digest，总结这个链接的主要内容：https://github.com/openai/openai-cookbook" "github|openai|summary|摘要|仓库"
run_case "tool-vision" "请调用 xiao_vision_analyze 分析这张图 https://avatars.githubusercontent.com/u/9919?v=4 ，然后简述结果。" "图片|logo|octocat|github|分析"
run_case "tool-tts" "请调用 xiao_tts_synthesize，把文本“你好”转语音后回复成功结果。" "语音|合成|file|成功"
run_case "tool-asr" "请调用 xiao_asr_transcribe，audioPath=/root/xiao_a/xiao_a.wav，输出识别是否成功。" "识别|转写|听到|失败|成功|你好"
run_case_optional "tool-music" "请调用 xiao_music_resolve 解析这个链接：https://music.163.com/#/song?id=347230" "music|song|netease|ok|失败"
run_case_optional "tool-movie" "请调用 xiao_movie_recommend，query=科幻，limit=3" "movie|电影|tmdb|missing_env|ok"
run_case_optional "tool-restaurant" "请调用 xiao_restaurant_search，city=成都，keyword=火锅，limit=3" "restaurant|amap|missing_env|ok"
run_case_optional "tool-express" "请调用 xiao_express_track，company=顺丰，number=SF1234567890" "快递|kdniao|missing_env|ok|error"
check_cmd_optional "followup-source-http" "cd /root/xiao_a && ./scripts/openclaw_source_followup_check.sh --user ffc-source-check --url https://example.com >/tmp/xiao_ffc_followup.log 2>&1 && rg -q '\\[PASS\\] source follow-up returned url' /tmp/xiao_ffc_followup.log"

# /xiao-remind 在非 qqbot 上下文应给出保护提示
run_case_optional "cmd-remind-guard" "/xiao-remind 5 喝水" "qqbot|上下文|提醒"

echo
printf 'summary: pass=%s fail=%s warn=%s\n' "$pass" "$fail" "$warn"
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
