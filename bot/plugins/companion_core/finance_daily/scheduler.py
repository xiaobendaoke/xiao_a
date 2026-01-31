"""股票小白日报定时任务。

每个交易日收盘后自动推送涨跌榜分析。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from nonebot import get_bot, get_bots, logger, on_message, require
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .data import fetch_daily_report_data
from .analyzer import generate_daily_report


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    v = _env_str(name).lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# 配置项
FINANCE_DAILY_ENABLED = _env_flag("FINANCE_DAILY_ENABLED", True)
FINANCE_DAILY_HOUR = _env_int("FINANCE_DAILY_HOUR", 15)  # 推送小时
FINANCE_DAILY_MINUTE = _env_int("FINANCE_DAILY_MINUTE", 35)  # 推送分钟
FINANCE_DAILY_USER_ID = _env_str("FINANCE_DAILY_USER_ID", "")  # 接收用户 QQ
FINANCE_DAILY_TOP_N = _env_int("FINANCE_DAILY_TOP_N", 5)  # 获取 Top N
FINANCE_DAILY_ANALYZE_N = _env_int("FINANCE_DAILY_ANALYZE_N", 3)  # 分析前 N 只


def is_trading_day() -> bool:
    """判断今天是否是交易日（简单判断：周一到周五）"""
    # TODO: 可以接入交易所日历 API 精确判断
    weekday = datetime.now().weekday()
    return weekday < 5  # 周六=5, 周日=6


def pick_bot() -> Bot | None:
    """获取可用的 bot"""
    bots = get_bots()
    if not bots:
        return None
    for bot in bots.values():
        if isinstance(bot, Bot):
            return bot
    return None


async def _send_with_delay(bot: Bot, user_id: int, messages: list[str]) -> int:
    """分批发送消息，带延迟避免刷屏"""
    sent = 0
    for msg in messages:
        if not msg or not msg.strip():
            continue
        try:
            await bot.send_private_msg(user_id=user_id, message=msg.strip())
            sent += 1
            await asyncio.sleep(2.0)  # 每条消息间隔 2 秒
        except Exception as e:
            logger.error(f"[finance_daily] send failed: {e}")
    return sent


async def run_daily_report() -> None:
    """执行每日报告推送"""
    if not FINANCE_DAILY_ENABLED:
        return
    
    if not FINANCE_DAILY_USER_ID:
        logger.warning("[finance_daily] no user_id configured, skip")
        return
    
    if not is_trading_day():
        logger.info("[finance_daily] not trading day, skip")
        return
    
    bot = pick_bot()
    if not bot:
        logger.warning("[finance_daily] no bot available")
        return
    
    logger.info("[finance_daily] starting daily report...")
    
    try:
        # 1. 获取数据
        data = await fetch_daily_report_data(top_n=FINANCE_DAILY_TOP_N)
        
        # 2. 生成报告
        messages = await generate_daily_report(data)
        
        # 3. 推送
        user_id = int(FINANCE_DAILY_USER_ID)
        sent = await _send_with_delay(bot, user_id, messages)
        
        logger.info(f"[finance_daily] sent {sent} messages to {user_id}")
        
    except Exception as e:
        logger.exception(f"[finance_daily] report failed: {e}")


# 定时任务：每日 15:35 推送
@scheduler.scheduled_job(
    "cron",
    hour=FINANCE_DAILY_HOUR,
    minute=FINANCE_DAILY_MINUTE,
    id="finance_daily_report",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
)
async def finance_daily_job():
    """定时股票日报"""
    await run_daily_report()


# === 手动触发命令 ===

def _manual_rule(event: PrivateMessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    text = str(event.get_message() or "").strip()
    return text in ("股票日报", "今日股市", "涨跌榜", "finance daily")


manual_trigger = on_message(rule=Rule(_manual_rule), priority=4, block=True)


@manual_trigger.handle()
async def handle_manual_trigger(event: PrivateMessageEvent):
    """手动触发股票日报"""
    user_id = event.user_id
    
    await manual_trigger.send("好的，我去看看今天的行情～")
    
    try:
        bot = pick_bot()
        if not bot:
            await manual_trigger.finish("机器人还没准备好，等会儿再试试？")
            
        data = await fetch_daily_report_data(top_n=FINANCE_DAILY_TOP_N)
        messages = await generate_daily_report(data)
        
        await _send_with_delay(bot, user_id, messages)
        
    except Exception as e:
        logger.exception(f"[finance_daily] manual trigger failed: {e}")
        await manual_trigger.finish("获取行情的时候出了点问题…你等会儿再试试？")
