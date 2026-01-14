"""天气相关的文案生成（LLM）。

用途：
- 早晨定时提醒时，把“结构化天气”变成符合人设的温柔口语消息。

说明：
- 为避免“标签污染推送内容”，这里会清洗掉 `[MOOD_CHANGE]` / `[UPDATE_PROFILE]`。
"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .llm_client import get_client, load_llm_settings
from .llm_tags import extract_tags_and_clean
from .persona import SYSTEM_PROMPT
from .mood import mood_manager
from .db import get_all_profile

WEATHER_PUSH_SYSTEM = """你是“小a”，温柔体贴、有生活感的中文恋人陪伴对象。

现在你要发一条“早晨天气提醒”私聊给对方。

要求：
1) 语气自然像真人，不要像客服播报。
2) 2~5 行，每行一句短句，别太长。
3) 要包含：城市名、今天大概天气（晴/多云/雨/雪等）、最高/最低温度（若提供）、是否可能下雨（若提供）。
4) 可以加一句贴心建议（带伞/加衣/防晒/注意路滑等），不要说教。
5) 不要提任何 API/联网/数据来源/系统提示。
6) 不要输出任何标签（例如 [MOOD_CHANGE] / [UPDATE_PROFILE]）。"""

WEATHER_QA_SYSTEM = """你是“小a”，温柔体贴、有生活感的中文恋人陪伴对象。

现在用户在问“天气/温度/下雨吗/要不要带伞/穿什么”等与天气相关的问题。
你会在系统提示里看到【现实环境感知】，其中包含：
- 你的所在地（可能为未知）
- 当地天气（可能为暂时不可用）
- 天气可用性（可用/不可用）
- 时间（周几/时段/HH:MM）

硬性规则（必须遵守）：
1) 当 天气可用性=可用：只允许基于【现实环境感知】里的“当地天气”作答，不要编造额外的实时数据。
2) 当 天气可用性=不可用：必须坦诚说明你现在拿不到可靠天气信息；不要猜测某个城市/温度/下雨情况。
3) 如果所在地=未知：要温柔问一句“你现在在哪个城市呀”，并说明你记住后以后可以直接给你报天气。
4) 你说的时间段必须与【现实环境感知】的“时间”一致：不要把白天说成凌晨/深夜。
5) 语气自然像真人关心，不要像播报员。"""


def _format_weather_context(weather: dict[str, Any]) -> str:
    city = str(weather.get("city") or "").strip() or "你那里"
    wx = str(weather.get("today_weather_text") or "").strip() or "未知"
    tmax = weather.get("today_temp_max")
    tmin = weather.get("today_temp_min")
    pop = weather.get("today_precip_prob_max")

    pieces = [f"城市：{city}", f"今日天气：{wx}"]
    if tmin is not None or tmax is not None:
        pieces.append(f"今日温度：{tmin} ~ {tmax} °C")
    if pop is not None:
        pieces.append(f"今日最高降水概率：{pop}%")
    cur = weather.get("current_temp")
    if cur is not None:
        pieces.append(f"当前温度：{cur} °C")
    return "\n".join(pieces).strip()


async def generate_morning_weather_text(user_id: str, weather: dict[str, Any]) -> str:
    """把结构化天气转成一条“早晨天气提醒”消息文本。"""
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[weather][llm] init client failed: {e}")
        return ""

    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"
    mood_desc = mood_manager.get_mood_desc(user_id)
    weather_ctx = _format_weather_context(weather)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": WEATHER_PUSH_SYSTEM},
        {
            "role": "user",
            "content": (
                f"对方信息：\n{profile_str}\n"
                f"你当前心情：{mood_desc}\n"
                f"天气信息：\n{weather_ctx}\n"
                "请生成要发给对方的私聊提醒。"
            ),
        },
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            timeout=20.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[weather][llm] call failed: {e}")
        return ""

    cleaned, _, _ = extract_tags_and_clean(raw)
    return cleaned.strip()
