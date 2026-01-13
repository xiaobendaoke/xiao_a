"""情绪管理（mood state）。

机制：
- 心情值范围：`-100 ~ 100`，数值越高越黏人/更活泼，越低越需要安抚/更克制。
- 单次变化幅度：`-3 ~ 3`（由 LLM 标签与本模块双重约束）。
- 自然回归：当心情值过于极端时，会每次对话自动向 0 轻微回归，避免长期卡死。

实现：
- 使用内存字典做缓存（减少频繁读 SQLite）。
- 以 `db.get_mood/save_mood` 持久化到 `user_mood` 表。
"""

from .db import get_mood, save_mood

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

class MoodManager:
    def __init__(self):
        self._moods = {}

    def get_user_mood(self, user_id: str) -> int:
        if user_id not in self._moods:
            self._moods[user_id] = get_mood(user_id)
        return self._moods[user_id]

    def _recover_towards_zero(self, v: int) -> int:
        """
        自然回归：越极端回得越快，接近0时不强行回归
        返回一个小的恢复量（可能为 -2~-1 或 +1~+2 或 0）
        """
        av = abs(v)
        if av <= 10:
            return 0
        step = 1
        if av >= 70:
            step = 2
        # v>0 往下回；v<0 往上回
        return -step if v > 0 else step

    def update_mood(self, user_id: str, change: int):
        current = self.get_user_mood(user_id)

        # 保险：限制单次变化幅度（配合你 llm.py 里的 clamp 双保险）
        change = clamp(int(change), -3, 3)

        # 自然回归（让情绪不会一直卡在极端）
        recover = self._recover_towards_zero(current)

        new_mood = clamp(current + change + recover, -100, 100)

        self._moods[user_id] = new_mood
        save_mood(user_id, new_mood)
        return new_mood

    def get_mood_desc(self, user_id: str):
        v = self.get_user_mood(user_id)
        # 描述尽量别太“上头/太狠”，否则模型会演得很用力
        if v >= 80: return "特别开心，语气会更黏人更甜一点"
        if v >= 30: return "心情不错，说话更活泼俏皮"
        if v >= -10: return "情绪平稳，正常自然聊天"
        if v >= -50: return "有点小情绪，可能会略微冷淡或别扭"
        return "情绪很差，需要一点安抚和空间"

mood_manager = MoodManager()
