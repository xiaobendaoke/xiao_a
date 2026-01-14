"""人类打字速度模拟（统一的发送节奏控制）。

设计目标：
- 按“文本长度 + 标点停顿 + 轻微随机抖动”估算打字耗时；
- 同一用户保持相近速度（避免忽快忽慢）；
- 给所有对话发送统一节奏算法。
"""

from __future__ import annotations

import random
from typing import Optional

_USER_CPS: dict[str, float] = {}
_DEFAULT_CPS = 6.5
_CPS_RANGE = (2, 3)  # 每秒字符数（中文）


def _pick_cps() -> float:
    return random.uniform(*_CPS_RANGE)


def get_user_cps(user_id: Optional[str | int]) -> float:
    """返回用户的“打字速度”（每秒字符数）。"""
    if user_id is None:
        return _DEFAULT_CPS
    uid = str(user_id)
    if uid not in _USER_CPS:
        _USER_CPS[uid] = _pick_cps()
    return _USER_CPS[uid]


def _count_units(text: str) -> float:
    """估算文本“打字单位数”（中文 1.0，英文/数字略快）。"""
    units = 0.0
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            units += 1.0
        elif ch.isascii():
            if ch.isalpha() or ch.isdigit():
                units += 0.6
            elif ch.isspace():
                units += 0.05
            else:
                units += 0.2
        else:
            units += 0.8
    return max(units, 1.0)


def typing_delay_seconds(text: str, *, user_id: Optional[str | int] = None) -> float:
    """根据文本内容估算“人类打字耗时”，用于发送前等待。"""
    s = (text or "").strip()
    if not s:
        return 0.2

    cps = get_user_cps(user_id)
    units = _count_units(s)

    base = random.uniform(0.2, 0.45)  # 起手思考
    end_pause = 0.0
    if s.endswith(("。", "！", "？", "!", "?", "…")):
        end_pause += 0.35
    comma_count = s.count("，") + s.count(",") + s.count("、")
    end_pause += min(0.35, comma_count * 0.05)

    jitter = random.uniform(-0.08, 0.18)
    delay = base + units / cps + end_pause + jitter
    return max(0.35, min(delay, 4.5))
