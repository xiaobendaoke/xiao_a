"""每日反思定时任务。

每天凌晨（如 04:00）运行，遍历最近活跃的用户，执行 reflection.daily_reflection。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from nonebot import logger, require
from .reflection import process_user_reflection
from .db import get_recent_active_users

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


# 每天凌晨 4 点运行
@scheduler.scheduled_job("cron", hour=4, minute=0, id="companion_core_daily_reflection", misfire_grace_time=3600)
async def daily_reflection_job():
    logger.info("[reflection] job started")
    
    # 1. 获取过去 24h 活跃的用户
    # (需要在 db.py 实现 get_recent_active_users，或者直接查 distinct user_id)
    # 为简单起见，可以扫描最近 24h 有 chat log 的用户
    try:
        users = get_recent_active_users(hours=24)
    except Exception as e:
        logger.error(f"[reflection] failed to get active users: {e}")
        return

    if not users:
        logger.info("[reflection] no active users")
        return

    count = 0
    for uid in users:
        try:
            ok = await process_user_reflection(uid)
            if ok:
                count += 1
            # 稍微 sleep 一下，避免短时间大量调用 LLM 触发限流
            await asyncio.sleep(2.0)
        except Exception as e:
            logger.error(f"[reflection] failed for {uid}: {e}")
            
    logger.info(f"[reflection] job finished. generated {count} reflections.")
