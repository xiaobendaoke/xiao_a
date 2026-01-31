"""用户洞察提取模块。

从聊天记录中提取用户的兴趣、偏好和习惯，用于：
1. Info Agent 智能推送（推送用户感兴趣的内容）
2. 主动互动（根据用户习惯选择话题）
3. 个性化回复（了解用户偏好）

灵感来源：memU 项目的连续学习思想。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from nonebot import logger

from .db import load_chats, save_user_insight, get_user_insights
from .llm_client import get_client, load_llm_settings


INSIGHT_SYSTEM_PROMPT = """你是一个用户分析助手。你的任务是从对话记录中提取用户的特征。

请分析以下对话记录，提取用户的：
1. **兴趣（interest）**：用户喜欢聊什么话题？（如：编程、游戏、科技、投资等）
2. **偏好（preference）**：用户的偏好是什么？（如：喜欢简洁回复、喜欢表情包等）
3. **习惯（habit）**：用户的行为习惯是什么？（如：夜猫子、早起、周末活跃等）
4. **关注话题（topic）**：用户最近在关注什么具体话题？（如：AI大模型、股市、某个项目等）

输出格式（严格 JSON）：
{
  "insights": [
    {"type": "interest", "content": "编程和技术", "confidence": 0.9},
    {"type": "topic", "content": "AI大模型", "confidence": 0.8},
    {"type": "habit", "content": "夜猫子，经常深夜聊天", "confidence": 0.7}
  ]
}

规则：
- 每种类型最多提取 3 条
- confidence 表示置信度（0-1）
- 只提取确定性较高的特征，不要猜测
- 如果对话太短或无法提取，返回空列表：{"insights": []}
- content 要简短精炼，不超过 20 个字
"""


async def extract_insights_from_chats(
    user_id: str,
    *,
    chat_limit: int = 30,
) -> list[dict[str, Any]]:
    """
    从用户最近的聊天记录中提取洞察。
    
    Args:
        user_id: 用户 ID
        chat_limit: 分析最近多少条对话
        
    Returns:
        提取的洞察列表 [{"type": "interest", "content": "...", "confidence": 0.8}, ...]
    """
    # 加载聊天记录
    chats = load_chats(str(user_id), limit=chat_limit)
    if len(chats) < 5:
        logger.debug(f"[insights] skip: not enough chats for {user_id}")
        return []
    
    # 构建对话摘要
    chat_text = "\n".join([
        f"{'用户' if c['role'] == 'user' else '小a'}：{c['content'][:100]}"
        for c in chats[-30:]  # 最近 30 条
    ])
    
    if len(chat_text) < 50:
        return []
    
    # 调用 LLM 提取
    client = get_client()
    _, _, model = load_llm_settings()
    
    try:
        response = await client.chat.completions.create(
            model=model or "qwen-plus",
            messages=[
                {"role": "system", "content": INSIGHT_SYSTEM_PROMPT},
                {"role": "user", "content": f"以下是用户最近的对话记录：\n\n{chat_text}"},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        
        raw = (response.choices[0].message.content or "").strip()
        
        # 解析 JSON
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            data = json.loads(match.group())
            insights = data.get("insights", [])
            logger.info(f"[insights] extracted {len(insights)} insights for {user_id}")
            return insights
        
        return []
        
    except Exception as e:
        logger.error(f"[insights] extract failed for {user_id}: {e}")
        return []


async def update_user_insights(user_id: str) -> int:
    """
    更新用户洞察（从聊天记录提取并保存到数据库）。
    
    Returns:
        新增/更新的洞察数量
    """
    insights = await extract_insights_from_chats(str(user_id))
    
    saved = 0
    for ins in insights:
        itype = str(ins.get("type", "")).strip()
        content = str(ins.get("content", "")).strip()
        confidence = float(ins.get("confidence", 0.5))
        
        if itype and content:
            save_user_insight(str(user_id), itype, content, confidence)
            saved += 1
    
    if saved:
        logger.info(f"[insights] saved {saved} insights for {user_id}")
    
    return saved


def get_insights_for_agent(user_id: str) -> str:
    """
    获取用于 Info Agent 决策的洞察文本。
    
    Returns:
        格式化的洞察文本（可直接嵌入 prompt）
    """
    insights = get_user_insights(str(user_id))
    if not insights:
        return "（暂无用户洞察）"
    
    # 按类型分组
    by_type: dict[str, list[str]] = {}
    type_labels = {
        "interest": "兴趣领域",
        "preference": "偏好",
        "habit": "行为习惯",
        "topic": "近期关注",
    }
    
    for ins in insights[:15]:
        t = ins["type"]
        label = type_labels.get(t, t)
        if label not in by_type:
            by_type[label] = []
        if ins["confidence"] >= 0.5:  # 只取置信度 >= 0.5 的
            by_type[label].append(ins["content"])
    
    if not by_type:
        return "（暂无用户洞察）"
    
    lines = []
    for label, items in by_type.items():
        if items:
            lines.append(f"- {label}：{', '.join(items[:3])}")
    
    return "\n".join(lines) if lines else "（暂无用户洞察）"
