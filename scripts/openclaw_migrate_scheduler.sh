#!/usr/bin/env bash
set -euo pipefail

APPLY=0
REMOVE=0
AUTO_TARGET=0
TARGET=""
WEATHER_CITY="${WEATHER_CITY:-上海}"
WEATHER_TIME="${WEATHER_TIME:-08:20}"
FINANCE_TIME="${FINANCE_TIME:-15:35}"
PROACTIVE_EVERY_MIN="${PROACTIVE_EVERY_MIN:-480}"
GITHUB_WEEKLY_ENABLED="${GITHUB_WEEKLY_ENABLED:-1}"
GITHUB_WEEKLY_DAY="${GITHUB_WEEKLY_DAY:-sun}"
GITHUB_WEEKLY_TIME="${GITHUB_WEEKLY_TIME:-20:30}"
GITHUB_WEEKLY_LIMIT="${GITHUB_WEEKLY_LIMIT:-5}"
INFO_DIGEST_ENABLED="${INFO_DIGEST_ENABLED:-1}"
INFO_DIGEST_TIME="${INFO_DIGEST_TIME:-12:20}"
INFO_DIGEST_TOPIC="${INFO_DIGEST_TOPIC:-AI与科技热点}"
REFLECTION_ENABLED="${REFLECTION_ENABLED:-1}"
REFLECTION_TIME="${REFLECTION_TIME:-04:05}"
REFLECTION_HOURS="${REFLECTION_HOURS:-24}"
REFLECTION_MIN_MESSAGES="${REFLECTION_MIN_MESSAGES:-5}"
TZ_NAME="${TZ_NAME:-Asia/Shanghai}"
AGENT_ID="${AGENT_ID:-main}"
JOB_PREFIX="${JOB_PREFIX:-xiao}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_migrate_scheduler.sh [options]

Options:
  --apply                     Apply changes (default is dry-run)
  --remove                    Remove migrated jobs for the resolved target
  --auto-target               Auto-detect latest QQ direct target from openclaw status
  --target <qqbot:c2c:ID>     Explicit delivery target
  --weather-city <city>       Weather push city (default: 上海)
  --weather-time <HH:MM>      Weather push local time (default: 08:20)
  --finance-time <HH:MM>      Finance push local time (default: 15:35)
  --proactive-every <min>     Proactive interval minutes (default: 480)
  --github-weekly <0|1>       Enable GitHub weekly push migration (default: 1)
  --github-day <dow>          GitHub weekly day (sun..sat or 0..6, default: sun)
  --github-time <HH:MM>       GitHub weekly local time (default: 20:30)
  --github-limit <n>          GitHub weekly item limit hint (default: 5)
  --info-digest <0|1>         Enable daily info digest push (default: 1)
  --info-time <HH:MM>         Info digest local time (default: 12:20)
  --info-topic <text>         Info digest topic hint (default: AI与科技热点)
  --reflection <0|1>          Enable daily reflection push (default: 1)
  --reflection-time <HH:MM>   Reflection local time (default: 04:05)
  --reflection-hours <h>      Reflection lookback hours (default: 24)
  --reflection-min-msg <n>    Reflection min user messages (default: 5)
  --tz <IANA>                 Timezone (default: Asia/Shanghai)
  --agent <id>                Agent id (default: main)
  --job-prefix <prefix>       Job name prefix (default: xiao)
  -h, --help                  Show this help

Examples:
  scripts/openclaw_migrate_scheduler.sh --auto-target
  scripts/openclaw_migrate_scheduler.sh --apply --auto-target
  scripts/openclaw_migrate_scheduler.sh --remove --auto-target
  scripts/openclaw_migrate_scheduler.sh --apply --auto-target --github-day sun --github-time 20:30
  scripts/openclaw_migrate_scheduler.sh --apply --auto-target --info-digest 0
  scripts/openclaw_migrate_scheduler.sh --apply --target qqbot:c2c:483a... --weather-city 上海
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --remove)
      REMOVE=1
      shift
      ;;
    --auto-target)
      AUTO_TARGET=1
      shift
      ;;
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --weather-city)
      WEATHER_CITY="${2:-}"
      shift 2
      ;;
    --weather-time)
      WEATHER_TIME="${2:-}"
      shift 2
      ;;
    --finance-time)
      FINANCE_TIME="${2:-}"
      shift 2
      ;;
    --proactive-every)
      PROACTIVE_EVERY_MIN="${2:-}"
      shift 2
      ;;
    --github-weekly)
      GITHUB_WEEKLY_ENABLED="${2:-}"
      shift 2
      ;;
    --github-day)
      GITHUB_WEEKLY_DAY="${2:-}"
      shift 2
      ;;
    --github-time)
      GITHUB_WEEKLY_TIME="${2:-}"
      shift 2
      ;;
    --github-limit)
      GITHUB_WEEKLY_LIMIT="${2:-}"
      shift 2
      ;;
    --info-digest)
      INFO_DIGEST_ENABLED="${2:-}"
      shift 2
      ;;
    --info-time)
      INFO_DIGEST_TIME="${2:-}"
      shift 2
      ;;
    --info-topic)
      INFO_DIGEST_TOPIC="${2:-}"
      shift 2
      ;;
    --reflection)
      REFLECTION_ENABLED="${2:-}"
      shift 2
      ;;
    --reflection-time)
      REFLECTION_TIME="${2:-}"
      shift 2
      ;;
    --reflection-hours)
      REFLECTION_HOURS="${2:-}"
      shift 2
      ;;
    --reflection-min-msg)
      REFLECTION_MIN_MESSAGES="${2:-}"
      shift 2
      ;;
    --tz)
      TZ_NAME="${2:-}"
      shift 2
      ;;
    --agent)
      AGENT_ID="${2:-}"
      shift 2
      ;;
    --job-prefix)
      JOB_PREFIX="${2:-}"
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

require_cmd openclaw
require_cmd jq
require_cmd sha1sum

json_payload() {
  sed -n '/^[[:space:]]*{/,$p'
}

parse_hhmm() {
  local value="$1"
  if [[ ! "$value" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
    echo "Invalid HH:MM value: $value" >&2
    exit 1
  fi
  echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"
}

if [[ "$AUTO_TARGET" -eq 1 && -z "$TARGET" ]]; then
  STATUS_RAW="$(openclaw status --json 2>/dev/null || true)"
  TARGET_ID="$(
    printf '%s\n' "$STATUS_RAW" \
      | json_payload \
      | jq -r '.sessions.recent[]?.key | select(startswith("agent:main:qqbot:direct:")) | split(":")[-1]' \
      | head -n1
  )"
  if [[ -n "$TARGET_ID" && "$TARGET_ID" != "null" ]]; then
    TARGET="qqbot:c2c:${TARGET_ID}"
  fi
fi

if [[ -z "$TARGET" ]]; then
  echo "Target is required. Use --target or --auto-target." >&2
  exit 1
fi

if [[ ! "$TARGET" =~ ^qqbot:(c2c|group):[A-Za-z0-9._:-]{6,128}$ ]]; then
  echo "Invalid target format: $TARGET" >&2
  echo "Expected: qqbot:c2c:<openid> or qqbot:group:<groupid>" >&2
  exit 1
fi

if [[ ! "$PROACTIVE_EVERY_MIN" =~ ^[0-9]+$ ]] || [[ "$PROACTIVE_EVERY_MIN" -lt 30 ]]; then
  echo "--proactive-every must be an integer >= 30 minutes" >&2
  exit 1
fi

if [[ ! "$GITHUB_WEEKLY_ENABLED" =~ ^[01]$ ]]; then
  echo "--github-weekly must be 0 or 1" >&2
  exit 1
fi

if [[ ! "$INFO_DIGEST_ENABLED" =~ ^[01]$ ]]; then
  echo "--info-digest must be 0 or 1" >&2
  exit 1
fi

if [[ ! "$REFLECTION_ENABLED" =~ ^[01]$ ]]; then
  echo "--reflection must be 0 or 1" >&2
  exit 1
fi

if [[ ! "$GITHUB_WEEKLY_DAY" =~ ^(sun|mon|tue|wed|thu|fri|sat|0|1|2|3|4|5|6)$ ]]; then
  echo "--github-day must be one of sun..sat or 0..6" >&2
  exit 1
fi

if [[ ! "$GITHUB_WEEKLY_LIMIT" =~ ^[0-9]+$ ]] || [[ "$GITHUB_WEEKLY_LIMIT" -lt 1 ]] || [[ "$GITHUB_WEEKLY_LIMIT" -gt 20 ]]; then
  echo "--github-limit must be an integer in [1, 20]" >&2
  exit 1
fi

if [[ ! "$REFLECTION_HOURS" =~ ^[0-9]+$ ]] || [[ "$REFLECTION_HOURS" -lt 1 ]] || [[ "$REFLECTION_HOURS" -gt 168 ]]; then
  echo "--reflection-hours must be an integer in [1, 168]" >&2
  exit 1
fi

if [[ ! "$REFLECTION_MIN_MESSAGES" =~ ^[0-9]+$ ]] || [[ "$REFLECTION_MIN_MESSAGES" -lt 3 ]] || [[ "$REFLECTION_MIN_MESSAGES" -gt 60 ]]; then
  echo "--reflection-min-msg must be an integer in [3, 60]" >&2
  exit 1
fi

read -r WEATHER_H WEATHER_M <<<"$(parse_hhmm "$WEATHER_TIME")"
read -r FIN_H FIN_M <<<"$(parse_hhmm "$FINANCE_TIME")"
read -r GITHUB_H GITHUB_M <<<"$(parse_hhmm "$GITHUB_WEEKLY_TIME")"
read -r INFO_H INFO_M <<<"$(parse_hhmm "$INFO_DIGEST_TIME")"
read -r REFL_H REFL_M <<<"$(parse_hhmm "$REFLECTION_TIME")"

TARGET_HASH="$(printf '%s' "$TARGET" | sha1sum | awk '{print substr($1,1,10)}')"

WEATHER_JOB_NAME="${JOB_PREFIX}-weather-${TARGET_HASH}"
FINANCE_JOB_NAME="${JOB_PREFIX}-finance-${TARGET_HASH}"
PROACTIVE_JOB_NAME="${JOB_PREFIX}-proactive-${TARGET_HASH}"
GITHUB_JOB_NAME="${JOB_PREFIX}-github-weekly-${TARGET_HASH}"
INFO_JOB_NAME="${JOB_PREFIX}-info-digest-${TARGET_HASH}"
REFLECTION_JOB_NAME="${JOB_PREFIX}-reflection-${TARGET_HASH}"

WEATHER_MSG="你是小a。请给用户做${WEATHER_CITY}今日天气简报。要求：2-4行，先结论后建议，语气自然口语化，不要编造。"
FINANCE_MSG="你是小a。请生成A股收盘小白日报。要求：2-4行，包含整体盘面+风险提醒，避免投资承诺。"
PROACTIVE_MSG="你是小a。请主动发起一句简短关心问候，不超过2行，避免模板化。"
GITHUB_MSG="你是小a。请先调用工具 xiao_github_trending（since=weekly, limit=${GITHUB_WEEKLY_LIMIT}），再生成GitHub周榜简报。要求：2-5行，至少提到3个仓库与一句看点；若工具失败请明确说明暂时抓取失败，不要编造。"
INFO_MSG="你是小a。请先尝试调用 xiao_search_google（query='${INFO_DIGEST_TOPIC} 今日', maxResults=3）和 xiao_github_trending（since=daily, limit=2），再给出1段不超过4行的轻资讯简报；若工具不可用请明确说明，不要编造。"
REFLECTION_MSG="你是小a。请调用 xiao_daily_reflection（userKey='${TARGET}', hours=${REFLECTION_HOURS}, minUserMessages=${REFLECTION_MIN_MESSAGES}）。若 saved=true：用2-3行自然语气发一条关心式总结；若 no_data/insufficient_messages：简短说“今天先不打扰你，晚点再陪你”即可。"

list_jobs_json() {
  openclaw cron list --json | json_payload
}

find_job_id_by_name() {
  local name="$1"
  list_jobs_json | jq -r --arg n "$name" '.jobs[] | select(.name == $n) | .id' | head -n1
}

upsert_job() {
  local name="$1"
  shift

  local existing_id
  existing_id="$(find_job_id_by_name "$name")"

  if [[ -n "$existing_id" ]]; then
    if [[ "$APPLY" -eq 1 ]]; then
      echo "[apply] remove existing job: $name ($existing_id)"
      openclaw cron rm "$existing_id" >/dev/null
    else
      echo "[dry-run] would remove existing job: $name ($existing_id)"
    fi
  fi

  if [[ "$APPLY" -eq 1 ]]; then
    local out
    out="$(openclaw cron add --name "$name" "$@" --json)"
    echo "$out" | json_payload | jq '{id,name,enabled,schedule,delivery}'
  else
    echo "[dry-run] openclaw cron add --name $name $* --json"
  fi
}

remove_job_if_exists() {
  local name="$1"
  local existing_id
  existing_id="$(find_job_id_by_name "$name")"

  if [[ -z "$existing_id" ]]; then
    echo "[skip] job not found: $name"
    return 0
  fi

  if [[ "$APPLY" -eq 1 ]]; then
    echo "[apply] remove job: $name ($existing_id)"
    openclaw cron rm "$existing_id" >/dev/null
  else
    echo "[dry-run] would remove job: $name ($existing_id)"
  fi
}

echo "== Scheduler migration plan =="
echo "apply=$APPLY"
echo "remove=$REMOVE"
echo "target=$TARGET"
echo "timezone=$TZ_NAME"
echo "weather: city=$WEATHER_CITY time=$WEATHER_TIME"
echo "finance: time=$FINANCE_TIME (weekdays)"
echo "proactive: every=${PROACTIVE_EVERY_MIN}m"
echo "github_weekly: enabled=$GITHUB_WEEKLY_ENABLED day=$GITHUB_WEEKLY_DAY time=$GITHUB_WEEKLY_TIME limit=$GITHUB_WEEKLY_LIMIT"
echo "info_digest: enabled=$INFO_DIGEST_ENABLED time=$INFO_DIGEST_TIME topic=$INFO_DIGEST_TOPIC"
echo "reflection: enabled=$REFLECTION_ENABLED time=$REFLECTION_TIME hours=$REFLECTION_HOURS min_msg=$REFLECTION_MIN_MESSAGES"
echo

if [[ "$REMOVE" -eq 1 ]]; then
  remove_job_if_exists "$WEATHER_JOB_NAME"
  remove_job_if_exists "$FINANCE_JOB_NAME"
  remove_job_if_exists "$PROACTIVE_JOB_NAME"
  remove_job_if_exists "$GITHUB_JOB_NAME"
  remove_job_if_exists "$INFO_JOB_NAME"
  remove_job_if_exists "$REFLECTION_JOB_NAME"

  if [[ "$APPLY" -eq 1 ]]; then
    echo
    echo "== Remaining jobs with prefix =="
    openclaw cron list --json | json_payload | jq --arg p "${JOB_PREFIX}-" '{jobs: [.jobs[] | select(.name | startswith($p)) | {id,name,enabled,schedule,delivery}], total: (.jobs | map(select(.name | startswith($p))) | length)}'
  fi
  exit 0
fi

upsert_job "$WEATHER_JOB_NAME" \
  --agent "$AGENT_ID" \
  --cron "${WEATHER_M} ${WEATHER_H} * * *" \
  --tz "$TZ_NAME" \
  --message "$WEATHER_MSG" \
  --announce \
  --channel qqbot \
  --to "$TARGET" \
  --session isolated \
  --thinking minimal \
  --description "xiao weather daily push (${WEATHER_CITY})"

echo

upsert_job "$FINANCE_JOB_NAME" \
  --agent "$AGENT_ID" \
  --cron "${FIN_M} ${FIN_H} * * 1-5" \
  --tz "$TZ_NAME" \
  --message "$FINANCE_MSG" \
  --announce \
  --channel qqbot \
  --to "$TARGET" \
  --session isolated \
  --thinking minimal \
  --description "xiao finance daily push"

echo

upsert_job "$PROACTIVE_JOB_NAME" \
  --agent "$AGENT_ID" \
  --every "${PROACTIVE_EVERY_MIN}m" \
  --message "$PROACTIVE_MSG" \
  --announce \
  --channel qqbot \
  --to "$TARGET" \
  --session isolated \
  --thinking minimal \
  --description "xiao proactive periodic ping"

echo

if [[ "$GITHUB_WEEKLY_ENABLED" -eq 1 ]]; then
  upsert_job "$GITHUB_JOB_NAME" \
    --agent "$AGENT_ID" \
    --cron "${GITHUB_M} ${GITHUB_H} * * ${GITHUB_WEEKLY_DAY}" \
    --tz "$TZ_NAME" \
    --message "$GITHUB_MSG" \
    --announce \
    --channel qqbot \
    --to "$TARGET" \
    --session isolated \
    --thinking minimal \
    --description "xiao github weekly push"
else
  remove_job_if_exists "$GITHUB_JOB_NAME"
fi

echo

if [[ "$INFO_DIGEST_ENABLED" -eq 1 ]]; then
  upsert_job "$INFO_JOB_NAME" \
    --agent "$AGENT_ID" \
    --cron "${INFO_M} ${INFO_H} * * *" \
    --tz "$TZ_NAME" \
    --message "$INFO_MSG" \
    --announce \
    --channel qqbot \
    --to "$TARGET" \
    --session isolated \
    --thinking minimal \
    --description "xiao info digest daily push"
else
  remove_job_if_exists "$INFO_JOB_NAME"
fi

echo

if [[ "$REFLECTION_ENABLED" -eq 1 ]]; then
  upsert_job "$REFLECTION_JOB_NAME" \
    --agent "$AGENT_ID" \
    --cron "${REFL_M} ${REFL_H} * * *" \
    --tz "$TZ_NAME" \
    --message "$REFLECTION_MSG" \
    --announce \
    --channel qqbot \
    --to "$TARGET" \
    --session isolated \
    --thinking minimal \
    --description "xiao reflection daily push"
else
  remove_job_if_exists "$REFLECTION_JOB_NAME"
fi

if [[ "$APPLY" -eq 1 ]]; then
  echo
  echo "== Installed jobs =="
  openclaw cron list --json | json_payload | jq --arg p "${JOB_PREFIX}-" '{jobs: [.jobs[] | select(.name | startswith($p)) | {id,name,enabled,schedule,delivery,nextRunAtMs:.state.nextRunAtMs}]}'
fi
