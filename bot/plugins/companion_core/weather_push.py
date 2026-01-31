"""早晨天气提醒（apscheduler）。

功能：
- 每天早上定时给“已记录城市”的用户发一条天气提醒。
- 文案由 LLM 按人设生成，但天气数据来自 Open-Meteo。

开关：
- `WEATHER_PUSH_ENABLED`：默认 1
- `WEATHER_PUSH_HOUR`/`WEATHER_PUSH_MINUTE`：默认 8:20
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime

from nonebot import get_bots, logger, require
from nonebot.adapters.onebot.v11 import MessageSegment

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .db import get_weather_user_targets, weather_mark_pushed, weather_pushed_today
from .llm_weather import generate_morning_weather_text
from .memory import add_memory
from .mood import mood_manager
from .voice.tts import synthesize_record_base64
from .utils.open_meteo import geocode_city, fetch_forecast, summarize_today_weather
from .utils.typing_speed import typing_delay_seconds


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


WEATHER_PUSH_ENABLED = _env_int("WEATHER_PUSH_ENABLED", 1) == 1
WEATHER_PUSH_HOUR = _env_int("WEATHER_PUSH_HOUR", 8)
WEATHER_PUSH_MINUTE = _env_int("WEATHER_PUSH_MINUTE", 20)


def pick_bot():
    """从 NoneBot 当前连接的 bots 里取一个可用 bot。"""
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


async def _push_weather_once(now: datetime):
    """执行一次“早晨天气提醒”推送（内部会做去重：同一天只发一次）。"""
    if not WEATHER_PUSH_ENABLED:
        return

    bot = pick_bot()
    if bot is None:
        logger.info("[weather] skip: no connected bot")
        return

    targets = await get_weather_user_targets()
    if not targets:
        return

    today = now.date()
    for uid, city in targets:
        try:
            if await weather_pushed_today(uid, today):
                continue

            loc = await geocode_city(city)
            if not loc:
                continue

            forecast = await fetch_forecast(loc)
            weather = summarize_today_weather(loc, forecast)

            text = await generate_morning_weather_text(uid, weather)
            if not text:
                continue

            # 尝试语音播报
            sent_voice = False
            try:
                mood = mood_manager.get_user_mood(str(uid))
                record_b64 = await synthesize_record_base64(text, mood=mood)
                # 语音发送成功前稍微等待，模拟“录制发送”
                await asyncio.sleep(typing_delay_seconds(text, user_id=uid) * 0.5)
                await bot.call_api("send_private_msg", user_id=int(uid), message=MessageSegment.record(file=record_b64))
                sent_voice = True
                logger.info(f"[weather] sent voice uid={uid} city={city!r}")
            except Exception as e:
                logger.warning(f"[weather] tts failed uid={uid}, fallback to text: {e}")

            if not sent_voice:
                await asyncio.sleep(typing_delay_seconds(text, user_id=uid))
                await bot.call_api("send_private_msg", user_id=int(uid), message=text)
                logger.info(f"[weather] sent text uid={uid} city={city!r}")
            
            add_memory(str(uid), "assistant", text)
            await weather_mark_pushed(uid, today)
        except Exception as e:
            logger.exception(f"[weather] push failed uid={uid}: {e}")


@scheduler.scheduled_job(
    "cron",
    hour=WEATHER_PUSH_HOUR,
    minute=WEATHER_PUSH_MINUTE,
    id="companion_core_weather_morning",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
)
async def weather_morning():
    await _push_weather_once(datetime.now())
