"""Skills 路由器。

使用 LLM 判断用户问题是否需要调用专业 skill。
"""

from __future__ import annotations

import json
import re
from typing import Any

from nonebot import logger

from ..llm_client import get_client, load_llm_settings
from . import get_skills_summary, list_skills


ROUTER_SYSTEM = """你是一个意图分类器，负责判断用户问题是否需要调用专业能力模块。

可用模块：
{skills_summary}

规则：
1. 只有当用户明确在询问相关专业问题时才匹配
2. 普通闲聊、情感交流不需要调用任何模块
3. 如果匹配多个，选择最相关的一个

输出（严格 JSON）：
- 需要调用：{{"skill": "模块名", "reason": "简短原因"}}
- 不需要：{{"skill": null}}
"""


_KEYWORD_PREFILTER = {
    "financial_analysis": [
        "股票", "基金", "期货", "外汇", "财经", "行情", "涨跌", "涨幅", "跌幅",
        "推荐股票", "分析", "大盘", "A股", "港股", "美股", "牛市", "熊市",
        "PE", "市盈率", "换手率", "K线", "macd", "均线", "技术面", "基本面",
    ],
    "coding_helper": [
        "代码", "编程", "python", "javascript", "java", "函数", "bug", "报错",
        "debug", "写一个", "实现", "怎么写", "代码问题", "程序", "算法",
        "排序", "循环", "数组", "类", "接口", "api", "sql", "数据库",
    ],
    "emotional_support": [
        "心情不好", "难过", "焦虑", "失眠", "压力大", "想哭", "失恋",
        "烦躁", "崩溃", "抑郁", "不开心", "郁闷", "伤心", "累了",
        "好累", "受不了", "绝望", "孤独", "寂寞", "害怕",
    ],
    "life_helper": [
        "今天吃什么", "做菜", "菜谱", "怎么做", "健身", "减肥",
        "旅行", "周末", "食谱", "做饭", "营养", "运动", "锻炼",
        "瘦身", "增肌", "卡路里", "早餐", "午餐", "晚餐",
    ],
}


def _keyword_prefilter(user_text: str) -> list[str]:
    """关键词预过滤，返回可能匹配的 skill 名称列表"""
    t = (user_text or "").lower()
    candidates = []
    for skill_name, keywords in _KEYWORD_PREFILTER.items():
        if any(kw.lower() in t for kw in keywords):
            candidates.append(skill_name)
    return candidates


async def route_skill(user_text: str) -> str | None:
    """判断是否需要调用 skill，返回 skill 名称或 None"""
    # 1) 关键词预过滤（减少 LLM 调用）
    candidates = _keyword_prefilter(user_text)
    if not candidates:
        return None

    # 2) 如果只有一个候选且置信度高，直接返回
    if len(candidates) == 1:
        # 对于金融类问题，再用 LLM 确认一下（避免误触发）
        pass  # 继续走 LLM 判断

    # 3) LLM 判断
    skills = list_skills()
    if not skills:
        return None

    skills_summary = get_skills_summary()
    system_prompt = ROUTER_SYSTEM.format(skills_summary=skills_summary)

    try:
        client = get_client()
        _, _, model = load_llm_settings()

        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户问题：{user_text}"},
            ],
            temperature=0.1,
            timeout=10.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[skills][router] LLM call failed: {e}")
        return None

    # 解析 JSON
    result = _parse_router_response(raw)
    skill_name = result.get("skill")

    if skill_name:
        logger.info(f"[skills][router] routed to: {skill_name} | reason: {result.get('reason', '')}")
    else:
        logger.debug(f"[skills][router] no skill matched for: {user_text[:50]}")

    return skill_name


def _parse_router_response(raw: str) -> dict[str, Any]:
    """解析 router 响应"""
    s = (raw or "").strip()

    # 尝试直接解析
    try:
        return json.loads(s)
    except Exception:
        pass

    # 尝试提取 JSON 块
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    return {"skill": None}
