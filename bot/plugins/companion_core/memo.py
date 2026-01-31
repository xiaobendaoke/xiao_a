"""智能备忘录功能。

处理用户的“记一下”、“备忘”、“查询笔记”等指令。
"""
from __future__ import annotations

import re
from datetime import datetime
from .db import add_memo, search_memos, delete_memo
from .memory import add_memory as add_chat_memory

def _strip_prefix(text: str, prefixes: tuple[str, ...]) -> str | None:
    for p in prefixes:
        if text.startswith(p):
            return text[len(p):].strip()
    return None

async def try_handle_memo(user_id: str, user_input: str) -> str | None:
    """
    尝试处理备忘录指令。
    如果命中指令，执行操作并返回回复文本；否则返回 None。
    """
    text = (user_input or "").strip()
    if not text:
        return None

    # 1. 保存指令
    # 触发词：记一下、备忘、添加笔记、记笔记、memo
    save_content = _strip_prefix(text, ("记一下", "备忘", "添加笔记", "记笔记", "memo "))
    # 如果是 "memo: xxx" 这种格式也要兼容
    if save_content is None and text.lower().startswith("memo:"):
        save_content = text[5:].strip()
    elif save_content is None and text.lower().startswith("memo："):
        save_content = text[5:].strip()

    if save_content is not None:
        if not save_content:
            return "你要我帮你记什么呀？\n比如：记一下明天要买牛奶"
        
        # 简单提取标签：如果内容里有 #tag
        tags = []
        # 简单的 regex 提取 #tag
        tag_matches = re.findall(r"#(\S+)", save_content)
        if tag_matches:
            tags = tag_matches
            # 可以选择把标签从内容里去掉，也可以保留。这里保留比较自然。
        
        add_memo(user_id, save_content, tags=",".join(tags))
        
        # 记录到对话记忆，保持上下文连贯
        add_chat_memory(user_id, "user", text)
        add_chat_memory(user_id, "assistant", "好哒，记下来了！")
        return "好哒，记下来了！"

    # 2. 查询指令
    # 触发词：查询笔记、搜索笔记、找一下笔记、查备忘、找备忘
    # 或者：“查询 xxx” 如果 xxx 是显式的备忘相关词
    # 为了避免和“查询股票”冲突，这里稍微严格一点，或者在 handlers 里调整优先级
    search_kw = _strip_prefix(text, ("查询笔记", "搜索笔记", "找一下笔记", "查备忘", "找备忘", "搜备忘"))
    
    # 兼容“查询 xxx”但仅当 xxx 不像股票代码时。
    # 简单策略：用户必须显式说“查笔记/找笔记”相关，或者“查询/搜索”+“关键字”
    if search_kw is None:
        # 尝试“查询/搜索”+ 明确的意图
        # 如果用户只说“查询”，那是无效的
        # 如果用户说“查询 wi-fi”，我们可以尝试搜一下 memo
        # 但这容易误触。先保守点，只支持明确的指令。
        pass

    if search_kw is not None:
        # search_kw 可能是空字符串（列出最近）
        results = search_memos(user_id, search_kw)
        if not results:
            if search_kw:
                return f"我翻了一下小本本，没找到关于“{search_kw}”的记录呢。"
            else:
                return "你的备忘录是空的哦。"
        
        lines = [f"找到 {len(results)} 条记录：" if search_kw else "最近的记录："]
        for item in results:
            dt = datetime.fromtimestamp(item["created_at"]).strftime("%m-%d %H:%M")
            content = item["content"]
            # 截断过长内容
            if len(content) > 30:
                content = content[:29] + "…"
            lines.append(f"- [{dt}] {content}")
        
        reply = "\n".join(lines)
        add_chat_memory(user_id, "user", text)
        add_chat_memory(user_id, "assistant", reply)
        return reply

    return None
