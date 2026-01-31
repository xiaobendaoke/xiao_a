# 小a（xiao_a）

> 一键部署的 QQ 私聊 AI 伴侣栈：NapCat（OneBot v11）+ NoneBot2 + Docker Compose  
> 目标：把“能长期陪你聊天、会记得你、能查东西、能看图、能听你说话并用语音回你”的小a，跑在你自己的机器上。

## 这是什么

`xiao_a` 是一个开箱即用的 QQ 机器人项目，核心是 **NoneBot2 插件化机器人** + **NapCat（OneBot v11）协议桥接**，用 `docker compose` 一键启动。

默认只在 **私聊** 生效（避免群聊刷屏/误触发）。

内置能力（持续扩展中）：

- 陪伴式对话：人设、记忆（SQLite）、情绪（mood）、用户画像（profile）
- 联网搜新闻/热点：Google CSE 优先，RSS 兜底；支持追问“来源/链接”
- 发链接自动“确认 → 总结”：抓取正文、总结、缓存（减少重复消耗）
- 图片理解：Qwen-VL（DashScope OpenAI 兼容接口）
- QQ 语音对话：语音 → ASR → LLM → TTS → 语音（DashScope）
- 天气 / 股票：更像“日常工具型技能”，同时保持陪伴口吻
- **Skills 动态能力系统**：金融分析等专业模块，LLM 自动路由 + 实时数据
- 主动互动/定时推送：RSS 推送、GitHub 周榜推送、晨间天气提醒等
- 外部 HTTP API：`POST /api/chat`（适配 STM32/局域网设备）
- 财经日报：A 股收盘复盘（私聊订阅制，独立数据库）

## 架构一图流

```
QQ <-> NapCat (OneBot v11)
          |
          | WebSocket Client: ws://nonebot:8080/onebot/v11/ws  (token 必须一致)
          v
      NoneBot2 (FastAPI Driver)
        |-- plugins/companion_core  (对话/记忆/情绪/搜索/看图/语音/推送/skills)
        |-- plugins/finance_daily   (A股收盘日报/订阅制)
        `-- SQLite: data.db / finance.db
```

## 目录结构

- `docker-compose.yml`：一键启动 NapCat + NoneBot
- `napcat/`：NapCat 持久化配置与 QQ 数据
  - `napcat/config/`：OneBot / WebUI 配置（包含 token，建议自行更换）
  - `napcat/qq/`：QQ 登录数据（容器重建不丢）
- `bot/`：NoneBot2 机器人本体
  - `bot/bot.py`：启动入口（注册适配器、加载插件）
  - `bot/.env`：运行配置（从 `.env.example` 复制）
  - `bot/plugins/companion_core/`：小a核心能力
    - `skills/`：动态能力加载系统
  - `bot/plugins/finance_daily/`：财经日报插件

## 快速开始（Docker Compose）

### 0) 前置条件

- 已安装 Docker 与 Docker Compose（`docker compose version` 能输出版本）
- 推荐 Linux/Ubuntu；Windows/macOS 也可（注意代理/路径差异）

### 1) 克隆并准备环境变量

```bash
git clone https://github.com/xiaobendaoke/xiao_a.git
cd xiao_a
cp bot/.env.example bot/.env
```

Windows PowerShell：

```powershell
Copy-Item bot/.env.example bot/.env
```

然后编辑 `bot/.env`，至少配置一个 LLM Key（`SILICONFLOW_*` / `DEEPSEEK_*` / `OPENAI_API_KEY` 三选一）。

安全提示：

- 不要把真实 Key 提交到 Git；公共仓库泄露后请立即作废并更换。

### 2) 启动

在仓库根目录执行：

```bash
docker compose up -d --build
```

查看状态与日志：

```bash
docker compose ps
docker compose logs -f nonebot
docker compose logs -f napcat
```

端口（默认）：

- NapCat WebUI：`http://localhost:6099`
- NoneBot：`http://localhost:8080`

### 3) NapCat WebUI 登录与 OneBot 对接

1. 打开 NapCat WebUI：`http://localhost:6099`
2. 按 WebUI 提示完成 QQ 登录
3. 确认 OneBot v11 已连接 NoneBot（本仓库提供了示例配置）：

- NapCat OneBot 配置通常在：`napcat/config/onebot11_<QQ号>.json`
- 关键字段（示例）：
  - `url`: `ws://nonebot:8080/onebot/v11/ws`
  - `token`: 需要与 `docker-compose.yml` 里的 `ONEBOT_ACCESS_TOKEN` 一致

建议：把 OneBot token、NapCat WebUI token 都替换成你自己的随机字符串，避免默认值被滥用。

## 配置说明（bot/.env）

### 1) LLM（对话模型，必须）

`companion_core` 使用 OpenAI Python SDK（`AsyncOpenAI`），因此只要是 **OpenAI 兼容** 的接口都能接入。

当前取值逻辑（按优先级取第一个有值的 key）：

- SiliconFlow：`SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` / `SILICONFLOW_MODEL`
- DeepSeek：`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`
- OpenAI：只读取 `OPENAI_API_KEY`（base_url/model 仍会从 `SILICONFLOW_BASE_URL`/`SILICONFLOW_MODEL` 读取）

因此，如果你用 OpenAI 官方接口，请同时设置：

- `OPENAI_API_KEY=<你的key>`
- `SILICONFLOW_BASE_URL=https://api.openai.com/v1`
- `SILICONFLOW_MODEL=<你要用的模型名>`（例如 `gpt-4.1-mini`）

常见坑：

- `.env` 行尾写注释：`KEY=xxx  # comment` —— 本项目已做兼容，会自动截断行尾注释。

### 2) 联网搜索（可选，但推荐）

新闻/热点/“帮我搜”类问题：

- 优先走 Google Programmable Search（Custom Search JSON API）
- 失败会自动用 RSS 兜底（更像“刷到资讯”）

配置项：

- `GOOGLE_CSE_API_KEY`
- `GOOGLE_CSE_CX`
- 可选：`GOOGLE_CSE_GL` / `GOOGLE_CSE_HL` / `GOOGLE_CSE_PROXY`

如果容器无法直连外网（国内常见），可以设置代理：

- `HTTP_PROXY` / `HTTPS_PROXY`（全局）
- 或只给 Google 配代理：`GOOGLE_CSE_PROXY=http://host.docker.internal:7890`

### 3) 图片理解（可选）

使用 DashScope OpenAI 兼容接口调用 Qwen-VL。

配置项：

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL`（默认已给）
- `DASHSCOPE_REGION=cn`（海外 Key 用 `intl`）
- `QWEN_VL_MODEL`（默认 `qwen-vl-plus-latest`）
- 可选：`DASHSCOPE_PROXY`

### 4) 语音对话（可选）

能力：QQ 语音 → ASR（paraformer）→ LLM → TTS（Qwen realtime）→ QQ 语音。

说明：

- NoneBot 镜像已内置 `ffmpeg`，无需额外安装
- **TTS 需要一个音色**：必须配置 `QWEN_TTS_VOICE`（来自“声音复刻”的 `output.voice`）

`.env.example` 目前未包含全部语音参数，你需要在 `bot/.env` 里手动补充：

- `DASHSCOPE_API_KEY=<你的key>`
- `DASHSCOPE_REGION=cn`（海外 Key 用 `intl`）
- `DASHSCOPE_ASR_MODEL=paraformer-realtime-v2`（可不填，默认即此）
- `QWEN_TTS_MODEL=qwen3-tts-vc-realtime-2025-11-27`（可不填，有默认）
- `QWEN_TTS_VOICE=<你的 output.voice>`
- 可选：`QWEN_TTS_SPEECH_RATE` / `QWEN_TTS_PITCH_RATE` / `QWEN_TTS_VOLUME` / `QWEN_TTS_ENABLE_TN` / `QWEN_TTS_LANGUAGE_TYPE`

#### 获取 `QWEN_TTS_VOICE`（声音复刻）

脚本在：`bot/plugins/companion_core/voice/qwen_voice_clone.py`

推荐做法：把示例音频放进 `bot/` 后在容器里跑（省得本机装依赖）。

Linux/macOS：

```bash
cp xiao_a.wav bot/xiao_a.wav
docker compose exec nonebot python plugins/companion_core/voice/qwen_voice_clone.py \
  --file xiao_a.wav \
  --target-model qwen3-tts-vc-realtime-2025-11-27 \
  --name xiao_a
```

Windows PowerShell：

```powershell
Copy-Item xiao_a.wav bot/xiao_a.wav
docker compose exec nonebot python plugins/companion_core/voice/qwen_voice_clone.py `
  --file xiao_a.wav `
  --target-model qwen3-tts-vc-realtime-2025-11-27 `
  --name xiao_a
```

输出里的 `output.voice` 就是要写进 `QWEN_TTS_VOICE` 的值。

#### 语音触发方式

- 你直接给小a发 QQ 语音（私聊），会自动走“听 → 回语音”
- 你发文字时，如果包含“发语音/语音回复”等字样，会尝试用语音回你
- 也可用开关：
  - `VOICE_REPLY_ON_TEXT=1`：所有文字都用语音回复
  - `VOICE_REPLY_KEYWORDS=语音,来段语音`：命中关键词才语音回复

### 5) 主动互动 / 推送（可选）

- 主动互动：`PROACTIVE_ENABLED` / `PROACTIVE_INTERVAL_MINUTES` / `PROACTIVE_IDLE_HOURS` / `PROACTIVE_MAX_PER_DAY` / `PROACTIVE_COOLDOWN_MINUTES`
- RSS：`RSS_FEEDS`（空则使用内置默认源）
- GitHub 周榜：`GITHUB_WEEKLY_ENABLED=1` + `GITHUB_WEEKLY_USER_ID=<你的QQ号>`

### 6) 财经日报（A股收盘复盘，finance_daily）

特点：

- 只私聊发送，不发群聊
- 只给“已订阅用户”推送（避免刷屏）
- 独立数据库：`bot/plugins/finance_daily/finance.db`（不与 `companion_core/data.db` 共用）

环境变量（见 `bot/.env.example`）：

- `FIN_DAILY_ENABLED`、`FIN_DAILY_RUN_HOUR` / `FIN_DAILY_RUN_MINUTE`、`FIN_DAILY_TOP_N`
- `FIN_DAILY_DATA_PROVIDER`（推荐 `sina`；也可用 `eastmoney`，不依赖 Tushare 权限）

### 7) STM32 / 外部入口（HTTP API，可选）

用于把“小a”的对话能力暴露为一个受保护的 HTTP 接口，方便 STM32MP157 等设备用 `curl`/Python 调用。

接口：

- `POST /api/chat`
- Header：`X-API-Key: <STM32_API_KEY>`
- Body(JSON)：`{"text":"...", "user_id":"...", "source":"stm32"}`
- Response(JSON)：`{"reply":"..."}`

注意：

- **要和 QQ 上下文连续**：`user_id` 填你在 QQ 私聊对应的 `event.user_id`（同一个桶才能共享记忆/情绪/画像）。
- `STM32_API_KEY` 在 `bot/.env` 里配置；未配置时接口会返回 503（fail-closed）。

## 使用说明（私聊）

### 1) 普通聊天

直接私聊发消息即可。

小a 会自动维护：记忆（对话历史、长期信息）、情绪（心情值会随对话波动）、用户画像（例如所在地等）。

### 2) 新闻/热点/“帮我搜”

示例：

- “今天有什么热点？”
- “帮我搜一下 xxx”
- “最近发生啥了？”

如果你追问“链接/来源/出处/原文”，小a 会把上一轮检索到的来源整理发给你。

### 3) 发链接 → 自动总结

你发一个 URL，小a 会判断是否需要总结：

- 需要总结时会先发确认（避免误耗 token）
- 你回复“总结/总结一下”会开始抓取网页并总结
- 结果会缓存，降低重复消耗

### 4) 图片理解

直接发图（可带问题；也支持“先发图，再发问题”）。

### 5) 语音对话（QQ 语音 → ASR → LLM → TTS → QQ 语音）

当前仅支持私聊语音：你给小a发语音，小a会“听写→理解→语音回复”。

如果 TTS 未配置音色（`QWEN_TTS_VOICE`），会自动降级成发文字。

### 6) 股票查询

私聊命令：

- `查股 600519`
- `股票 sh688110`
- `000001.SZ`

### 7) 财经日报（订阅制）

私聊命令（仅私聊生效）：

- `开启财经日报`：写入订阅表 enabled=1，回复“已开启”
- `关闭财经日报`：enabled=0，回复“已关闭”
- `财经日报状态`：回复当前是否开启 + 每天几点推送
- `财经日报 强制`：只对当前用户跑一次并回发（不影响订阅）
- `财经状态`：查看最近一次任务状态与当前配置

### 8) Skills 专业能力模块

小a 内置了动态能力加载系统（Skills），当你问专业问题时，会自动调用相应模块：

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **金融分析** | "推荐个股票"、"今天涨幅榜" | 获取实时涨跌榜数据 |
| **编程助手** | "帮我写一个快速排序"、"这段代码什么意思" | 代码+解释 |
| **情感支持** | "心情不好"、"压力好大" | 专业共情陪伴 |
| **生活助手** | "今天吃什么"、"鸡肉怎么做" | 菜谱推荐（TheMealDB API） |

添加更多 Skills：在 `bot/plugins/companion_core/skills/` 下创建 `.skill.md` 文件即可，无需改代码。


## 外部 HTTP API（可选）

用于把“小a”的对话能力暴露为 HTTP 接口，并复用 QQ 的同一套“记忆/情绪/画像”。

- 接口：`POST /api/chat`
- Header：`X-API-Key: <STM32_API_KEY>`
- Body(JSON)：`{"text":"...", "user_id":"...", "source":"stm32"}`

示例：

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "X-API-Key: <STM32_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好呀","user_id":"123456","source":"stm32"}'
```

## 数据与持久化

- 记忆/状态数据库（SQLite）：`bot/plugins/companion_core/data.db`
- 财经日报数据库（SQLite）：`bot/plugins/finance_daily/finance.db`

注意：

- `docker-compose.yml` 已把 `data.db` 单独挂载，重建容器不会丢。
- 建议把数据库当作运行时数据做备份，不要长期提交到 Git 历史里。

## 运维常用命令

- 启动/重建：`docker compose up -d --build`
- 看日志：`docker compose logs -f nonebot` / `docker compose logs -f napcat`
- 停止：`docker compose down`
- 更新代码后重建：`docker compose up -d --build nonebot`

## 使用 Docker Hub 镜像运行（可选）

如果你不想本地构建，也可以直接使用镜像（示例：`xiaobendaoke/xiao_a:latest`）。

拉取并运行：

```bash
docker pull xiaobendaoke/xiao_a:latest
NONEBOT_IMAGE=xiaobendaoke/xiao_a:latest docker compose up -d --no-build nonebot
```

更新镜像：

```bash
docker pull xiaobendaoke/xiao_a:latest
NONEBOT_IMAGE=xiaobendaoke/xiao_a:latest docker compose up -d --no-build nonebot
```

> 如果你用的是自己的镜像名，把上面的镜像地址替换成你的即可。

## 开发者指南

### 插件加载方式

启动入口在 `bot/bot.py`：

- 注册 OneBot v11 适配器
- `nonebot.require("nonebot_plugin_apscheduler")`
- `nonebot.load_plugins("plugins")`：自动加载 `bot/plugins/` 下的本地插件

当前内置插件：

- `bot/plugins/companion_core/`：核心对话、联网检索、看图、语音、推送、HTTP API
- `bot/plugins/finance_daily/`：财经日报（定时任务 + 私聊命令）

### 写一个最小插件（本地 skills）

在 `bot/plugins/` 下新建目录（例如 `hello`）：

```python
# bot/plugins/hello/__init__.py
from nonebot import on_command
from nonebot.plugin import PluginMetadata

hello = on_command("hello")

@hello.handle()
async def _():
    await hello.finish("你好呀，我是小a～")

__plugin_meta__ = PluginMetadata(
    name="hello",
    description="示例插件",
    usage="私聊发送：hello",
)
```

重建 nonebot：

```bash
docker compose up -d --build nonebot
```

### 代码阅读入口（建议）

- 私聊入口：`bot/plugins/companion_core/handlers.py`
- 对话编排：`bot/plugins/companion_core/llm_core.py`
- Skills 系统：`bot/plugins/companion_core/skills/`
- 新闻/搜索：`bot/plugins/companion_core/llm_news.py`
- URL 总结：`bot/plugins/companion_core/llm_web.py`
- 图片理解：`bot/plugins/companion_core/llm_vision.py`
- 语音 ASR/TTS：`bot/plugins/companion_core/voice/asr.py` / `bot/plugins/companion_core/voice/tts.py`
- 财经日报：`bot/plugins/finance_daily/`

### 内部 API 说明

以下 API 是内部实现细节，通常不需要用户修改（除非源接口故障需要切换）：

| 用途 | API 提供商 | 说明 |
|------|------------|------|
| A 股行情快照 | 东方财富 / 新浪财经 | 涨跌榜、股票查询 |
| 公司画像/公告 | 东方财富 F10 | 财经日报、股票查询 |
| 菜谱推荐 | TheMealDB | 生活助手 skill（可通过 `THEMEALDB_URL` 配置） |


## 常见问题（Troubleshooting）

### 1) NapCat 能登录但 NoneBot 收不到消息

- 检查 `napcat/config/onebot11_<QQ号>.json` 里的 `url/token` 是否正确
- 确认 `ONEBOT_ACCESS_TOKEN` 与 NapCat token 一致
- 看 `docker compose logs -f napcat` 是否有 WebSocket 重连/鉴权失败

### 2) 小a回复“钥匙没配置好/401”

- 检查 `bot/.env` 的 Key 是否正确（`SILICONFLOW_API_KEY`/`DEEPSEEK_API_KEY`/`OPENAI_API_KEY`）
- 更新后重建：`docker compose up -d --build nonebot`

### 3) “搜新闻”一直失败

- 配置 `GOOGLE_CSE_API_KEY` 与 `GOOGLE_CSE_CX`
- 国内网络给容器配代理：`HTTP_PROXY/HTTPS_PROXY` 或 `GOOGLE_CSE_PROXY`
- 即便 Google 不可用，也会自动用 RSS 兜底（但相关性可能更弱）

### 4) 语音转写/语音回复失败

- 检查 `DASHSCOPE_API_KEY` / `DASHSCOPE_REGION`
- TTS 报 `QWEN_TTS_VOICE` 缺失：先跑声音复刻脚本拿 `output.voice`

### 5) Docker Hub 拉取/登录网络失败（国内网络）

如果你需要让 Docker daemon 走代理（例如 Clash `127.0.0.1:7890`），Ubuntu/systemd 示例：

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d

sudo tee /etc/systemd/system/docker.service.d/proxy.conf >/dev/null <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1,host.docker.internal,napcat,nonebot,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```
