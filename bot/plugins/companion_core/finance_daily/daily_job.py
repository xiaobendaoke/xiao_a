"""财经日报定时任务（apscheduler cron）。

职责：
- 收盘后跑一次 A 股 TopN 涨/跌榜分析（见 `pipeline.run_cn_a_daily()`）；
- 推送方式：只给“订阅了财经日报”的私聊用户发送（见 commands.py）。

重要约束：
- 只私聊发送，不向群聊发送（避免误触发群风控）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from nonebot import get_bots, logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from ..utils.typing_speed import typing_delay_seconds
from ..db import filter_active_user_ids

from .config import (
    FIN_DAILY_ENABLED,
    FIN_DAILY_MARKET,
    FIN_DAILY_RUN_HOUR,
    FIN_DAILY_RUN_MINUTE,
    FIN_DAILY_SEND_INTERVAL_SECONDS,
)
from .pipeline import run_cn_a_daily
from .storage import list_enabled_subscribers


def pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


def _split_long_text(text: str, *, max_chars: int = 850) -> list[str]:
    """只做“过长拆分”，不合并多条消息（满足“一股一条”的 QQ 气泡需求）。"""
    s = str(text or "").strip()
    if not s:
        return []
    if len(s) <= max_chars:
        return [s]

    parts: list[str] = []
    cur = ""
    for ln in s.splitlines():
        ln = ln.rstrip()
        if not cur:
            cur = ln
            continue
        if len(cur) + 1 + len(ln) <= max_chars:
            cur = cur + "\n" + ln
            continue
        parts.append(cur)
        cur = ln
    if cur:
        parts.append(cur)

    # 兜底：仍超长就硬截
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        for i in range(0, len(p), max_chars):
            final.append(p[i : i + max_chars])
    return [x for x in final if str(x).strip()]


async def send_private_messages(bot, user_id: int, messages: list[str], *, interval: float = 0.8) -> None:
    """按顺序发送多条私聊消息；每条消息过长则拆分后发送。"""
    uid = int(user_id)
    interval = float(interval or 0.0)
    for msg in messages or []:
        chunks = _split_long_text(str(msg or ""))
        for c in chunks:
            try:
                # 统一发送节奏：发送前等待“人类打字时间”
                await asyncio.sleep(max(typing_delay_seconds(c, user_id=uid), interval))
                await bot.call_api("send_private_msg", user_id=uid, message=c)
            except Exception as e:
                logger.warning(f"[finance] send_private failed uid={uid}: {e}")
                return


async def _run_and_push(force_trade_date: str | None = None) -> None:
    if not FIN_DAILY_ENABLED:
        return
    try:
        res = await run_cn_a_daily(force_trade_date=force_trade_date)
        if res.get("skipped"):
            logger.info(f"[finance] skipped: {res}")
            return
        parts = res.get("report_parts")
        if isinstance(parts, list) and parts:
            messages = [str(p) for p in parts if str(p).strip()]
        else:
            text = str(res.get("report_text") or "").strip()
            messages = [text] if text else []
        if not messages:
            return

        bot = pick_bot()
        if bot is None:
            logger.info("[finance] skip push: no connected bot")
            return

        subscribers = await list_enabled_subscribers(FIN_DAILY_MARKET)
        if not subscribers:
            # 兼容：未订阅则不推送（避免全好友/全群误刷屏）
            logger.info("[finance] no subscribers; skip pushing")
            return
        subscribers = await filter_active_user_ids(subscribers)
        if not subscribers:
            logger.info("[finance] no active subscribers (inactive>24h); skip pushing")
            return

        interval = float(FIN_DAILY_SEND_INTERVAL_SECONDS or 0.0)
        for uid in subscribers:
            await send_private_messages(bot, int(uid), messages, interval=interval)
    except Exception as e:
        logger.exception(f"[finance] daily run failed: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=FIN_DAILY_RUN_HOUR,
    minute=FIN_DAILY_RUN_MINUTE,
    id="finance_daily_cn_a",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=180,
)
async def finance_daily_cn_a_job():
    logger.info(f"[finance] tick {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    await _run_and_push()
