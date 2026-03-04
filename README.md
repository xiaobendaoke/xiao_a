# 小 A (XiaoA)

[![Node.js](https://img.shields.io/badge/Node.js-20+-green.svg)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-blue.svg)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> 🤖 一个运行在 OpenClaw + QQ 通道上的拟人化 AI 伴侣

**核心能力**：陪伴式对话、稳定人设、长期记忆、工具调用、定时主动互动

---

## 📋 目录

- [项目简介](#项目简介)
- [功能特性](#功能特性)
- [系统要求](#系统要求)
- [快速开始](#快速开始)
- [配置指南](#配置指南)
- [使用指南](#使用指南)
- [项目结构](#项目结构)
- [命令参考](#命令参考)
- [故障排除](#故障排除)
- [文档索引](#文档索引)

---

## 项目简介

小 A 是一个基于 OpenClaw 框架开发的 QQ 机器人，专注于提供拟人化的 AI 陪伴体验。

### 什么是 OpenClaw？

OpenClaw 是一个插件化的 AI 代理框架，支持多种通信通道（QQ、微信等）。本项目使用 OpenClaw 的 QQ 通道实现与用户的对话交互。

### 核心插件

| 插件 | 功能描述 |
|------|----------|
| **xiao-core** | 对话主流程、命令处理、记忆管理 |
| **xiao-emotion** | 情绪画像系统、情感分析 |
| **xiao-services** | 工具服务（天气、股票、搜索等） |

---

## 功能特性

### 💬 对话能力
- **拟人化回复**：自然语言处理，模拟真人打字节奏
- **人设稳定**：基于系统提示词维护一致的角色性格
- **情绪感知**：根据对话内容调整回复语气和风格

### 🧠 记忆系统
- **短期记忆**：保持最近 20 轮对话上下文
- **长期记忆 (RAG)**：基于向量检索的历史对话回忆
- **显式记忆**：支持 `记住：xxx` 指令保存重要信息

### 🛠️ 工具集成
- 📡 天气查询
- 📈 股票分析
- 🔗 网页摘要
- 🎵 音乐解析
- 🎬 电影推荐
- 🍽️ 餐厅推荐

### ⏰ 主动互动
- 定时天气简报
- 财经日报推送
- 主动问候
- GitHub 周榜
- 每日反思

---

## 系统要求

### 必需环境

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| **操作系统** | Linux (Ubuntu/OpenCloudOS) | 推荐 Ubuntu 22.04+ |
| **Node.js** | 20.x 或更高 | 推荐 22.x LTS |
| **OpenClaw CLI** | 最新版 | AI 代理框架 |
| **systemd** | --user 模式 | 服务管理 |

### 可选依赖

- **SQLite**: 数据持久化（自动创建）
- **ChromaDB**: 向量存储（用于 RAG 记忆）

### 第三方服务

你需要准备以下 API 密钥：

| 服务 | 用途 | 获取地址 |
|------|------|----------|
| **DeepSeek API** | 对话 LLM | [platform.deepseek.com](https://platform.deepseek.com) |
| **SiliconFlow** | 备选 LLM | [cloud.siliconflow.cn](https://cloud.siliconflow.cn) |
| **QQ 机器人** | 消息通道 | [bot.q.qq.com](https://bot.q.qq.com) |

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/xiaobendaoke/xiao_a.git
cd xiao_a
```

### 2. 安装 Node.js 20+

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

# 验证安装
node --version  # 应显示 v20.x.x 或更高
npm --version
```

### 3. 安装 OpenClaw CLI

```bash
# 通过 npm 全局安装
npm install -g @openclaw/cli

# 验证安装
openclaw --version
```

### 4. 配置环境变量

```bash
# 创建 OpenClaw 配置目录
mkdir -p ~/.openclaw/extensions

# 复制环境变量模板
cp .env.example ~/.openclaw/.env

# 编辑配置文件
nano ~/.openclaw/.env
```

**必须配置的环境变量：**

```env
# OpenClaw 网关认证令牌（自定义一个安全的字符串）
OPENCLAW_GATEWAY_TOKEN=your_secure_random_token_here

# LLM API 密钥（至少配置一个）
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
# 或
SILICONFLOW_API_KEY=sk-your-siliconflow-key

# QQ 机器人凭证（从 QQ 开放平台获取）
QQBOT_APP_ID=your_qq_app_id
QQBOT_CLIENT_SECRET=your_qq_secret
```

### 5. 配置 OpenClaw

将项目配置复制到 OpenClaw 目录：

```bash
# 复制主配置
cp openclaw/openclaw.json ~/.openclaw/

# 同步插件代码
rsync -av openclaw/extensions/xiao-core/ ~/.openclaw/extensions/xiao-core/
rsync -av openclaw/extensions/xiao-emotion/ ~/.openclaw/extensions/xiao-emotion/
rsync -av openclaw/extensions/xiao-services/ ~/.openclaw/extensions/xiao-services/
```

### 6. 创建 systemd 服务

创建用户级 systemd 服务文件：

```bash
mkdir -p ~/.config/systemd/user
```

创建 `~/.config/systemd/user/openclaw-gateway.service`：

```ini
[Unit]
Description=OpenClaw Gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/.openclaw
EnvironmentFile=%h/.openclaw/.env
ExecStart=%h/.local/bin/openclaw gateway
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

### 7. 启动服务

```bash
# 重新加载 systemd 配置
systemctl --user daemon-reload

# 启用并启动服务
systemctl --user enable openclaw-gateway.service
systemctl --user start openclaw-gateway.service

# 检查状态
systemctl --user status openclaw-gateway.service

# 查看日志
journalctl --user -u openclaw-gateway.service -f
```

### 8. 验证安装

```bash
# 检查网关状态
openclaw status --json

# 检查插件加载
openclaw plugins list
```

如果看到 `xiao-core`、`xiao-emotion`、`xiao-services` 状态为 `enabled`，说明安装成功！

---

## 配置指南

### QQ 容器部署（本仓库）

本仓库仅保留 QQ 通道部署：

```bash
cd /root/xiao_a
cp env.channel.qq.example env.channel.qq
nano env.channel.qq
docker compose -f docker-compose.qq.yml up -d --build
```

默认端口：

- `openclaw-qq`: `127.0.0.1:18790`

飞书通道已迁移为独立项目：

- 目录：`/root/xiao_feishu`
- 容器名：`feishu`

Nginx 路由隔离（系统级）：

- 配置文件：`/etc/nginx/default.d/channel_isolation.conf`
- QQ webhook 前缀：`/webhook/qq/` -> `127.0.0.1:18790`
- 飞书 webhook 前缀：`/webhook/feishu/` -> `127.0.0.1:28789`
- 自检脚本：`/root/xiao_a/scripts/check_channel_routing.sh`

### 环境变量详解

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `OPENCLAW_GATEWAY_TOKEN` | ✅ | 网关认证令牌 |
| `DEEPSEEK_API_KEY` | ✅* | DeepSeek API 密钥 |
| `SILICONFLOW_API_KEY` | ✅* | SiliconFlow API 密钥 |
| `QQBOT_APP_ID` | ✅ | QQ 机器人 App ID |
| `QQBOT_CLIENT_SECRET` | ✅ | QQ 机器人密钥 |
| `XIAO_USER_ALIAS_MAP` | ❌ | 用户 ID 映射（迁移用） |
| `XIAO_MEDIA_MAX_MB` | ❌ | 媒体文件大小限制（默认 20MB） |

*至少配置一个 LLM API 密钥

### 配置定时任务

```bash
# 查看可用脚本
ls -la scripts/

# 迁移定时任务（试运行）
./scripts/openclaw_migrate_scheduler.sh --auto-target

# 正式应用
./scripts/openclaw_migrate_scheduler.sh --apply --auto-target
```

默认安装的定时任务：
- `weather`: 每日天气简报
- `finance`: 财经日报
- `github-weekly`: GitHub 热门仓库周榜
- `proactive`: 主动问候
- `info-digest`: 信息简报
- `reflection`: 每日反思

---

## 使用指南

### 基础对话

直接 @机器人或发送私聊消息即可开始对话。

### 记忆功能

```
用户: 记住：我明天要开会
小A: 好的，我记住了你明天要开会~

用户: 我明天有什么安排？
小A: 你昨天告诉我明天要开会，别忘了准备材料哦~
```

### URL 摘要

发送链接后，机器人会自动询问是否需要总结：

```
用户: https://example.com/article
小A: 检测到一个链接，需要我帮你总结一下吗？

用户: 是的
小A: [文章摘要内容...]
```

---

## 项目结构

```
xiao_a/
├── README.md                 # 项目说明（本文档）
├── .env.example              # 环境变量模板
├── openclaw/
│   ├── openclaw.json         # OpenClaw 主配置
│   └── extensions/           # 插件目录
│       ├── xiao-core/        # 核心对话插件
│       ├── xiao-emotion/     # 情绪画像插件
│       └── xiao-services/    # 工具服务插件
├── scripts/                  # 运维脚本
│   ├── openclaw_migrate_scheduler.sh
│   ├── openclaw_regression_smoke.sh
│   └── openclaw_cron_delivery_audit.sh
└── docs/                     # 详细文档
    ├── OPENCLAW_SETUP.md
    ├── ARCHITECTURE.md
    └── OPENCLAW_REGRESSION_CASES.md
```

---

## 命令参考

### 用户命令（在 QQ 中发送）

| 命令 | 功能 |
|------|------|
| `/xiao-health` | 检查机器人健康状态 |
| `/xiao-whoami` | 显示当前用户信息 |
| `/xiao-memory list` | 列出保存的记忆 |
| `/xiao-links 5` | 显示最近 5 个链接 |
| `/xiao-reflect 24` | 反思最近 24 小时对话 |
| `/xiao-remind 30 内容` | 设置 30 分钟后提醒 |
| `/xiao-persona list` | 列出可用人设 |
| `/xiao-love-score` | 查看亲密度评分 |
| `/xiao-plan list` | 列出计划 |
| `/xiao-habit list` | 列出习惯追踪 |
| `/xiao-diary today` | 查看今日日记 |
| `/xiao-game start riddle` | 开始猜谜游戏 |

### 运维命令

```bash
# 服务管理
systemctl --user restart openclaw-gateway.service
systemctl --user stop openclaw-gateway.service
systemctl --user status openclaw-gateway.service

# 查看日志
journalctl --user -u openclaw-gateway.service -n 100 --no-pager

# 插件管理
openclaw plugins list
openclaw plugins enable xiao-core

# 定时任务
openclaw cron list
openclaw cron status
```

---

## 故障排除

### 服务无法启动

**症状**: `systemctl --user start` 失败

**排查步骤**:
1. 检查 Node.js 版本: `node --version`
2. 检查 OpenClaw 安装: `which openclaw`
3. 查看日志: `journalctl --user -u openclaw-gateway.service`
4. 检查环境变量文件是否存在: `ls -la ~/.openclaw/.env`

### 插件未加载

**症状**: `openclaw plugins list` 看不到 xiao-* 插件

**解决方案**:
```bash
# 重新同步插件代码
rsync -av openclaw/extensions/xiao-core/ ~/.openclaw/extensions/xiao-core/
rsync -av openclaw/extensions/xiao-emotion/ ~/.openclaw/extensions/xiao-emotion/
rsync -av openclaw/extensions/xiao-services/ ~/.openclaw/extensions/xiao-services/

# 重启服务
systemctl --user restart openclaw-gateway.service
```

### QQ 机器人无响应

**症状**: 消息发送后无回复

**排查步骤**:
1. 检查 QQ 机器人状态: `openclaw status --json`
2. 验证 QQ 凭证是否正确
3. 检查网关是否可达: `openclaw gateway status`
4. 查看日志中的错误信息

### 常见问题

**Q: 为什么看到 `loaded without install/load-path provenance` 警告？**

A: 这是本地扩展目录加载的正常提示，不影响运行。可以通过规范化插件安装消除。

**Q: 需要 Docker 吗？**

A: 不需要。当前主链路是纯 OpenClaw + systemd 用户服务。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/OPENCLAW_SETUP.md](docs/OPENCLAW_SETUP.md) | 详细部署和配置说明 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构和技术实现详解 |
| [docs/OPENCLAW_REGRESSION_CASES.md](docs/OPENCLAW_REGRESSION_CASES.md) | 回归测试清单 |
| [docs/OPENCLAW_QQ_MIGRATION_PLAN.md](docs/OPENCLAW_QQ_MIGRATION_PLAN.md) | 从旧版本迁移指南 |
| [docs/GITHUB_PUBLISH_GUIDE.md](docs/GITHUB_PUBLISH_GUIDE.md) | 发布流程指南 |

---

## 许可证

[MIT](LICENSE) © 小 A 项目团队

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给个 Star！**

</div>
