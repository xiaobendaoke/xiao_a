# OpenClaw 部署与验收（腾讯云 + QQ 通道）

本文档是 README 的部署细化版，专注线上运行环境：`systemd --user` 的 OpenClaw。

## 1. 核心路径

- 配置：`~/.openclaw/openclaw.json`
- 插件目录：`~/.openclaw/extensions/`
- 服务名：`openclaw-gateway.service`
- 网关端口：`127.0.0.1:18789`

## 2. 配置检查

确认 `~/.openclaw/openclaw.json` 至少包含：

- `channels.qqbot.enabled = true`
- `plugins.entries.qqbot.enabled = true`
- `plugins.entries.xiao-core.enabled = true`
- `plugins.entries.xiao-emotion.enabled = true`
- `plugins.entries.xiao-services.enabled = true`

## 3. 同步插件

```bash
mkdir -p ~/.openclaw/extensions
rsync -a /root/xiao_a/openclaw/extensions/xiao-core/ ~/.openclaw/extensions/xiao-core/
rsync -a /root/xiao_a/openclaw/extensions/xiao-emotion/ ~/.openclaw/extensions/xiao-emotion/
rsync -a /root/xiao_a/openclaw/extensions/xiao-services/ ~/.openclaw/extensions/xiao-services/
```

## 4. 重启并验证

```bash
systemctl --user restart openclaw-gateway.service
systemctl --user is-active openclaw-gateway.service
systemctl --user status openclaw-gateway.service --no-pager | sed -n '1,8p'
```

再做一次网关 smoke test：

```bash
curl -sS http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>" \
  -H "x-openclaw-agent-id: main" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","user":"smoke","messages":[{"role":"user","content":"只回复 OPENCLAW_OK"}]}'
```

## 5. QQ 通道验活

在 QQ 私聊发送：

- `/xiao-health`
- `/xiao-whoami`
- `/xiao-echo test`
- `/xiao-memory list`
- `/xiao-links 5`
- `/xiao-reflect 24`
- `/xiao-remind 30 记得喝水`

如果都能回，说明通道和插件链路正常。

## 6. 定时任务迁移与回滚

脚本：`scripts/openclaw_migrate_scheduler.sh`

```bash
# 预览
./scripts/openclaw_migrate_scheduler.sh --auto-target

# 安装
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target

# 安装时关闭 GitHub 周榜任务（可选）
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target --github-weekly 0

# 安装时关闭“信息简报/反思”任务（可选）
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target --info-digest 0 --reflection 0

# 删除（预览）
./scripts/openclaw_migrate_scheduler.sh --remove --auto-target

# 删除（执行）
./scripts/openclaw_migrate_scheduler.sh --remove --apply --auto-target
```

核验：

```bash
openclaw cron status --json
openclaw cron list --json
```

### 6.1 导入旧提醒任务（schedules 表）

脚本：`scripts/openclaw_migrate_schedules_from_db.sh`

```bash
# 预览
./scripts/openclaw_migrate_schedules_from_db.sh --auto-target

# 执行导入
./scripts/openclaw_migrate_schedules_from_db.sh --apply --auto-target
```

如果要导入后回写旧表状态：

```bash
./scripts/openclaw_migrate_schedules_from_db.sh --apply --auto-target --mark-cancelled
```

## 7. 迁移期身份映射

如需衔接历史账号：

```env
XIAO_USER_ALIAS_MAP=qqbot:<当前OpenID>=qqbot:<历史QQ号>
XIAO_LEGACY_DB_PATH=/root/xiao_a/data.db
```

## 8. 说明

本仓库主路径是 OpenClaw-only。若你本地仍保留 `legacy/nonebot` 或 `legacy/openclaw-docker` 目录，仅用于回滚对照，不是推荐部署路径。

## 9. 可观测性与回归验收

工具链日志字段：

- `request_id`
- `user_key`
- `tool_name`
- `latency_ms`
- `error_code`

默认日志文件：

- `~/.openclaw/xiao-core/observability.jsonl`

可选媒体上限（语音/图片下载保护）：

- `XIAO_MEDIA_MAX_MB=20`（默认 20MB）
- `XIAO_VISION_TIMEOUT_MS=35000`
- `XIAO_VISION_DEFAULT_PROMPT=...`（可选，覆盖默认图片理解提示词）
- `XIAO_ASR_TIMEOUT_MS=45000`
- `XIAO_TTS_TIMEOUT_MS=45000`
- `QWEN_TTS_RATE=1.0` / `QWEN_TTS_PITCH=1.0` / `QWEN_TTS_VOLUME=1.0`

快速验收：

```bash
./scripts/openclaw_regression_smoke.sh
./scripts/openclaw_regression_smoke.sh --deep
./scripts/openclaw_full_feature_check.sh
./scripts/openclaw_media_success_rate.sh --hours 24 --min-samples 6 --target 95
./scripts/openclaw_cron_delivery_audit.sh --days 3 --job-prefix xiao-
./scripts/openclaw_source_followup_check.sh --user phase2-source-check --url https://example.com
```

其中 `openclaw_cron_delivery_audit.sh` 的 `manual` 列代表手工触发，不算漏发/重发异常。
