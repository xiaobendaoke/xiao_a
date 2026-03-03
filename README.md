# 小a（xiao_a）

> 一个运行在 **OpenClaw + QQ 通道** 上的拟人化 AI 伴侣项目。  
> 核心方向：陪伴式对话、人设稳定、长期记忆、工具调用、定时主动互动。

## 目录

1. [项目概述](#项目概述)
2. [核心能力](#核心能力)
3. [系统架构](#系统架构)
4. [目录结构](#目录结构)
5. [环境要求](#环境要求)
6. [快速开始（10分钟）](#快速开始10分钟)
7. [详细配置](#详细配置)
8. [验收与回归](#验收与回归)
9. [常用运维命令](#常用运维命令)
10. [常见问题](#常见问题)
11. [文档索引](#文档索引)

---

## 项目概述

小a当前主路径是 **腾讯云服务器上的 OpenClaw（systemd）+ OpenClaw QQ channel**，不是 旧主控栈 主控。

这个仓库的目标是：

- 提供一套可复现的 OpenClaw QQ 伴侣机器人实现
- 保留拟人化体验（人设、语气、记忆、主动互动）
- 用脚本化方式完成迁移、回归和稳定性审计

如果你是第一次接触本项目，建议先走「快速开始」，再看详细配置和回归脚本。

---

## 核心能力

### 1) 对话主流程（xiao-core）

- 统一用户标识（含 `XIAO_USER_ALIAS_MAP` 映射）
- 显式记忆（`记住：...`）
- 轻量 RAG 检索（notes/chats）
- 天气/股票/GitHub 周榜意图识别与工具引导
- 链接总结（`xiao_url_digest`）与来源追问（recent links）
- 命令：`/xiao-health`、`/xiao-whoami`、`/xiao-memory`、`/xiao-links`、`/xiao-reflect`、`/xiao-remind`
- 扩展命令：`/xiao-persona`、`/xiao-love-score`、`/xiao-plan`、`/xiao-habit`、`/xiao-diary`、`/xiao-game`、`/xiao-greet`

### 2) 情绪画像（xiao-emotion）

- 情绪与画像持久化
- 输出标签清洗
- 历史身份映射兼容

### 3) 工具能力（xiao-services）

- 搜索、天气、股票、网页摘要
- 音乐解析、电影推荐、餐厅推荐、快递追踪
- 语音 ASR/TTS、图片理解
- 定时提醒创建
- 工具调用可观测性字段落地

### 4) 定时任务（OpenClaw cron）

- 天气简报、财经日报、主动问候、GitHub 周榜
- 信息简报（info-digest）、每日反思（reflection）
- `scripts/openclaw_cron_delivery_audit.sh` 支持漏发/重发/失败审计

---

## 系统架构

```text
QQ 用户
  -> OpenClaw QQ channel
  -> OpenClaw Agent(main)
  -> OpenClaw plugins
     - xiao-core
     - xiao-emotion
     - xiao-services
  -> OpenClaw state + xiao-core state
```

---

## 目录结构

```text
xiao_a/
├─ README.md
├─ .env.example
├─ docs/
│  ├─ OPENCLAW_SETUP.md
│  ├─ OPENCLAW_REGRESSION_CASES.md
│  ├─ OPENCLAW_QQ_MIGRATION_PLAN.md
│  ├─ ARCHITECTURE.md
│  └─ GITHUB_PUBLISH_GUIDE.md
├─ openclaw/
│  ├─ openclaw.json
│  └─ extensions/
│     ├─ xiao-core/
│     ├─ xiao-emotion/
│     └─ xiao-services/
└─ scripts/
   ├─ openclaw_migrate_scheduler.sh
   ├─ openclaw_migrate_schedules_from_db.sh
   ├─ openclaw_regression_smoke.sh
   ├─ openclaw_full_feature_check.sh
   ├─ openclaw_media_success_rate.sh
   ├─ openclaw_cron_delivery_audit.sh
   └─ openclaw_source_followup_check.sh
```

---

## 环境要求

- Linux（推荐 Ubuntu / OpenCloudOS）
- `systemd --user`
- Node.js 20+（推荐 22）
- OpenClaw CLI/Gateway 可用（`openclaw` 命令可执行）
- QQ 机器人通道凭据（`appId` / `clientSecret`）

---

## 快速开始（10分钟）

### 1) 拉取项目

```bash
git clone https://github.com/xiaobendaoke/xiao_a.git
cd xiao_a
```

### 2) 准备 OpenClaw 运行目录

```bash
mkdir -p ~/.openclaw/extensions
cp .env.example ~/.openclaw/.env
```

编辑 `~/.openclaw/.env`，至少填这些：

```env
OPENCLAW_GATEWAY_TOKEN=替换成你自己的token
DEEPSEEK_API_KEY=你的key
QQBOT_APP_ID=你的qqbot_appid
QQBOT_CLIENT_SECRET=你的qqbot_secret
```

### 3) 配置 OpenClaw（QQ 通道 + 插件）

建议将你的 `~/.openclaw/openclaw.json` 对齐为以下关键配置：

```json
{
  "gateway": {
    "mode": "local",
    "bind": "loopback",
    "port": 18789,
    "auth": { "mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}" }
  },
  "channels": {
    "qqbot": {
      "enabled": true,
      "appId": "${QQBOT_APP_ID}",
      "clientSecret": "${QQBOT_CLIENT_SECRET}"
    }
  },
  "plugins": {
    "allow": ["qqbot", "xiao-core", "xiao-emotion", "xiao-services"],
    "entries": {
      "qqbot": { "enabled": true },
      "xiao-core": { "enabled": true },
      "xiao-emotion": { "enabled": true },
      "xiao-services": { "enabled": true }
    }
  }
}
```

### 4) 同步插件代码到运行时

```bash
rsync -a openclaw/extensions/xiao-core/ ~/.openclaw/extensions/xiao-core/
rsync -a openclaw/extensions/xiao-emotion/ ~/.openclaw/extensions/xiao-emotion/
rsync -a openclaw/extensions/xiao-services/ ~/.openclaw/extensions/xiao-services/
```

### 5) 重启并验活

```bash
systemctl --user restart openclaw-gateway.service
systemctl --user is-active openclaw-gateway.service
openclaw status --json
```

如果返回 `active` 且 `gateway.reachable=true`，说明网关链路正常。

---

## 详细配置

### 1) cron 任务迁移（推荐）

```bash
# dry-run
./scripts/openclaw_migrate_scheduler.sh --auto-target

# apply
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target
```

默认会安装 6 类任务（weather/finance/proactive/github-weekly/info-digest/reflection）。

可选关闭信息简报/反思：

```bash
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target --info-digest 0 --reflection 0
```

### 2) 导入旧 reminders（schedules 表）

```bash
# dry-run
./scripts/openclaw_migrate_schedules_from_db.sh --auto-target

# apply
./scripts/openclaw_migrate_schedules_from_db.sh --apply --auto-target
```

### 3) 身份映射（迁移期可选）

```env
XIAO_USER_ALIAS_MAP=qqbot:<当前OpenID>=qqbot:<历史ID>
```

---

## 验收与回归

### 1) QQ 内命令验活

- `/xiao-health`
- `/xiao-whoami`
- `/xiao-memory list`
- `/xiao-links 5`
- `/xiao-reflect 24`
- `/xiao-remind 30 记得喝水`
- `/xiao-persona list`
- `/xiao-love-score`
- `/xiao-plan list`
- `/xiao-habit list`
- `/xiao-diary today`
- `/xiao-game start riddle`

### 2) 脚本化验收

```bash
# 基础 + 观测字段
./scripts/openclaw_regression_smoke.sh --deep

# 全量功能回归
./scripts/openclaw_full_feature_check.sh

# 多媒体成功率
./scripts/openclaw_media_success_rate.sh --hours 24 --min-samples 6 --target 95

# 定时任务审计（漏发/重发/失败）
./scripts/openclaw_cron_delivery_audit.sh --days 3 --job-prefix xiao-

# 链接来源追问两轮验证
./scripts/openclaw_source_followup_check.sh --user phase2-source-check --url https://example.com
```

审计脚本字段说明：

- `dup/miss/fail`：会计入异常
- `manual`：手工 `cron run` 或偏离计划窗口触发，不计入异常

---

## 常用运维命令

```bash
# 服务状态
systemctl --user status openclaw-gateway.service --no-pager
journalctl --user -u openclaw-gateway.service -n 100 --no-pager

# 插件状态
openclaw --no-color plugins list

# cron 状态
openclaw cron status --json
openclaw cron list --json
openclaw cron runs --id <job_id> --limit 5
```

---

## 常见问题

### 1) 现在还需要 LegacyBot / NapCat 吗？

不需要。当前主链路是 OpenClaw QQ channel。

### 2) 为什么会看到 `loaded without install/load-path provenance`？

这是本地扩展目录加载提示，不影响运行。可通过规范化插件安装记录消除。

### 3) 为什么别人容易误判为 Docker 项目？

因为很多旧项目会把历史 Docker/LegacyBot 文件和主路径混放。这个仓库主文档和目录已经按 OpenClaw-only 组织。

---

## 文档索引

- 部署细化：`docs/OPENCLAW_SETUP.md`
- 回归清单：`docs/OPENCLAW_REGRESSION_CASES.md`
- 迁移计划：`docs/OPENCLAW_QQ_MIGRATION_PLAN.md`
- 架构说明：`docs/ARCHITECTURE.md`
- 发布指引：`docs/GITHUB_PUBLISH_GUIDE.md`
