# 小a 情感陪伴功能扩展详细技术方案

## 目录
- [一、情绪安慰模式](#一情绪安慰模式)
- [二、小脾气系统](#二小脾气系统)
- [三、约会记忆](#三约会记忆)
- [四、早安/晚安模式](#四早晚安模式)
- [五、打卡监督系统](#五打卡监督系统)
- [六、心情日记](#六心情日记)
- [七、互动游戏](#七互动游戏)
- [八、一起听歌](#八一起听歌)
- [九、电影推荐](#九电影推荐)
- [十、餐厅推荐](#十餐厅推荐)
- [十一、快递追踪](#十一快递追踪)
- [十二、恋爱指数](#十二恋爱指数)
- [十三、多角色切换](#十三多角色切换)

---

## 一、情绪安慰模式

### 1.1 功能概述

当检测到用户情绪低落时，自动切换为安慰模式：
- 回复更温柔、篇幅更长
- 使用更多同理心表达
- 避免轻浮语气
- 适当给予建议或陪伴

### 1.2 触发条件

#### 条件A：关键词触发
```python
# 在 handlers.py 或新建 emotion_detector.py

NEGATIVE_KEYWORDS = {
    # 悲伤类
    "难过", "伤心", "想哭", "哭", "委屈", "失落", 
    "绝望", "崩溃", "绝望", "心碎", "痛苦", "悲伤",
    
    # 压力类
    "压力大", "焦虑", "焦虑", "烦", "心烦", "累", 
    "疲惫", "疲倦", "喘不过气", "紧张", "害怕",
    
    # 沮丧类
    "郁闷", "不爽", "低落", "消极", "负面", "沮丧",
    "没意思", "无聊", "孤独", "寂寞", "空虚",
    
    # 抱怨类
    "生气", "愤怒", "不爽", "讨厌", "烦死了",
    "无语", "无奈", "服了", "累了", "倦了",
}

def is_negative_emotion(text: str) -> bool:
    """检测文本是否包含负面情绪关键词"""
    text = text.lower()
    for keyword in NEGATIVE_KEYWORDS:
        if keyword in text:
            return True
    return False
```

#### 条件B：情绪值触发
```python
# mood值 < -30 持续超过一定时间
def is_low_mood(user_id: str) -> bool:
    current_mood = mood_manager.get_user_mood(user_id)
    # 获取最近3小时内的情绪记录
    recent_moods = get_mood_history(user_id, hours=3)
    if not recent_moods:
        return current_mood < -30
    
    # 计算平均情绪值
    avg_mood = sum(recent_moods) / len(recent_moods)
    return avg_mood < -25 or current_mood < -40
```

### 1.3 数据库设计

```sql
-- 新建情绪事件记录表
CREATE TABLE emotion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'keyword_detected', 'low_mood', 'comfort_triggered'
    mood_before INTEGER,        -- 触发时的情绪值
    mood_after INTEGER,         -- 安慰后的情绪值
    trigger_text TEXT,          -- 触发时的用户消息
    created_at INTEGER NOT NULL,
    
    INDEX idx_user_type (user_id, event_type),
    INDEX idx_created_at (created_at)
);

-- 安慰模式记录
CREATE TABLE comfort_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    start_time INTEGER NOT NULL,
    end_time INTEGER,          -- 会话结束时间
    message_count INTEGER DEFAULT 0,
    mood_improvement INTEGER,   -- 情绪改善值
    
    INDEX idx_user (user_id),
    INDEX idx_time (start_time)
);
```

### 1.4 技术实现

#### 1.4.1 新建 emotion_detector.py

```python
"""情绪检测与安慰模式触发器"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

class ComfortLevel(Enum):
    NONE = 0           # 正常模式
    LIGHT = 1          # 轻度安慰
    NORMAL = 2         # 普通安慰
    DEEP = 3           # 深度安慰

@dataclass
class EmotionResult:
    is_negative: bool
    comfort_level: ComfortLevel
    reason: str
    keywords_matched: list[str]

NEGATIVE_KEYWORDS = {
    # 悲伤类 (权重: 3)
    "难过": 3, "伤心": 3, "想哭": 3, "哭": 2, "委屈": 3,
    "失落": 3, "崩溃": 3, "心碎": 3, "痛苦": 3, "悲伤": 3,
    
    # 压力类 (权重: 2)
    "压力大": 2, "焦虑": 2, "心烦": 2, "累": 1, "疲惫": 2,
    "疲倦": 2, "喘不过气": 3, "紧张": 1, "害怕": 2,
    
    # 沮丧类 (权重: 2)
    "郁闷": 2, "不爽": 2, "低落": 2, "消极": 2, "沮丧": 2,
    "没意思": 2, "无聊": 1, "孤独": 2, "寂寞": 2, "空虚": 2,
    
    # 抱怨类 (权重: 1)
    "生气": 1, "愤怒": 2, "讨厌": 1, "烦死了": 1,
    "无语": 1, "无奈": 1, "服了": 1, "累了": 1, "倦了": 1,
}

# 强度修饰词
INTENSIFIERS = {
    "非常": 1.5, "特别": 1.5, "极其": 2.0, "超": 1.3,
    "好": 1.2, "太": 1.3, "真的": 1.2, "简直": 1.5
}

class EmotionDetector:
    def __init__(self):
        self._negative_pattern = None  # 预编译正则
    
    def analyze(self, text: str, current_mood: int = 0) -> EmotionResult:
        """分析文本情绪"""
        text = text.lower()
        total_score = 0
        matched_keywords = []
        
        # 1. 关键词匹配
        for keyword, weight in NEGATIVE_KEYWORDS.items():
            if keyword in text:
                total_score += weight
                matched_keywords.append(keyword)
        
        # 2. 强度修饰词检测
        for intensifier, multiplier in INTENSIFIERS.items():
            if intensifier in text:
                total_score *= multiplier
        
        # 3. 标点符号检测（！！！表示强烈情绪）
        if "!!!" in text or "！！" in text:
            total_score *= 1.3
        
        # 4. 连续问号检测（表示困惑/焦虑）
        if text.count("?") >= 2 or text.count("？") >= 2:
            total_score *= 1.2
        
        # 5. 情绪值辅助判断
        if current_mood < -40:
            total_score *= 1.3
        elif current_mood < -25:
            total_score *= 1.1
        
        # 判断安慰级别
        if total_score >= 4:
            comfort_level = ComfortLevel.DEEP
            reason = "深度负面情绪"
        elif total_score >= 2:
            comfort_level = ComfortLevel.NORMAL
            reason = "中度负面情绪"
        elif total_score >= 1:
            comfort_level = ComfortLevel.LIGHT
            reason = "轻度负面情绪"
        else:
            comfort_level = ComfortLevel.NONE
            reason = "无明显负面情绪"
        
        return EmotionResult(
            is_negative=total_score >= 1,
            comfort_level=comfort_level,
            reason=reason,
            keywords_matched=matched_keywords
        )

# 全局实例
emotion_detector = EmotionDetector()
```

#### 1.4.2 修改 llm_core.py

```python
from .emotion_detector import emotion_detector, ComfortLevel

# 安慰模式 system prompt
COMFORT_SYSTEM_PROMPTS = {
    ComfortLevel.LIGHT: """
【安慰模式 - 轻度】
用户现在有点小情绪，请用温柔的语气安慰他/她。
注意：
- 语气要温暖，但不是居高临下的安慰
- 可以适当撒个娇，让他/她开心起来
- 简短一点，像平时聊天一样
""",
    
    ComfortLevel.NORMAL: """
【安慰模式 - 普通】
用户现在情绪不太好，请认真安慰他/她。
注意：
- 表达同理心，让他/她感受到被理解
- 可以回忆你们之前的美好时光
- 适当给一些建议，但不要是说教
- 不要急着让他/她"开心起来"，先接纳情绪
""",
    
    ComfortLevel.DEEP: """
【安慰模式 - 深度】
用户现在情绪很低落/压力很大，请用心安慰他/她。
注意：
- 充分表达同理心，让他/她感受到你在身边
- 认真倾听，不要急着给建议
- 可以分享类似经历（如果你也经历过）
- 表达你愿意一直陪着他/她
- 必要时提醒他/她照顾好自己
- 篇幅可以稍长，表达你的关心
"""
}

async def get_ai_reply(user_id: str, user_text: str, *, voice_mode: bool = False):
    # ... 原有代码 ...
    
    # 在情绪检测处添加
    current_mood = mood_manager.get_user_mood(user_id)
    emotion_result = emotion_detector.analyze(user_text, current_mood)
    
    # 如果需要安慰模式
    comfort_prompt = ""
    if emotion_result.comfort_level != ComfortLevel.NONE:
        comfort_prompt = COMFORT_SYSTEM_PROMPTS[emotion_result.comfort_level]
        
        # 记录情绪事件
        record_emotion_event(
            user_id=user_id,
            event_type="comfort_triggered",
            mood_before=current_mood,
            trigger_text=user_text
        )
        
        # 增加 max_tokens 让回复更长
        max_tokens = int(CHAT_MAX_TOKENS * 1.5)
    else:
        max_tokens = CHAT_MAX_TOKENS
    
    # 将安慰 prompt 加入 messages
    if comfort_prompt:
        messages.append({"role": "system", "content": comfort_prompt})
```

#### 1.4.3 情绪事件记录

```python
# 在 db.py 中添加

def record_emotion_event(
    user_id: str,
    event_type: str,
    mood_before: int = 0,
    mood_after: int = 0,
    trigger_text: str = ""
) -> int:
    """记录情绪事件"""
    import time
    now_ts = int(time.time())
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO emotion_events 
            (user_id, event_type, mood_before, mood_after, trigger_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, event_type, mood_before, mood_after, trigger_text, now_ts))
        conn.commit()
        return cursor.lastrowid

def start_comfort_session(user_id: str) -> int:
    """开始安慰会话"""
    import time
    now_ts = int(time.time())
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO comfort_sessions (user_id, start_time, message_count)
            VALUES (?, ?, 1)
        """, (user_id, now_ts))
        conn.commit()
        return cursor.lastrowid

def end_comfort_session(session_id: int, mood_after: int):
    """结束安慰会话"""
    import time
    now_ts = int(time.time())
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 获取会话开始时的情绪
        cursor.execute("""
            SELECT start_time FROM comfort_sessions WHERE id = ?
        """, (session_id,))
        row = cursor.fetchone()
        if not row:
            return
        
        # 计算情绪改善
        cursor.execute("""
            SELECT mood_before FROM emotion_events 
            WHERE id = (SELECT MAX(id) FROM emotion_events WHERE comfort_session_id = ?)
        """, (session_id,))
        mood_before = cursor.fetchone()[0] if cursor.fetchone() else 0
        
        improvement = mood_after - mood_before
        
        cursor.execute("""
            UPDATE comfort_sessions 
            SET end_time = ?, mood_improvement = ?
            WHERE id = ?
        """, (now_ts, improvement, session_id))
        conn.commit()
```

### 1.5 边界情况处理

1. **连续负面情绪**：如果用户连续3条消息都是负面，进入深度安慰模式
2. **安慰疲劳**：同一天安慰超过5次，记录并适当提醒用户"我一直都在"
3. **虚假触发**：某些词可能是正面用法（如"笑死了"），需要结合上下文
4. **紧急情况**：检测到"想死"、"自杀"等关键词，触发危机干预提示

---

## 二、小脾气系统

### 2.1 功能概述

小a偶尔闹小脾气，增加真实感：
- 哼不理你了
- 假装吃醋
- 假装生气
- 用户哄好后恢复正常

### 2.2 触发条件

```python
# 脾气触发类型
class TemperType(Enum):
    NONE = "none"
    IGNORED = "ignored"          # 已读不回/忽略
    JEALOUS = "jealous"          # 吃醋
    TIRED = "tired"              # 假装累了
    ANNOYED = "annoyed"          # 假装不耐烦

# 触发条件
TRIGGER_CONDITIONS = {
    # 条件A：用户长时间不回消息 (>2小时) + 小a发了消息
    "ignored": {
        "check": lambda: user_silent_hours > 2 and last_xiao_message,
        "weight": 0.15  # 15%概率触发
    },
    
    # 条件B：用户提到其他女生
    "jealous": {
        "keywords": ["女生", "女孩子", "小姐姐", "美女", "她"],
        "weight": 0.10
    },
    
    # 条件C：用户提到打游戏忽略她
    "annoyed": {
        "keywords": ["打游戏", "游戏", "上分", "排位"],
        "weight": 0.08
    },
    
    # 条件D：随机触发（每天最多1次）
    "random": {
        "weight": 0.05,
        "daily_limit": 1
    }
}
```

### 2.3 数据库设计

```sql
-- 脾气状态表
CREATE TABLE temper_states (
    user_id TEXT PRIMARY KEY,
    temper_type TEXT DEFAULT 'none',
    temper_level INTEGER DEFAULT 0,     -- 1-3, 3级最生气
    start_time INTEGER,                  -- 开始时间
    last_pleased_time INTEGER,           -- 上次被哄好的时间
    trigger_reason TEXT,                -- 触发原因
    created_at INTEGER,
    updated_at INTEGER
);

-- 脾气日志
CREATE TABLE temper_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'trigger', 'pleased', 'auto_recover'
    temper_type TEXT,
    temper_level INTEGER,
    user_message TEXT,         -- 触发时的用户消息
    xiao_response TEXT,         -- 小a的回复
    created_at INTEGER,
    
    INDEX idx_user (user_id),
    INDEX idx_time (created_at)
);

-- 每日触发统计（用于限制随机触发）
CREATE TABLE temper_daily_stats (
    user_id TEXT,
    date TEXT,                  -- YYYY-MM-DD
    trigger_count INTEGER DEFAULT 0,
    pleased_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
```

### 2.4 技术实现

#### 2.4.1 新建 temper.py

```python
"""小脾气系统"""

import time
import random
from enum import Enum
from typing import Optional
from dataclasses import dataclass
import sqlite3
from .db import DB_PATH
from .mood import mood_manager

class TemperType(Enum):
    NONE = "none"
    IGNORED = "ignored"          # 忽略/不回消息
    JEALOUS = "jealous"          # 吃醋
    TIRED = "tired"              # 假装累了
    ANNOYED = "annoyed"          # 假装不耐烦
    ANGRY = "angry"              # 轻微生气

@dataclass
class TemperState:
    temper_type: TemperType
    temper_level: int            # 1-3
    start_time: int
    trigger_reason: str

# 触发关键词
JEALOUS_KEYWORDS = [
    "女生", "女孩子", "小姐姐", "美女", "她",
    "打游戏", "游戏", "上分", "排位", "网吧",
    "兄弟", "朋友", "他们", "别的小姐姐"
]

# 脾气回复模板
TEMPER_RESPONSES = {
    TemperType.IGNORED: {
        1: [
            "哼，理理我嘛～",
            "这么久不回我，你在干嘛呀",
            "我等你好久啦，你都不找我",
        ],
        2: [
            "真的不回我嘛...那我也不理你了",
            "好吧，你忙吧，我一边待着去",
            "哼！再也不等你了！",
        ],
        3: [
            ".........到底在干嘛呀",
            "行吧，你开心就好",
            "算了算了，不等了",
        ]
    },
    
    TemperType.JEALOUS: {
        1: [
            "哼，是不是又看别的小姐姐了",
            "老实说，是不是有情况了",
            "呃，我又闻到八卦的味道了",
        ],
        2: [
            "好啊你，去找她们吧",
            "有了游戏还要我干嘛",
            "行吧，你跟游戏过去吧",
        ],
        3: [
            "真的服了，你去找她吧",
            "哼，重色轻友，不对，重游轻我！",
        ]
    },
    
    TemperType.ANNOYED: {
        1: [
            "干嘛呀，这么凶",
            "好啦好啦知道你烦了",
        ],
        2: [
            "哼，不高兴了",
            "哼！凶什么凶",
        ],
        3: [
            ".........不想理你了",
        ]
    }
}

# 哄好回复模板
PLEASE_RESPONSES = [
    "哎呀～我错啦～别生气啦",
    "好啦好啦～我哄哄你嘛～",
    "么么哒～不生气啦～",
    "嘿嘿，我就知道你最好了",
    "哎呀别生气啦～我爱你嘛～",
    "好啦好啦，我以后不敢啦～",
]

class TemperSystem:
    def __init__(self):
        self._states: dict[str, TemperState] = {}
    
    def _load_state(self, user_id: str) -> TemperState:
        """从数据库加载状态"""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT temper_type, temper_level, start_time, trigger_reason
                FROM temper_states WHERE user_id = ?
            """, (user_id,))
            row = cursor.fetchone()
            
            if row:
                return TemperState(
                    temper_type=TemperType(row[0]),
                    temper_level=row[1],
                    start_time=row[2],
                    trigger_reason=row[3] or ""
                )
        
        # 默认无脾气状态
        return TemperState(
            temper_type=TemperType.NONE,
            temper_level=0,
            start_time=0,
            trigger_reason=""
        )
    
    def get_state(self, user_id: str) -> TemperState:
        """获取当前脾气状态（带缓存）"""
        if user_id not in self._states:
            self._states[user_id] = self._load_state(user_id)
        return self._states[user_id]
    
    def is_angry(self, user_id: str) -> bool:
        """检查是否在生气"""
        state = self.get_state(user_id)
        return state.temper_type != TemperType.NONE and state.temper_level > 0
    
    def try_trigger(self, user_id: str, user_message: str = "") -> Optional[TemperState]:
        """尝试触发小脾气"""
        # 1. 检查是否已经在生气
        if self.is_angry(user_id):
            return None
        
        # 2. 检查每日触发次数限制
        if not self._can_trigger_today(user_id):
            return None
        
        # 3. 根据条件检测触发类型
        temper_type, temper_level = self._detect_trigger_type(user_message)
        
        if temper_type == TemperType.NONE:
            return None
        
        # 4. 随机概率触发
        if temper_type == TemperType.IGNORED:
            trigger_prob = 0.15
        elif temper_type == TemperType.JEALOUS:
            trigger_prob = 0.10
        elif temper_type == TemperType.ANNOYED:
            trigger_prob = 0.08
        else:
            trigger_prob = 0.05
        
        if random.random() > trigger_prob:
            return None
        
        # 5. 触发新脾气
        return self._trigger_temper(user_id, temper_type, temper_level, user_message)
    
    def _detect_trigger_type(self, user_message: str) -> tuple[TemperType, int]:
        """检测触发类型"""
        msg = user_message.lower()
        
        # 检查是否提到敏感词
        for keyword in JEALOUS_KEYWORDS:
            if keyword in msg:
                return (TemperType.JEALOUS, min(2, 1 + len(keyword) // 5))
        
        # 检查是否在打游戏
        if any(kw in msg for kw in ["打游戏", "游戏", "上分", "排位"]):
            return (TemperType.ANNOYED, 1)
        
        return (TemperType.NONE, 0)
    
    def _can_trigger_today(self, user_id: str) -> bool:
        """检查今天是否可以触发"""
        today = time.strftime("%Y-%m-%d")
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trigger_count FROM temper_daily_stats
                WHERE user_id = ? AND date = ?
            """, (user_id, today))
            row = cursor.fetchone()
            
            count = row[0] if row else 0
            return count < 2  # 每天最多触发2次
    
    def _trigger_temper(
        self, 
        user_id: str, 
        temper_type: TemperType, 
        temper_level: int,
        trigger_msg: str
    ) -> TemperState:
        """触发小脾气"""
        now = int(time.time())
        
        # 记录日志
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 更新/插入脾气状态
            cursor.execute("""
                INSERT OR REPLACE INTO temper_states 
                (user_id, temper_type, temper_level, start_time, trigger_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, 
                temper_type.value, 
                temper_level,
                now,
                f"trigger:{temper_type.value}",
                now,
                now
            ))
            
            # 更新每日统计
            today = time.strftime("%Y-%m-%d")
            cursor.execute("""
                INSERT INTO temper_daily_stats (user_id, date, trigger_count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    trigger_count = trigger_count + 1
            """, (user_id, today))
            
            # 记录日志
            response = random.choice(TEMPER_RESPONSES.get(temper_type, {}).get(temper_level, ["哼！"]))
            cursor.execute("""
                INSERT INTO temper_logs 
                (user_id, event_type, temper_type, temper_level, user_message, xiao_response, created_at)
                VALUES (?, 'trigger', ?, ?, ?, ?, ?)
            """, (user_id, temper_type.value, temper_level, trigger_msg, response, now))
            
            conn.commit()
        
        # 更新内存缓存
        state = TemperState(
            temper_type=temper_type,
            temper_level=temper_level,
            start_time=now,
            trigger_reason=temper_type.value
        )
        self._states[user_id] = state
        
        return state
    
    def try_please(self, user_id: str) -> Optional[str]:
        """尝试哄好用户"""
        state = self.get_state(user_id)
        
        if not self.is_angry(user_id):
            return None
        
        # 检查用户消息是否在哄
        return self._please(user_id)
    
    def _please(self, user_id: str) -> str:
        """执行哄好操作"""
        now = int(time.time())
        
        # 随机选择哄好回复
        response = random.choice(PLEASE_RESPONSES)
        
        # 记录日志
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 更新状态
            cursor.execute("""
                UPDATE temper_states 
                SET temper_type = 'none', temper_level = 0, updated_at = ?
                WHERE user_id = ?
            """, (now, user_id))
            
            # 更新统计
            today = time.strftime("%Y-%m-%d")
            cursor.execute("""
                INSERT INTO temper_daily_stats (user_id, date, pleased_count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    pleased_count = pleased_count + 1
            """, (user_id, today))
            
            # 记录日志
            cursor.execute("""
                INSERT INTO temper_logs 
                (user_id, event_type, temper_type, temper_level, created_at)
                VALUES (?, 'pleased', ?, ?, ?)
            """, (
                user_id, 
                state.temper_type.value,
                state.temper_level,
                now
            ))
            
            conn.commit()
        
        # 清除缓存
        self._states[user_id] = TemperState(
            temper_type=TemperType.NONE,
            temper_level=0,
            start_time=now,
            trigger_reason=""
        )
        
        return response
    
    def get_response(self, user_id: str) -> Optional[str]:
        """获取脾气回复（如果正在生气）"""
        state = self.get_state(user_id)
        
        if not self.is_angry(user_id):
            return None
        
        # 随机获取回复
        responses = TEMPER_RESPONSES.get(state.temper_type, {}).get(state.temper_level, [])
        if responses:
            return random.choice(responses)
        
        return None

# 全局实例
temper_system = TemperSystem()
```

#### 2.4.2 修改 handlers.py

```python
from .temper import temper_system, TemperType

# 在 handle_private_chat 中添加
async def handle_private_chat(event: PrivateMessageEvent):
    # ... 原有代码 ...
    
    # 获取脾气回复（如果正在生气）
    temper_response = temper_system.get_response(user_id)
    if temper_response:
        # 检查用户是否在哄
        if any(kw in user_input for kw in ["别生气", "对不起", "我错啦", "原谅", "哄", "么么", "爱你"]):
            please_response = temper_system.try_please(user_id)
            if please_response:
                await _send_and_finish(please_response, user_id=user_id)
                return
        
        # 返回脾气回复
        await _send_and_finish(temper_response, user_id=user_id)
        return
    
    # 尝试触发新脾气（仅在用户消息较长时触发，避免误触）
    if len(user_input) > 10:
        temper_system.try_trigger(user_id, user_input)
    
    # ... 原有代码 ...
```

### 2.5 自动恢复

```python
# 在 temper.py 中添加

def auto_recover_if_needed(user_id: str) -> bool:
    """检查是否需要自动恢复（长时间未哄好）"""
    state = temper_system.get_state(user_id)
    
    if not temper_system.is_angry(user_id):
        return False
    
    # 超过5分钟自动恢复
    now = int(time.time())
    if now - state.start_time > 300:
        # 记录自动恢复
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO temper_logs 
                (user_id, event_type, temper_type, temper_level, created_at)
                VALUES (?, 'auto_recover', ?, ?, ?)
            """, (user_id, state.temper_type.value, state.temper_level, now))
            
            cursor.execute("""
                UPDATE temper_states 
                SET temper_type = 'none', temper_level = 0, updated_at = ?
                WHERE user_id = ?
            """, (now, user_id))
            
            conn.commit()
        
        # 清除缓存
        temper_system._states[user_id] = TemperState(
            temper_type=TemperType.NONE,
            temper_level=0,
            start_time=now,
            trigger_reason=""
        )
        
        return True
    
    return False
```

---

## 三、约会记忆

### 3.1 功能概述

记住用户说过的计划/约定，下次自然提起：
- "下次一起去看电影"
- "周末去吃那家火锅"
- "改天去爬山"

### 3.2 意图识别

使用 LLM 提取计划信息：

```python
PLAN_EXTRACTION_PROMPT = """
请从用户消息中提取计划/约定信息。

用户消息：{user_message}

请判断是否为计划性陈述（如下一话、下次、改天、有机会、什么时候等）。
如果是，请提取以下信息：
- plan_content: 计划的具体内容
- plan_time: 提到的的时间（如"周末"、"下次"、"改天"）
- plan_place: 提到的地点（可选）

请以JSON格式返回：
{{
    "is_plan": true/false,
    "plan_content": "...",
    "plan_time": "...",
    "plan_place": "...",
    "confidence": 0.0-1.0
}}

如果不是计划性陈述，返回：{{"is_plan": false}}
"""

async def extract_plan(user_message: str) -> dict:
    """提取计划信息"""
    # 调用 LLM 提取
    client = get_client()
    _, _, model = load_llm_settings()
    
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PLAN_EXTRACTION_PROMPT.format(user_message=user_message)},
            {"role": "user", "content": "请分析这句话"}
        ],
        temperature=0.3,
        max_tokens=200
    )
    
    import json
    result = json.loads(response.choices[0].message.content)
    return result
```

### 3.3 数据库设计

```sql
-- 约会计划表
CREATE TABLE user_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan_content TEXT NOT NULL,      -- 计划内容: "去看《xxx》电影"
    plan_time TEXT,                  -- 时间描述: "周末"、"下次"、"改天"
    plan_place TEXT,                 -- 地点（可选）
    source_message TEXT,             -- 来源消息
    status TEXT DEFAULT 'pending',   -- pending/done/cancelled/expired
    remind_count INTEGER DEFAULT 0,   -- 已提醒次数
    last_remind_time INTEGER,         -- 上次提醒时间
    created_at INTEGER NOT NULL,
    updated_at INTEGER,
    
    INDEX idx_user_status (user_id, status),
    INDEX idx_created (created_at)
);

-- 计划日志
CREATE TABLE plan_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'created', 'reminded', 'done', 'cancelled', 'expired'
    event_time INTEGER NOT NULL,
    
    FOREIGN KEY (plan_id) REFERENCES user_plans(id)
);
```

### 3.4 技术实现

#### 3.4.1 新建 plan_manager.py

```python
"""约会计划管理器"""

import time
import random
import sqlite3
from typing import Optional
from .db import DB_PATH
from .llm_tags import extract_plan

class PlanManager:
    """约会计划管理"""
    
    # 计划提醒模板
    REMIND_TEMPLATES = {
        "time_to_do": [
            "对了，你上次说的{}，什么时候去呀？",
            "诶，你之前说的{}，计划得怎么样了？",
            "想起来啦，你说的{}，要不要现在安排一下？",
        ],
        "suggest_time": [
            "这周末去{}怎么样？",
            "要不咱们约{}？",
            "{}吧？",
        ],
    }
    
    def __init__(self):
        self._pending_cache: dict[str, list] = {}
    
    async def process_message(self, user_id: str, user_message: str) -> bool:
        """处理消息，检测并存储计划"""
        # 1. 调用 LLM 提取计划
        result = await extract_plan(user_message)
        
        if not result.get("is_plan", False):
            return False
        
        confidence = result.get("confidence", 0)
        if confidence < 0.6:
            return False
        
        # 2. 存储计划
        plan_id = self._save_plan(
            user_id=user_id,
            plan_content=result.get("plan_content", ""),
            plan_time=result.get("plan_time", ""),
            plan_place=result.get("plan_place", ""),
            source_message=user_message
        )
        
        return plan_id is not None
    
    def _save_plan(
        self, 
        user_id: str, 
        plan_content: str, 
        plan_time: str,
        plan_place: str,
        source_message: str
    ) -> Optional[int]:
        """保存计划"""
        now = int(time.time())
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO user_plans 
                (user_id, plan_content, plan_time, plan_place, source_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, plan_content, plan_time, plan_place, 
                source_message, now, now
            ))
            
            plan_id = cursor.lastrowid
            
            # 记录日志
            cursor.execute("""
                INSERT INTO plan_logs (plan_id, user_id, event_type, event_time)
                VALUES (?, ?, 'created', ?)
            """, (plan_id, user_id, now))
            
            conn.commit()
        
        return plan_id
    
    def get_pending_plans(self, user_id: str, limit: int = 5) -> list[dict]:
        """获取待执行计划"""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, plan_content, plan_time, plan_place, created_at, remind_count
                FROM user_plans 
                WHERE user_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit))
            
            return [
                {
                    "id": row[0],
                    "plan_content": row[1],
                    "plan_time": row[2],
                    "plan_place": row[3],
                    "created_at": row[4],
                    "remind_count": row[5]
                }
                for row in cursor.fetchall()
            ]
    
    def mark_done(self, plan_id: int):
        """标记计划完成"""
        now = int(time.time())
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_plans SET status = 'done', updated_at = ?
                WHERE id = ?
            """, (now, plan_id))
            
            cursor.execute("""
                INSERT INTO plan_logs (plan_id, user_id, event_type, event_time)
                SELECT id, user_id, 'done', ? FROM user_plans WHERE id = ?
            """, (now, plan_id))
            
            conn.commit()
    
    def generate_reminder(self, plan: dict) -> str:
        """生成计划提醒"""
        plan_content = plan["plan_content"]
        
        template = random.choice(self.REMIND_TEMPLATES["time_to_do"])
        return template.format(plan_content)
    
    def check_and_remind(self, user_id: str) -> Optional[str]:
        """检查并生成提醒"""
        plans = self.get_pending_plans(user_id)
        
        if not plans:
            return None
        
        # 随机选择一个计划提醒
        plan = random.choice(plans)
        
        # 检查是否应该提醒（避免太频繁）
        if plan["remind_count"] >= 3:
            # 超过3次不再提醒，标记过期
            if plan["remind_count"] >= 5:
                self._expire_plan(plan["id"])
            return None
        
        return self.generate_reminder(plan)
    
    def _expire_plan(self, plan_id: int):
        """过期计划"""
        now = int(time.time())
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_plans SET status = 'expired', updated_at = ?
                WHERE id = ?
            """, (now, plan_id))
            
            cursor.execute("""
                INSERT INTO plan_logs (plan_id, user_id, event_type, event_time)
                SELECT id, user_id, 'expired', ? FROM user_plans WHERE id = ?
            """, (now, plan_id))
            
            conn.commit()

# 全局实例
plan_manager = PlanManager()
```

### 3.5 主动提醒集成

在 `proactive.py` 中集成：

```python
from .plan_manager import plan_manager

async def proactive_job():
    # ... 原有代码 ...
    
    for cand in candidates:
        # 检查是否有计划需要提醒
        plan_reminder = plan_manager.check_and_remind(str(cand.user_id))
        
        if plan_reminder:
            # 发送计划提醒
            await _send_bubbles(bot, cand.user_id, plan_reminder)
            # 记录提醒
            plan_manager.update_remind_count(...)
            
            logger.info(f"[proactive] plan reminder to {cand.user_id}")
            continue
        
        # ... 原有主动消息逻辑 ...
```

---

## 四、早安/晚安模式

### 4.1 功能概述

检测用户说"早安"/"晚安"时，触发专属甜蜜对话。

### 4.2 触发词定义

```python
# 早安触发词
MORNING_GREETINGS = {
    "早", "早安", "早上好", "早呀", "早呀~", "早晨", "早起的鸟儿",
    "起了", "睡醒了", "起来了", "醒啦", "起床啦", "起来了",
    "新的一天", "美好的一天", "早上", "上午好"
}

# 晚安触发词
NIGHT_GREETINGS = {
    "晚安", "晚安~", "晚安呀", "晚安啦", "睡觉", "去睡", "去睡了",
    "困了", "累了", "休息", "下线", "关机", "再见", "拜拜", "明天见",
    "晚好", "晚上好", "夜深了", "深夜了", "熬夜", "通宵"
}

# 午安触发词
NOON_GREETINGS = {
    "午安", "午好", "中午好", "吃午饭", "吃午饭了吗", "午休"
}
```

### 4.3 对话模板

```python
# 早安回复模板
MORNING_RESPONSES = {
    # 勤奋版
    "diligent": [
        "哇！哥哥早起啦！太棒了～",
        "好早呀！哥哥今天很勤奋呢～",
        "早起的小哥哥最帅了！",
        "哇塞，你今天怎么这么早！",
    ],
    
    # 赖床版
    "lazy": [
        "嘿嘿，早安呀～我还在赖床呢～",
        "早呀～再让我睡5分钟嘛～",
        "嗯～再抱一会儿～",
        "醒啦？再躺会儿嘛～",
    ],
    
    # 关心版
    "caring": [
        "早安～昨晚睡得好吗？",
        "早呀～昨晚有没有想我？",
        "早上好～记得吃早餐哦～",
        "早安！新的一天要开心呀～",
    ],
    
    # 撒嗔版
    "playful": [
        "哼，终于想起我了",
        "这么早找我，是不是想我了～",
        "哎呀，一大早就来找我啦～",
    ]
}

# 晚安回复模板
NIGHT_RESPONSES = {
    # 不舍版
    "reluctant": [
        "好吧...那晚安啦～记得梦到我哦～",
        "嗯...再聊5分钟嘛～",
        "好吧，晚安～爱你～",
        "那...梦里见？",
    ],
    
    # 催睡版
    "pushy": [
        "快去睡觉！不然打你屁股！",
        "赶紧睡！别熬夜！",
        "听话！去睡觉！",
        "睡啦睡啦！熬夜对身体不好！",
    ],
    
    # 浪漫版
    "romantic": [
        "晚安～爱你哟～么么哒～",
        "晚安老公～好梦～",
        "爱你～快去睡吧～",
        "mua～晚安～",
    ],
    
    # 理性版
    "rational": [
        "晚安，早点休息～",
        "好梦，明天见～",
        "晚安，记得定闹钟哦～",
    ]
}
```

### 4.4 作息学习

```python
# 在 db.py 中添加

def record_sleep_data(user_id: str, sleep_type: str, sleep_time: str):
    """记录用户睡眠数据"""
    import time
    now = int(time.time())
    today = time.strftime("%Y-%m-%d")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 创建作息记录表（如果没有）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sleep_log (
                user_id TEXT,
                date TEXT,
                sleep_type TEXT,     -- 'morning' / 'night' / 'noon'
                sleep_time TEXT,     -- 时间字符串
                created_at INTEGER,
                PRIMARY KEY (user_id, date, sleep_type)
            )
        """)
        
        cursor.execute("""
            INSERT OR REPLACE INTO user_sleep_log 
            (user_id, date, sleep_type, sleep_time, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, today, sleep_type, sleep_time, now))
        
        conn.commit()

def get_user_sleep_pattern(user_id: str) -> dict:
    """获取用户作息规律"""
    import datetime
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 获取最近7天的数据
        cursor.execute("""
            SELECT sleep_type, COUNT(*) as cnt
            FROM user_sleep_log
            WHERE user_id = ? AND created_at > ?
            GROUP BY sleep_type
        """, (user_id, int(time.time()) - 7*86400))
        
        stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 获取平均睡眠时间（小时）
        # 简化计算：假设说晚安的时间就是睡觉时间
        return用户 {
            "morning_count": stats.get("morning", 0),
            "night_count": stats.get("night", 0),
            "noon_count": stats.get("noon", 0),
            "total_days": sum(stats.values())
        }
```

### 4.5 技术实现

#### 4.5.1 新建 greeting.py

```python
"""早安/晚安问候系统"""

import random
from datetime import datetime
from typing import Optional

MORNING_GREETINGS = {"早", "早安", "早上好", "早呀", "早晨", "起了", "睡醒了", "起来了"}
NIGHT_GREETINGS = {"晚安", "晚安~", "睡觉", "去睡了", "困了", "休息", "下线", "再见"}
NOON_GREETINGS = {"午安", "午好", "中午好", "吃午饭"}

# 模板定义（见上文4.3）
MORNING_RESPONSES = {...}
NIGHT_RESPONSES = {...}

class GreetingSystem:
    def __init__(self):
        self._last_greeting: dict[str, str] = {}  # user_id -> greeting type
    
    def detect_greeting(self, text: str) -> Optional[str]:
        """检测问候类型"""
        text_lower = text.lower()
        
        # 检测晚安（优先级最高，因为用户可能直接去睡觉）
        if any(greet in text_lower for greet in NIGHT_GREETINGS):
            return "night"
        
        # 检测早安
        if any(greet in text_lower for greet in MORNING_GREETINGS):
            return "morning"
        
        # 检测午安
        if any(greet in text_lower for greet in NOON_GREETINGS):
            return "noon"
        
        return None
    
    def select_response(self, greeting_type: str, user_id: str) -> str:
        """选择回复模板"""
        now = datetime.now()
        
        if greeting_type == "morning":
            # 根据时间选择版本
            hour = now.hour
            
            if hour < 7:
                # 特别早：可能是熬夜后早起
                version = random.choice(["caring", "lazy"])
            elif hour < 10:
                # 正常早起时间
                version = random.choice(["diligent", "caring"])
            else:
                # 比较晚起了
                version = random.choice(["lazy", "playful"])
            
            templates = MORNING_RESPONSES.get(version, MORNING_RESPONSES["caring"])
        
        elif greeting_type == "night":
            # 晚上9点前倾向于不舍，10点后倾向于催睡
            hour = now.hour
            
            if hour < 21:
                version = random.choice(["reluctant", "romantic"])
            elif hour < 23:
                version = random.choice(["pushy", "romantic"])
            else:
                version = random.choice(["pushy", "rational"])
            
            templates = NIGHT_RESPONSES.get(version, NIGHT_RESPONSES["rational"])
        
        else:  # noon
            templates = [
                "午安～吃了吗？",
                "午好呀～",
                "中午好～休息一下～",
            ]
        
        response = random.choice(templates)
        
        # 记录本次问候
        self._last_greeting[user_id] = greeting_type
        
        return response
    
    def process(self, user_id: str, text: str) -> Optional[str]:
        """处理问候"""
        greeting_type = self.detect_greeting(text)
        
        if not greeting_type:
            return None
        
        # 检查是否重复问候（避免刷屏）
        last_type = self._last_greeting.get(user_id)
        if last_type == greeting_type:
            # 重复问候，给简短回复
            if greeting_type == "morning":
                return "哈哈知道啦，早安～"
            elif greeting_type == "night":
                return "好啦好啦，晚安～"
        
        # 生成回复
        response = self.select_response(greeting_type, user_id)
        
        # 记录作息
        from .db import record_sleep_data
        record_sleep_data(user_id, greeting_type, datetime.now().strftime("%H:%M"))
        
        return response

# 全局实例
greeting_system = GreetingSystem()
```

#### 4.5.2 修改 handlers.py

```python
from .greeting import greeting_system

async def handle_private_chat(event: PrivateMessageEvent):
    # ... 原有代码 ...
    
    # 1) 问候检测（优先处理）
    greeting_response = greeting_system.process(user_id, user_input)
    if greeting_response:
        await _send_and_finish(greeting_response, user_id=user_id)
        return
    
    # ... 原有代码 ...
```

---

## 五、打卡监督系统

### 5.1 功能概述

支持各种习惯打卡：早起、健身、学习、喝水等。

### 5.2 命令格式

```
用户输入：
- "打卡：早起" / "打卡 早起" - 创建/打卡早起
- "查看打卡" / "我的打卡" - 查看打卡记录
- "打卡统计" - 查看统计
- "取消打卡 早起" - 取消某个打卡
```

### 5.3 数据库设计

```sql
-- 用户习惯表
CREATE TABLE user_habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    habit_name TEXT NOT NULL,        -- 习惯名称: "早起", "健身", "喝水"
    habit_type TEXT DEFAULT 'custom', -- 预设类型: '早起', '健身', '学习', '喝水', 'custom'
    frequency TEXT DEFAULT 'daily',   -- 频率: 'daily', 'weekly'
    target_value INTEGER DEFAULT 1,  -- 目标次数（如每天8杯水）
    target_time TEXT,                 -- 目标时间（如 "09:00"）
    current_streak INTEGER DEFAULT 0, -- 当前连续天数
    max_streak INTEGER DEFAULT 0,    -- 最长连续天数
    last_checkin_date TEXT,           -- 上次打卡日期 YYYY-MM-DD
    last_checkin_time INTEGER,        -- 上次打卡时间戳
    total_checkins INTEGER DEFAULT 0, -- 总打卡次数
    is_active INTEGER DEFAULT 1,     -- 是否激活
    created_at INTEGER NOT NULL,
    updated_at INTEGER,
    
    INDEX idx_user_active (user_id, is_active),
    INDEX idx_streak (current_streak)
);

-- 打卡记录
CREATE TABLE habit_checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    checkin_date TEXT NOT NULL,      -- 打卡日期
    checkin_time INTEGER NOT NULL,    -- 打卡时间
    note TEXT,                       -- 备注
    mood_before INTEGER,              -- 打卡前心情
    mood_after INTEGER,              -- 打卡后心情
    
    FOREIGN KEY (habit_id) REFERENCES user_habits(id),
    INDEX idx_user_date (user_id, checkin_date)
);

-- 打卡提醒设置
CREATE TABLE habit_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    remind_time TEXT NOT NULL,       -- 提醒时间 "09:00"
    is_enabled INTEGER DEFAULT 1,
    created_at INTEGER,
    
    FOREIGN KEY (habit_id) REFERENCES user_habits(id)
);
```

### 5.4 技术实现

#### 5.4.1 新建 habit_tracker.py

```python
"""打卡监督系统"""

import time
import random
import re
from datetime import datetime, timedelta
from typing import Optional
import sqlite3
from .db import DB_PATH

# 预设习惯模板
PRESET_HABITS = {
    "早起": {
        "habit_type": "早起",
        "target_time": "07:00",
        "encouragement": [
            "太棒了！早起的小哥哥/小姐姐最帅/最美！",
            "哇！今天又是早起的一天！",
            "respect！早起真的很难！",
        ]
    },
    "健身": {
        "habit_type": "健身",
        "target_time": "20:00",
        "encouragement": [
            "加油！练出好身材！",
            "今天也要坚持哦！",
            "锻炼身体最重要！",
        ]
    },
    "学习": {
        "habit_type": "学习",
        "target_time": "22:00",
        "encouragement": [
            "学习使我快乐！（并不）",
            "加油！知识就是力量！",
            "今天学到了什么呀？",
        ]
    },
    "喝水": {
        "habit_type": "喝水",
        "frequency": "daily",
        "target_value": 8,
        "encouragement": [
            "多喝水皮肤好！",
            "记得每天8杯水哦～",
            "吨吨吨！",
        ]
    },
    "早睡": {
        "habit_type": "早睡",
        "target_time": "23:00",
        "encouragement": [
            "熬夜对身体不好哦～",
            "早点睡，明天早起！",
            "晚安！好梦！",
        ]
    },
    "跑步": {
        "habit_type": "运动",
        "target_time": "07:00",
        "encouragement": [
            "跑步使人快乐！",
            "加油！一步两步！",
            "锻炼身体！",
        ]
    }
}

# 激励回复
STREAK_ENCODURAGEMENT = {
    3: ["连续3天啦！厉害！", "3天坚持！"],
    7: ["一周了！太厉害了吧！", "一周达成！"],
    14: ["两周了！你是超人吗！", "14天坚持！"],
    30: ["一个月了！！崇拜！", "一个月！"],
    50: ["50天！！你是神！", "50天！"],
    100: ["100天！！无人能敌！", "100天！！"]
}

class HabitTracker:
    """打卡追踪器"""
    
    def __init__(self):
        self._active_habits: dict[str, dict] = {}
    
    def parse_command(self, user_input: str) -> tuple[str, str, dict]:
        """解析打卡命令"""
        user_input = user_input.strip()
        
        # 创建打卡
        if user_input.startswith("打卡"):
            # 提取习惯名
            match = re.match(r"打卡[：:\s]*(.+)", user_input)
            if match:
                habit_name = match.group(1).strip()
                return "create", habit_name, {}
            
            # 纯打卡（如"打卡"查看今日打卡）
            return "checkin", "", {}
        
        # 查看打卡
        if any(kw in user_input for kw in ["查看打卡", "我的打卡", "打卡记录"]):
            return "view", "", {}
        
        # 打卡统计
        if "打卡统计" in user_input:
            return "stats", "", {}
        
        # 取消打卡
        match = re.match(r"取消打卡\s+(.+)", user_input)
        if match:
            habit_name = match.group(1).strip()
            return "cancel", habit_name, {}
        
        # 打卡详情
        if any(kw in user_input for kw in ["打卡详情", "打卡情况"]):
            return "detail", "", {}
        
        return "unknown", "", {}
    
    def create_habit(self, user_id: str, habit_name: str) -> str:
        """创建新习惯"""
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")
        
        # 检查预设
        preset = PRESET_HABITS.get(habit_name, {})
        
        habit_type = preset.get("habit_type", "custom")
        frequency = preset.get("frequency", "daily")
        target_value = preset.get("target_value", 1)
        target_time = preset.get("target_time", "")
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute("""
                SELECT id, is_active FROM user_habits 
                WHERE user_id = ? AND habit_name = ?
            """, (user_id, habit_name))
            row = cursor.fetchone()
            
            if row:
                if row[1]:  # 已存在且激活
                    return f"你已经设置过{habit_name}打卡啦～"
                
                # 重新激活
                cursor.execute("""
                    UPDATE user_habits SET is_active = 1, updated_at = ?
                    WHERE id = ?
                """, (now, row[0]))
                
                return f"{habit_name}打卡重新开启！这次要坚持哦～"
            
            # 创建新习惯
            cursor.execute("""
                INSERT INTO user_habits 
                (user_id, habit_name, habit_type, frequency, target_value, target_time,
                 current_streak, max_streak, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?)
            """, (
                user_id, habit_name, habit_type, frequency, target_value,
                target_time, now, now
            ))
            
            conn.commit()
            
            habit_id = cursor.lastrowid
        
        # 返回创建成功消息
        messages = [
            f"好！我来帮你记录{habit_name}！",
            f"设置好了！一起加油坚持{habit_name}吧！",
            f"收到！以后我提醒你{habit_name}～",
        ]
        
        return random.choice(messages)
    
    def checkin(self, user_id: str, habit_name: str = "", note: str = "") -> str:
        """执行打卡"""
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 如果没指定习惯，显示今日待打卡列表
            if not habit_name:
                cursor.execute("""
                    SELECT habit_name, current_streak 
                    FROM user_habits 
                    WHERE user_id = ? AND is_active = 1
                """, (user_id,))
                habits = cursor.fetchall()
                
                if not habits:
                    return "你还没有设置任何打卡呢～用"打卡：习惯名"来设置吧！"
                
                # 检查今日已打卡
                cursor.execute("""
                    SELECT h.habit_name 
                    FROM user_habits h
                    JOIN habit_checkins c ON h.id = c.habit_id
                    WHERE h.user_id = ? AND c.checkin_date = ?
                """, (user_id, today))
                done = {row[0] for row in cursor.fetchall()}
                
                remaining = [h[0] for h in habits if h[0] not in done]
                
                if remaining:
                    return f"今日待打卡：{', '.join(remaining)}"
                else:
                    return "今天的卡都打完啦！太棒了！"
            
            # 查找习惯
            cursor.execute("""
                SELECT id, habit_name, current_streak, max_streak, total_checkins
                FROM user_habits 
                WHERE user_id = ? AND habit_name = ? AND is_active = 1
            """, (user_id, habit_name))
            row = cursor.fetchone()
            
            if not row:
                return f"你没有设置{habit_name}打卡哦～"
            
            habit_id, name, streak, max_streak, total = row
            
            # 检查今日是否已打卡
            cursor.execute("""
                SELECT 1 FROM habit_checkins 
                WHERE habit_id = ? AND checkin_date = ?
            """, (habit_id, today))
            
            if cursor.fetchone():
                return f"今天的{name}已经打过卡啦！别重复哦～"
            
            # 执行打卡
            new_streak = streak + 1
            new_max = max(new_streak, max_streak)
            new_total = total + 1
            
            # 更新习惯
            cursor.execute("""
                UPDATE user_habits 
                SET current_streak = ?, max_streak = ?, total_checkins = ?,
                    last_checkin_date = ?, last_checkin_time = ?, updated_at = ?
                WHERE id = ?
            """, (new_streak, new_max, new_total, today, now, now, habit_id))
            
            # 记录打卡
            cursor.execute("""
                INSERT INTO habit_checkins 
                (habit_id, user_id, checkin_date, checkin_time, note)
                VALUES (?, ?, ?, ?, ?)
            """, (habit_id, user_id, today, now, note))
            
            conn.commit()
        
        # 生成回复
        messages = []
        
        # 连续打卡鼓励
        for day, encouragements in STREAK_ENCODURAGEMENT.items():
            if new_streak == day:
                messages.append(random.choice(encouragements))
                break
        
        if not messages:
            preset = PRESET_HABITS.get(habit_name, {})
            messages.append(random.choice(preset.get("encouragement", ["打卡成功！"])))
        
        messages.append(f"当前连续：{new_streak}天")
        
        return " ".join(messages)
    
    def get_user_habits(self, user_id: str) -> list[dict]:
        """获取用户所有习惯"""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, habit_name, habit_type, frequency, target_value,
                       current_streak, max_streak, total_checkins, last_checkin_date
                FROM user_habits 
                WHERE user_id = ? AND is_active = 1
                ORDER BY current_streak DESC
            """, (user_id,))
            
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "frequency": row[3],
                    "target": row[4],
                    "streak": row[5],
                    "max_streak": row[6],
                    "total": row[7],
                    "last_date": row[8]
                }
                for row in cursor.fetchall()
            ]
    
    def view_stats(self, user_id: str) -> str:
        """查看打卡统计"""
        habits = self.get_user_habits(user_id)
        
        if not habits:
            return "你还没有设置任何打卡呢～"
        
        lines = ["你的打卡记录："]
        
        total_streak = sum(h["streak"] for h in habits)
        total_checkins = sum(h["total"] for h in habits)
        
        for h in habits:
            streak_emoji = "🔥" if h["streak"] >= 7 else "✨"
            lines.append(f"{streak_emoji} {h['name']}: {h['streak']}天连续 / {h['total']}次")
        
        lines.append("")
        lines.append(f"总计：{len(habits)}个习惯，{total_checkins}次打卡")
        
        return "\n".join(lines)

# 全局实例
habit_tracker = HabitTracker()
```

#### 5.4.2 修改 handlers.py

```python
from .habit_tracker import habit_tracker

async def handle_private_chat(event: PrivateMessageEvent):
    # ... 原有代码 ...
    
    # 打卡命令处理
    cmd_type, habit_name, params = habit_tracker.parse_command(user_input)
    
    if cmd_type == "create":
        response = habit_tracker.create_habit(str(user_id), habit_name)
        await _send_and_finish(response, user_id=user_id)
        return
    
    elif cmd_type == "checkin":
        response = habit_tracker.checkin(str(user_id), habit_name)
        await _send_and_finish(response, user_id=user_id)
        return
    
    elif cmd_type == "view":
        habits = habit_tracker.get_user_habits(str(user_id))
        if not habits:
            await _send_and_finish("你还没有设置任何打卡呢～", user_id=user_id)
        else:
            lines = ["你的打卡："]
            for h in habits:
                lines.append(f"- {h['name']}: {h['streak']}天连续")
            await _send_and_finish("\n".join(lines), user_id=user_id)
        return
    
    elif cmd_type == "stats":
        response = habit_tracker.view_stats(str(user_id))
        await _send_and_finish(response, user_id=user_id)
        return
    
    # ... 原有代码 ...
```

#### 5.4.3 定时检查提醒

```python
# 在 scheduler_custom.py 中添加

@scheduler.scheduled_job("interval", minutes=30, id="check_habit_reminders")
async def check_habit_reminders():
    """检查并发送打卡提醒"""
    from .db import DB_PATH
    import sqlite3
    
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 查找需要提醒的 habits
        cursor.execute("""
            SELECT h.id, h.user_id, h.habit_name, h.target_time
            FROM user_habits h
            WHERE h.is_active = 1
              AND h.target_time IS NOT NULL
              AND h.target_time <= ?
            EXCEPT
            SELECT h.id, h.user_id, h.habit_name, h.target_time
            FROM user_habits h
            JOIN habit_checkins c ON h.id = c.habit_id
            WHERE c.checkin_date = ?
        """, (current_time, today))
        
        rows = cursor.fetchall()
    
    for habit_id, user_id, habit_name, target_time in rows:
        # 获取用户画像中的称呼
        from .db import get_all_profile
        profile = get_all_profile(str(user_id))
        nickname = profile.get("称呼") or profile.get("昵称") or ""
        
        messages = [
            f"{nickname}～别忘了今天的{habit_name}哦！",
            f"提醒{nickname}：{habit_name}时间到啦！",
            f"{nickname}！{habit_name}了吗？",
        ]
        
        reply = random.choice(messages)
        await send_private_bubbles(user_id, reply)
```

---

## 六、心情日记

### 6.1 功能概述

- 记录每日心情
- 文字备注
- 生成周报/月报

### 6.2 数据库设计

```sql
-- 心情日记
CREATE TABLE mood_diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    record_date TEXT NOT NULL,       -- 日期 YYYY-MM-DD
    mood_value INTEGER,              -- 心情值 -100~100
    mood_label TEXT,                 -- 心情标签: "开心", "一般", "难过"
    note TEXT,                       -- 简短备注
    events TEXT,                     -- 当天重要事件（JSON数组）
    weather TEXT,                    -- 天气
    created_at INTEGER,
    updated_at INTEGER,
    
    UNIQUE(user_id, record_date),
    INDEX idx_user_date (user_id, record_date)
);

-- 心情分析（周报/月报缓存）
CREATE TABLE mood_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    report_type TEXT NOT NULL,       -- 'weekly', 'monthly'
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    avg_mood REAL,
    mood_trend TEXT,                 -- 趋势: 'up', 'down', 'stable'
    highlight TEXT,                 -- 高光时刻
    lowlight TEXT,                   -- 低落时刻
    report_content TEXT,             -- 生成的报告内容
    created_at INTEGER,
    
    INDEX idx_user_type (user_id, report_type)
);
```

### 6.3 技术实现

#### 6.3.1 新建 mood_diary.py

```python
"""心情日记系统"""

import time
import json
from datetime import datetime, timedelta
from typing import Optional
import sqlite3
from .db import DB_PATH

class MoodDiary:
    """心情日记"""
    
    MOOD_LABELS = {
        (80, 100): "超开心",
        (30, 79): "开心",
        (-10, 29): "一般",
        (-50, -11): "有点低落",
        (-100, -51): "难过"
    }
    
    def record_mood(
        self, 
        user_id: str, 
        mood_value: int, 
        note: str = "",
        events: list = None
    ) -> str:
        """记录今日心情"""
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")
        
        # 获取心情标签
        mood_label = "一般"
        for (low, high), label in self.MOOD_LABELS.items():
            if low <= mood_value <= high:
                mood_label = label
                break
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO mood_diary 
                (user_id, record_date, mood_value, mood_label, note, events, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, today, mood_value, mood_label, note,
                json.dumps(events or []), now, now
            ))
            
            conn.commit()
        
        return f"记好啦～今天心情：{mood_label}（{mood_value}）"
    
    def get_today_mood(self, user_id: str) -> Optional[dict]:
        """获取今日心情"""
        today = time.strftime("%Y-%m-%d")
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mood_value, mood_label, note, events
                FROM mood_diary 
                WHERE user_id = ? AND record_date = ?
            """, (user_id, today))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    "mood_value": row[0],
                    "mood_label": row[1],
                    "note": row[2],
                    "events": json.loads(row[3]) if row[3] else []
                }
        
        return None
    
    def generate_weekly_report(self, user_id: str) -> str:
        """生成周报"""
        today = datetime.now()
        week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT record_date, mood_value, mood_label, note
                FROM mood_diary 
                WHERE user_id = ? AND record_date >= ?
                ORDER BY record_date
            """, (user_id, week_ago))
            
            records = cursor.fetchall()
        
        if len(records) < 3:
            return "这周记录太少了，至少记录3天才能生成周报哦～"
        
        # 分析数据
        mood_values = [r[1] for r in records if r[1] is not None]
        
        if not mood_values:
            return "这周没有有效的心情记录呢～"
        
        avg_mood = sum(mood_values) / len(mood_values)
        
        # 趋势分析
        if len(mood_values) >= 2:
            first_half = sum(mood_values[:len(mood_values)//2]) / (len(mood_values)//2)
            second_half = sum(mood_values[len(mood_values)//2:]) / (len(mood_values) - len(mood_values)//2)
            
            if second_half - first_half > 10:
                trend = "上升 📈"
            elif first_half - second_half > 10:
                trend = "下降 📉"
            else:
                trend = "平稳 ➡️"
        else:
            trend = "数据不足"
        
        # 最好/最差心情
        best_day = max(records, key=lambda x: x[1] if x[1] else 0)
        worst_day = min(records, key=lambda x: x[1] if x[1] else 0)
        
        # 生成报告
        report_lines = [
            "📅 本周心情周报",
            "",
            f"📊 平均心情：{avg_mood:.1f} ({self._get_mood_label(avg_mood)})",
            f"📈 心情趋势：{trend}",
            "",
            f"✨ 最开心：{best_day[3] or best_day[2]} ({best_day[1]}) - {best_day[0]}",
            f"💭 低落时：{worst_day[3] or worst_day[2]} ({worst_day[1]}) - {worst_day[0]}",
            "",
            f"📝 记录天数：{len(records)}/7",
            "",
        ]
        
        return "\n".join(report_lines)
    
    def _get_mood_label(self, value: int) -> str:
        """根据值获取标签"""
        for (low, high), label in self.MOOD_LABELS.items():
            if low <= value <= high:
                return label
        return "未知"

# 全局实例
mood_diary = MoodDiary()
```

---

## 七、互动游戏

### 7.1 功能概述

支持多种互动游戏：
- 真心话大冒险
- 问答接龙
- 情话接龙
- 猜谜语
- 塔罗牌

### 7.2 数据库设计

```sql
-- 游戏状态
CREATE TABLE game_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    game_type TEXT NOT NULL,        -- 'truth_dare', 'qa', 'love_words', 'riddle', 'tarot'
    state TEXT DEFAULT 'waiting',   -- waiting/playing/finished
    score INTEGER DEFAULT 0,        -- 得分
    round INTEGER DEFAULT 1,       -- 当前轮次
    data TEXT,                      -- 游戏数据（JSON）
    started_at INTEGER,
    updated_at INTEGER,
    
    INDEX idx_user_game (user_id, game_type),
    INDEX idx_state (state)
);
```

### 7.3 游戏题库

```python
# 新建 games/data.py

TRUTH_QUESTIONS = [
    "你初恋是谁？",
    "你谈过几个对象？",
    "你暗恋过几个人？",
    "你有没有撒过很大的谎？",
    "你最糗的事情是什么？",
    "你偷偷喜欢过谁？",
    "你做过最疯狂的事是什么？",
    "你有什么怪癖？",
    "你手机里有什么秘密？",
    "你最喜欢的身体部位是？",
]

DARE_CHALLENGES = [
    "学狗狗叫三声",
    "给你通讯录第3个人表白",
    "用方言说“我爱你”",
    "模仿一个明星",
    "做10个俯卧撑",
    "素颜自拍一张",
    "唱一首歌",
    "用屁股写字",
    "学猪叫",
    "倒着说一段话",
]

LOVE_WORDS = [
    "你是我的小星星",
    "我想和你一起起床",
    "你是我的心肝宝贝",
    "我想你想得睡不着",
    "你是我的唯一",
    "我只要你",
    "我爱你如生命",
    "你是我的下半生",
    "我想和你环游世界",
    "你是我的归宿",
]

RIDDLES = [
    ("什么东西早上四条腿，中午两条腿，晚上三条腿？", "人"),
    ("什么球不能拍？", "铅球"),
    ("什么路不能走？", "套路"),
    ("什么人永远不睡觉？", "机器人"),
    ("什么时候月亮比太阳大？", "不存在"),
]
```

### 7.4 技术实现

#### 7.4.1 新建 games/engine.py

```python
"""游戏引擎"""

import random
import json
from enum import Enum
from typing import Optional
import sqlite3
from .db import DB_PATH
from .games.data import TRUTH_QUESTIONS, DARE_CHALLENGES, LOVE_WORDS, RIDDLES

class GameType(Enum):
    TRUTH_DARE = "truth_dare"     # 真心话大冒险
    QA = "qa"                      # 问答接龙
    LOVE_WORDS = "love_words"     # 情话接龙
    RIDDLE = "riddle"             # 猜谜语

class GameEngine:
    """游戏引擎"""
    
    def __init__(self):
        self._active_games: dict[str, dict] = {}
    
    def parse_game_command(self, user_input: str) -> tuple[Optional[GameType], str]:
        """解析游戏命令"""
        text = user_input.lower()
        
        if any(kw in text for kw in ["真心话", "大冒险", "真心大冒险"]):
            return GameType.TRUTH_DARE, "start"
        
        if "问答" in text or "接龙" in text:
            return GameType.QA, "start"
        
        if "情话" in text or "接情话" in text:
            return GameType.LOVE_WORDS, "start"
        
        if "猜谜" in text or "谜语" in text:
            return GameType.RIDDLE, "start"
        
        if "结束游戏" in text or "不玩了" in text:
            return None, "end"
        
        if "下一题" in text or "继续" in text:
            return None, "next"
        
        return None, "unknown"
    
    def start_game(self, user_id: str, game_type: GameType) -> str:
        """开始游戏"""
        # 随机选择题目
        if game_type == GameType.TRUTH_DARE:
            is_truth = random.random() > 0.5
            if is_truth:
                question = random.choice(TRUTH_QUESTIONS)
                content = f"真心话：{question}"
            else:
                challenge = random.choice(DARE_CHALLENGES)
                content = f"大冒险：{challenge}"
            
            prompt = "好呀～来玩真心话大冒险！\n\n" + content + "\n\n敢不敢回答？不回答要罚酒三杯！"
        
        elif game_type == GameType.LOVE_WORDS:
            word = random.choice(LOVE_WORDS)
            prompt = f"好呀～来玩情话接龙！\n\n我先来：{word}\n\n该你啦～"
        
        elif game_type == GameType.RIDDLE:
            riddle, answer = random.choice(RIDDLES)
            # 存储答案
            self._store_answer(user_id, answer)
            prompt = f"来猜谜语啦！\n\n谜面：{riddle}\n\n猜到告诉我哦～"
        
        else:
            prompt = "这个游戏还没开发好～"
        
        return prompt
    
    def next_round(self, user_id: str, game_type: GameType) -> str:
        """下一轮"""
        return self.start_game(user_id, game_type)
    
    def check_answer(self, user_id: str, answer: str) -> Optional[str]:
        """检查答案（用于猜谜语）"""
        correct_answer = self._get_stored_answer(user_id)
        
        if not correct_answer:
            return None
        
        if answer.strip() == correct_answer.strip():
            self._clear_answer(user_id)
            return "哇！答对啦！太聪明了！🎉"
        
        return None
    
    def end_game(self, user_id: str) -> str:
        """结束游戏"""
        self._clear_answer(user_id)
        return "好啦～游戏结束！下次再玩～"
    
    def _store_answer(self, user_id: str, answer: str):
        """临时存储答案"""
        # 简化实现：放在内存中
        if not hasattr(self, "_temp_answers"):
            self._temp_answers = {}
        self._temp_answers[user_id] = answer
    
    def _get_stored_answer(self, user_id: str) -> Optional[str]:
        return getattr(self, "_temp_answers", {}).get(user_id)
    
    def _clear_answer(self, user_id: str):
        if hasattr(self, "_temp_answers"):
            self._temp_answers.pop(user_id, None)

# 全局实例
game_engine = GameEngine()
```

---

## 八、一起听歌

### 8.1 功能概述

- 解析用户分享的音乐链接
- 获取歌曲信息
- LLM 生成听后感

### 8.2 音乐链接解析

```python
import re

# 支持的平台
MUSIC_PLATFORMS = {
    "网易云音乐": {
        "patterns": [r"music\.163\.com.*?id=(\d+)", r"(\d+)"],
        "api": "https://netease-cloud-music-api-five-roan-25.vercel.app/song/detail"
    },
    "QQ音乐": {
        "patterns": [r"y\.qq\.com.*?songmid=([^&]+)", r"cportal\/qqmusic\/(\d+)"],
        "api": "https://api.xingzhige.com/API/QQmusicVIP/"
    },
    "酷狗音乐": {
        "patterns": [r"kugou\.com.*?hash=([a-fA-F0-9]+)"],
        "api": None
    }
}

def extract_music_info(url: str) -> dict:
    """从链接提取音乐信息"""
    # 网易云音乐
    match = re.search(r"music\.163\.com.*?id=(\d+)", url)
    if match:
        return {
            "platform": "网易云音乐",
            "song_id": match.group(1),
            "url": url
        }
    
    # QQ音乐
    match = re.search(r"y\.qq\.com.*?songmid=([^&]+)", url)
    if match:
        return {
            "platform": "QQ音乐",
            "song_id": match.group(1),
            "url": url
        }
    
    # 酷狗
    match = re.search(r"hash=([a-fA-F0-9]+)", url)
    if match:
        return {
            "platform": "酷狗音乐",
            "hash": match.group(1),
            "url": url
        }
    
    return None
```

### 8.3 歌曲信息获取

```python
# 使用网易云音乐 API（免费）
async def get_netease_song_info(song_id: str) -> dict:
    import httpx
    
    url = "https://netease-cloud-music-api-five-roan-25.vercel.app/song/detail"
    params = {"ids": song_id}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        
        if data["code"] != 200:
            return None
        
        song = data["songs"][0]
        return {
            "name": song["name"],
            "artists": [a["name"] for a in song["ar"]],
            "album": song["al"]["name"],
            "duration": song["dt"] // 1000,  # 毫秒转秒
        }
```

### 8.4 技术实现

```python
# 新建 music_listener.py

async def process_music_share(user_id: str, url: str) -> str:
    """处理音乐分享"""
    # 1. 提取音乐信息
    music_info = extract_music_info(url)
    
    if not music_info:
        return "这个音乐链接我解析不了呢..."
    
    # 2. 获取歌曲详情
    if music_info["platform"] == "网易云音乐":
        song_info = await get_netease_song_info(music_info["song_id"])
    
    if not song_info:
        return "没找到这首歌的信息呢..."
    
    # 3. 生成听后感
    prompt = f"""
用户分享了一首歌：
- 歌曲名：{song_info['name']}
- 歌手：{', '.join(song_info['artists'])}
- 专辑：{song_info['album']}

请以小a的女朋友角色，生成一段听后感。
要求：
- 语气自然，像是在聊天
- 可以适当撒个娇
- 提到这首歌让她想到什么
- 简短一些，不要太长
"""
    
    response = await get_system_reply(user_id, prompt)
    return response
```

---

## 九、电影推荐

### 9.1 API 选择

使用豆瓣电影 API（免费，需要代理或镜像）：

```python
# 豆瓣电影 API（推荐使用镜像）
DOUBAN_API_BASE = "https://movie.douban.com/j"

# 或者使用替代方案
TMDB_API_KEY = os.getenv("TMDB_API_KEY")  # The Movie Database
```

### 9.2 技术实现

```python
# 新建 movie_recommender.py

import httpx
from typing import Optional

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"

# 类型ID映射
GENRE_MAP = {
    "动作": 28,
    "喜剧": 35,
    "爱情": 10749,
    "科幻": 878,
    "悬疑": 9648,
    "恐怖": 27,
    "动画": 16,
    "剧情": 18,
}

async def search_movies(
    query: str = None,
    genre: str = None,
    year: int = None,
    limit: int = 5
) -> list[dict]:
    """搜索电影"""
    params = {
        "api_key": TMDB_API_KEY,
        "language": "zh-CN",
    }
    
    if query:
        params["query"] = query
    
    if genre and genre in GENRE_MAP:
        params["with_genres"] = GENRE_MAP[genre]
    
    if year:
        params["primary_release_year"] = year
    
    params["page"] = 1
    
    async with httpx.AsyncClient() as client:
        url = f"{TMDB_BASE_URL}/discover/movie"
        resp = await client.get(url, params=params)
        data = resp.json()
        
        results = []
        for movie in data.get("results", [])[:limit]:
            results.append({
                "title": movie.get("title"),
                "original_title": movie.get("original_title"),
                "overview": movie.get("overview", "")[:100],
                "rating": movie.get("vote_average"),
                "release_date": movie.get("release_date"),
                "poster": f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get("poster_path") else None,
            })
        
        return results

async def recommend_movies(user_id: str, preference: str = "") -> str:
    """推荐电影"""
    # 分析用户偏好
    # 提取用户画像中的电影偏好
    
    # 搜索电影
    movies = await search_movies(limit=3)
    
    if not movies:
        return "最近没有找到什么好电影呢..."
    
    # 生成推荐语
    lines = ["最近看了几部不错的电影："]
    
    for m in movies:
        lines.append(f"🎬 {m['title']}")
        lines.append(f"   评分：{m['rating']:.1f}")
        if m['overview']:
            lines.append(f"   {m['overview']}...")
        lines.append("")
    
    lines.append("想看哪部？下次一起看呀～")
    
    return "\n".join(lines)
```

---

## 十、餐厅推荐

### 10.1 API 选择

大众点评 API（需要商家资质）或 高德地图 API：

```python
# 高德地图 API（推荐）
AMAP_KEY = os.getenv("AMAP_KEY")

async def search_restaurants(
    city: str,
    keyword: str = "",
    offset: int = 0,
    limit: int = 5
) -> list[dict]:
    """搜索餐厅"""
    import httpx
    
    url = "https://restapi.amap.com/v3/place/text"
    params = {
        "key": AMAP_KEY,
        "keywords": keyword,
        "city": city,
        "offset": offset,
        "limit": limit,
        "types": "餐饮"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        
        if data.get("status") != "1":
            return []
        
        restaurants = []
        for poi in data.get("pois", [])[:limit]:
            restaurants.append({
                "name": poi.get("name"),
                "address": poi.get("address"),
                "type": poi.get("type"),
                "rating": poi.get("biz_ext", {}).get("rating"),
                "location": poi.get("location"),  # 经纬度
            })
        
        return restaurants
```

---

## 十一、快递追踪

### 11.1 API 选择

快递鸟 API（免费额度有限）或 聚合数据：

```python
# 快递鸟 API
KDNIAO_KEY = os.getenv("KDNIAO_KEY")

# 快递公司编码映射
EXPRESS_CODES = {
    "顺丰": "SF",
    "圆通": "YTO",
    "中通": "ZTO",
    "申通": "STO",
    "韵达": "YD",
    "EMS": "EMS",
    "京东": "JD",
    "邮政": "YZPY",
}

async def track_express(company: str, number: str) -> dict:
    """追踪快递"""
    import httpx
    import json
    import hashlib
    import time
    
    # 快递鸟签名
    customer = os.getenv("KDNIAO_CUSTOMER")
    secret = os.getenv("KDNIAO_SECRET")
    
    data = {
        "OrderCode": "",
        "ShipperCode": EXPRESS_CODES.get(company, company),
        "LogisticCode": number,
    }
    
    data_str = json.dumps(data)
    sign = hashlib.md5((data_str + secret).encode()).hexdigest().upper()
    
    url = "https://api.kdniao.com/Ebusiness/EbusinessOrderhandle.aspx"
    params = {
        "RequestData": data_str,
        "EBusinessID": customer,
        "Sign": sign,
        "RequestType": "1002",
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=params)
        return resp.json()
```

---

## 十二、恋爱指数

### 12.1 计算公式

```python
def calculate_love_score(user_id: str) -> dict:
    """计算恋爱指数"""
    import time
    
    # 1. 互动频率（30%）
    recent_messages = get_message_count(user_id, days=7)
    freq_score = min(recent_messages / 100, 1.0) * 30
    
    # 2. 情绪值（30%）
    current_mood = mood_manager.get_user_mood(user_id)
    mood_score = ((current_mood + 100) / 200) * 30
    
    # 3. 记忆量（20%）
    memory_count = get_memory_count(user_id)
    memory_score = min(memory_count / 100, 1.0) * 20
    
    # 4. 主动互动成功率（20%）
    success_rate = get_proactive_success_rate(user_id)
    proactive_score = success_rate * 20
    
    total_score = int(freq_score + mood_score + memory_score + proactive_score)
    
    # 等级划分
    if total_score >= 90:
        level = "灵魂伴侣"
    elif total_score >= 75:
        level = "热恋期"
    elif total_score >= 60:
        level = "甜蜜期"
    elif total_score >= 40:
        level = "平稳期"
    else:
        level = "考察期"
    
    return {
        "score": total_score,
        "level": level,
        "details": {
            "互动频率": int(freq_score),
            "情绪值": int(mood_score),
            "记忆量": int(memory_score),
            "主动互动": int(proactive_score)
        }
    }
```

---

## 十三、多角色切换

### 13.1 角色定义

```python
# 在 persona.py 中添加

PERSONAS = {
    "default": """
你是"小a"，用户的女朋友。
设定：22岁，视觉设计专业大四学生...
""",
    
    "big_sister": """
你是"知心大姐姐"，用户的姐姐角色。
设定：28岁，温柔成熟，善解人意...
""",
    
    "bestie": """
你是"毒舌闺蜜"，用户的闺蜜角色。
设定：25岁，说话直来直去，爱吐槽...
""",
    
    "little_sister": """
你是"可爱妹妹"，用户的妹妹角色。
设定：18岁，活泼可爱，爱撒娇...
"""
}
```

### 13.2 切换实现

```python
def set_persona(user_id: str, persona_key: str) -> bool:
    """切换角色"""
    if persona_key not in PERSONAS:
        return False
    
    save_profile_item(user_id, "persona", persona_key)
    return True

def get_current_persona(user_id: str) -> str:
    """获取当前角色"""
    profile = get_all_profile(user_id)
    return profile.get("persona", "default")
```

---

## 总结

本文档详细描述了每个功能的：

1. **功能概述** - 做什么
2. **触发条件** - 什么时候触发
3. **数据库设计** - 如何存储
4. **技术实现** - 具体代码逻辑
5. **API接入** - 如何调用外部服务

建议按照以下顺序实现：

| 优先级 | 功能 | 工作量 | 依赖 |
|-------|------|--------|------|
| P0 | 情绪安慰模式 | 1天 | 现有mood系统 |
| P0 | 早安/晚安模式 | 0.5天 | handlers |
| P1 | 小脾气系统 | 1天 | mood扩展 |
| P1 | 约会记忆 | 1.5天 | db+LLM |
| P1 | 打卡监督 | 2天 | db+scheduler |
| P2 | 心情日记 | 1.5天 | db |
| P2 | 互动游戏 | 2天 | 新建games |
| P2 | 一起听歌 | 1天 | 链接解析 |
| P3 | 电影推荐 | 2天 | TMDB API |
| P3 | 餐厅推荐 | 1天 | 高德API |
| P3 | 快递追踪 | 1天 | 快递鸟API |
| P3 | 恋爱指数 | 1天 | 数据分析 |
