# 小a（xiao_a）- AI女友QQ机器人

> **项目定位**：一个拥有真实人设、长期记忆、情感系统的 QQ 私聊 AI 伴侣
> **技术栈**：NapCat（OneBot v11）+ NoneBot2 + Docker Compose + SQLite + ChromaDB
> **核心特色**：拟人化交互（生物钟、情绪、打字节奏、气泡分割）+ 长期记忆（RAG）+ 主动互动

---

## 📋 目录

1. [项目概述](#项目概述)
2. [功能特性](#功能特性)
3. [系统架构](#系统架构)
4. [目录结构](#目录结构)
5. [环境要求](#环境要求)
6. [快速部署](#快速部署)
7. [详细配置](#详细配置)
8. [使用指南](#使用指南)
9. [开发文档](#开发文档)
10. [故障排查](#故障排查)
11. [更新日志](#更新日志)

---

## 项目概述

### 这是什么

小a是一个基于大语言模型的QQ私聊机器人，设计目标是成为用户的"AI女友"。区别于普通的问答机器人，小a具备：

- **拟人化交互**：有情绪（-100~100）、有生物钟（2-7点睡觉）、会假忙碌、打字有节奏
- **长期记忆**：自动保存对话到RAG，能回忆半年前的聊天内容
- **主动互动**：超过8小时不说话会主动找你聊天
- **多模态能力**：支持文字、语音、图片理解
- **实用工具**：股票查询、天气推送、新闻搜索、日程提醒、备忘录

### 适用场景

- 想要一个24小时在线的AI伴侣
- 需要自动化的信息推送（新闻、天气、GitHub趋势）
- 学习NoneBot2插件开发的最佳实践
- 研究AI Agent的情感化设计

### 技术亮点

```
┌─────────────────────────────────────────────────────────────┐
│  拟人化层                                                    │
│  ├── 情绪系统（Mood -100~100，每分钟衰减）                     │
│  ├── 生物钟（2-7点睡觉，5%概率假忙碌）                         │
│  ├── 打字节奏（动态延迟 0.35s~15s）                           │
│  ├── 气泡分割（12字阈值，语义词切分）                          │
│  └── 防打断（监听输入状态，最长等60秒）                         │
├─────────────────────────────────────────────────────────────┤
│  记忆系统                                                    │
│  ├── 瞬时记忆（最近20轮）                                     │
│  ├── RAG长期记忆（ChromaDB，向量化检索）                       │
│  ├── 显式记忆（"记住：xxx"指令）                              │
│  └── 备忘录（带标签的笔记系统）                                │
├─────────────────────────────────────────────────────────────┤
│  认知系统                                                    │
│  ├── 用户画像（自动学习喜好、习惯、所在地）                     │
│  ├── 用户洞察（LLM分析聊天记录，提取兴趣/偏好）                │
│  └── 主动互动（8小时未聊则主动发消息）                         │
├─────────────────────────────────────────────────────────────┤
│  能力系统                                                    │
│  ├── Skills动态加载（金融/编程/情感/生活）                    │
│  ├── 联网搜索（Google CSE + RSS）                            │
│  ├── 多模态（语音ASR/TTS、图片理解）                          │
│  └── 定时任务（天气/股票/GitHub/RSS推送）                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 功能特性

### 核心对话能力

| 功能 | 说明 | 实现文件 |
|------|------|----------|
| **陪伴式对话** | 20岁女友人设，拒绝客服腔 | `persona.py` |
| **情绪系统** | 心情值-100~100，影响语气和语音 | `mood.py` |
| **三层记忆** | 瞬时(20轮) + RAG(向量检索) + 备忘录 | `memory.py`, `rag_core.py` |
| **打字节奏** | 按字数计算延迟，长消息等15秒 | `typing_speed.py` |
| **气泡分割** | 自动拆成多条短消息，像微信聊天 | `bubble_splitter.py` |

### 拟人化细节

| 功能 | 触发条件 | 表现 |
|------|----------|------|
| **生物钟** | 每天 2:00-7:00 | 80%概率不回，20%概率说"困死了..." |
| **假忙碌** | 5%概率（非命令类消息） | "等下哈，我在吹头发"，然后已读不回5-10分钟 |
| **防打断** | 检测到对方正在输入 | 暂停发送，最长等60秒 |
| **情绪衰减** | 每分钟 | 心情值向0回归（±1） |

### 实用工具

| 功能 | 指令示例 | 说明 |
|------|----------|------|
| **日程提醒** | "提醒我10分钟后关火" | 自然语言解析，到点主动提醒 |
| **备忘录** | "记一下 wifi 密码是 xxx" | 支持 #标签，可搜索 |
| **股票查询** | "查股 600519" | 实时行情+公告+LLM分析 |
| **天气推送** | 自动推送 | 每天早上8:20语音/文字提醒 |
| **URL总结** | 直接发链接 | 自动抓取+总结，支持缓存 |
| **图片理解** | 发图片+可选文字 | 支持"先发图后提问"（60秒内） |
| **语音对话** | 发语音条 | ASR→LLM→TTS，情绪联动 |

### 主动推送

| 推送类型 | 时间 | 内容 |
|----------|------|------|
| **天气提醒** | 每天 8:20 | 根据用户所在城市生成提醒 |
| **GitHub周榜** | 每周日 20:30 | TOP5项目总结 |
| **财经日报** | 交易日 15:35 | A股涨跌榜分析 |
| **RSS新闻** | 定时轮询 | 根据用户兴趣筛选推送 |
| **主动聊天** | 8小时未聊 | 结合用户画像生成开场白 |

### Skills系统

通过`.skill.md`文件动态加载专业能力：

```markdown
---
name: financial_analysis
description: 金融分析专家
triggers_prompt: 当用户询问股票、基金、行情等金融问题时触发
data_sources:
  - name: top_gainers
    function: fetch_top_gainers
    args: {limit: 3}
---

你是金融分析专家，擅长用大白话解释复杂的金融概念...
```

内置Skills：金融分析、编程助手、情感支持、生活助手

---

## 系统架构

### 整体架构图

```
                    ┌──────────────────────────────────────────┐
                    │              用户层 (QQ Client)            │
                    └──────────────┬───────────────────────────┘
                                   │ WebSocket
                    ┌──────────────▼───────────────────────────┐
                    │           NapCat (OneBot v11)             │
                    │  - QQ协议桥接                              │
                    │  - 语音文件处理                             │
                    │  - 消息格式转换                             │
                    └──────────────┬───────────────────────────┘
                                   │ HTTP/WebSocket
                    ┌──────────────▼───────────────────────────┐
                    │          NoneBot2 (FastAPI)               │
                    │  - 插件生命周期管理                         │
                    │  - 消息路由                                │
                    │  - 定时任务调度 (apscheduler)              │
                    └──────┬───────────────┬───────────────────┘
                           │               │
           ┌───────────────▼───┐   ┌──────▼────────────────┐
           │  companion_core   │   │    finance_daily      │
           │  核心对话插件      │   │    财经日报插件        │
           └───────┬───────────┘   └───────────────────────┘
                   │
    ┌──────────────┼──────────────┬──────────────┬──────────────┐
    │              │              │              │              │
┌───▼───┐    ┌───▼───┐     ┌───▼───┐    ┌───▼───┐    ┌───▼───┐
│Handlers│    │  LLM  │     │Memory │    │  RAG  │    │Skills │
│消息处理│    │对话编排│     │瞬时记忆│    │向量检索│    │技能系统│
└───┬───┘    └───┬───┘     └───┬───┘    └───┬───┘    └───┬───┘
    │            │             │            │            │
    └────────────┴─────────────┴────────────┴────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌───▼───┐   ┌──────▼──────┐
       │   SQLite    │ │Chroma │   │  External   │
       │  data.db    │ │  DB   │   │    APIs     │
       │ -聊天记录   │ │向量存储│   │ -DashScope  │
       │ -用户画像   │ └───────┘   │ -Google CSE │
       │ -备忘录     │             │ -Open-Meteo │
       │ -日程提醒   │             │ -Tushare    │
       └─────────────┘             └─────────────┘
```

### 核心数据流

```
用户发送消息
    │
    ▼
┌───────────────────────────────────────┐
│ 1. 消息预处理                          │
│    - 限流检查（1.2秒内只处理一次）      │
│    - 生物钟检查（2-7点可能不回）        │
│    - 假忙碌检查（5%概率）              │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ 2. 指令识别                            │
│    - 日程提醒？→ 解析时间 → 存入SQLite  │
│    - 备忘录？→ 保存/查询               │
│    - 股票查询？→ 查行情 → 返回          │
│    - URL？→ 询问是否总结               │
│    - 图片？→ 视觉理解                   │
│    - 语音？→ ASR转文字                  │
└───────────────┬───────────────────────┘
                │ 以上都不是，进入对话流程
                ▼
┌───────────────────────────────────────┐
│ 3. 构建Prompt                          │
│    - 世界设定（当前时间/天气）          │
│    - 人设核心（性格定义）               │
│    - 用户画像（从SQLite读取）           │
│    - 历史记录（最近20轮）               │
│    - RAG检索（相关长期记忆）            │
│    - 当前心情（mood值）                 │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ 4. LLM调用                             │
│    - Skills路由（是否需要专业能力）     │
│    - 联网搜索（实时性问题）             │
│    - 生成回复（带MOOD_CHANGE标签）      │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ 5. 后处理                              │
│    - 解析标签（MOOD_CHANGE/UPDATE_PROFILE）
│    - 更新心情值                        │
│    - 保存用户画像                      │
│    - 存入RAG（异步）                   │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ 6. 发送回复                            │
│    - 气泡分割（按语义切分）             │
│    - 计算打字延迟（0.35s~15s）          │
│    - 防打断（等用户输入结束）           │
│    - 逐条发送（带随机抖动）             │
└───────────────────────────────────────┘
```

---

## 目录结构

```
xiao_a/
├── docker-compose.yml              # Docker编排文件
├── .env.example                    # 环境变量模板
├── README.md                       # 本文件
├── ARCHITECTURE.md                 # 架构详细文档
│
├── napcat/                         # NapCat配置目录
│   ├── config/                     # OneBot配置文件
│   │   └── onebot11_<QQ号>.json   # 自动生成
│   └── qq/                         # QQ登录数据
│       └── <QQ号>/                # 二维码、token等
│
└── bot/                            # NoneBot2机器人
    ├── bot.py                      # 启动入口
    ├── .env                        # 环境变量（从.example复制）
    ├── pyproject.toml              # Python依赖
    │
    └── plugins/                    # 插件目录
        ├── companion_core/         # 核心对话插件
        │   ├── __init__.py
        │   ├── handlers.py         # 消息处理入口（721行）
        │   ├── llm_core.py         # LLM对话编排（300+行）
        │   ├── llm_tags.py         # 标签解析
        │   ├── llm_bubbles.py      # 气泡JSON解析
        │   │
        │   ├── memory.py           # 瞬时记忆（SQLite）
        │   ├── rag_core.py         # RAG长期记忆（ChromaDB）
        │   ├── db.py               # 数据库操作（500+行）
        │   │
        │   ├── mood.py             # 情绪系统（-100~100）
        │   ├── persona.py          # 人设系统
        │   ├── llm_insights.py     # 用户洞察提取
        │   │
        │   ├── reply_manager.py    # 消息发送管理
        │   ├── bubble_splitter.py  # 气泡分割算法
        │   ├── send_rhythm.py      # 发送节奏控制
        │   └── utils/              # 工具函数
        │       ├── typing_speed.py # 打字速度计算
        │       ├── world_info.py   # 世界信息生成
        │       └── open_meteo.py   # 天气API
        │
        │   ├── proactive.py        # 主动互动系统
        │   ├── scheduler_custom.py # 日程提醒
        │   ├── memo.py             # 备忘录系统
        │   ├── weather_push.py     # 天气推送
        │   ├── github_weekly_push.py # GitHub周榜
        │   ├── rss_push.py         # RSS推送
        │   │
        │   ├── llm_news.py         # 新闻搜索
        │   ├── llm_web.py          # URL总结
        │   ├── llm_vision.py       # 图片理解
        │   ├── llm_stock.py        # 股票分析
        │   ├── llm_weather.py      # 天气文案生成
        │   ├── llm_proactive.py    # 主动互动文案
        │   │
        │   ├── stock.py            # 股票数据获取
        │   ├── web/                # 网页处理
        │   │   ├── fetch.py        # HTTP请求
        │   │   ├── parse.py        # HTML解析
        │   │   └── utils.py        # 工具函数
        │   │
        │   ├── voice/              # 语音处理
        │   │   ├── asr.py          # 语音识别
        │   │   ├── tts.py          # 语音合成（情绪联动）
        │   │   └── qwen_voice_clone.py # 音色复刻
        │   │
        │   ├── skills/             # Skills系统
        │   │   ├── __init__.py     # Skill加载器
        │   │   ├── router.py       # 意图路由
        │   │   ├── executor.py     # 执行器
        │   │   ├── financial_analysis.skill.md
        │   │   ├── coding_helper.skill.md
        │   │   ├── emotional_support.skill.md
        │   │   └── life_helper.skill.md
        │   │
        │   ├── info_agent/         # 智能信息推送
        │   │   ├── __init__.py
        │   │   ├── agent.py        # LLM决策
        │   │   ├── pool.py         # 信息池
        │   │   ├── models.py       # 数据模型
        │   │   └── push.py         # 推送逻辑
        │   │
        │   └── finance_daily/      # 财经日报（子模块）
        │       ├── __init__.py
        │       ├── data.py         # 数据获取
        │       ├── analyzer.py     # 分析生成
        │       ├── scheduler.py    # 定时任务
        │       └── storage.py      # 数据存储
        │
        └── finance_daily/          # 财经日报插件（独立）
            └── ...
```

---

## 环境要求

### 必需

| 组件 | 版本 | 说明 |
|------|------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | 2.0+ | 容器编排 |
| Linux/Windows/macOS | - | 64位系统 |
| RAM | 4GB+ | 建议8GB |
| Disk | 10GB+ | 数据持久化 |

### 外部API（至少配置一个LLM）

| API | 用途 | 获取方式 |
|-----|------|----------|
| **SiliconFlow** | LLM对话 | https://siliconflow.cn |
| **DeepSeek** | LLM对话 | https://deepseek.com |
| **OpenAI** | LLM对话 | https://openai.com |
| DashScope | 语音/图片 | https://dashscope.aliyun.com |
| Google CSE | 联网搜索 | https://programmablesearchengine.google.com |

---

## 快速部署

### 步骤1：克隆项目

```bash
git clone https://github.com/xiaobendaoke/xiao_a.git
cd xiao_a
```

### 步骤2：配置环境变量

```bash
# 复制模板
cp bot/.env.example bot/.env

# 编辑配置（必选）
nano bot/.env
```

**最小配置示例**：

```ini
# ========== LLM配置（三选一，建议SiliconFlow）==========
SILICONFLOW_API_KEY=sk-your-key-here
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3

# ========== 数据库配置（默认即可）==========
DATABASE_URL=sqlite:///data.db

# ========== OneBot配置（必须与NapCat一致）==========
ONEBOT_ACCESS_TOKEN=your-random-token-here
```

### 步骤3：启动服务

```bash
# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f nonebot
docker compose logs -f napcat
```

### 步骤4：配置NapCat

1. 打开 NapCat WebUI：`http://localhost:6099`
2. 扫码登录QQ
3. 确认OneBot配置：
   - URL: `ws://nonebot:8080/onebot/v11/ws`
   - Token: 与 `bot/.env` 中的 `ONEBOT_ACCESS_TOKEN` 一致

### 步骤5：测试

私聊你的QQ号，发送"你好"，应该收到小a的回复。

---

## 详细配置

### 完整环境变量说明

#### LLM配置（必需）

```ini
# SiliconFlow（推荐）
SILICONFLOW_API_KEY=sk-xxxx
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3

# DeepSeek（备选）
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# OpenAI（备选）
OPENAI_API_KEY=sk-xxxx
# 使用OpenAI时需同时设置SILICONFLOW_BASE_URL=https://api.openai.com/v1
```

#### 联网搜索（可选）

```ini
GOOGLE_CSE_API_KEY=your-key
GOOGLE_CSE_CX=your-cx
GOOGLE_CSE_PROXY=http://host.docker.internal:7890  # 国内网络需代理
```

#### 语音配置（可选）

```ini
DASHSCOPE_API_KEY=sk-xxxx
DASHSCOPE_REGION=cn  # 海外Key用 intl
QWEN_TTS_VOICE=your-output-voice-id  # 必须先复刻音色

# TTS参数（可选）
QWEN_TTS_SPEECH_RATE=0  # 语速 -50~50
QWEN_TTS_PITCH_RATE=0    # 音调 -50~50
QWEN_TTS_VOLUME=0        # 音量 -50~50
```

#### 图片理解（可选）

```ini
DASHSCOPE_API_KEY=sk-xxxx  # 与语音共用
QWEN_VL_MODEL=qwen-vl-plus-latest
```

#### 主动互动（可选）

```ini
PROACTIVE_ENABLED=1
PROACTIVE_INTERVAL_MINUTES=5      # 检查间隔
PROACTIVE_IDLE_HOURS=8            # 多少小时未聊触发
PROACTIVE_MAX_PER_DAY=2           # 每天最多主动几次
PROACTIVE_COOLDOWN_MINUTES=240    # 主动互动冷却时间
```

#### 推送配置（可选）

```ini
# 天气推送
WEATHER_PUSH_ENABLED=1
WEATHER_PUSH_HOUR=8
WEATHER_PUSH_MINUTE=20

# GitHub周榜
GITHUB_WEEKLY_ENABLED=1
GITHUB_WEEKLY_USER_ID=123456789   # 你的QQ号

# 财经日报
FINANCE_DAILY_ENABLED=1
FINANCE_DAILY_USER_ID=123456789
FINANCE_DAILY_HOUR=15
FINANCE_DAILY_MINUTE=35
```

#### HTTP API（可选）

```ini
STM32_API_KEY=your-api-key-here  # 用于外部设备调用
```

### 数据库Schema

#### 核心表（data.db）

```sql
-- 聊天记录
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- 'user' or 'assistant'
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户画像
CREATE TABLE user_profile (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);

-- 心情值
CREATE TABLE mood (
    user_id TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0,  -- -100~100
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 备忘录
CREATE TABLE memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,  -- 逗号分隔
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 日程提醒
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trigger_at INTEGER NOT NULL,  -- Unix timestamp
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending'  -- 'pending', 'done'
);

-- 用户洞察
CREATE TABLE user_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,  -- 'interest', 'habit', 'preference', 'topic'
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 网页缓存
CREATE TABLE web_cache (
    url TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 使用指南

### 基础对话

直接私聊发消息即可。小a会：
1. 自动保存对话到记忆
2. 更新心情值（根据对话内容±1~3）
3. 学习你的画像（所在地、喜好等）
4. 保持20轮上下文

### 高级功能

#### 1. 日程提醒

```
用户：提醒我10分钟后关火
小a：好的，我会在10:35提醒你关火~

[10分钟后]
小a：⏰ 时间到啦！快去关火吧~
```

支持格式：
- 相对时间："10分钟后"、"半小时后"、"2小时后"
- 绝对时间："明天8点"、"晚上9点"

#### 2. 备忘录

```
用户：记一下 wifi密码是123456 #wifi
小a：✅ 已保存到备忘录

用户：查询笔记 wifi
小a：找到1条记录：
      [01-15 14:30] wifi密码是123456
```

#### 3. 股票查询

```
用户：查股 600519
小a：【查股】贵州茅台(600519) +1.23%
      今天成交挺热的...
      可能和"xxx公告"有关...
```

#### 4. URL总结

```
用户：[发送链接]
小a：我看到你发了个链接~
      你是想让我帮你整理重点吗？
      回我"总结"就行。

用户：总结
小a：[总结内容...]
```

#### 5. 图片理解

```
用户：[发图]
小a：（等待60秒内）

用户：这是哪里？
小a：这是故宫的角楼呀~ 你去过吗？
```

#### 6. 语音对话

```
用户：[发语音]
小a：[语音回复]
```

---

## 开发文档

### 添加新Skill

1. 创建 `.skill.md` 文件：

```bash
touch bot/plugins/companion_core/skills/my_skill.skill.md
```

2. 编写Skill定义：

```markdown
---
name: my_skill
description: 我的自定义技能
triggers_prompt: 当用户询问xxx时触发
data_sources:
  - name: data1
    function: fetch_data_function  # 需在executor.py注册
    args: {key: value}
---

你是xxx专家，擅长...

【实时数据】
{data_text}
```

3. 注册数据函数（如需）：

```python
# executor.py
@register_data_function("fetch_data_function")
async def _fetch_data(key: str) -> dict:
    # 实现数据获取
    return {"key": "value"}
```

4. 重启服务：

```bash
docker compose up -d --build nonebot
```

### API接口

#### POST /api/chat

外部设备调用小a对话能力。

**请求**：

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "X-API-Key: <STM32_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好呀",
    "user_id": "123456789",
    "source": "stm32"
  }'
```

**响应**：

```json
{
  "reply": "你好呀~我是小a，很高兴认识你！"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | 是 | 用户消息 |
| user_id | string | 是 | 用户ID（必须与QQ号一致才能共享记忆）|
| source | string | 否 | 来源标识 |

---

## 故障排查

### 常见问题速查

#### Q1: NapCat连接但收不到消息

**检查清单**：
1. `napcat/config/onebot11_<QQ号>.json` 中的URL是否正确
2. `ONEBOT_ACCESS_TOKEN` 是否与NapCat一致
3. 查看日志：`docker compose logs -f napcat | grep WebSocket`

#### Q2: LLM返回401错误

**解决**：
```bash
# 检查环境变量是否生效
docker compose exec nonebot env | grep SILICONFLOW

# 如果不存在，重新配置 .env 并重建
docker compose up -d --build nonebot
```

#### Q3: 语音功能不工作

**排查步骤**：
1. 确认 `DASHSCOPE_API_KEY` 已配置
2. 确认 `QWEN_TTS_VOICE` 已配置（必须复刻音色）
3. 检查日志是否有ffmpeg错误

#### Q4: 数据库丢失

**注意**：数据库挂载在Docker Volume中，确保 `docker-compose.yml` 中有：

```yaml
volumes:
  - ./bot/plugins/companion_core/data.db:/app/plugins/companion_core/data.db
```

#### Q5: RAG检索不工作

**检查**：
1. ChromaDB是否正确初始化（首次运行会自动创建）
2. 查看日志：`docker compose logs nonebot | grep rag`

### 调试模式

```bash
# 启用调试日志
DEBUG=1 docker compose up -d

# 进入容器调试
docker compose exec nonebot /bin/bash

# Python交互式测试
docker compose exec nonebot python
>>> from plugins.companion_core.llm_core import get_ai_reply
>>> import asyncio
>>> asyncio.run(get_ai_reply("123456", "测试"))
```

---

## 更新日志

### v2.0.0 (2025-01)

- ✨ 新增拟人化细节：生物钟、假忙碌、打字节奏
- ✨ 新增四层记忆系统：瞬时/RAG/显式/备忘录
- ✨ 新增用户洞察系统
- ✨ 新增日程提醒和备忘录
- ✨ 新增Skills动态加载系统
- ✨ 新增信息推送Agent
- 🔧 优化气泡分割算法（12字阈值+语义词切分）
- 🔧 优化打字延迟计算（动态上限0.35s~15s）

### v1.0.0 (2024-12)

- 🎉 项目初始化
- ✨ 基础对话能力
- ✨ 情绪系统
- ✨ 天气/股票查询
- ✨ 语音对话

---

## 贡献指南

欢迎提交Issue和PR！

### 提交规范

- **Bug修复**：`fix: 修复xxx问题`
- **新功能**：`feat: 添加xxx功能`
- **文档**：`docs: 更新xxx文档`

### 代码规范

- 使用 Black 格式化
- 类型注解必须完整
- 异步函数使用 `async/await`

---

## 许可证

MIT License

---

## 联系方式

- GitHub Issues: https://github.com/xiaobendaoke/xiao_a/issues
- 作者：xiaobendaoke

---

**祝你和小a聊天愉快！** 💕
