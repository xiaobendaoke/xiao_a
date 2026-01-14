"""主动互动定时任务（apscheduler interval）。

职责：
- 定期扫描“久未活跃、允许被打扰”的候选用户（`db.get_proactive_candidates()`）。
- 为候选用户占用发送配额/锁（`db.claim_proactive_slot()`，防并发重复发送）。
- 调用 `llm_proactive.generate_proactive_message()` 生成结构化结果：
  - `should_send`：是否适合打扰；
  - `text`：要发送的文案；
  - `intent/reason/need_reply`：用于日志与策略。
- 以“气泡式分段”发送：将多行拆成多条私聊并模拟停顿（更像真人）。
- 成功/失败都会写回冷却、次数与锁状态（避免刷屏与重复尝试）。

可通过环境变量控制：
- `PROACTIVE_ENABLED`、`PROACTIVE_INTERVAL_MINUTES`、`PROACTIVE_IDLE_HOURS`
- `PROACTIVE_MAX_PER_DAY`、`PROACTIVE_COOLDOWN_MINUTES`
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from nonebot import get_bot, logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_apscheduler import scheduler

from .db import (
    ProactiveCandidate,
    claim_proactive_slot,
    get_proactive_candidates,
    mark_proactive_failed,
    mark_proactive_sent,
)
from .llm_proactive import generate_proactive_message
from .utils.typing_speed import typing_delay_seconds


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


PROACTIVE_ENABLED = _env_int("PROACTIVE_ENABLED", 1) == 1
PROACTIVE_INTERVAL_MINUTES = _env_int("PROACTIVE_INTERVAL_MINUTES", 5)
PROACTIVE_IDLE_HOURS = _env_int("PROACTIVE_IDLE_HOURS", 8)
PROACTIVE_MAX_PER_DAY = _env_int("PROACTIVE_MAX_PER_DAY", 2)
PROACTIVE_COOLDOWN_MINUTES = _env_int("PROACTIVE_COOLDOWN_MINUTES", 240)


async def _send_bubbles(bot: Bot, user_id: int, text: str) -> None:
    parts = [p.strip() for p in (text or "").splitlines() if p.strip()]
    if not parts:
        return
    if len(parts) > 4:
        parts = parts[:3] + [" ".join(parts[3:])]

    for p in parts[:-1]:
        await asyncio.sleep(typing_delay_seconds(p, user_id=user_id))
        await bot.send_private_msg(user_id=user_id, message=p)
    last = parts[-1]
    await asyncio.sleep(typing_delay_seconds(last, user_id=user_id))
    await bot.send_private_msg(user_id=user_id, message=last)


async def _handle_one_candidate(bot: Bot, cand: ProactiveCandidate) -> bool:
    now = datetime.now()
    idle_hours = max(0, int((now - cand.last_active_at).total_seconds() // 3600))

    ok = await claim_proactive_slot(
        cand.user_id,
        now=now,
        today=now.date(),
        max_per_day=PROACTIVE_MAX_PER_DAY,
    )
    if not ok:
        return False

    try:
        data = await generate_proactive_message(
            user_id=str(cand.user_id),
            now=now,
            idle_hours=idle_hours,
            nickname=cand.nickname,
            last_user_text=cand.last_user_text,
        )
        text = (data.get("text") or "").strip()
        should_send = bool(data.get("should_send", True))

        if not should_send or not text:
            await mark_proactive_failed(cand.user_id, now=now, cooldown_seconds=900)
            return False

        await _send_bubbles(bot, cand.user_id, text)
        await mark_proactive_sent(cand.user_id, now=now, cooldown_minutes=PROACTIVE_COOLDOWN_MINUTES)
        logger.info(
            f"[proactive] sent to {cand.user_id} idle={idle_hours}h intent={data.get('intent')} reason={data.get('reason')}"
        )
        return True
    except Exception as e:
        logger.error(f"[proactive] failed user={cand.user_id}: {e}")
        await mark_proactive_failed(cand.user_id, now=now)
        return False


@scheduler.scheduled_job("interval", minutes=PROACTIVE_INTERVAL_MINUTES, id="companion_core_proactive", jitter=20)
async def proactive_job():
    if not PROACTIVE_ENABLED:
        return

    try:
        bot = get_bot()
    except Exception:
        # 机器人未连接时会抛错
        return

    if not isinstance(bot, Bot):
        return

    now = datetime.now()
    idle_before = now - timedelta(hours=PROACTIVE_IDLE_HOURS)
    candidates = await get_proactive_candidates(now=now, idle_before=idle_before, limit=20)
    if not candidates:
        return

    # 每轮最多发 1 个，避免太“机器人”
    for cand in candidates:
        sent = await _handle_one_candidate(bot, cand)
        if sent:
            break
