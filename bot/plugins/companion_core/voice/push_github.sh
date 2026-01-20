#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法:
  scripts/push_github.sh [-m "提交信息"] [--dry-run]

行为:
  1) git add -A（自动排除 .gitignore 中忽略的文件）
  2) 若有变更则自动 commit
  3) push 到远端（默认 origin + 当前分支）

示例:
  scripts/push_github.sh
  scripts/push_github.sh -m "update"
  scripts/push_github.sh --dry-run
EOF
}

msg=""
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      msg="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "找不到 git，请先安装 git。" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "当前目录不是 git 仓库。" >&2
  exit 1
fi

cd "$repo_root"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "未找到远端 origin，请先配置 GitHub 远端，例如：" >&2
  echo "  git remote add origin git@github.com:USER/REPO.git" >&2
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" == "HEAD" ]]; then
  echo "当前处于 detached HEAD，无法判断要 push 到哪个分支。" >&2
  exit 1
fi

if [[ $dry_run -eq 1 ]]; then
  echo "[dry-run] git add -A"
else
  git add -A
fi

if git diff --cached --quiet; then
  echo "没有需要提交的变更。"
  exit 0
fi

if [[ -z "$msg" ]]; then
  msg="chore: auto update $(date '+%Y-%m-%d %H:%M:%S')"
fi

if [[ $dry_run -eq 1 ]]; then
  echo "[dry-run] git commit -m \"$msg\""
  echo "[dry-run] git push origin \"$branch\""
  exit 0
fi

git commit -m "$msg"
git push origin "$branch"
