"""Info Agent 推送执行。

执行实际的消息推送，包括用户状态检查、推送记录等。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, date, time
from typing import Any

from nonebot import get_bots, logger

from ..db import get_idle_user_states, touch_active
from ..memory import add_memory
from ..utils.typing_speed import typing_delay_seconds
from ..bubble_splitter import bubble_parts as _bubble_parts
from ..llm_tags import extract_tags_and_clean

from . import config
from .models import InfoItem
from . import pool


# _bubble_parts 已移至 bubble_splitter.py 统一管理（通过导入使用）


# 每日推送计数（user_id -> {date: count}）
_daily_push_count: dict[str, dict[str, int]] = {}


def _get_today_key() -> str:
    return date.today().isoformat()


def get_daily_push_count(user_id: str) -> int:
    """获取用户今日推送次数"""
    uid = str(user_id)
    today = _get_today_key()
    return _daily_push_count.get(uid, {}).get(today, 0)


def increment_daily_push_count(user_id: str) -> None:
    """增加用户今日推送次数"""
    uid = str(user_id)
    today = _get_today_key()
    if uid not in _daily_push_count:
        _daily_push_count[uid] = {}
    _daily_push_count[uid][today] = _daily_push_count[uid].get(today, 0) + 1


def in_quiet_hours(now: datetime | None = None) -> bool:
    """检查是否在免打扰时段"""
    now = now or datetime.now()
    t = now.time()
    
    quiet_start = time(config.INFO_AGENT_QUIET_START_HOUR, 0)
    quiet_end = time(config.INFO_AGENT_QUIET_END_HOUR, 0)
    
    if quiet_start > quiet_end:
        # 跨午夜，如 23:00 - 08:00
        return t >= quiet_start or t < quiet_end
    else:
        return quiet_start <= t < quiet_end


def in_preferred_hours(now: datetime | None = None, user_id: str | None = None) -> bool:
    """
    检查是否在推送优先时段（智能学习用户活跃时间）。
    
    逻辑：
    1. 如果有用户活跃小时数据，检查当前小时是否是用户的活跃时段
    2. 如果没有足够数据（新用户），使用兜底配置
    """
    from ..db import get_user_active_hours_sync
    
    now = now or datetime.now()
    current_hour = now.hour
    
    # 没有指定用户：使用兜底配置
    if not user_id:
        return str(current_hour) in config.INFO_AGENT_FALLBACK_PUSH_HOURS
    
    # 获取用户活跃小时数据
    active_hours = get_user_active_hours_sync(str(user_id))
    
    # 数据不足（少于 20 条记录）：使用兜底配置
    total_count = sum(active_hours.values())
    if total_count < 20:
        return str(current_hour) in config.INFO_AGENT_FALLBACK_PUSH_HOURS
    
    # 计算当前小时的活跃度占比
    current_count = active_hours.get(current_hour, 0)
    ratio = current_count / total_count if total_count > 0 else 0
    
    # 超过阈值则认为是活跃时段
    return ratio >= config.INFO_AGENT_ACTIVE_HOUR_THRESHOLD


def pick_bot():
    """获取可用的 bot"""
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


async def get_push_targets() -> list[tuple[str, int]]:
    """获取可推送的目标用户列表：(user_id, idle_minutes)"""
    targets = await get_idle_user_states()
    if not targets:
        return []
    
    now_ts = int(datetime.now().timestamp())
    result = []
    for uid, last_active_ts in targets:
        idle_minutes = max(0, (now_ts - int(last_active_ts or 0)) // 60)
        result.append((str(uid), idle_minutes))
    
    return result


async def push_to_user(
    user_id: str,
    item: InfoItem,
    text: str,
) -> bool:
    """向用户推送一条信息"""
    bot = pick_bot()
    if bot is None:
        logger.warning("[info_agent] no connected bot")
        return False
    
    uid = int(user_id)
    text = str(text or "").strip()
    if not text:
        return False
    
    # 清理标签（动作/表情等）
    text, _, _ = extract_tags_and_clean(text)
    if not text.strip():
        return False
    
    try:
        # 模拟打字延迟并分段发送
        parts = _bubble_parts(text)
        for part in parts:
            await asyncio.sleep(typing_delay_seconds(part, user_id=user_id))
            await bot.call_api("send_private_msg", user_id=uid, message=part)
        
        # 记录
        add_memory(str(user_id), "assistant", text)
        pool.mark_pushed(item.id, user_id)
        increment_daily_push_count(user_id)
        
        logger.info(f"[info_agent] pushed to {user_id}: {item.title[:30]}")
        return True
    except Exception as e:
        logger.error(f"[info_agent] push failed {user_id}: {e}")
        return False


async def push_messages(
    user_id: str,
    messages: list[dict[str, Any]],
) -> int:
    """批量推送消息，返回成功数量"""
    bot = pick_bot()
    if bot is None:
        return 0
    
    uid = int(user_id)
    success = 0
    
    for msg in messages or []:
        item_id = msg.get("id", "")
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        
        # 清理标签（动作/表情等）
        text, _, _ = extract_tags_and_clean(text)
        if not text.strip():
            continue
        
        try:
            # 模拟打字延迟并分段发送
            parts = _bubble_parts(text)
            for part in parts:
                await asyncio.sleep(typing_delay_seconds(part, user_id=user_id))
                await bot.call_api("send_private_msg", user_id=uid, message=part)
            
            # 记忆和计数放在消息发送完成后（每条 message 只记一次）
            add_memory(str(user_id), "assistant", text)
            if item_id:
                pool.mark_pushed(item_id, user_id)
            increment_daily_push_count(user_id)
            success += 1
            
        except Exception as e:
            logger.error(f"[info_agent] push message failed {user_id}: {e}")
            break
    
    return success
