"""发送节奏控制 - 让消息发送更像真人聊天。

设计目标：
- 每条间隔增加随机抖动（250~900ms）
- 长内容（超过 4 条）中间额外停顿 1.5~2s
- 统一节奏计算逻辑
"""

from __future__ import annotations

import random
from .utils.typing_speed import typing_delay_seconds as _base_typing_delay

# 每条消息之间的额外抖动范围（秒）
BUBBLE_JITTER_MIN = 0.25
BUBBLE_JITTER_MAX = 0.90

# 长内容每隔 N 条增加一次额外停顿
LONG_CONTENT_THRESHOLD = 4
LONG_CONTENT_PAUSE_MIN = 1.5
LONG_CONTENT_PAUSE_MAX = 2.5


def bubble_delay_seconds(
    text: str,
    *,
    user_id: int | str | None = None,
    bubble_index: int = 0,
    total_bubbles: int = 1,
) -> float:
    """计算单条气泡发送前的等待时间。

    Args:
        text: 要发送的文本内容
        user_id: 用户 ID（用于保持同一用户速度一致性）
        bubble_index: 当前是第几条气泡（0-indexed）
        total_bubbles: 总共有多少条气泡

    Returns:
        等待秒数
    """
    # 基础延迟（基于打字速度模拟）
    base = _base_typing_delay(text, user_id=user_id)

    # 随机抖动（让节奏更自然）
    jitter = random.uniform(BUBBLE_JITTER_MIN, BUBBLE_JITTER_MAX)

    # 长内容额外停顿：超过 4 条时，每发 3~4 条停一下
    extra_pause = 0.0
    if total_bubbles > LONG_CONTENT_THRESHOLD:
        # 第 3、6、9... 条后额外停顿
        if bubble_index > 0 and bubble_index % 3 == 2:
            extra_pause = random.uniform(LONG_CONTENT_PAUSE_MIN, LONG_CONTENT_PAUSE_MAX)

    delay = base + jitter + extra_pause
    return max(0.35, min(delay, 6.0))  # 上限稍微放宽到 6s


def typing_delay_seconds(text: str, *, user_id: int | str | None = None) -> float:
    """兼容旧接口：单条消息延迟（无气泡上下文时使用）。"""
    return bubble_delay_seconds(text, user_id=user_id, bubble_index=0, total_bubbles=1)
