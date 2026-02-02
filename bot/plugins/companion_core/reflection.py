"""每日反思核心逻辑。

负责从数据库拉取昨日对话，调用 LLM 提炼高阶记忆（Reflection），并存入 RAG。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from nonebot import logger

from .db import load_chats_by_time_range
from .llm_client import get_client, load_llm_settings
from .rag_core import add_document
from .utils.time_utils import get_now


REFLECTION_SYSTEM_PROMPT = """你是一个专业的心理侧写与记忆反思助手。

【任务】
1. 阅读用户在过去24小时内的对话记录。
2. 忽略琐碎的闲聊（如“早安”、“吃了吗”、“哈哈”）。
3. 提炼出用户的**深层状态**、**新增事实**或**潜在需求**。
4. 如果没有任何有价值的信息，返回“无”。

【输出示例】
- 用户最近对工作感到焦虑，因为项目进度滞后。
- 用户提到下周要去上海出差，需要提前准备。
- 用户似乎对科幻电影很感兴趣，尤其是诺兰的电影。

【要求】
- 使用第三人称（“用户...”）。
- 语言精炼，直击重点。
- 不要摘抄原话，要进行概括和推断。
"""


async def _generate_reflection(chat_text: str) -> str | None:
    """调用 LLM 生成反思摘要。"""
    client = get_client()
    _, _, model = load_llm_settings()
    
    try:
        response = await client.chat.completions.create(
            model=model or "qwen-plus",
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"以下是用户过去24小时的对话记录：\n\n{chat_text}"},
            ],
            temperature=0.3, # 事实提炼，温度低一点
            max_tokens=500,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content or content == "无":
            return None
        return content
    except Exception as e:
        logger.error(f"[reflection] llm failed: {e}")
        return None


async def process_user_reflection(user_id: str | int) -> bool:
    """对指定用户执行一次“每日反思”。
    
    Returns:
        bool: 是否成功生成并保存了反思。
    """
    uid = str(user_id)
    now = get_now()
    yesterday = now - timedelta(days=1)
    
    # 1. 拉取过去 24 小时记录
    # 注意：db.py 需要确保有 load_chats_by_time_range 接口，或者我们直接用 load_chats 并自己在内存过滤
    # 这里假设我们去扩充 db.py，或者先用 load_chats(limit=100) 简单代替
    # 为了稳健，我们先用 load_chats 并在内存里按时间过滤
    from .db import load_chats
    
    chats = load_chats(uid, limit=200) #以此作为上限
    if not chats:
        return False
        
    # 内存过滤时间
    valid_chats = []
    min_ts = yesterday.timestamp()
    
    for c in chats:
        # created_at 是 float timestamp
        if c.get("created_at", 0) >= min_ts:
            valid_chats.append(c)
            
    if len(valid_chats) < 5:
        # 聊得太少，没必要反思
        return False
        
    # 2. 格式化文本
    # 倒序排列（load_chats 通常是时间倒序，即最近的在最前？需要确认。
    # 通常 chat log UI 是最近的在下，但 query 出来可能是 DESC。
    # 假设 load_chats 返回的是 DESC (最新的在list[0])，我们需要反转成时间正序，方便 LLM 阅读。
    valid_chats.sort(key=lambda x: x["created_at"]) 
    
    lines = []
    for c in valid_chats:
        role = "用户" if c["role"] == "user" else "小a"
        text = (c["content"] or "").replace("\n", " ").strip()
        if text:
            lines.append(f"{role}：{text}")
            
    chat_block = "\n".join(lines)
    
    # 3. LLM 生成
    summary = await _generate_reflection(chat_block)
    if not summary:
        logger.info(f"[reflection] skip uid={uid}: llm generated nothing")
        return False
        
    # 4. 存入 RAG
    # metadata 标记 type=reflection，方便后续权重调整
    # source 标记日期，方便追溯
    date_str = yesterday.strftime("%Y-%m-%d")
    meta = {
        "user_id": uid,
        "type": "reflection",
        "source": f"daily_summary_{date_str}",
        "created_at": str(now.timestamp())
    }
    
    success = await add_document(summary, metadata=meta)
    if success:
        logger.info(f"[reflection] saved for {uid}: {summary[:30]}...")
        return True
    else:
        logger.warning(f"[reflection] save rag failed for {uid}")
        return False
