# GitHub 发布指引（OpenClaw-only）

目标：只发布当前 OpenClaw 主路径，不包含 旧主控栈 历史和运行时数据。

## 1. 发布前检查

```bash
cd /root/xiao_a

# 1) 确认关键文件存在
test -f README.md
test -f .env.example
test -f openclaw/extensions/xiao-core/index.ts
test -f openclaw/extensions/xiao-emotion/index.ts
test -f openclaw/extensions/xiao-services/index.ts

# 2) 快速回归
./scripts/openclaw_regression_smoke.sh --deep
./scripts/openclaw_full_feature_check.sh
```

## 2. 建议发布内容（白名单）

- `README.md`
- `.env.example`
- `.gitignore`
- `docs/`
- `openclaw/`
- `scripts/`
- `test_rag.py`
- `xiao_a.wav`（如果你希望保留 ASR 验收样本）

## 3. 明确不发布

- `legacy/`
- `bot/`
- `napcat/`
- `data.db`、`*.db*`
- 任何 `.env`、token、passkey、运行日志

## 4. 推送策略（推荐）

推荐优先使用“新目录重建 Git”（方案C）。  
原因：如果当前仓库历史里曾提交过 `bot/napcat`，普通新分支可能仍保留这些历史内容。

### 方案A：推到新仓库（仅适用于当前工作树已经是干净白名单）

```bash
cd /root/xiao_a
git checkout -b openclaw-clean
git add README.md .env.example .gitignore docs openclaw scripts test_rag.py xiao_a.wav
git commit -m "chore: publish OpenClaw-only project layout"

# 把 <new_repo_url> 换成你的新仓库地址
git remote add clean <new_repo_url>
git push clean openclaw-clean:main
```

### 方案B：推到当前仓库新分支（仅适用于当前工作树已经是干净白名单）

```bash
cd /root/xiao_a
git checkout -b openclaw-clean
git add README.md .env.example .gitignore docs openclaw scripts test_rag.py xiao_a.wav
git commit -m "chore: publish OpenClaw-only project layout"
git push origin openclaw-clean
```

### 方案C：新目录重建 Git（清空历史，最适合“完全去 legacy”）

```bash
mkdir -p /root/xiao_a_publish
cd /root/xiao_a
rsync -a README.md .env.example .gitignore docs openclaw scripts test_rag.py xiao_a.wav /root/xiao_a_publish/
cd /root/xiao_a_publish
git init
git add .
git commit -m "chore: initial OpenClaw-only publish"
# git remote add origin <new_repo_url>
# git push -u origin main
```

说明：该方案不会动你当前工作目录，风险最低，且发布仓库历史从零开始。

## 5. PR 描述建议

- 本次发布为 OpenClaw-only 主路径
- 已移除 旧主控栈 历史运行内容
- 文档入口统一在 `docs/`
- 已包含一键验收与 cron 审计脚本
