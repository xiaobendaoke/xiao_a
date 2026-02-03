"""主动消息生成（Proactive LLM）。

目标：在“对方一段时间没互动”时，生成一条自然、不打扰的私聊开场。
核心策略：优先分享有趣的内容（RSS），其次才是纯粹的关心（避免尬聊）。

输入（由调用方提供/拼接）：
- 当前时间、对方称呼、离上次互动的时长；
- 当前心情值与描述；
- 用户画像（备忘录）与最近聊天片段；
- 对方最后一句话（可选线索）。

输出：
- 严格 JSON（见 `PROACTIVE_SYSTEM_PROMPT` 约束），字段含：
  `text/reason/intent/need_reply/should_send`。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from nonebot import logger

from .db import get_all_profile
from .llm_client import get_client, load_llm_settings
from .llm_tags import extract_tags_and_clean
from .memory import get_chat_history
from .mood import mood_manager
from .utils.world_info import get_world_prompt
from .web.rss import get_random_recommendation  # 引入新写的 RSS 引擎

PROACTIVE_SYSTEM_PROMPT = """你是“小a”，用户的女朋友。
场景：你正在刷手机摸鱼，突然看到了内容，想分享给男朋友。

# 任务
阅读【输入的新闻素材】（如果提供了），用**女朋友分享八卦/好物/新闻的口吻**发给他。

# 转换规则
1. **去新闻味**：千万不要直接把标题发给他！要转化成你的话。
   - 🚫 “苹果发布新款iPad，搭载M4芯片。”
   - ✅ “卧槽！苹果新发的这个iPad也太薄了吧，想买...（暗示）”
   - ✅ “亲爱的，你看这个新闻，感觉跟你之前说的那个技术好像！”
2. **激发对话**：分享是为了和他聊天，结尾可以带个小问题。
3. **如果是无聊内容**：如果素材很枯燥（比如纯政策），就吐槽一下“今天的新闻都好无聊啊，想你了”。
4. **如果没有素材**：那就撒个娇，或者分享你刚才（虚构）吃的好吃的东西。

# 输出格式
JSON 格式（只能输出这个）：
{
  "text": "你的消息内容（中文，尽量 1-3 行）",
  "reason": "你选择这样开场的理由",
  "intent": "share",
  "need_reply": false,
  "should_send": true
}
"""

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _try_parse_json(s: str) -> dict[str, Any] | None:
    s = (s or "").strip()
    try:
        return json.loads(s)
    except Exception:
        m = _JSON_RE.search(s)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _build_user_prompt(
    *,
    user_id: str,
    now: datetime,
    idle_hours: int,
    nickname: str | None,
    last_user_text: str | None,
    rss_item: dict[str, str] | None,
) -> str:
    nickname = (nickname or "").strip() or "你"
    last_user_text = (last_user_text or "").strip()

    mood_value = mood_manager.get_user_mood(user_id)
    mood_desc = mood_manager.get_mood_desc(user_id)

    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    history = get_chat_history(user_id) or []
    hist_str = "\n".join([f'{m.get("role")}: {m.get("content")}' for m in history[-6:]]) if history else "（最近没有聊天记录）"

    rss_section = ""
    if rss_item:
        rss_section = (
            f"【你刚刷到的内容】\n"
            f"来源：{rss_item.get('source', '网络')}\n"
            f"标题：{rss_item.get('title', '')}\n"
            f"摘要：{rss_item.get('summary', '')}\n"
            f"链接：{rss_item.get('link', '')}\n"
        )
    else:
        rss_section = "【你刚刷到的内容】：(无，刷新失败了，你就随便聊聊别的吧)\n"

    return (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
        f"对方称呼：{nickname}\n"
        f"对方最近未互动：约 {idle_hours} 小时\n"
        f"你的当前心情：{mood_desc}（心情值:{mood_value}）\n"
        f"你记得的对方信息：\n{profile_str}\n"
        f"最近聊天片段：\n{hist_str}\n"
        f"{rss_section}\n"
        "请你生成一条自然的主动私聊开场。\n"
        "如果你判断现在不适合打扰，把 should_send=false。"
    )


async def generate_proactive_message(
    *,
    user_id: str,
    now: datetime,
    idle_hours: int,
    nickname: str | None,
    last_user_text: str | None,
) -> dict[str, Any]:
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[proactive][llm] init client failed: {e}")
        return {
            "should_send": False,
            "text": "",
            "reason": "client_init_failed",
            "intent": "checkin",
            "need_reply": False,
        }

    # 1. 尝试获取 RSS 素材
    rss_item = await get_random_recommendation()

    messages = [
        {"role": "system", "content": PROACTIVE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{await get_world_prompt(user_id)}\n"
                + _build_user_prompt(
                    user_id=user_id,
                    now=now,
                    idle_hours=idle_hours,
                    nickname=nickname,
                    last_user_text=last_user_text,
                    rss_item=rss_item,
                )
            ),
        },
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.85, # 稍微高一点，让分享更有趣
            timeout=30.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[proactive][llm] call failed: {e}")
        return {
            "should_send": False,
            "text": "",
            "reason": "llm_call_failed",
            "intent": "checkin",
            "need_reply": False,
        }

    data = _try_parse_json(raw)
    if data is None:
        cleaned, _, _ = extract_tags_and_clean(raw)
        data = _try_parse_json(cleaned)

    if not isinstance(data, dict):
        cleaned, _, _ = extract_tags_and_clean(raw)
        cleaned = cleaned.strip()
        return {
            "should_send": bool(cleaned),
            "text": cleaned,
            "reason": "fallback_plaintext",
            "intent": "share",
            "need_reply": False,
        }

    data.setdefault("intent", "share")
    data.setdefault("need_reply", False)
    data.setdefault("should_send", True)
    data.setdefault("reason", "")

    text = (data.get("text") or "").strip()
    text, _, _ = extract_tags_and_clean(text)
    data["text"] = text.strip()

    banned = ("系统", "检测", "定时", "任务", "prompt", "模型", "API", "标签", "主人")
    if any(x in text for x in banned):
        return {
            "should_send": False,
            "text": "",
            "reason": "banned_words",
            "intent": data.get("intent", "share"),
            "need_reply": False,
        }

    return data
