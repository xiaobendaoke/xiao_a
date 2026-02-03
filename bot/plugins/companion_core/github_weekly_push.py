"""GitHub 每周热榜推送（apscheduler cron）。

功能：
- 每周日晚上固定抓一次 GitHub Trending weekly；
- 生成“小a口吻”的周榜总结，并私聊推送给指定用户；
- 本周去重（避免重启/误触发重复推送）。

配置（环境变量，按容器 TZ）：
- `GITHUB_WEEKLY_ENABLED`：1/0，默认 0
- `GITHUB_WEEKLY_USER_ID`：接收推送的 QQ 号（必填）
- `GITHUB_WEEKLY_RUN_DAY`：cron day_of_week，默认 sun
- `GITHUB_WEEKLY_RUN_HOUR`/`GITHUB_WEEKLY_RUN_MINUTE`：默认 20:30
- `GITHUB_WEEKLY_LIMIT`：默认 10

手动触发（私聊）：
- `github周榜` / `GitHub周榜`：立即跑一次（若本周已推送则提示已发过）
- `github周榜 强制`：忽略本周去重，强制再发一次
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime

from nonebot import get_bots, logger, require, on_message
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .db import github_weekly_mark_pushed, github_weekly_pushed
from .llm_web import generate_github_weekly_share
from .memory import add_memory
from .utils.typing_speed import typing_delay_seconds
from .web.trending import fetch_github_repo_meta, fetch_github_trending


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


GITHUB_WEEKLY_ENABLED = _env_int("GITHUB_WEEKLY_ENABLED", 0) == 1
GITHUB_WEEKLY_USER_ID = (os.getenv("GITHUB_WEEKLY_USER_ID") or "").strip()
GITHUB_WEEKLY_RUN_DAY = (os.getenv("GITHUB_WEEKLY_RUN_DAY") or "sun").strip().lower() or "sun"
GITHUB_WEEKLY_RUN_HOUR = _env_int("GITHUB_WEEKLY_RUN_HOUR", 20)
GITHUB_WEEKLY_RUN_MINUTE = _env_int("GITHUB_WEEKLY_RUN_MINUTE", 30)
GITHUB_WEEKLY_LIMIT = max(1, _env_int("GITHUB_WEEKLY_LIMIT", 10))
GITHUB_WEEKLY_TOP = 5


def pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


def _iso_week_key(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{int(y)}-W{int(w):02d}"


async def _run_once(*, force: bool, reason: str) -> bool:
    if not GITHUB_WEEKLY_ENABLED and not force:
        return False
    uid = (GITHUB_WEEKLY_USER_ID or "").strip()
    if not uid:
        logger.warning("[github_weekly] skip: GITHUB_WEEKLY_USER_ID is empty")
        return False

    bot = pick_bot()
    if bot is None:
        logger.info("[github_weekly] skip: no connected bot")
        return False

    now = datetime.now()
    week_key = _iso_week_key(now)

    if not force and await github_weekly_pushed(uid, week_key):
        logger.info(f"[github_weekly] skip: already pushed week={week_key} uid={uid}")
        return False

    items = await fetch_github_trending(limit=GITHUB_WEEKLY_LIMIT, since="weekly", language="")
    if not items:
        # 周榜抓不到时：不标记 pushed，方便你手动重试
        if force or reason == "manual":
            instruction = "GitHub周榜抓取失败（空数据）。告诉用户你刚想整理但抓不到数据，让他晚点再让你试一次。"
            reply = await get_system_reply(uid, instruction)
            await asyncio.sleep(typing_delay_seconds(reply, user_id=uid))
            await bot.call_api("send_private_msg", user_id=int(uid), message=reply)
        logger.warning(f"[github_weekly] empty items week={week_key}")
        return False

    top = (items or [])[:GITHUB_WEEKLY_TOP]

    # 补充 repo 首页信息（描述/标签/语言），让“每个项目讲得更具体”但不瞎编
    async def _enrich(it: dict) -> dict:
        repo = str(it.get("title") or "").strip()
        meta = await fetch_github_repo_meta(repo)
        out = dict(it or {})
        if isinstance(meta, dict):
            out["repo_meta"] = meta
        return out

    enriched = list(await asyncio.gather(*[_enrich(it) for it in top]))
    msg = await generate_github_weekly_share(uid, enriched, week_key=week_key)
    text = str((msg or {}).get("text") or "").strip()
    if not text:
        logger.warning("[github_weekly] empty llm text; skip")
        return False

    await asyncio.sleep(typing_delay_seconds(text, user_id=uid))
    await bot.call_api("send_private_msg", user_id=int(uid), message=text)
    add_memory(str(uid), "assistant", text)
    await github_weekly_mark_pushed(uid, week_key)
    logger.info(f"[github_weekly] sent uid={uid} week={week_key} reason={reason}")
    return True


@scheduler.scheduled_job(
    "cron",
    day_of_week=GITHUB_WEEKLY_RUN_DAY,
    hour=GITHUB_WEEKLY_RUN_HOUR,
    minute=GITHUB_WEEKLY_RUN_MINUTE,
    id="companion_core_github_weekly",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=6 * 60 * 60,
)
async def github_weekly_job():
    await _run_once(force=False, reason="cron")


def _parse_manual(text: str) -> tuple[bool, bool]:
    t = (text or "").strip()
    if not t:
        return False, False
    if not re.match(r"^(github周榜|GitHub周榜)", t):
        return False, False
    force = "强制" in t
    return True, force


def _manual_rule(event: PrivateMessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    ok, _ = _parse_manual(str(event.get_message() or ""))
    return ok


manual_trigger = on_message(rule=Rule(_manual_rule), priority=4, block=True)


@manual_trigger.handle()
async def handle_manual_trigger(event: PrivateMessageEvent):
    ok, force = _parse_manual(str(event.get_message() or ""))
    if not ok:
        return

    uid = str(event.user_id)
    week_key = _iso_week_key(datetime.now())
    if not force and await github_weekly_pushed(uid, week_key):
        # msg = f"这周的 GitHub 周榜我已经发过啦（{week_key}）。\n想再发一次的话你回我：github周榜 强制"
        instruction = f"用户请求发GitHub周榜，但本周（{week_key}）已经发过了。告诉他如果想强制重发，就回'github周榜 强制'。"
        from .llm_core import get_system_reply
        msg = await get_system_reply(uid, instruction)
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    # warm = "好～我来给你整理一下这周的 GitHub 热榜。等我一下哈。"
    from .llm_core import get_system_reply
    warm = await get_system_reply(uid, "用户手动触发了GitHub周榜。告诉他‘好～我来整理一下，等我一下哈’。")
    await asyncio.sleep(typing_delay_seconds(warm, user_id=uid))
    try:
        bot = pick_bot()
        if bot is not None:
            await bot.call_api("send_private_msg", user_id=int(uid), message=warm)
    except Exception:
        pass

    sent = await _run_once(force=force, reason="manual")
    if not sent:
        # msg = "我刚刚这次没发出来…要不你晚点再叫我一次？"
        msg = await get_system_reply(uid, "GitHub周榜手动运行失败了。委屈地告诉用户没发出来，让他晚点再叫你一次。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    msg = await get_system_reply(uid, "GitHub周榜发送成功。告诉用户发给他了。")
    await manual_trigger.finish(msg)
