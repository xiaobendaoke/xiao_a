"""股票查询的 LLM 讲述层（小a口吻，证据驱动）。"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from nonebot import logger

from .llm_client import get_client, load_llm_settings
from .finance_daily.prompts import STOCK_DAILY_REPORT_V3_SYSTEM, FOLLOW_UP_V3_SYSTEM


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _try_json(s: str) -> Optional[dict[str, Any]]:
    # 尝试解析 JSON，如果失败则认为整个回复就是文本
    s = (s or "").strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except Exception:
        m = _JSON_RE.search(s)
        if not m:
            return None
        try:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else None
        except Exception:
            return None


async def generate_stock_chat_text(payload: dict[str, Any]) -> str:
    client = get_client()
    _, _, model = load_llm_settings()

    # 使用 V3 版“吃瓜女友”Prompt
    messages = [
        {"role": "system", "content": STOCK_DAILY_REPORT_V3_SYSTEM},
        {"role": "user", "content": json.dumps(payload or {}, ensure_ascii=False)},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.75, # 稍微调高，增加口语随机性
            timeout=45.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[stock][llm] call failed: {e}")
        return ""

    # 现在的 Prompt 可能直接输出文本，也可能输出 JSON
    # 如果输出是 JSON，提取 text 字段；如果是纯文本，直接用
    data = _try_json(raw)
    if data:
        text = str(data.get("text") or "").strip()
    else:
        text = raw
        
    # 清理一下可能的 markdown 代码块标记
    text = text.replace("```json", "").replace("```", "").strip()
    return text


async def generate_follow_up_answer(user_text: str, context_text: str) -> str:
    """进入追问模式，回答用户对之前行情的追问。"""
    client = get_client()
    _, _, model = load_llm_settings()

    messages = [
        {"role": "system", "content": FOLLOW_UP_V3_SYSTEM},
        {"role": "user", "content": f"【之前的行情分析】\n{context_text}\n\n【用户的追问】\n{user_text}"},
    ]

    try:
        # 追问模式需要更多的耐心和解释
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7, 
            timeout=45.0,
        )
        answer = (resp.choices[0].message.content or "").strip()
        return answer
    except Exception as e:
        logger.warning(f"[stock][followup] call failed: {e}")
        return "哎呀，刚才那段我有点忘了，要不我们换个话题？"


