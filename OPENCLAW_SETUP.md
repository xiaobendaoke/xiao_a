# OpenClaw 侧车接入（方案A）

## 1) 启动 OpenClaw 网关

1. 在项目根目录创建/编辑 `.env`，增加：
```env
OPENCLAW_GATEWAY_TOKEN=change_me_openclaw_token
# 默认 openclaw/openclaw.json 走 DeepSeek 官方 API
OPENCLAW_DEEPSEEK_API_KEY=你的DeepSeekKey
# 可选 provider key
OPENCLAW_ANTHROPIC_API_KEY=
OPENCLAW_OPENAI_API_KEY=
OPENCLAW_BRAVE_API_KEY=
```

2. 启动网关：
```bash
docker compose -f docker-compose.openclaw.yml up -d --build
```

网关默认端口：`18789`
默认 Agent 配置文件：`openclaw/openclaw.json`（可按你的 provider/model 修改）

## 2) 验证网关是否可用

PowerShell:
```powershell
./scripts/openclaw_smoke_test.ps1 -BaseUrl http://127.0.0.1:18789 -Token $env:OPENCLAW_GATEWAY_TOKEN
```

## 3) 让小a连接 OpenClaw

编辑 `bot/.env`：
```env
OPENCLAW_TOOL_ENABLED=1
OPENCLAW_BASE_URL=http://openclaw:18789
OPENCLAW_CHAT_PATH=/v1/chat/completions
OPENCLAW_MODEL=openclaw
OPENCLAW_AGENT_ID=main
OPENCLAW_API_KEY=change_me_openclaw_token
OPENCLAW_TIMEOUT_MS=25000
OPENCLAW_MAX_TOKENS=700
```

重启 nonebot：
```bash
docker compose restart nonebot
```

## 4) 关于 OPENCLAW_API_KEY

`OPENCLAW_API_KEY` 在本项目里指的是 **OpenClaw 网关 token**，通常与 `OPENCLAW_GATEWAY_TOKEN` 相同。

它不是“OpenClaw 官网发放的固定 key”，而是你在自托管网关里自己设置的鉴权 token。
