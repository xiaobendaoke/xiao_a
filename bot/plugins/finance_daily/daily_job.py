"""财经日报定时任务（apscheduler cron）。

职责：
- 收盘后跑一次 A 股 TopN 涨/跌榜分析（见 `pipeline.run_cn_a_daily()`）；
- 推送方式支持两种：
  1) 指定私聊目标（`FIN_DAILY_TARGETS=private:123`）；
  2) 广播给小a所有好友（`FIN_DAILY_BROADCAST_ALL_FRIENDS=1` 或 `FIN_DAILY_TARGETS=all_friends`）。

重要约束：
- 只私聊发送，不向群聊发送（符合“广播给所有好友”的需求，也避免误触发群风控）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from nonebot import get_bots, logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import (
    FIN_DAILY_BROADCAST_ALL_FRIENDS,
    FIN_DAILY_BROADCAST_LIMIT,
    FIN_DAILY_ENABLED,
    FIN_DAILY_RUN_HOUR,
    FIN_DAILY_RUN_MINUTE,
    FIN_DAILY_SEND_INTERVAL_SECONDS,
    FIN_DAILY_TARGETS,
)
from .pipeline import run_cn_a_daily


def pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


def _split_qq_messages(text: str, *, max_chars: int = 850) -> list[str]:
    s = str(text or "").strip()
    if not s:
        return []
    blocks = [b.strip() for b in s.split("\n\n") if b.strip()]
    parts: list[str] = []
    buf = ""
    for b in blocks:
        if not buf:
            buf = b
            continue
        if len(buf) + 2 + len(b) <= max_chars:
            buf = buf + "\n\n" + b
        else:
            parts.append(buf)
            buf = b
    if buf:
        parts.append(buf)
    # 兜底：仍过长就按行切
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        lines = p.splitlines()
        cur = ""
        for ln in lines:
            if not cur:
                cur = ln
                continue
            if len(cur) + 1 + len(ln) <= max_chars:
                cur = cur + "\n" + ln
            else:
                final.append(cur)
                cur = ln
        if cur:
            final.append(cur)
    return final


async def _get_friend_user_ids(bot) -> list[int]:
    """从 OneBot 获取好友列表，返回 user_id 列表。"""
    try:
        friends = await bot.call_api("get_friend_list")
    except Exception as e:
        logger.warning(f"[finance] get_friend_list failed: {e}")
        return []
    out: list[int] = []
    for f in friends or []:
        uid = f.get("user_id")
        try:
            uid_i = int(uid)
        except Exception:
            continue
        if uid_i > 0:
            out.append(uid_i)
    # 去重，保持稳定顺序
    return list(dict.fromkeys(out))


async def _send_to_targets(text: str) -> None:
    bot = pick_bot()
    if bot is None:
        logger.info("[finance] skip push: no connected bot")
        return

    parts = _split_qq_messages(text)
    priv: list[int] = []

    if FIN_DAILY_BROADCAST_ALL_FRIENDS:
        priv = await _get_friend_user_ids(bot)
        if FIN_DAILY_BROADCAST_LIMIT and len(priv) > FIN_DAILY_BROADCAST_LIMIT:
            priv = priv[: FIN_DAILY_BROADCAST_LIMIT]
            logger.warning(f"[finance] broadcast limited to first {FIN_DAILY_BROADCAST_LIMIT} friends")
        if not priv:
            logger.info("[finance] broadcast enabled but friend list empty; skip pushing")
            return
    else:
        if not FIN_DAILY_TARGETS:
            logger.info("[finance] no FIN_DAILY_TARGETS configured; skip pushing")
            return
        # 只允许 private（群目标忽略）
        priv = [i for t, i in FIN_DAILY_TARGETS if t == "private"]
        if not priv:
            logger.info("[finance] FIN_DAILY_TARGETS has no private targets; skip pushing")
            return

    interval = float(FIN_DAILY_SEND_INTERVAL_SECONDS or 0.0)

    for uid in priv:
        for p in parts:
            try:
                await bot.call_api("send_private_msg", user_id=int(uid), message=p)
                if interval:
                    await asyncio.sleep(interval)
            except Exception as e:
                logger.warning(f"[finance] send_private failed uid={uid}: {e}")
                break


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
            text = "\n\n".join([str(p) for p in parts if str(p).strip()]).strip()
        else:
            text = str(res.get("report_text") or "").strip()
        if not text:
            return
        await _send_to_targets(text)
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
