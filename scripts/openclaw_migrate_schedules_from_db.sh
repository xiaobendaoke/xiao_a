#!/usr/bin/env bash
set -euo pipefail

APPLY=0
AUTO_TARGET=0
TARGET=""
DB_PATH="${DB_PATH:-/root/xiao_a/data.db}"
AGENT_ID="${AGENT_ID:-main}"
JOB_PREFIX="${JOB_PREFIX:-xiao-imported-reminder}"
MARK_CANCELLED=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/openclaw_migrate_schedules_from_db.sh [options]

Options:
  --apply                     Apply changes (default dry-run)
  --auto-target               Auto-detect latest QQ target from openclaw status
  --target <qqbot:c2c:ID>     Override target for all imported reminders
  --db <path>                 SQLite DB path (default: /root/xiao_a/data.db)
  --agent <id>                Agent id (default: main)
  --job-prefix <prefix>       Job name prefix (default: xiao-imported-reminder)
  --mark-cancelled            Mark imported rows as cancelled in DB (apply only)
  -h, --help                  Show this help

Examples:
  scripts/openclaw_migrate_schedules_from_db.sh
  scripts/openclaw_migrate_schedules_from_db.sh --apply
  scripts/openclaw_migrate_schedules_from_db.sh --apply --auto-target --mark-cancelled

Env fallback:
  XIAO_DEFAULT_TARGET=qqbot:c2c:<openid> or qqbot:group:<groupid>
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
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
    --db)
      DB_PATH="${2:-}"
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
    --mark-cancelled)
      MARK_CANCELLED=1
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
require_cmd sqlite3
require_cmd jq

json_payload() {
  sed -n '/^[[:space:]]*{/,$p'
}

is_valid_target() {
  local t="$1"
  [[ "$t" =~ ^qqbot:(c2c|group):[A-Za-z0-9._:-]{6,128}$ ]]
}

resolve_auto_target() {
  local raw="$1"
  local resolved=""
  resolved="$(
    printf '%s\n' "$raw" \
      | json_payload \
      | jq -r --arg a "$AGENT_ID" '
          .sessions.recent[]?.key // empty
          | if startswith("agent:" + $a + ":qqbot:direct:") then
              "qqbot:c2c:" + (split(":")[-1])
            elif startswith("agent:" + $a + ":qqbot:c2c:") then
              "qqbot:c2c:" + (split(":")[-1])
            elif startswith("agent:" + $a + ":qqbot:group:") then
              "qqbot:group:" + (split(":")[-1])
            else empty end
        ' \
      | head -n1
  )"
  if [[ -n "$resolved" && "$resolved" != "null" ]]; then
    printf '%s' "$resolved"
    return 0
  fi

  local fallback="${XIAO_DEFAULT_TARGET:-}"
  if [[ -n "$fallback" ]] && is_valid_target "$fallback"; then
    echo "[info] auto-target not found, fallback to XIAO_DEFAULT_TARGET=${fallback}" >&2
    printf '%s' "$fallback"
    return 0
  fi
  return 1
}

if [[ ! -f "$DB_PATH" ]]; then
  echo "DB file not found: $DB_PATH" >&2
  exit 1
fi

if [[ "$AUTO_TARGET" -eq 1 && -z "$TARGET" ]]; then
  STATUS_RAW="$(openclaw status --json 2>/dev/null || true)"
  if TARGET="$(resolve_auto_target "$STATUS_RAW")"; then
    :
  else
    echo "[warn] auto-target not found; will fallback to row user_id mapping (qqbot:c2c:<user_id>)." >&2
  fi
fi

if [[ -n "$TARGET" ]] && ! is_valid_target "$TARGET"; then
  echo "Invalid target format: $TARGET" >&2
  exit 1
fi

NOW_TS="$(date +%s)"

readarray -t ROWS < <(
  sqlite3 -separator $'\t' "$DB_PATH" \
    "SELECT id, user_id, trigger_time, content, status FROM schedules WHERE status='pending' AND trigger_time >= ${NOW_TS} ORDER BY trigger_time ASC;"
)

echo "== Schedule migration (db -> openclaw cron) =="
echo "apply=$APPLY"
echo "db=$DB_PATH"
echo "agent=$AGENT_ID"
echo "job_prefix=$JOB_PREFIX"
echo "target_override=${TARGET:-<none>}"
echo "mark_cancelled=$MARK_CANCELLED"
echo "pending_rows=${#ROWS[@]}"
echo

if [[ ${#ROWS[@]} -eq 0 ]]; then
  echo "No pending schedules to migrate."
  exit 0
fi

migrated=0
skipped=0
failed=0

for row in "${ROWS[@]}"; do
  IFS=$'\t' read -r sid user_id trigger_ts content status <<<"$row"

  sid="${sid:-}"
  user_id="${user_id:-}"
  trigger_ts="${trigger_ts:-}"
  content="${content:-}"

  if [[ -z "$sid" || -z "$user_id" || -z "$trigger_ts" ]]; then
    echo "[skip] bad row: $row"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ "$trigger_ts" =~ ^[0-9]+$ ]] && [[ "$trigger_ts" -lt "$NOW_TS" ]]; then
    echo "[skip] expired schedule id=$sid"
    skipped=$((skipped + 1))
    continue
  fi

  to="$TARGET"
  if [[ -z "$to" ]]; then
    to="qqbot:c2c:${user_id}"
  fi

  if [[ ! "$to" =~ ^qqbot:(c2c|group):[A-Za-z0-9._:-]{6,128}$ ]]; then
    echo "[skip] invalid destination for id=$sid: $to"
    skipped=$((skipped + 1))
    continue
  fi

  iso_time="$(date -d "@${trigger_ts}" +"%Y-%m-%dT%H:%M:%S%:z")"
  safe_msg="你是小a。提醒内容：${content}"
  name="${JOB_PREFIX}-${sid}"

  existing_id="$(openclaw cron list --json | json_payload | jq -r --arg n "$name" '.jobs[] | select(.name == $n) | .id' | head -n1)"

  if [[ -n "$existing_id" ]]; then
    if [[ "$APPLY" -eq 1 ]]; then
      echo "[apply] remove existing job: $name ($existing_id)"
      openclaw cron rm "$existing_id" >/dev/null || true
    else
      echo "[dry-run] would remove existing job: $name ($existing_id)"
    fi
  fi

  if [[ "$APPLY" -eq 1 ]]; then
    if out="$(openclaw cron add \
      --name "$name" \
      --agent "$AGENT_ID" \
      --at "$iso_time" \
      --message "$safe_msg" \
      --announce \
      --channel qqbot \
      --to "$to" \
      --session isolated \
      --delete-after-run \
      --description "migrated from schedules.id=${sid}" \
      --json 2>/tmp/xiao_sched_migrate_err.log)"; then
      echo "$out" | json_payload | jq '{id,name,schedule,delivery}'
      migrated=$((migrated + 1))

      if [[ "$MARK_CANCELLED" -eq 1 ]]; then
        sqlite3 "$DB_PATH" "UPDATE schedules SET status='cancelled' WHERE id=${sid};"
      fi
    else
      echo "[fail] schedule id=$sid to=$to"
      sed -n '1,40p' /tmp/xiao_sched_migrate_err.log || true
      failed=$((failed + 1))
    fi
  else
    echo "[dry-run] openclaw cron add --name $name --at $iso_time --to $to --message '$safe_msg'"
    migrated=$((migrated + 1))
  fi

done

echo
echo "== Summary =="
echo "migrated=$migrated"
echo "skipped=$skipped"
echo "failed=$failed"

if [[ "$APPLY" -eq 1 ]]; then
  echo
  echo "== Installed migrated jobs =="
  openclaw cron list --json | json_payload | jq --arg p "${JOB_PREFIX}-" '{jobs: [.jobs[] | select(.name | startswith($p)) | {id,name,schedule,delivery}], total: (.jobs | map(select(.name | startswith($p))) | length)}'
fi
