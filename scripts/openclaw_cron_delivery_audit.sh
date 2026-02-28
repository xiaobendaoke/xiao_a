#!/usr/bin/env bash
set -euo pipefail

DAYS=3
LIMIT=200
JOB_PREFIX="xiao-"
STRICT=0
MATCH_GRACE_MIN=30

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_cron_delivery_audit.sh [options]

Options:
  --days <n>           Lookback days (default: 3)
  --limit <n>          Max run history entries per job (default: 200, max: 200)
  --job-prefix <pfx>   Filter cron jobs by name prefix (default: xiao-)
  --match-grace-min <n> Allowed timing drift minutes for scheduled run matching (default: 30)
  --strict             Exit non-zero when anomalies exist
  -h, --help           Show this help

Examples:
  scripts/openclaw_cron_delivery_audit.sh
  scripts/openclaw_cron_delivery_audit.sh --days 7 --strict
  scripts/openclaw_cron_delivery_audit.sh --job-prefix xiao-
  scripts/openclaw_cron_delivery_audit.sh --days 7 --match-grace-min 20
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days)
      DAYS="${2:-}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-}"
      shift 2
      ;;
    --job-prefix)
      JOB_PREFIX="${2:-}"
      shift 2
      ;;
    --match-grace-min)
      MATCH_GRACE_MIN="${2:-}"
      shift 2
      ;;
    --strict)
      STRICT=1
      shift
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
require_cmd date

if [[ ! "$DAYS" =~ ^[0-9]+$ ]] || [[ "$DAYS" -lt 1 ]] || [[ "$DAYS" -gt 30 ]]; then
  echo "--days must be an integer in [1, 30]" >&2
  exit 1
fi
if [[ ! "$LIMIT" =~ ^[0-9]+$ ]] || [[ "$LIMIT" -lt 20 ]] || [[ "$LIMIT" -gt 200 ]]; then
  echo "--limit must be an integer in [20, 200]" >&2
  exit 1
fi
if [[ ! "$MATCH_GRACE_MIN" =~ ^[0-9]+$ ]] || [[ "$MATCH_GRACE_MIN" -lt 1 ]] || [[ "$MATCH_GRACE_MIN" -gt 180 ]]; then
  echo "--match-grace-min must be an integer in [1, 180]" >&2
  exit 1
fi

json_payload() {
  sed -n '/^[[:space:]]*{/,$p'
}

to_epoch_ms() {
  local dt="$1"
  local tz="$2"
  TZ="$tz" date -d "$dt" +%s 2>/dev/null | awk '{print $1 * 1000}'
}

to_date() {
  local ms="$1"
  local tz="$2"
  TZ="$tz" date -d "@$((ms / 1000))" +%F
}

bucket_daily() {
  local ms="$1"
  local tz="$2"
  TZ="$tz" date -d "@$((ms / 1000))" +%F
}

bucket_weekly() {
  local ms="$1"
  local tz="$2"
  TZ="$tz" date -d "@$((ms / 1000))" +%G-W%V
}

token_to_dow_num() {
  local raw
  raw="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    sun|0|7) echo 0 ;;
    mon|1) echo 1 ;;
    tue|2) echo 2 ;;
    wed|3) echo 3 ;;
    thu|4) echo 4 ;;
    fri|5) echo 5 ;;
    sat|6) echo 6 ;;
    *) echo -1 ;;
  esac
}

dow_matches() {
  local expr="$1"
  local date_ymd="$2"
  local tz="$3"
  local e
  e="$(echo "${expr:-*}" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
  if [[ -z "$e" || "$e" == "*" ]]; then
    return 0
  fi

  local uday cron_day
  uday="$(TZ="$tz" date -d "${date_ymd} 00:00:00" +%u)"
  if [[ "$uday" == "7" ]]; then
    cron_day=0
  else
    cron_day="$uday"
  fi

  IFS=',' read -r -a parts <<<"$e"
  local part left right left_num right_num tok_num
  for part in "${parts[@]}"; do
    if [[ "$part" == "*" ]]; then
      return 0
    fi
    if [[ "$part" == *-* ]]; then
      left="${part%-*}"
      right="${part#*-}"
      left_num="$(token_to_dow_num "$left")"
      right_num="$(token_to_dow_num "$right")"
      if [[ "$left_num" -ge 0 && "$right_num" -ge 0 ]]; then
        if [[ "$left_num" -le "$right_num" ]]; then
          if [[ "$cron_day" -ge "$left_num" && "$cron_day" -le "$right_num" ]]; then
            return 0
          fi
        else
          if [[ "$cron_day" -ge "$left_num" || "$cron_day" -le "$right_num" ]]; then
            return 0
          fi
        fi
      fi
      continue
    fi
    tok_num="$(token_to_dow_num "$part")"
    if [[ "$tok_num" -ge 0 && "$tok_num" -eq "$cron_day" ]]; then
      return 0
    fi
  done

  return 1
}

count_expected_cron() {
  local start_ms="$1"
  local end_ms="$2"
  local minute="$3"
  local hour="$4"
  local dow_expr="$5"
  local tz="$6"

  local d end_date sched_dt sched_ms expected
  d="$(to_date "$start_ms" "$tz")"
  end_date="$(to_date "$end_ms" "$tz")"
  expected=0

  while [[ "$d" < "$end_date" || "$d" == "$end_date" ]]; do
    if dow_matches "$dow_expr" "$d" "$tz"; then
      sched_dt="${d} ${hour}:${minute}:00"
      sched_ms="$(to_epoch_ms "$sched_dt" "$tz")"
      if [[ "$sched_ms" =~ ^[0-9]+$ ]] && [[ "$sched_ms" -ge "$start_ms" ]] && [[ "$sched_ms" -le "$end_ms" ]]; then
        expected=$((expected + 1))
      fi
    fi
    d="$(TZ="$tz" date -d "${d} +1 day" +%F)"
  done

  echo "$expected"
}

list_expected_cron_slots() {
  local start_ms="$1"
  local end_ms="$2"
  local minute="$3"
  local hour="$4"
  local dow_expr="$5"
  local tz="$6"

  local d end_date sched_dt sched_ms
  d="$(to_date "$start_ms" "$tz")"
  end_date="$(to_date "$end_ms" "$tz")"

  while [[ "$d" < "$end_date" || "$d" == "$end_date" ]]; do
    if dow_matches "$dow_expr" "$d" "$tz"; then
      sched_dt="${d} ${hour}:${minute}:00"
      sched_ms="$(to_epoch_ms "$sched_dt" "$tz")"
      if [[ "$sched_ms" =~ ^[0-9]+$ ]] && [[ "$sched_ms" -ge "$start_ms" ]] && [[ "$sched_ms" -le "$end_ms" ]]; then
        echo "$sched_ms"
      fi
    fi
    d="$(TZ="$tz" date -d "${d} +1 day" +%F)"
  done
}

NOW_MS="$(( $(date +%s) * 1000 ))"
SINCE_MS="$(( NOW_MS - DAYS * 24 * 3600 * 1000 ))"
MATCH_GRACE_MS="$(( MATCH_GRACE_MIN * 60 * 1000 ))"

JOBS_JSON="$(openclaw cron list --json | json_payload)"
readarray -t JOBS < <(
  printf '%s\n' "$JOBS_JSON" \
    | jq -c --arg p "$JOB_PREFIX" '.jobs[] | select(.name | startswith($p)) | {
      id: .id,
      name: .name,
      kind: .schedule.kind,
      expr: (.schedule.expr // ""),
      tz: (.schedule.tz // "Asia/Shanghai"),
      everyMs: (.schedule.everyMs // 0),
      anchorMs: (.schedule.anchorMs // 0)
    }'
)

echo "== OpenClaw Cron Delivery Audit =="
echo "now_ms=$NOW_MS"
echo "lookback_days=$DAYS"
echo "since_ms=$SINCE_MS"
echo "match_grace_min=$MATCH_GRACE_MIN"
echo "job_prefix=$JOB_PREFIX"
echo "jobs=${#JOBS[@]}"
echo

if [[ "${#JOBS[@]}" -eq 0 ]]; then
  echo "No jobs matched prefix: $JOB_PREFIX"
  exit 0
fi

anomalies=0

printf '%-34s %-6s %6s %6s %5s %5s %6s %5s %s\n' "job" "kind" "runs" "expect" "dup" "miss" "manual" "fail" "note"
printf '%-34s %-6s %6s %6s %5s %5s %6s %5s %s\n' "----------------------------------" "------" "------" "------" "-----" "-----" "------" "-----" "----"

for row in "${JOBS[@]}"; do
  jid="$(jq -r '.id' <<<"$row")"
  jname="$(jq -r '.name' <<<"$row")"
  jkind="$(jq -r '.kind' <<<"$row")"
  jexpr="$(jq -r '.expr' <<<"$row")"
  jtz="$(jq -r '.tz' <<<"$row")"
  jevery="$(jq -r '.everyMs' <<<"$row")"
  janchor="$(jq -r '.anchorMs' <<<"$row")"

  RUNS_JSON="$(openclaw cron runs --id "$jid" --limit "$LIMIT" 2>/tmp/xiao_cron_audit_err.log | json_payload || true)"
  if [[ -z "$RUNS_JSON" ]]; then
    RUNS_JSON='{"entries":[]}'
  fi

  readarray -t RUN_MS_ALL < <(printf '%s\n' "$RUNS_JSON" | jq -r '.entries[]?.runAtMs // empty' | awk '/^[0-9]+$/' | sort -n)
  readarray -t RUN_MS_WIN < <(printf '%s\n' "$RUNS_JSON" | jq -r --argjson s "$SINCE_MS" '.entries[]? | select((.runAtMs // 0) >= $s) | .runAtMs // empty' | awk '/^[0-9]+$/' | sort -n)
  FAIL_CNT="$(printf '%s\n' "$RUNS_JSON" | jq -r '.entries[]? | select((.status // "") != "ok" or ((.deliveryStatus // "")|startswith("failed"))) | 1' | wc -l | tr -d ' ')"

  runs="${#RUN_MS_WIN[@]}"
  expected=0
  duplicates=0
  missing=0
  manual_runs=0
  note=""

  if [[ "${#RUN_MS_ALL[@]}" -gt 0 ]]; then
    first_ms="${RUN_MS_ALL[0]}"
    if [[ "$first_ms" -gt "$SINCE_MS" ]]; then
      WINDOW_START_MS="$first_ms"
    else
      WINDOW_START_MS="$SINCE_MS"
    fi
  else
    WINDOW_START_MS="$SINCE_MS"
  fi

  declare -A bucket_counts=()

  if [[ "$jkind" == "cron" ]]; then
    read -r cmin chour _dom _mon cdow <<<"$jexpr"
    cmin="${cmin:-00}"
    chour="${chour:-00}"
    cdow="${cdow:-*}"
    readarray -t SLOT_MS < <(list_expected_cron_slots "$WINDOW_START_MS" "$NOW_MS" "$cmin" "$chour" "$cdow" "$jtz")
    expected="${#SLOT_MS[@]}"

    for ms in "${RUN_MS_WIN[@]}"; do
      match_slot=-1
      match_diff=0
      for idx in "${!SLOT_MS[@]}"; do
        slot_ms="${SLOT_MS[$idx]}"
        diff="$(( ms - slot_ms ))"
        if [[ "$diff" -lt 0 ]]; then
          diff="$(( -diff ))"
        fi
        if [[ "$diff" -le "$MATCH_GRACE_MS" ]]; then
          if [[ "$match_slot" -lt 0 || "$diff" -lt "$match_diff" ]]; then
            match_slot="$idx"
            match_diff="$diff"
          fi
        fi
      done

      if [[ "$match_slot" -ge 0 ]]; then
        key="slot:${match_slot}"
        bucket_counts["$key"]="$(( ${bucket_counts[$key]:-0} + 1 ))"
      else
        manual_runs=$((manual_runs + 1))
      fi
    done

    observed="${#bucket_counts[@]}"
    for k in "${!bucket_counts[@]}"; do
      if [[ "${bucket_counts[$k]}" -gt 1 ]]; then
        duplicates=$((duplicates + bucket_counts[$k] - 1))
      fi
    done
    if [[ "$expected" -gt "$observed" ]]; then
      missing=$((expected - observed))
    fi
  elif [[ "$jkind" == "every" ]]; then
    if [[ ! "$jevery" =~ ^[0-9]+$ ]] || [[ "$jevery" -le 0 ]] || [[ ! "$janchor" =~ ^[0-9]+$ ]]; then
      note="invalid every schedule fields"
    else
      if [[ "$NOW_MS" -ge "$janchor" ]]; then
        if [[ "$WINDOW_START_MS" -le "$janchor" ]]; then
          first_slot=0
        else
          delta="$(( WINDOW_START_MS - janchor ))"
          first_slot="$(( (delta + jevery - 1) / jevery ))"
        fi
        last_slot="$(( (NOW_MS - janchor) / jevery ))"
        if [[ "$last_slot" -ge "$first_slot" ]]; then
          expected="$(( last_slot - first_slot + 1 ))"
        fi
      fi

      for ms in "${RUN_MS_WIN[@]}"; do
        if [[ "$ms" -lt "$janchor" ]]; then
          manual_runs=$((manual_runs + 1))
          continue
        fi
        slot="$(( (ms - janchor + jevery / 2) / jevery ))"
        slot_center="$(( janchor + slot * jevery ))"
        diff="$(( ms - slot_center ))"
        if [[ "$diff" -lt 0 ]]; then
          diff="$(( -diff ))"
        fi
        if [[ "$diff" -le "$MATCH_GRACE_MS" ]]; then
          key="slot:${slot}"
          bucket_counts["$key"]="$(( ${bucket_counts[$key]:-0} + 1 ))"
        else
          manual_runs=$((manual_runs + 1))
        fi
      done
      observed="${#bucket_counts[@]}"
      for k in "${!bucket_counts[@]}"; do
        if [[ "${bucket_counts[$k]}" -gt 1 ]]; then
          duplicates=$((duplicates + bucket_counts[$k] - 1))
        fi
      done
      if [[ "$expected" -gt "$observed" ]]; then
        missing=$((expected - observed))
      fi
    fi
  else
    note="unsupported schedule kind"
  fi

  if [[ "${#RUN_MS_ALL[@]}" -eq 0 ]]; then
    expected=0
    duplicates=0
    missing=0
    manual_runs=0
    note="no run history in limit=${LIMIT} (expected unknown)"
  fi

  if [[ "$manual_runs" -gt 0 ]]; then
    if [[ -n "$note" ]]; then
      note="${note}; manual_runs=${manual_runs}"
    else
      note="manual_runs=${manual_runs}"
    fi
  fi

  if [[ "${#RUN_MS_ALL[@]}" -gt 0 && ( "$duplicates" -gt 0 || "$missing" -gt 0 || "$FAIL_CNT" -gt 0 ) ]]; then
    anomalies=$((anomalies + 1))
    if [[ -z "$note" ]]; then
      note="anomaly"
    fi
  fi

  printf '%-34s %-6s %6s %6s %5s %5s %6s %5s %s\n' \
    "$jname" "$jkind" "$runs" "$expected" "$duplicates" "$missing" "$manual_runs" "$FAIL_CNT" "$note"

  unset bucket_counts
done

echo
echo "anomaly_jobs=$anomalies"
if [[ "$STRICT" -eq 1 && "$anomalies" -gt 0 ]]; then
  exit 1
fi
