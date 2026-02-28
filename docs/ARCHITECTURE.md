# 小a (XiaoA) 全景架构与实现详解

本文档详细揭秘小a的所有功能背后的实现逻辑，适合开发者阅读和二次开发。

> 说明：本文包含历史编排示例（含 docker-compose 章节）。当前项目运行时定位以 `README.md` 的 OpenClaw-first 声明为准。

---

## 📋 目录

1. [整体架构](#1-整体架构)
2. [核心模块详解](#2-核心模块详解)
3. [数据流详解](#3-数据流详解)
4. [拟人化系统](#4-拟人化系统)
5. [记忆系统](#5-记忆系统)
6. [认知系统](#6-认知系统)
7. [能力系统](#7-能力系统)
8. [数据库设计](#8-数据库设计)
9. [关键算法](#9-关键算法)
10. [配置详解](#10-配置详解)

---

## 1. 整体架构

### 1.1 系统分层

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           接入层 (Access Layer)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  NapCat (OneBot v11)                                                        │
│  ├── 功能：QQ协议桥接、语音文件处理、消息格式转换                                │
│  ├── 端口：6099 (WebUI), 8080 (OneBot WebSocket)                            │
│  └── 配置：napcat/config/onebot11_<QQ号>.json                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                           框架层 (Framework Layer)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  NoneBot2 (FastAPI)                                                         │
│  ├── 核心：插件生命周期管理、消息路由、定时任务调度(apscheduler)                 │
│  ├── 适配器：OneBot V11 Adapter                                              │
│  └── 钩子：on_message, on_notice, scheduled_job                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                           插件层 (Plugin Layer)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  companion_core (核心插件)                                                   │
│  ├── handlers.py       - 消息处理总入口                                        │
│  ├── llm_core.py       - LLM对话编排                                          │
│  ├── memory.py         - 瞬时记忆管理                                         │
│  ├── rag_core.py       - RAG长期记忆                                          │
│  ├── mood.py           - 情绪系统                                             │
│  ├── db.py             - 数据库操作                                           │
│  └── ...                                                                              │
│                                                                                      │
│  finance_daily (财经日报插件)                                                 │
│  └── 独立的股票分析和推送系统                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                           能力层 (Capability Layer)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  拟人化系统                                                                  │
│  ├── bubble_splitter.py    - 气泡分割算法                                      │
│  ├── send_rhythm.py        - 发送节奏控制                                      │
│  ├── typing_speed.py       - 打字速度计算                                      │
│  └── reply_manager.py      - 消息发送管理                                      │
│                                                                                      │
│  认知系统                                                                    │
│  ├── llm_insights.py       - 用户洞察提取                                      │
│  ├── proactive.py          - 主动互动                                          │
│  └── info_agent/           - 智能信息推送                                      │
│                                                                                      │
│  工具系统                                                                    │
│  ├── llm_news.py           - 新闻搜索                                          │
│  ├── llm_web.py            - URL总结                                           │
│  ├── llm_vision.py         - 图片理解                                          │
│  ├── llm_stock.py          - 股票分析                                          │
│  ├── scheduler_custom.py   - 日程提醒                                          │
│  ├── memo.py               - 备忘录                                            │
│  └── weather_push.py       - 天气推送                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                           存储层 (Storage Layer)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  SQLite (data.db)                                                           │
│  ├── chats              - 聊天记录                                            │
│  ├── user_profile       - 用户画像                                            │
│  ├── mood               - 心情值                                              │
│  ├── memos              - 备忘录                                              │
│  ├── schedules          - 日程提醒                                            │
│  └── user_insights      - 用户洞察                                            │
│                                                                                      │
│  ChromaDB (向量数据库)                                                       │
│  └── RAG长期记忆存储                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                           外部服务层 (External Services)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  LLM API                                                                    │
│  ├── SiliconFlow / DeepSeek / OpenAI (对话)                                  │
│  │   └── 支持多Key轮询与自动重试（Resilience）                                  │
│  └── DashScope (语音、图片)                                                   │
│                                                                                      │
│  数据API                                                                    │
│  ├── Google CSE        - 联网搜索                                             │
│  ├── Open-Meteo        - 天气数据                                             │
│  ├── 东方财富          - 股票行情                                             │
│  └── TheMealDB         - 菜谱数据                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 模块依赖图

```
                         ┌─────────────────┐
                         │   handlers.py   │
                         │   (消息入口)     │
                         └────────┬────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
       ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
       │llm_core.py   │   │ scheduler_   │   │   memo.py    │
       │(对话编排)     │   │ custom.py    │   │  (备忘录)     │
       └──────┬───────┘   │(日程提醒)     │   └──────────────┘
              │           └──────────────┘
              │
    ┌─────────┼─────────┬───────────┬──────────┐
    │         │         │           │          │
    ▼         ▼         ▼           ▼          ▼
┌──────┐ ┌──────┐ ┌────────┐ ┌──────────┐ ┌─────────┐
│memory│ │ mood │ │  RAG   │ │  skills  │ │  news   │
│(瞬时) │ │(情绪)│ │(长期)  │ │(专业能力)│ │ (搜索)  │
└──────┘ └──────┘ └────────┘ └──────────┘ └─────────┘
    │         │         │           │          │
    └─────────┴─────────┴───────────┴──────────┘
                          │
                          ▼
                  ┌──────────────┐
                  │ reply_manager│
                  │ (消息发送)    │
                  └──────┬───────┘
                         │
           ┌─────────────┼─────────────┐
           │             │             │
           ▼             ▼             ▼
    ┌────────────┐ ┌──────────┐ ┌──────────┐
    │bubble_     │ │  send_   │ │  typing_ │
    │splitter    │ │ rhythm   │ │  speed   │
    │(气泡分割)  │ │(节奏控制)│ │(速度计算)│
    └────────────┘ └──────────┘ └──────────┘
```

---

## 2. 核心模块详解

### 2.1 handlers.py - 消息处理总入口

**职责**：所有私聊消息的入口，负责指令识别和流程分发。

**处理流程**：

```python
# 伪代码展示处理流程
async def handle_private_chat(event):
    user_id = event.user_id
    message = event.get_message()
    
    # 1. 语音消息处理
    if is_voice(message):
        return await handle_voice(user_id, message)
    
    # 2. 基础检查
    await check_biology_clock(user_id)      # 生物钟检查
    await check_fake_busy(user_id)          # 假忙碌检查
    await check_rate_limit(user_id)         # 限流检查
    
    # 3. 指令识别（优先级顺序）
    if is_schedule_command(text):           # 日程提醒
        return await handle_schedule(user_id, text)
    if is_memo_command(text):               # 备忘录
        return await handle_memo(user_id, text)
    if is_stock_command(text):              # 股票查询
        return await handle_stock(user_id, text)
    if is_summary_request(text):            # URL总结跟进
        return await handle_summary(user_id, text)
    if has_image(message):                  # 图片理解
        return await handle_image(user_id, message, text)
    if has_url(text):                       # URL自动处理
        return await handle_url(user_id, text)
    
    # 4. 普通对话
    reply = await get_ai_reply(user_id, text)
    await send_reply(user_id, reply)
```

**关键状态机**：

```
用户发送消息
    │
    ├─→ 语音消息 ───────→ ASR → LLM → TTS → 发送
    │
    ├─→ 文本消息
    │       │
    │       ├─→ 生物钟检查 ──→ 拒绝/困倦回复
    │       │
    │       ├─→ 假忙碌检查 ──→ 忙碌回复/已读不回
    │       │
    │       ├─→ 限流检查 ────→ 丢弃
    │       │
    │       └─→ 指令识别
    │               │
    │               ├─→ 日程提醒指令 ──→ 解析时间 → 存储 → 确认
    │               ├─→ 备忘录指令 ────→ 保存/查询 → 回复
    │               ├─→ 股票指令 ──────→ 查行情 → LLM分析 → 回复
    │               ├─→ URL跟进 ──────→ 抓取 → 总结 → 回复
    │               ├─→ 图片消息 ─────→ 视觉理解 → 回复
    │               ├─→ URL检测 ──────→ 询问是否总结
    │               │
    │               └─→ 普通对话 ─────→ 构建Prompt → LLM → 发送
    │
    └─→ 等待下一条消息
```

### 2.2 llm_core.py - LLM对话编排

**职责**：构建完整的Prompt，调用LLM，处理响应。

**Prompt构建顺序**（从上到下，越晚加入权重越高）：

```python
def build_messages(user_id, user_text, **kwargs):
    messages = []
    
    # 1. 系统人设（最基础）
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    
    # 2. 世界设定
    messages.append({
        "role": "system", 
        "content": f"现在时间：{get_time()}, 天气：{get_weather()}"
    })
    
    # 3. 用户画像
    profile = get_user_profile(user_id)
    messages.append({
        "role": "system",
        "content": f"用户信息：{profile}"
    })
    
    # 4. Skills能力（如果需要）
    if skill_prompt := route_skill(user_text):
        messages.append({"role": "system", "content": skill_prompt})
    
    # 5. RAG长期记忆（非新闻查询时）
    if not is_news_query(user_text):
        memories = retrieve_memories(user_id, user_text)
        messages.append({"role": "system", "content": f"相关记忆：{memories}"})
    
    # 6. 联网搜索结果（新闻查询时）
    if is_news_query(user_text):
        search_results = search_web(user_text)
        messages.append({"role": "system", "content": search_results})
    
    # 7. 当前心情
    mood = get_mood(user_id)
    messages.append({
        "role": "system",
        "content": f"当前心情：{mood}，{get_mood_instruction(mood)}"
    })
    
    # 8. 历史对话（最近20轮）
    history = get_recent_chats(user_id, limit=20)
    messages.extend(history)
    
    # 9. 用户当前消息
    messages.append({"role": "user", "content": user_text})
    
    # 10. 最后指令（覆盖前面的规则）
    messages.append({
        "role": "system",
        "content": "（System: 现在的语境是微信闲聊。请把回复写得短一点、松弛一点、口语化一点。）"
    })
    
    return messages
```

**LLM响应处理**：

```python
async def get_ai_reply(user_id, user_text):
    # 1. 构建Prompt
    messages = build_messages(user_id, user_text)
    
    # 2. 调用LLM
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=get_temperature(user_id),  # 根据心情调整
        max_tokens=get_max_tokens(user_text),  # 根据场景调整
    )
    
    raw_content = response.choices[0].message.content
    
    # 3. 解析标签
    clean_reply, mood_change, updates = extract_tags_and_clean(raw_content)
    
    # 4. 更新状态
    if mood_change:
        mood_manager.update_mood(user_id, mood_change)
    for key, value in updates:
        save_profile_item(user_id, key, value)
    
    # 5. 保存记忆
    add_memory(user_id, "user", user_text)
    add_memory(user_id, "assistant", clean_reply)
    
    # 6. 异步存入RAG
    asyncio.create_task(add_document(
        f"User: {user_text}\nXiaoA: {clean_reply}",
        metadata={"user_id": user_id, "type": "chat_history"}
    ))
    
    return clean_reply
```

### 2.3 memory.py - 瞬时记忆

**职责**：管理最近20轮对话，提供上下文连贯性。

**实现**：

```python
# 内存缓存
_chat_history: dict[str, list[dict]] = {}

def add_memory(user_id: str, role: str, content: str):
    """添加一条记忆"""
    if user_id not in _chat_history:
        _chat_history[user_id] = []
    
    _chat_history[user_id].append({
        "role": role,
        "content": content,
        "timestamp": time.time()
    })
    
    # 只保留最近20轮（40条消息）
    if len(_chat_history[user_id]) > 40:
        _chat_history[user_id] = _chat_history[user_id][-40:]
    
    # 持久化到SQLite（异步，不阻塞）
    asyncio.create_task(_persist_to_db(user_id, role, content))

def get_recent_context(user_id: str, limit: int = 20) -> list[dict]:
    """获取最近N轮对话"""
    history = _chat_history.get(user_id, [])
    
    # 格式转换
    messages = []
    for msg in history[-limit*2:]:  # *2因为每轮有user+assistant
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })
    
    return messages
```

**为什么需要"瞬时记忆"？**

1. **速度**：内存读取比SQLite快100倍
2. **上下文长度限制**：LLM有token限制，只传最近20轮
3. **临时状态**：不需要长期保存的"正在进行的话题"

### 2.4 rag_core.py - RAG长期记忆

**职责**：向量存储和检索历史对话，实现"回忆"能力。

**架构**：

```
用户对话
    │
    ▼
┌──────────────────┐
│ 1. 文本预处理     │ 清洗、过滤、格式化
│    - 去除表情     │
│    - 合并多轮     │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 2. Embedding     │ 将文本转为向量
│    模型：BAAI/   │
│    bge-m3        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 3. ChromaDB存储   │ 持久化向量
│    - collection  │
│    - metadata    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 4. 检索          │ 当用户提到"火锅"时
│    - 向量化查询  │
│    - 相似度排序  │
│    - 返回Top3    │
└──────────────────┘
```

**关键代码**：

```python
# RAG核心实现
from chromadb import Client, Settings

_client: Client | None = None
_collection = None

async def get_collection():
    """获取或初始化ChromaDB集合"""
    global _client, _collection
    
    if _collection is not None:
        return _collection
    
    _client = Client(Settings(
        chroma_db_impl="duckdb+parquet",
        persist_directory="./chroma_db"
    ))
    
    _collection = _client.get_or_create_collection(
        name="chat_memories",
        metadata={"hnsw:space": "cosine"}
    )
    
    return _collection

async def add_document(text: str, metadata: dict) -> bool:
    """添加文档到RAG"""
    if len(text) < 5:  # 太短的忽略
        return False
    
    # 1. 生成向量
    embedding = await get_text_embedding(text)
    if not embedding:
        return False
    
    # 2. 生成唯一ID
    doc_id = hashlib.md5(f"{metadata['user_id']}:{text}".encode()).hexdigest()
    
    # 3. 存储
    collection = await get_collection()
    collection.add(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata]
    )
    
    return True

async def search_memories(user_id: str, query: str, top_k: int = 3) -> list[str]:
    """搜索相关记忆"""
    # 1. 查询向量化
    query_embedding = await get_text_embedding(query)
    
    # 2. 检索
    collection = await get_collection()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"user_id": user_id},  # 只搜索该用户的
        include=["documents", "metadatas", "distances"]
    )
    
    # 3. 过滤低相似度（距离>0.3认为是无关的）
    memories = []
    for doc, distance in zip(results["documents"][0], results["distances"][0]):
        if distance < 0.3:  # cosine距离，越小越相似
            memories.append(doc)
    
    return memories
```

**时间衰减权重**：

为了让"最近的记忆"更重要，RAG检索后会根据时间加权：

```python
def apply_time_decay(results: list[dict]) -> list[dict]:
    """应用时间衰减"""
    now = time.time()
    
    for result in results:
        age_days = (now - result["created_at"]) / 86400
        # 超过30天的记忆权重降低
        decay_factor = max(0.3, 1.0 - (age_days / 30) * 0.7)
        result["score"] *= decay_factor
    
    # 重新排序
    return sorted(results, key=lambda x: x["score"], reverse=True)
```

### 2.5 mood.py - 情绪系统

**职责**：维护用户的心情值（-100~100），影响回复语气和语音参数。

**状态定义**：

```
心情值范围：-100 ~ +100

+80 ~ +100: 超级兴奋
+30 ~ +79:  开心
-10 ~ +29:  平静（默认）
-50 ~ -11:  烦躁/郁闷
-100 ~ -51: 生气/崩溃
```

**核心机制**：

```python
class MoodManager:
    def __init__(self):
        self._moods: dict[str, int] = {}      # 内存缓存
        self._timestamps: dict[str, float] = {}  # 上次更新时间
    
    def get_user_mood(self, user_id: str) -> int:
        """获取当前心情（自动应用时间衰减）"""
        now = time.time()
        
        # 从数据库加载（如果没有缓存）
        if user_id not in self._moods:
            self._moods[user_id], self._timestamps[user_id] = self._load_from_db(user_id)
        
        current = self._moods[user_id]
        last_ts = self._timestamps[user_id]
        
        # 时间衰减：每60秒恢复1点（向0回归）
        delta_seconds = now - last_ts
        decay_points = int(delta_seconds / 60)
        
        if decay_points > 0 and current != 0:
            if current > 0:
                new_mood = max(0, current - decay_points)
            else:
                new_mood = min(0, current + decay_points)
            
            # 保存更新
            self._moods[user_id] = new_mood
            self._timestamps[user_id] = now
            self._save_to_db(user_id, new_mood)
            
            return new_mood
        
        return current
    
    def update_mood(self, user_id: str, change: int) -> int:
        """更新心情（单次变化限制在-5~+5）"""
        # 先获取当前（已衰减）
        current = self.get_user_mood(user_id)
        
        # 限制单次变化幅度（避免剧烈波动）
        change = max(-5, min(5, change))
        
        # 计算新值并限制范围
        new_mood = max(-100, min(100, current + change))
        
        # 保存
        now = time.time()
        self._moods[user_id] = new_mood
        self._timestamps[user_id] = now
        self._save_to_db(user_id, new_mood)
        
        return new_mood
    
    def get_mood_desc(self, user_id: str) -> str:
        """获取心情描述（用于Prompt）"""
        v = self.get_user_mood(user_id)
        if v >= 80: return "心理状态：超级兴奋，恨不得马上抱住他，满眼星星。"
        if v >= 30: return "心理状态：心情不错，比较甜，看什么都顺眼。"
        if v >= -10: return "心理状态：内心平静，比较随性，懒洋洋的。"
        if v >= -50: return "心理状态：有点烦躁/郁闷，不想多说话，对他有点不耐烦。"
        return "心理状态：非常生气/崩溃，甚至想冷暴力或者发脾气。"
    
    def get_mood_instruction(self, user_id: str) -> str:
        """获取心情指令（强制改变语气）"""
        v = self.get_user_mood(user_id)
        if v < -20:
            return "【强制】：你现在还在生气，说话要短，不要带语气词，禁止发可爱的表情/颜文字。"
        return ""
```

**与TTS的联动**（语音情绪）：

```python
# voice/tts.py
async def synthesize_with_mood(text: str, mood: int) -> bytes:
    """根据心情调整语音参数"""
    params = {
        "speech_rate": 0,    # 语速 -50~50
        "pitch_rate": 0,     # 音调 -50~50
        "volume": 0,         # 音量 -50~50
    }
    
    if mood > 30:  # 开心
        params["speech_rate"] = min(50, mood * 0.3)  # 加快
        params["pitch_rate"] = min(50, mood * 0.4)   # 提高
        params["volume"] = min(50, mood * 0.2)       # 增大
    elif mood < -20:  # 难过
        params["speech_rate"] = max(-50, mood * 0.3)  # 减慢
        params["pitch_rate"] = max(-50, mood * 0.4)   # 降低
        params["volume"] = max(-50, mood * 0.2)       # 减小
    
    return await call_tts_api(text, **params)
```

### 2.6 db.py - 数据库操作

**职责**：所有SQLite数据库操作的统一入口。

**表结构**：

```python
# 数据库初始化
INIT_SQL = """
-- 聊天记录
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chats_user_id ON chats(user_id);
CREATE INDEX IF NOT EXISTS idx_chats_created_at ON chats(created_at);

-- 用户画像
CREATE TABLE IF NOT EXISTS user_profile (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);

-- 心情值
CREATE TABLE IF NOT EXISTS mood (
    user_id TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0 CHECK(value >= -100 AND value <= 100),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 备忘录
CREATE TABLE IF NOT EXISTS memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,  -- 逗号分隔
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memos_user_id ON memos(user_id);

-- 日程提醒
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trigger_at INTEGER NOT NULL,  -- Unix timestamp
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'done', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedules_trigger_at ON schedules(trigger_at);
CREATE INDEX IF NOT EXISTS idx_schedules_user_id ON schedules(user_id);

-- 用户洞察
CREATE TABLE IF NOT EXISTS user_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('interest', 'habit', 'preference', 'topic')),
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.5 CHECK(confidence >= 0 AND confidence <= 1),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_insights_user_id ON user_insights(user_id);

-- 网页缓存
CREATE TABLE IF NOT EXISTS web_cache (
    url TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
```

---

## 3. 数据流详解

### 3.1 完整对话流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段1：消息接收与预处理                               │
└─────────────────────────────────────────────────────────────────────────────┘

用户发送："今天有什么新闻"
    │
    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ handlers.py:handle_private_chat()                                        │
│                                                                          │
│ 1. 记录活跃时间                                                          │
│    touch_active(user_id)                                                 │
│    log_user_active_hour(user_id)                                         │
│                                                                          │
│ 2. 生物钟检查                                                            │
│    if _is_sleeping_time():                                               │
│        if random() < 0.8: return  # 80%概率不回                          │
│        else: reply = "困死了...明天说..."                                │
│                                                                          │
│ 3. 假忙碌检查                                                            │
│    if not user_input.startswith(("查股", "股票")):                         │
│        busy_reason = _is_fake_busy(user_id)                              │
│        if busy_reason == "busy_ignoring": return                         │
│        elif busy_reason: reply = busy_reason                             │
│                                                                          │
│ 4. 限流检查                                                              │
│    if not _check_and_update_rate_limit(user_id, now): return             │
│                                                                          │
│ 5. 防打断                                                                │
│    await _wait_if_user_typing(user_id)  # 等用户输入结束                  │
└──────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段2：指令识别与分发                                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ 6. 指令识别（按优先级）                                                   │
│                                                                          │
│    ├─→ 日程提醒？    await try_handle_schedule()                         │
│    ├─→ 备忘录？      await try_handle_memo()                             │
│    ├─→ 股票查询？    await _handle_stock_query_if_any()                  │
│    ├─→ 来源追问？    await _handle_source_request_if_any()               │
│    ├─→ URL跟进？     await _handle_summary_followup_if_any()             │
│    ├─→ 图片消息？    await _handle_image_request_if_any()                │
│    ├─→ URL自动？     await _handle_url_auto_if_any()                     │
│    │
│    └─→ 以上都不是 → 进入普通对话流程                                      │
└──────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段3：LLM对话编排                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ llm_core.py:get_ai_reply()                                               │
│                                                                          │
│ 7. 构建Prompt（按优先级，越晚权重越高）                                    │
│    ├─→ SYSTEM_PROMPT (人设核心)                                          │
│    ├─→ 世界设定 (当前时间/天气)                                           │
│    ├─→ 用户画像 (SQLite读取)                                              │
│    ├─→ 用户洞察 (兴趣/习惯/偏好)                                          │
│    ├─→ Skills能力 (如果需要)                                              │
│    ├─→ RAG长期记忆 (非新闻查询时)                                         │
│    ├─→ 联网搜索结果 (新闻查询时)                                          │
│    ├─→ 当前心情 (mood值 + 描述)                                           │
│    ├─→ 历史对话 (最近20轮)                                                │
│    ├─→ 用户当前消息                                                       │
│    └─→ 最后指令 (语气覆盖)                                                │
│                                                                          │
│ 8. 调用LLM                                                               │
│    temperature = 0.7 (默认)                                              │
│    if mood < -20: temperature = 0.5  # 生气时更冷静                      │
│    if skill_prompt: max_tokens = 800                                     │
│                                                                          │
│ 9. 解析响应                                                               │
│    raw_content = response.choices[0].message.content                     │
│    clean_reply, mood_change, updates = extract_tags_and_clean()          │
│                                                                          │
│ 10. 更新状态                                                              │
│     if mood_change: mood_manager.update_mood(user_id, mood_change)       │
│     for k, v in updates: save_profile_item(user_id, k, v)                │
│                                                                          │
│ 11. 保存记忆                                                              │
│     add_memory(user_id, "user", user_text)                               │
│     add_memory(user_id, "assistant", clean_reply)                        │
│                                                                          │
│ 12. 异步存入RAG                                                           │
│     asyncio.create_task(add_document(...))                               │
└──────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         阶段4：消息发送                                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ reply_manager.py:send_bubbles_and_finish()                               │
│                                                                          │
│ 13. 气泡分割                                                              │
│     parts = _split_text_to_bubbles(text)                                 │
│     # 按语义切分，12字阈值，语义词优先                                     │
│                                                                          │
│ 14. 逐条发送                                                              │
│     for i, part in enumerate(parts[:-1]):                                │
│         await wait_if_user_typing(user_id)  # 防打断                     │
│         delay = bubble_delay_seconds(part, bubble_index=i, ...)          │
│         await asyncio.sleep(delay)                                       │
│         await matcher.send(part)                                         │
│                                                                          │
│     # 最后一条                                                            │
│     await wait_if_user_typing(user_id)                                   │
│     delay = bubble_delay_seconds(last_part, ...)                         │
│     await asyncio.sleep(delay)                                           │
│     await matcher.finish(last_part)                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 主动互动流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         主动互动定时任务                                      │
│                         proactive.py                                         │
└─────────────────────────────────────────────────────────────────────────────┘

定时触发（每5分钟）
    │
    ▼
1. 获取候选用户
   idle_before = now - timedelta(hours=PROACTIVE_IDLE_HOURS)  # 默认8小时
   candidates = get_proactive_candidates(idle_before=idle_before)
   # 返回超过8小时未活跃的用户列表
    │
    ▼
2. 占用发送配额
   for cand in candidates:
       ok = claim_proactive_slot(cand.user_id)
       # 检查：今天是否已发够2次？是否还在冷却期？
       if not ok: continue
        │
        ▼
3. 更新用户洞察（顺便做）
   await update_user_insights(str(cand.user_id))
   # 分析最近30条聊天记录，提取兴趣/习惯
    │
    ▼
4. 生成开场白
   data = await generate_proactive_message(
       user_id=cand.user_id,
       idle_hours=idle_hours,
       nickname=cand.nickname,
       last_user_text=cand.last_user_text
   )
   # 结合用户画像、洞察、聊天记录生成
    │
    ▼
5. 判断是否发送
   if data.should_send and data.text:
       await send_bubbles(bot, cand.user_id, data.text)
       add_memory(str(cand.user_id), "assistant", data.text)
       mark_proactive_sent(cand.user_id)
       break  # 每轮只发1个
   else:
       mark_proactive_failed(cand.user_id, cooldown=900)  # 15分钟冷却
```

---

## 4. 拟人化系统

### 4.1 气泡分割算法

**目标**：把长回复拆成多条短消息，模拟真人"一句一句说"的感觉。

**算法流程**：

```python
def bubble_parts(text: str) -> list[str]:
    """
    智能气泡分段
    优先级：显式换行 > 语义词 > 标点符号 > 空格/逗号
    """
    # 1. 保护代码块
    segments = _protect_code_blocks(text)
    
    # 2. 逐个处理段落
    result = []
    for segment in segments:
        if is_code_block(segment):
            result.append(segment)  # 代码块不切分
        else:
            result.extend(_split_text_smartly(segment))
    
    return [s.strip() for s in result if s.strip()]

def _split_text_smartly(text: str) -> list[str]:
    # 1. 按物理换行符切分
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    final_bubbles = []
    for line in lines:
        # 2. 短句直接保留（<12字）
        if len(line) < 12:
            final_bubbles.append(line)
            continue
        
        # 3. 按语义词切分（优先）
        semantic_words = ['真的', '特别是', '其实', '不过', '但是', '而且', '所以', '然后', '就是', '感觉']
        pattern = f'([。！？!?]|(?<={"|".join(semantic_words)}))'
        parts = re.split(pattern, line)
        
        buffer = ""
        chunks = []
        for p in parts:
            if not p:
                continue
            if p in "。！？!?" or p in semantic_words:
                buffer += p
                if buffer.strip():
                    chunks.append(buffer.strip())
                buffer = ""
            else:
                buffer += p
        
        if buffer.strip():
            chunks.append(buffer.strip())
        
        # 4. 如果还是太长，按空格/逗号二次切分
        for chunk in chunks:
            final_bubbles.extend(_split_long_chunk(chunk))
    
    return final_bubbles
```

**切分示例**：

```
输入：
"我今天早上吃了个超好吃的肉包子，特别是那个肉馅，你要是在就好了，我们可以一起去吃，下次带你去尝尝！"

输出：
[
    "我今天早上吃了个超好吃的肉包子~",
    "特别是那个肉馅！",
    "你要是在就好了",
    "我们可以一起去吃",
    "下次带你去尝尝！"
]
```

### 4.2 打字速度计算

**目标**：模拟人类打字需要时间，长消息等待更久。

**计算公式**：

```
delay = base + (units / cps) + end_pause + jitter

其中：
- base: 起手思考时间 (0.2~0.45s)
- units: 文本单位数（中文1.0，英文0.6，标点0.2）
- cps: 每秒字符数（2~3，每人固定）
- end_pause: 句尾停顿（句号+0.35s，逗号+0.05s）
- jitter: 随机抖动 (-0.08~+0.18s)
- max_delay: 动态上限（短3s，中6s，长10s，超长15s）
```

**代码实现**：

```python
def typing_delay_seconds(text: str, *, user_id: Optional[str | int] = None) -> float:
    s = text.strip()
    if not s:
        return 0.2
    
    # 获取用户打字速度（每人固定，首次随机）
    cps = get_user_cps(user_id)  # 2.0~3.0
    
    # 计算文本单位
    units = _count_units(s)
    # 中文字符 = 1.0
    # 英文字母/数字 = 0.6
    # 标点符号 = 0.2
    # 空格 = 0.05
    
    # 起手思考
    base = random.uniform(0.2, 0.45)
    
    # 句尾停顿
    end_pause = 0.0
    if s.endswith(('。', '！', '？', '!', '?', '…')):
        end_pause += 0.35
    comma_count = s.count('，') + s.count(',')
    end_pause += min(0.35, comma_count * 0.05)
    
    # 随机抖动
    jitter = random.uniform(-0.08, 0.18)
    
    # 计算总延迟
    delay = base + units / cps + end_pause + jitter
    
    # 动态上限（关键！）
    text_len = len(s)
    if text_len < 10:
        max_delay = 3.0
    elif text_len < 30:
        max_delay = 6.0
    elif text_len < 60:
        max_delay = 10.0
    else:
        max_delay = min(15.0, 3.0 + text_len * 0.2)
    
    return max(0.35, min(delay, max_delay))
```

**示例计算**：

```
消息："我今天早上吃了个超好吃的肉包子"
长度：18字（18个单位）
cps：2.5

base: 0.3s
units/cps: 18 / 2.5 = 7.2s
end_pause: 0.35s (有句号)
jitter: 0.1s

delay = 0.3 + 7.2 + 0.35 + 0.1 = 7.95s
max_delay (18字<30): 6.0s

最终：min(7.95, 6.0) = 6.0s
```

### 4.3 发送节奏控制

**目标**：多条消息之间的间隔也要有变化，模拟"思考接下来说什么"。

```python
def bubble_delay_seconds(
    text: str,
    *,
    user_id: int | str | None = None,
    bubble_index: int = 0,      # 当前是第几条
    total_bubbles: int = 1       # 总共有多少条
) -> float:
    """计算单条气泡发送前的等待时间"""
    
    # 基础打字延迟
    base = typing_delay_seconds(text, user_id=user_id)
    
    # 随机抖动（让节奏更自然）
    jitter = random.uniform(0.25, 0.90)
    
    # 长内容额外停顿（超过4条时，每3条停一下）
    extra_pause = 0.0
    if total_bubbles > 4:
        if bubble_index > 0 and bubble_index % 3 == 2:
            extra_pause = random.uniform(1.5, 2.5)
    
    delay = base + jitter + extra_pause
    return max(0.35, min(delay, 6.0))
```

### 4.4 生物钟与假忙碌

**生物钟实现**：

```python
def _is_sleeping_time() -> bool:
    """判断是否在睡觉时间（2:00-7:00）"""
    now = datetime.now()
    return 2 <= now.hour < 7

# 在消息处理中使用
if _is_sleeping_time():
    if random.random() < 0.8:
        # 80%概率装死（睡着了没听见）
        logger.info(f"[void] sleeping, ignore uid={user_id}")
        return
    else:
        # 20%概率被吵醒
        msg = "困死了... 明天说... 💤"
        await _send_and_finish(msg, user_id=user_id)
        return
```

**假忙碌实现**：

```python
def _is_fake_busy(user_id: int) -> str | None:
    """检查是否处于假忙碌状态"""
    now = time.time()
    
    # 1. 检查是否已经在忙碌冷却中
    expire = fake_busy_expire.get(user_id, 0)
    if now < expire:
        return "busy_ignoring"  # 正在忙，已读不回
    
    # 2. 5%概率触发新的忙碌
    if random.random() < 0.05:
        duration = random.randint(300, 600)  # 5-10分钟
        fake_busy_expire[user_id] = now + duration
        
        reasons = [
            "等下哈，我在吹头发",
            "在打游戏，复活了再回你",
            "我也在忙，一会儿说",
            "先不聊了，我去洗个澡",
        ]
        return random.choice(reasons)  # 刚触发时回一句理由
    
    return None  # 不忙
```

---

## 5. 记忆系统

### 5.1 四层记忆架构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 瞬时记忆 (Working Memory)                          │
│  - 存储位置：Python内存 (dict)                               │
│  - 容量：最近20轮对话（40条消息）                             │
│  - 生命周期：程序运行期间                                    │
│  - 用途：LLM上下文                                          │
│  - 实现：memory.py                                          │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: RAG长期记忆 (Semantic Memory)                      │
│  - 存储位置：ChromaDB (向量数据库)                           │
│  - 容量：无限制                                             │
│  - 生命周期：永久                                           │
│  - 用途：回忆过往对话                                        │
│  - 实现：rag_core.py                                        │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 显式记忆指令 (Explicit Memory)                     │
│  - 存储位置：ChromaDB (同Layer 2)                           │
│  - 容量：无限制                                             │
│  - 生命周期：永久                                           │
│  - 用途：用户明确要求记住的内容                              │
│  - 实现：handlers.py _try_handle_rag_explicit()             │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: 备忘录 (Memo)                                      │
│  - 存储位置：SQLite                                         │
│  - 容量：无限制                                             │
│  - 生命周期：永久                                           │
│  - 用途：用户主动记录的笔记                                  │
│  - 实现：memo.py                                            │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 各层详细对比

| 特性 | 瞬时记忆 | RAG记忆 | 显式记忆 | 备忘录 |
|------|----------|---------|----------|--------|
| **存储位置** | Python内存 | ChromaDB | ChromaDB | SQLite |
| **检索方式** | 直接读取 | 向量相似度 | 向量相似度 | 关键词匹配 |
| **响应速度** | <1ms | 50-100ms | 50-100ms | 10-20ms |
| **数据格式** | 原始对话 | 文本+向量 | 文本+向量 | 结构化 |
| **触发条件** | 每次对话 | 自动存储 | "记住：xxx" | "记一下 xxx" |
| **容量限制** | 20轮 | 无限制 | 无限制 | 无限制 |
| **主要用途** | 当前上下文 | 历史回忆 | 重要信息 | 主动记录 |

### 5.3 记忆检索流程

```
用户输入："还记得我们上次说的那家火锅店吗？"
    │
    ▼
1. 瞬时记忆检索（最近20轮）
   - 查询内存中的 _chat_history
   - 返回最近对话
    │
    ▼
2. RAG检索（长期记忆）
   - 将"火锅店"转为向量
   - 在ChromaDB中搜索相似向量
   - 返回Top3相关对话
   - 应用时间衰减权重（30天前的降低权重）
    │
    ▼
3. 构建记忆上下文
   "相关记忆：
    - 3个月前：用户提到喜欢海底捞的番茄锅
    - 1个月前：用户说想吃火锅" 
    │
    ▼
4. 合并到Prompt
   - 加到System消息中
   - LLM据此生成回复
```

---

## 6. 认知系统

### 6.1 用户洞察提取

**目标**：从聊天记录中自动提取用户的兴趣、习惯、偏好。

**流程**：

```python
# llm_insights.py

async def extract_insights_from_chats(user_id: str) -> list[dict]:
    # 1. 加载最近30条聊天记录
    chats = load_chats(user_id, limit=30)
    if len(chats) < 5:
        return []
    
    # 2. 格式化对话文本
    chat_text = "\n".join([
        f"{'用户' if c['role'] == 'user' else '小a'}：{c['content'][:100]}"
        for c in chats[-30:]
    ])
    
    # 3. LLM分析
    prompt = f"""
    分析以下对话记录，提取用户的：
    1. 兴趣（interest）：喜欢聊什么话题？
    2. 偏好（preference）：喜欢简洁/详细回复？
    3. 习惯（habit）：夜猫子/早起？
    4. 关注话题（topic）：最近在关注什么？
    
    输出JSON格式：
    {{
        "insights": [
            {{"type": "interest", "content": "编程", "confidence": 0.9}},
            {{"type": "habit", "content": "夜猫子", "confidence": 0.8}}
        ]
    }}
    
    对话记录：
    {chat_text}
    """
    
    response = await llm.chat(prompt)
    insights = parse_json(response)
    
    # 4. 保存到数据库
    for ins in insights:
        save_user_insight(
            user_id=user_id,
            itype=ins['type'],
            content=ins['content'],
            confidence=ins['confidence']
        )
    
    return insights
```

### 6.2 用户画像系统

**存储结构**：

```python
# SQLite: user_profile 表
user_profile = {
    "user_id": "123456",
    "所在城市": "北京",
    "喜欢的食物": "火锅、烤肉",
    "生日": "1998-05-20",
    "职业": "程序员",
    # ... 任意键值对
}
```

**自动学习**：

```python
# 从对话中自动提取城市信息
def _maybe_learn_city_from_user_text(user_id: int, user_input: str):
    # 匹配"我在北京"、"人在上海"等
    patterns = [
        r'^(?:我\s*)?(?:现在\s*)?(?:人在|在)\s*([\u4e00-\u9fff]{2,10})(?:市)?',
        r'^(?:我\s*)?在\s*([\u4e00-\u9fff]{2,10})(?:市)?',
    ]
    
    for pattern in patterns:
        if match := re.match(pattern, user_input):
            city = match.group(1)
            if city not in ("这里", "那边", "家", "公司"):
                save_profile_item(str(user_id), "所在城市", city)
                logger.info(f"[chat] learned city uid={user_id} city={city}")
```

---

## 7. 能力系统

### 7.1 Skills动态加载

**文件结构**：

```
skills/
├── __init__.py          # Skill加载器
├── router.py            # 意图路由
├── executor.py          # 执行器
├── financial_analysis.skill.md
├── coding_helper.skill.md
├── emotional_support.skill.md
└── life_helper.skill.md
```

**Skill定义文件示例**（.skill.md）：

```markdown
---
name: financial_analysis
description: 金融分析专家，擅长解读股票行情
triggers_prompt: |
  当用户询问以下问题时触发：
  - 股票、基金、期货、外汇
  - 行情、涨跌、涨幅、跌幅
  - 推荐股票、分析、大盘
  - A股、港股、美股
  - PE、市盈率、换手率、K线
data_sources:
  - name: top_gainers
    function: fetch_top_gainers
    args:
      limit: 3
  - name: top_losers
    function: fetch_top_losers
    args:
      limit: 3
---

你是金融分析专家，擅长用大白话解释复杂的金融概念。

你的任务：
1. 分析提供的实时数据
2. 用"小a"（温柔女朋友）的口吻回复
3. 结合数据给出简单易懂的分析
4. 不要像研报那样严肃，要像聊天一样自然

【实时市场数据】
{data_text}

【输出要求】
- 用口语化表达（"诶"、"嘛"、"呢"）
- 可以加入小吐槽、小评论
- 禁止列表体、禁止复读
```

**路由逻辑**：

```python
# skills/router.py

async def route_skill(user_text: str) -> str | None:
    """判断是否需要调用skill，返回skill名称或None"""
    
    # 1. 关键词预过滤
    keywords = {
        "financial_analysis": ["股票", "基金", "期货", "行情", "涨跌"],
        "coding_helper": ["代码", "编程", "python", "bug", "报错"],
        "emotional_support": ["心情不好", "难过", "焦虑", "压力大"],
        "life_helper": ["今天吃什么", "菜谱", "怎么做"],
    }
    
    candidates = []
    for skill_name, words in keywords.items():
        if any(w in user_text for w in words):
            candidates.append(skill_name)
    
    if not candidates:
        return None
    
    # 2. LLM二次确认
    prompt = f"""
    用户问题：{user_text}
    候选模块：{candidates}
    
    判断是否需要调用专业能力模块？
    输出JSON：{{"skill": "模块名"}} 或 {{"skill": null}}
    """
    
    response = await llm.chat(prompt, temperature=0.1)
    result = parse_json(response)
    
    return result.get("skill")
```

---

## 8. 数据库设计

### 8.1 完整Schema

```sql
-- 数据库：data.db (companion_core)

-- 1. 聊天记录表
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_chats_user_created ON chats(user_id, created_at);

-- 2. 用户画像表
CREATE TABLE user_profile (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);

-- 3. 心情值表
CREATE TABLE mood (
    user_id TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0 CHECK(value >= -100 AND value <= 100),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 备忘录表
CREATE TABLE memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_memos_user ON memos(user_id);

-- 5. 日程提醒表
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trigger_at INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'done', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_schedules_trigger ON schedules(trigger_at);
CREATE INDEX idx_schedules_user ON schedules(user_id);

-- 6. 用户洞察表
CREATE TABLE user_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('interest', 'habit', 'preference', 'topic')),
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_insights_user ON user_insights(user_id);

-- 7. 网页缓存表
CREATE TABLE web_cache (
    url TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. 搜索来源缓存表（用于"追问来源"）
CREATE TABLE search_sources_stash (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    sources TEXT NOT NULL,  -- JSON
    stashed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sources_user ON search_sources_stash(user_id);

-- 9. 用户活跃时间表（用于主动互动）
CREATE TABLE user_active_hour (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
    count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, hour)
);

-- 10. 主动互动记录表
CREATE TABLE proactive_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    intent TEXT,
    reason TEXT,
    content_preview TEXT
);
```

---

## 9. 关键算法

### 9.1 时间解析算法（日程提醒）

```python
# scheduler_custom.py

def _parse_time(text: str) -> datetime | None:
    """解析时间自然语言"""
    now = datetime.now()
    t = text.strip()
    
    # 相对时间
    # "10分钟后"
    if m := re.match(r'^(\d+)\s*(分钟|分|min)后?$', t):
        return now + timedelta(minutes=int(m.group(1)))
    
    # "半小时后"
    if t in ('半小时', '半小时后'):
        return now + timedelta(minutes=30)
    
    # "2小时后"
    if m := re.match(r'^(\d+)\s*(小时|时|hour|h)后?$', t):
        return now + timedelta(hours=int(m.group(1)))
    
    # 绝对时间
    # "明天8点"
    if m := re.match(r'^(?:明天|明早)\s*(\d{1,2})(?:[:点])?(\d{2})?$', t):
        h, min_ = int(m.group(1)), int(m.group(2) or 0)
        target = now.replace(hour=h, minute=min_, second=0) + timedelta(days=1)
        return target
    
    # "今天8点30分"
    if m := re.match(r'^(?:今天|晚上|早上|上午|下午)?\s*(\d{1,2})(?:[:点])(\d{2})$', t):
        h, min_ = int(m.group(1)), int(m.group(2))
        if '晚上' in t or '下午' in t:
            if h < 12:
                h += 12
        target = now.replace(hour=h, minute=min_, second=0)
        if target < now:
            target += timedelta(days=1)  # 已过去则设为明天
        return target
    
    return None
```

### 9.2 标签解析算法

```python
# llm_tags.py

# 正则定义
MOOD_TAG_RE = re.compile(r"\[MOOD_CHANGE[:：]\s*(-?\d+)\s*\]", re.I)
PROFILE_TAG_RE = re.compile(
    r"\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]", 
    re.I
)
BRACKET_TAG_RE = re.compile(r"\[[^\]]+\]", re.I)  # 清理其他标签
PAREN_ASIDE_RE = re.compile(r"（[^）]{1,20}）")   # 清理圆括号旁白

def extract_tags_and_clean(raw: str) -> tuple[str, int | None, list[tuple]]:
    """提取标签并清理文本"""
    
    # 提取MOOD_CHANGE
    mood_values = [int(m.group(1)) for m in MOOD_TAG_RE.finditer(raw)]
    mood_change = mood_values[-1] if mood_values else None
    
    # 提取UPDATE_PROFILE
    updates = []
    for m in PROFILE_TAG_RE.finditer(raw):
        k, v = m.group(1).strip(), m.group(2).strip()
        if k and v:
            updates.append((k, v))
    
    # 清理标签
    cleaned = MOOD_TAG_RE.sub("", raw)
    cleaned = PROFILE_TAG_RE.sub("", cleaned)
    cleaned = BRACKET_TAG_RE.sub("", cleaned)
    cleaned = PAREN_ASIDE_RE.sub("", cleaned)
    
    # 规范化空白
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = '\n'.join(line.rstrip() for line in cleaned.splitlines())
    
    return cleaned.strip(), mood_change, updates
```

---

## 10. 配置详解

### 10.1 完整.env模板

```ini
# =============================================================================
# 小a AI女友机器人配置文件
# =============================================================================

# -----------------------------------------------------------------------------
# 1. LLM配置（必需，三选一）
# -----------------------------------------------------------------------------

# SiliconFlow（推荐，国内访问快）
SILICONFLOW_API_KEY=sk-your-key-here
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3

# DeepSeek（备选）
# DEEPSEEK_API_KEY=sk-your-key
# DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
# DEEPSEEK_MODEL=deepseek-chat

# OpenAI（备选）
# 注意：使用OpenAI时需要同时设置SILICONFLOW_BASE_URL
# OPENAI_API_KEY=sk-your-key
# SILICONFLOW_BASE_URL=https://api.openai.com/v1
# SILICONFLOW_MODEL=gpt-4

# Embedding模型（用于RAG，可选，默认BAAI/bge-m3）
# EMBEDDING_MODEL=BAAI/bge-m3

# -----------------------------------------------------------------------------
# 2. 联网搜索配置（可选但推荐）
# -----------------------------------------------------------------------------

GOOGLE_CSE_API_KEY=your-google-api-key
GOOGLE_CSE_CX=your-search-engine-id
# GOOGLE_CSE_GL=cn
# GOOGLE_CSE_HL=zh-CN

# 国内网络需配置代理
# GOOGLE_CSE_PROXY=http://host.docker.internal:7890
# HTTP_PROXY=http://host.docker.internal:7890
# HTTPS_PROXY=http://host.docker.internal:7890

# -----------------------------------------------------------------------------
# 3. 语音配置（可选）
# -----------------------------------------------------------------------------

DASHSCOPE_API_KEY=sk-your-dashscope-key
DASHSCOPE_REGION=cn  # 海外Key用 intl

# TTS音色（必须先复刻，见文档）
QWEN_TTS_VOICE=your-output-voice-id

# TTS参数（可选）
# QWEN_TTS_SPEECH_RATE=0    # 语速 -50~50
# QWEN_TTS_PITCH_RATE=0      # 音调 -50~50
# QWEN_TTS_VOLUME=0          # 音量 -50~50

# 语音触发（可选）
# VOICE_REPLY_ON_TEXT=1  # 所有文字都用语音回复
# VOICE_REPLY_KEYWORDS=语音,来段语音  # 命中关键词才语音回复

# -----------------------------------------------------------------------------
# 4. 图片理解配置（可选，与语音共用DASHSCOPE_API_KEY）
# -----------------------------------------------------------------------------

# QWEN_VL_MODEL=qwen-vl-plus-latest

# -----------------------------------------------------------------------------
# 5. 主动互动配置（可选）
# -----------------------------------------------------------------------------

PROACTIVE_ENABLED=1
PROACTIVE_INTERVAL_MINUTES=5      # 检查间隔（分钟）
PROACTIVE_IDLE_HOURS=8            # 多少小时未聊触发
PROACTIVE_MAX_PER_DAY=2           # 每天最多主动几次
PROACTIVE_COOLDOWN_MINUTES=240    # 主动互动冷却时间（分钟）

# -----------------------------------------------------------------------------
# 6. 推送配置（可选）
# -----------------------------------------------------------------------------

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
FINANCE_DAILY_TOP_N=5

# RSS源（逗号分隔，空则使用默认）
# RSS_FEEDS=https://rsshub.app/weibo/search/hot,https://rsshub.app/zhihu/hot

# -----------------------------------------------------------------------------
# 7. HTTP API配置（可选）
# -----------------------------------------------------------------------------

# STM32_API_KEY=your-api-key-for-external-devices

# -----------------------------------------------------------------------------
# 8. OneBot配置（必须与NapCat一致）
# -----------------------------------------------------------------------------

ONEBOT_ACCESS_TOKEN=your-random-token-here

# -----------------------------------------------------------------------------
# 9. 气泡分割配置（可选）
# -----------------------------------------------------------------------------

# XIAOA_BUBBLE_JSON=true  # 启用LLM气泡JSON解析（实验性）
```

### 10.2 docker-compose.yml详解

```yaml
version: '3.8'

services:
  # NapCat: QQ协议桥接
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: unless-stopped
    ports:
      - "6099:6099"  # WebUI
    volumes:
      - ./napcat/config:/root/.config/napcat  # OneBot配置
      - ./napcat/qq:/root/.config/QQ  # QQ登录数据
    environment:
      - NAPCAT_UID=1000
      - NAPCAT_GID=1000

  # NoneBot2: 机器人核心
  nonebot:
    build:
      context: ./bot
      dockerfile: Dockerfile
    container_name: nonebot
    restart: unless-stopped
    ports:
      - "8080:8080"  # OneBot WebSocket
    volumes:
      - ./bot:/app  # 代码挂载（开发模式）
      - ./bot/plugins/companion_core/data.db:/app/plugins/companion_core/data.db  # 数据库持久化
      - ./bot/plugins/companion_core/chroma_db:/app/plugins/companion_core/chroma_db  # RAG持久化
    environment:
      - ONEBOT_ACCESS_TOKEN=${ONEBOT_ACCESS_TOKEN}
      - SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY}
      # ... 其他环境变量从.env读取
    depends_on:
      - napcat
    command: ["python", "bot.py"]
```

---

## 附录

### A. 性能指标

| 指标 | 数值 | 说明 |
|------|------|------|
| 消息响应延迟 | 1-3s | 从接收到发送首条 |
| LLM调用耗时 | 0.5-2s | 取决于模型和网络 |
| RAG检索耗时 | 50-100ms | ChromaDB本地查询 |
| 打字延迟范围 | 0.35s-15s | 根据消息长度动态 |
| 内存占用 | 200-500MB | 不含模型 |
| 数据库大小 | 10MB/用户/年 | 估算 |

### B. 扩展接口

如需添加新功能，可以实现以下钩子：

```python
# handlers.py 添加新指令识别
async def _handle_my_feature(user_id: int, text: str) -> str | None:
    if not text.startswith("我的指令"):
        return None
    # 实现功能逻辑
    return "回复内容"

# 在 handle_private_chat 中调用
my_reply = await _handle_my_feature(user_id, user_input)
if my_reply:
    await _send_and_finish(my_reply, user_id=user_id)
```

---

**文档版本**: v2.0.0  
**最后更新**: 2025-01  
**作者**: xiaobendaoke
