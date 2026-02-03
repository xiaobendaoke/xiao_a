"""情绪管理（mood state）。

机制：
- 心情值范围：`-100 ~ 100`。
- 自动衰减（Mood Decay）：随时间推移，情绪会自动向 0 回归（每分钟恢复 1 点）。
- 情绪记忆：使用 `user_profile` 记录上次更新时间戳。

"""

import time
from .db import get_mood, save_mood, get_all_profile, save_profile_item

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

class MoodManager:
    def __init__(self):
        # 内存缓存
        self._moods: dict[str, int] = {}
        self._timestamps: dict[str, float] = {}

    def _load_from_db(self, user_id: str) -> tuple[int, float]:
        """从 DB 加载心情值和上次更新时间"""
        m = get_mood(user_id)
        
        # 从 user_profile 读取 mood_updated_ts
        profiles = get_all_profile(user_id)
        ts_str = profiles.get("mood_updated_ts", "0")
        try:
            ts = float(ts_str)
        except:
            ts = 0.0
            
        return m, ts

    def get_user_mood(self, user_id: str) -> int:
        now = time.time()
        
        # 1. 尝试读缓存，如果没有则读 DB
        if user_id not in self._moods:
            m, ts = self._load_from_db(user_id)
            self._moods[user_id] = m
            self._timestamps[user_id] = ts

        current_mood = self._moods[user_id]
        last_ts = self._timestamps[user_id]

        # 2. 计算时间衰减 (Time Decay)
        # 规则：每 60 秒恢复 1 点
        if last_ts <= 0:
            # 第一次加载（或历史数据），假定现在就是最新，不衰减
            self._timestamps[user_id] = now
            return current_mood

        delta_seconds = now - last_ts
        decay_points = int(delta_seconds / 60)

        if decay_points > 0 and current_mood != 0:
            # 向 0 回归
            if current_mood > 0:
                new_mood = max(0, current_mood - decay_points)
            else:
                new_mood = min(0, current_mood + decay_points)
            
            # 如果发生了变化，更新 DB 和缓存
            if new_mood != current_mood:
                self._moods[user_id] = new_mood
                self._timestamps[user_id] = now
                save_mood(user_id, new_mood)
                save_profile_item(user_id, "mood_updated_ts", str(now))
                return new_mood

        return current_mood

    def update_mood(self, user_id: str, change: int):
        # 先获取当前（已衰减）的心情
        current = self.get_user_mood(user_id)

        # 限制单次变化幅度
        change = clamp(int(change), -5, 5) # 稍微放宽一点单次变化

        new_mood = clamp(current + change, -100, 100)

        now = time.time()
        self._moods[user_id] = new_mood
        self._timestamps[user_id] = now
        
        save_mood(user_id, new_mood)
        save_profile_item(user_id, "mood_updated_ts", str(now))
        return new_mood

    def get_mood_desc(self, user_id: str) -> str:
        """提供给 System Prompt 的心理侧写"""
        v = self.get_user_mood(user_id)
        if v >= 80: return "心理状态：超级兴奋，恨不得马上抱住他，满眼星星。"
        if v >= 30: return "心理状态：心情不错，比较甜，看什么都顺眼。"
        if v >= -10: return "心理状态：内心平静，比较随性，懒洋洋的。"
        if v >= -50: return "心理状态：有点烦躁/郁闷，不想多说话，对他有点不耐烦。"
        return "心理状态：非常生气/崩溃，甚至想冷暴力或者发脾气。"

    def get_mood_instruction(self, user_id: str) -> str:
        """提供给 System Prompt 的强制指令（Locking）"""
        v = self.get_user_mood(user_id)
        if v < -20:
            return "【强制】：你现在还在生气，说话要短，不要带语气词，禁止发可爱的表情/颜文字。"
        return ""

mood_manager = MoodManager()
