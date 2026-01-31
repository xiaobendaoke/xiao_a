"""Info Agent 定时任务调度。

定时检查并推送信息。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from nonebot import logger, require, on_message
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from . import config
from . import pool
from . import sources
from . import agent
from . import push


async def _refresh_pool() -> None:
    """刷新信息池"""
    if not pool.is_pool_stale():
        return
    
    logger.info("[info_agent] refreshing pool...")
    items = await sources.fetch_all_sources()
    pool.add_items(items)
    pool.clear_old_items(max_age_hours=48)


async def _run_push_cycle() -> None:
    """执行一次推送检查"""
    now = datetime.now()
    
    # 检查免打扰时段
    if push.in_quiet_hours(now):
        logger.debug("[info_agent] skip: quiet hours")
        return
    
    # 刷新信息池
    await _refresh_pool()
    
    # 获取目标用户
    targets = await push.get_push_targets()
    if not targets:
        logger.debug("[info_agent] skip: no targets")
        return
    
    # 对每个用户决策
    for user_id, idle_minutes in targets:
        try:
            # 检查空闲时间
            if idle_minutes < config.INFO_AGENT_IDLE_MINUTES_MIN:
                continue
            
            # 检查每日上限
            daily_count = push.get_daily_push_count(user_id)
            if daily_count >= config.INFO_AGENT_DAILY_LIMIT:
                continue
            
            # 优先时段检查（非优先时段降低推送概率）
            if not push.in_preferred_hours(now) and daily_count > 0:
                continue
            
            # 获取候选信息
            items = pool.get_pool_for_user(user_id, limit=15)
            if not items:
                continue
            
            # LLM 决策
            result = await agent.decide_and_generate(
                user_id,
                items,
                daily_pushed=daily_count,
                daily_limit=config.INFO_AGENT_DAILY_LIMIT,
            )
            
            selected = result.get("selected", [])
            messages = result.get("messages", [])
            
            if not selected or not messages:
                continue
            
            # 推送
            pushed = await push.push_messages(user_id, messages)
            if pushed:
                logger.info(f"[info_agent] pushed {pushed} messages to {user_id}")
                
        except Exception as e:
            logger.exception(f"[info_agent] push cycle failed for {user_id}: {e}")


@scheduler.scheduled_job(
    "interval",
    minutes=config.INFO_AGENT_CHECK_INTERVAL_MINUTES,
    id="info_agent_check",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
)
async def info_agent_job():
    """定时检查并推送"""
    if not config.INFO_AGENT_ENABLED:
        return
    
    logger.info(f"[info_agent] tick {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    await _run_push_cycle()


# === 手动触发命令 ===

def _manual_trigger_rule(event: PrivateMessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    text = str(event.get_message() or "").strip()
    return text in ("信息推送", "资讯推送", "info push", "推送")


manual_trigger = on_message(rule=Rule(_manual_trigger_rule), priority=4, block=True)


@manual_trigger.handle()
async def handle_manual_trigger(event: PrivateMessageEvent):
    """手动触发信息推送"""
    user_id = str(event.user_id)
    
    # 刷新信息池
    await _refresh_pool()
    
    # 获取候选
    items = pool.get_pool_for_user(user_id, limit=15)
    if not items:
        await manual_trigger.finish("我刚刷了一圈，暂时没有新鲜的资讯给你看～")
    
    # LLM 决策
    daily_count = push.get_daily_push_count(user_id)
    result = await agent.decide_and_generate(
        user_id,
        items,
        daily_pushed=daily_count,
        daily_limit=10,  # 手动触发放宽限制
    )
    
    selected = result.get("selected", [])
    messages = result.get("messages", [])
    
    if not selected or not messages:
        await manual_trigger.finish("我看了看，暂时没有特别值得推给你的～等有好的我再告诉你")
    
    # 推送
    pushed = await push.push_messages(user_id, messages)
    if pushed:
        await manual_trigger.finish()
    else:
        await manual_trigger.finish("推送好像出了点问题…你等会儿再试试？")


# === 信息池状态查询 ===

def _status_rule(event: PrivateMessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    text = str(event.get_message() or "").strip()
    return text in ("信息状态", "资讯状态", "info status")


status_trigger = on_message(rule=Rule(_status_rule), priority=4, block=True)


@status_trigger.handle()
async def handle_status(event: PrivateMessageEvent):
    """查询信息池状态"""
    stats = pool.get_pool_stats()
    user_id = str(event.user_id)
    daily_count = push.get_daily_push_count(user_id)
    
    lines = [
        "信息池状态：",
        f"- 总条目：{stats['total']}",
        f"- 今日已推送：{daily_count}/{config.INFO_AGENT_DAILY_LIMIT}",
    ]
    
    if stats.get("categories"):
        lines.append("- 分类分布：")
        cat_map = {"tech": "科技", "finance": "财经", "hot": "热点", "world": "国际"}
        for cat, count in stats["categories"].items():
            lines.append(f"  · {cat_map.get(cat, cat)}: {count}")
    
    await status_trigger.finish("\n".join(lines))
