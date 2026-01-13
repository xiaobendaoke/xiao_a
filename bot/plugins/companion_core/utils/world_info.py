"""现实环境感知（world info）。

提供给 LLM 的“现实上下文”，用于让回复更自然：
- 时间：把当前时间转换成更口语的“周几 + 时段 + HH:MM”描述。
- 所在地：从用户画像里读取“所在城市/所在地”等字段（不做外部 API 查询）。

说明：
- 项目曾接入过和风天气（QWeather），但在当前部署网络环境下接口不可用，已从代码路径中移除，
  避免刷日志与拖慢回复；如需恢复天气能力，建议改成你可用的天气 API 再接入。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from nonebot import logger

from ..db import get_all_profile


def get_time_description(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[now.weekday()]

    if 5 <= hour < 9:
        period = "清晨"
    elif 9 <= hour < 12:
        period = "上午"
    elif 12 <= hour < 14:
        period = "中午"
    elif 14 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 23:
        period = "晚上"
    else:
        period = "深夜"

    return f"{now.strftime('%Y-%m-%d')} {weekday} {period} {now.strftime('%H:%M')}"


def _extract_user_city(profile: dict) -> str:
    if not profile:
        return ""
    for key in ("所在城市", "所在地", "城市", "位置", "当前城市", "常住地", "家乡"):
        v = str(profile.get(key) or "").strip()
        if v:
            return v
    return ""


async def get_world_prompt(user_id: Optional[str] = None) -> str:
    time_desc = get_time_description()

    user_city = ""
    if user_id:
        try:
            profile = get_all_profile(str(user_id)) or {}
            user_city = _extract_user_city(profile)
        except Exception as e:
            logger.warning(f"[world_info] get profile failed user_id={user_id!r}: {e}")

    # 天气能力已从当前版本移除，避免外部 API 不可用时刷日志/拖慢回复。
    weather = "（未启用）"
    available = False
    return (
        "【现实环境感知】\n"
        f"- 时间：{time_desc}\n"
        f"- 你的所在地：{user_city or '未知'}\n"
        f"- 当地天气：{weather}\n"
        f"- 天气可用性：{'可用' if available else '不可用'}\n"
        "【使用要求】把时间/天气自然融入关心或提醒；用户明确问到天气/时间时可以直接回答。\n"
        "【地点提示】当所在地=未知时，可以温柔问一句“你现在在哪个城市呀”，并在用户回答后记到画像“所在城市”。\n"
        "【重要】当用户问到天气：请坦诚说明你现在没有接入可用的天气能力，别编造实时天气。\n"
    )
