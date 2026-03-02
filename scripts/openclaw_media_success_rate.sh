#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--hours N] [--min-samples N] [--target RATE] [--obs-file PATH]

Options:
  --hours N        Look back window in hours (default: 24)
  --min-samples N  Minimum total samples required (default: 6)
  --target RATE    Success rate threshold percent (default: 95)
  --obs-file PATH  Observability jsonl file (default: \$XIAO_OBS_FILE or ~/.openclaw/xiao-core/observability.jsonl)
USAGE
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 2
  }
}

require_cmd jq
require_cmd date

hours=24
min_samples=6
target=95
obs_file="${XIAO_OBS_FILE:-$HOME/.openclaw/xiao-core/observability.jsonl}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours)
      hours="${2:-}"
      shift 2
      ;;
    --min-samples)
      min_samples="${2:-}"
      shift 2
      ;;
    --target)
      target="${2:-}"
      shift 2
      ;;
    --obs-file)
      obs_file="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ ! "$hours" =~ ^[0-9]+$ ]] || [[ "$hours" -le 0 ]]; then
  echo "--hours must be positive integer" >&2
  exit 2
fi
if [[ ! "$min_samples" =~ ^[0-9]+$ ]] || [[ "$min_samples" -le 0 ]]; then
  echo "--min-samples must be positive integer" >&2
  exit 2
fi
if [[ ! "$target" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "--target must be number" >&2
  exit 2
fi

if [[ ! -f "$obs_file" ]]; then
  echo "observability file not found: $obs_file" >&2
  exit 2
fi

now_epoch="$(date -u +%s)"
cutoff_epoch="$((now_epoch - hours * 3600))"

result_json="$({
  jq -Rs --argjson cutoff "$cutoff_epoch" '
    def ts_epoch:
      ((.ts // "") | tostring | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601?);
    split("\n")
    | map(select(length > 0) | (fromjson? // empty))
    | map(select((ts_epoch) != null and (.tool_name? != null)))
    | map(select((ts_epoch) >= $cutoff))
    | map(select(.tool_name == "xiao_vision_analyze" or .tool_name == "xiao_asr_transcribe" or .tool_name == "xiao_tts_synthesize"))
    | def stats($tool):
        (map(select(.tool_name == $tool))) as $rows
        | {
            tool: $tool,
            total: ($rows | length),
            ok: ($rows | map(select(((.error_code // "") | tostring | length) == 0)) | length),
            fail: ($rows | map(select(((.error_code // "") | tostring | length) > 0)) | length)
          }
        | . + {
            success_rate: (if .total > 0 then ((.ok * 10000 / .total) | round / 100) else 0 end)
          };
      {
        window_hours: ((now - $cutoff) / 3600),
        tools: [
          stats("xiao_vision_analyze"),
          stats("xiao_asr_transcribe"),
          stats("xiao_tts_synthesize")
        ]
      }
      | . + {
          total: (.tools | map(.total) | add),
          ok: (.tools | map(.ok) | add),
          fail: (.tools | map(.fail) | add)
        }
      | . + {
          success_rate: (if .total > 0 then ((.ok * 10000 / .total) | round / 100) else 0 end)
        }
  ' "$obs_file"
} 2>/tmp/xiao_media_rate.err)"

if [[ -z "$result_json" ]]; then
  echo "failed to parse observability file" >&2
  sed -n '1,3p' /tmp/xiao_media_rate.err >&2 || true
  exit 2
fi

echo "== Media Success Rate =="
echo "window_hours: $hours"
echo "obs_file: $obs_file"

echo "$result_json" | jq -r '.tools[] | "- \(.tool): total=\(.total) ok=\(.ok) fail=\(.fail) success_rate=\(.success_rate)%"'

overall_total="$(echo "$result_json" | jq -r '.total')"
overall_rate="$(echo "$result_json" | jq -r '.success_rate')"

echo "overall: total=${overall_total} success_rate=${overall_rate}% target=${target}%"

if [[ "$overall_total" -lt "$min_samples" ]]; then
  echo "result=insufficient_samples (need >= ${min_samples})"
  exit 3
fi

if awk "BEGIN {exit !($overall_rate >= $target)}"; then
  echo "result=pass"
  exit 0
fi

echo "result=fail"
exit 1
