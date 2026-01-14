"""现实环境感知（world info）。

提供给 LLM 的“现实上下文”，用于让回复更自然：
- 时间：把当前时间转换成更口语的“周几 + 时段 + HH:MM”描述。
- 所在地：优先从用户画像读取“所在城市/所在地”等字段。
- 天气：当能确定城市时，通过 Open-Meteo 获取“当前 + 今日预报”。

注意：
- 这里只输出“事实信息”供 LLM 编排语言；不要在这里写人设文案。
- 城市未知时，应引导用户说出城市，并记到画像“所在城市/城市”等字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from nonebot import logger

from ..db import get_all_profile
from .open_meteo import fetch_forecast, geocode_city, summarize_today_weather, get_open_meteo_proxy


def get_time_period(now: Optional[datetime] = None) -> str:
    """根据小时返回更口语的时段描述。"""
    now = now or datetime.now()
    hour = now.hour
    if 5 <= hour < 9:
        return "清晨"
    if 9 <= hour < 12:
        return "上午"
    if 12 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def get_time_description(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[now.weekday()]
    period = get_time_period(now)
    return f"{now.strftime('%Y-%m-%d')} {weekday} {period} {now.strftime('%H:%M')}"


def _extract_user_city(profile: dict) -> str:
    if not profile:
        return ""
    for key in ("所在城市", "所在地", "城市", "位置", "当前城市", "常住地", "家乡"):
        v = str(profile.get(key) or "").strip()
        if v:
            return v
    return ""


def _extract_city_from_text(user_text: str) -> str:
    """从用户文本里尝试提取“目标城市”（只在天气问句里启用的轻量启发式）。"""
    t = (user_text or "").strip()
    if not t or "天气" not in t:
        return ""

    import re

    # 这类问法通常是在问“你能不能看到我所在地的天气”，并没有给出城市名
    if re.search(r"(我在的城市|我所在的城市|我这边的城市|我这的城市|我在的地方|我所在的地方|我这边的地方)", t):
        return ""
    if re.search(r"(你能不能|你能否|你能|能不能|可不可以).*(看到|知道).*(我在|我这|我所在).*(城市|地方).*天气", t):
        return ""

    for pat in (
        r"([\u4e00-\u9fff]{2,6})(?:市)?(?:的)?(?:今天|现在|最近)?天气",
        r"(?:今天|现在|最近)?([\u4e00-\u9fff]{2,6})(?:市)?(?:的)?天气",
    ):
        m = re.search(pat, t)
        if not m:
            continue
        city = (m.group(1) or "").strip()
        if city in ("今天", "现在", "最近", "我们", "这边", "这里", "我这", "当地", "地方", "哪里", "哪儿"):
            return ""
        # 过滤明显不是城市名的片段（避免把“你能看到我在的城市的”之类当成城市）
        if re.search(r"[我你他她它这那哪能看到帮查问在的城市地方]", city):
            return ""
        return city
    return ""


async def get_world_prompt(user_id: Optional[str] = None, user_text: Optional[str] = None) -> str:
    """生成提供给 LLM 的现实上下文提示词（时间/地点/天气）。"""
    now = datetime.now()
    time_desc = get_time_description(now)
    period = get_time_period(now)

    user_city = ""
    if user_id:
        try:
            profile = get_all_profile(str(user_id)) or {}
            user_city = _extract_user_city(profile)
        except Exception as e:
            logger.warning(f"[world_info] get profile failed user_id={user_id!r}: {e}")

    query_city = _extract_city_from_text(user_text or "")
    city_for_weather = query_city or user_city

    weather_text = "（暂时不可用）"
    available = False
    if city_for_weather:
        try:
            loc = await geocode_city(city_for_weather)
            if loc:
                forecast = await fetch_forecast(loc)
                w = summarize_today_weather(loc, forecast)
                available = True
                parts: list[str] = [f"城市：{w.get('city')}", f"今日：{w.get('today_weather_text')}"]
                tmax, tmin = w.get("today_temp_max"), w.get("today_temp_min")
                if tmax is not None or tmin is not None:
                    parts.append(f"温度：{tmin} ~ {tmax}°C")
                if w.get("today_precip_prob_max") is not None:
                    parts.append(f"降水概率：最高约 {w.get('today_precip_prob_max')}%")
                if w.get("current_temp") is not None:
                    parts.append(f"当前：{w.get('current_temp')}°C（体感{w.get('current_feels_like')}°C）")
                if w.get("current_wind_speed") is not None:
                    parts.append(f"风速：{w.get('current_wind_speed')} m/s")
                weather_text = "；".join([p for p in parts if p and "None" not in p])
            else:
                logger.warning(
                    f"[world_info] open-meteo geocode empty city={city_for_weather!r} "
                    f"proxy={get_open_meteo_proxy()!r}"
                )
        except Exception as e:
            logger.warning(f"[world_info] open-meteo failed city={city_for_weather!r}: {e!r}")

    return (
        "【现实环境感知】\n"
        f"- 时间：{time_desc}\n"
        f"- 当前时段：{period}\n"
        f"- 你的所在地：{user_city or '未知'}\n"
        f"- 当地天气：{weather_text}\n"
        f"- 天气可用性：{'可用' if available else '不可用'}\n"
        "【使用要求】把时间/天气自然融入关心或提醒；用户明确问到天气/时间时可以直接回答。\n"
        "【地点提示】当所在地=未知时，可以温柔问一句“你现在在哪个城市呀”，并在用户回答后记到画像“所在城市”。\n"
        "【重要】当天气可用性=不可用：请坦诚说明你现在拿不到可靠天气信息，别编造实时天气。\n"
        "【重要】当用户问“你知不知道我在哪/我这是哪”：如果所在地不为未知，可以直接说出你记得的城市；否则请询问城市并建议你会记住。\n"
    )
