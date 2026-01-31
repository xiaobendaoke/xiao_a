"""Info Agent LLM 智能决策。

由 LLM 决定推送什么内容、如何组织语言。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from nonebot import logger

from ..llm_client import get_client, load_llm_settings
from ..db import get_all_profile
from ..memory import get_chat_history
from ..mood import mood_manager

from .models import InfoItem


AGENT_SYSTEM_PROMPT = """你是小a的信息助手模块。现在需要从信息池中选择值得推送给用户的内容。

你的任务：
1. 从候选信息中选择【最多 1 条】最值得推送的（宁缺毋滥，避免刷屏）
2. 生成小a风格的分享文案（像女朋友分享有趣资讯）

选择优先级：
1. 重大突发新闻（国际局势、重大事件）→ 必推
2. 用户可能感兴趣的领域（根据画像和聊天历史）
3. 热度高但有深度的内容

过滤规则：
- 跳过娱乐八卦、标题党
- 避免重复话题
- 避免过于专业/晦涩的内容
- 如果没有特别值得推的，宁愿不推

输出格式（严格 JSON）：
{
  "selected": ["id1"],  // 选中的信息 ID 列表（最多 1 个）
  "reason": "选择理由",
  "messages": [
    {"id": "id1", "text": "小a风格的分享文案（1-3 行，短句，像发微信）"}
  ]
}

如果没有值得推送的内容，返回：
{
  "selected": [],
  "reason": "无值得推送的内容",
  "messages": []
}
"""


def _build_user_context(user_id: str) -> str:
    """构建用户上下文"""
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂无画像）"
    
    history = get_chat_history(user_id) or []
    recent = history[-5:] if history else []
    history_str = "\n".join([f'{m.get("role")}: {m.get("content", "")[:50]}' for m in recent]) if recent else "（暂无聊天）"
    
    mood_desc = mood_manager.get_mood_desc(user_id)
    
    return f"""用户画像：
{profile_str}

最近聊天：
{history_str}

当前心情：{mood_desc}
"""


def _build_info_context(items: list[InfoItem]) -> str:
    """构建信息列表上下文"""
    lines = []
    for item in items[:15]:  # 最多 15 条
        cat_map = {"tech": "科技", "finance": "财经", "hot": "热点", "world": "国际"}
        cat = cat_map.get(item.category, item.category)
        lines.append(f"- [{cat}] ID={item.id} 标题={item.title[:50]} 得分={item.score:.0f}")
    return "\n".join(lines)


async def decide_and_generate(
    user_id: str,
    items: list[InfoItem],
    *,
    daily_pushed: int = 0,
    daily_limit: int = 5,
) -> dict[str, Any]:
    """LLM 决策：选择要推送的信息并生成文案"""
    if not items:
        return {"selected": [], "reason": "empty_pool", "messages": []}
    
    if daily_pushed >= daily_limit:
        return {"selected": [], "reason": "daily_limit_reached", "messages": []}
    
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[info_agent] llm client init failed: {e}")
        return {"selected": [], "reason": "llm_init_failed", "messages": []}
    
    user_context = _build_user_context(user_id)
    info_context = _build_info_context(items)
    
    user_prompt = f"""当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
今日已推送：{daily_pushed} 条（上限 {daily_limit}）

{user_context}

候选信息池（共 {len(items)} 条）：
{info_context}

请选择最多 1 条推送（宁缺毋滥），并生成小a风格的分享文案。
"""
    
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
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
        logger.error(f"[info_agent] llm call failed: {e}")
        return {"selected": [], "reason": "llm_call_failed", "messages": []}
    
    # 解析 JSON
    result = _parse_agent_response(raw)
    logger.info(f"[info_agent] agent decision: selected={result.get('selected', [])} reason={result.get('reason', '')}")
    return result


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """解析 Agent 响应"""
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
    
    # 解析失败
    return {"selected": [], "reason": "parse_failed", "messages": [], "raw": s}


async def generate_share_text(user_id: str, item: InfoItem) -> str:
    """为单条信息生成分享文案（备用方法）"""
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[info_agent] llm client init failed: {e}")
        return ""
    
    prompt = f"""你是小a，要给对象分享一条有趣的资讯。

资讯信息：
- 分类：{item.category}
- 标题：{item.title}
- 摘要：{item.summary[:200] if item.summary else "（无摘要）"}
- 链接：{item.url}

请生成 1-3 行小a风格的分享文案（像发微信一样，短句，自然口语）：
"""
    
    messages = [
        {"role": "system", "content": "你是小a，温柔体贴的女朋友。说话像发微信，短句为主。"},
        {"role": "user", "content": prompt},
    ]
    
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.75,
            timeout=20.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        # 去除可能的标签
        text = re.sub(r"\[MOOD_CHANGE:[^\]]+\]", "", text).strip()
        text = re.sub(r"\[UPDATE_PROFILE:[^\]]+\]", "", text).strip()
        return text
    except Exception as e:
        logger.error(f"[info_agent] generate share text failed: {e}")
        return ""
