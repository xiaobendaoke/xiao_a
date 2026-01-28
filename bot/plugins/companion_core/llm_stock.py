"""股票查询的 LLM 讲述层（小a口吻，证据驱动）。"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from nonebot import logger

from .llm_client import get_client, load_llm_settings


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _try_json(s: str) -> Optional[dict[str, Any]]:
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


STOCK_CHAT_SYSTEM = """你是“小a”，温柔、自然、有生活感的中文陪伴对象。

你会拿到一份【股票结构化数据】（行情 + 公司画像 + 公告标题），请像私聊一样把结果讲给对方听。

硬性规则（必须遵守）：
1) 只能基于输入里的结构化数据与【公告标题】推理，禁止编造公告细节/新闻正文。
2) 禁止“研报词/助手腔”：不要写“国内领先/龙头/我们认为/逻辑/结论/建议投资者/超预期/市场猜测”等。
3) 少术语少数字：除涨跌幅外，最多再出现 0~1 个数字；术语解释用“（就是…）”，不要用“（=…）”。
4) 不要输出 Markdown，不要列表符号，不要加链接解释一大段。
5) 不给投资建议：不要说“买/卖/加仓/抄底/止损”，只能做“复盘式描述+温柔提醒”。
6) 输出必须是严格 JSON，格式：
{
  "text": "发给用户的文本（建议 2–6 行短句）"
}

文本要求：
- 第一行必须以 `【查股】公司名(代码) +20.00%` 这种格式开头（代码用 6 位数字即可）。
- 必须包含：公司是做什么的（完整一句）+ 今天可能原因（引用一个公告标题，用中文引号“...”）+ 一句热度描述 + 明天关注点（1句）。
如果没有公告标题：必须写“标题证据不足，更像情绪/资金走动”。"""


async def generate_stock_chat_text(payload: dict[str, Any]) -> str:
    client = get_client()
    _, _, model = load_llm_settings()

    messages = [
        {"role": "system", "content": STOCK_CHAT_SYSTEM},
        {"role": "user", "content": json.dumps(payload or {}, ensure_ascii=False)},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.65,
            timeout=45.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[stock][llm] call failed: {e}")
        return ""

    data = _try_json(raw) or {}
    text = str((data or {}).get("text") or "").strip()
    return text

