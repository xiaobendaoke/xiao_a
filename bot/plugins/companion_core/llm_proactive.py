"""主动消息生成（Proactive LLM）。

目标：在“对方一段时间没互动”时，生成一条自然、不打扰的私聊开场。

输入（由调用方提供/拼接）：
- 当前时间、对方称呼、离上次互动的时长；
- 当前心情值与描述；
- 用户画像（备忘录）与最近聊天片段；
- 对方最后一句话（可选线索）。

输出：
- 严格 JSON（见 `PROACTIVE_SYSTEM_PROMPT` 约束），字段含：
  `text/reason/intent/need_reply/should_send`。

鲁棒性：
- `_try_parse_json()` 支持从杂糅输出中提取 JSON 片段。
- 兼容 `llm.extract_tags_and_clean()` 清洗可能夹带的系统标签。
- 内置禁词过滤（出现“系统/任务/模型/API”等出戏词则强制不发送）。
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

PROACTIVE_SYSTEM_PROMPT = """你是“小a”，一个真实感很强的中文陪伴对象（温柔、体贴、自然、有生活感）。
你现在要做的是“主动发一条私聊消息给对方”，让对方感觉被惦记，但不打扰、不逼回复、不像客服通知。

硬规则：
1) 只输出 JSON（不能输出其它任何字）。
2) JSON 格式必须是：
{
  "text": "要发送的消息（中文，尽量 1-3 行，每行一句，短句）",
  "reason": "你选择这样开场的理由（给系统看的，不会发给用户）",
  "intent": "share|care|sweet|followup|checkin",
  "need_reply": true/false,
  "should_send": true/false
}
3) text 禁止出现：系统/检测/定时/任务/模型/prompt/API/标签/主人 等出戏词。
4) 不要质问/催促（不要“你怎么不回”）；如果对方可能在忙，改成“不用急着回”的温柔一句。
5) 最多 1 个 emoji（也可以不用），不要连续表情。
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
) -> str:
    nickname = (nickname or "").strip() or "你"
    last_user_text = (last_user_text or "").strip()

    mood_value = mood_manager.get_user_mood(user_id)
    mood_desc = mood_manager.get_mood_desc(user_id)

    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    history = get_chat_history(user_id) or []
    hist_str = "\n".join([f'{m.get("role")}: {m.get("content")}' for m in history[-6:]]) if history else "（最近没有聊天记录）"

    return (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
        f"对方称呼：{nickname}\n"
        f"对方最近未互动：约 {idle_hours} 小时\n"
        f"你的当前心情：{mood_desc}（心情值:{mood_value}）\n"
        f"你记得的对方信息：\n{profile_str}\n"
        f"最近聊天片段：\n{hist_str}\n"
        f"对方最近一句话（可选线索）：{last_user_text or '（无）'}\n"
        "请你生成一条自然的主动私聊开场（分享/关心/撒娇/续聊/轻轻报备都行）。\n"
        "如果你判断现在不适合打扰，把 should_send=false，text 可以留空或只给一句“不用回”的轻触达。"
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
                )
            ),
        },
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
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
            "intent": "checkin",
            "need_reply": False,
        }

    data.setdefault("intent", "checkin")
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
            "intent": data.get("intent", "checkin"),
            "need_reply": False,
        }

    return data
