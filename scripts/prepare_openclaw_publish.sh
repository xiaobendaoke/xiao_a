#!/usr/bin/env bash
set -euo pipefail

STAGE=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/prepare_openclaw_publish.sh [--stage]

Default:
  Dry-run. Print OpenClaw-only whitelist and show git status hints.

Options:
  --stage   Actually run git add on whitelist paths.
  -h        Show help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)
      STAGE=1
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

WHITELIST=(
  "README.md"
  ".env.example"
  ".gitignore"
  "docs"
  "openclaw"
  "scripts"
  "test_rag.py"
  "xiao_a.wav"
)

echo "== OpenClaw-only publish whitelist =="
for p in "${WHITELIST[@]}"; do
  echo "- $p"
done
echo

if [[ "$STAGE" -eq 1 ]]; then
  git add "${WHITELIST[@]}"
  echo "[ok] staged whitelist paths."
else
  echo "[dry-run] no staging performed."
fi

echo
echo "== git status (short) =="
git status --short
