"""备忘录工具。

封装现有 memo.py / db.py 的备忘录 CRUD 能力。
"""

from __future__ import annotations

import json
from datetime import datetime

from ..tool_registry import register_tool, ToolParam
from ..db import add_memo, search_memos, delete_memo


@register_tool(
    name="memo_save",
    description=(
        "保存一条备忘录/笔记。当用户说要记住什么、备忘、添加笔记时使用。"
    ),
    parameters=[
        ToolParam(
            name="user_id",
            type="string",
            description="用户ID",
        ),
        ToolParam(
            name="content",
            type="string",
            description="备忘录的具体内容",
        ),
        ToolParam(
            name="tags",
            type="string",
            description="标签，逗号分隔（可选）",
            required=False,
        ),
    ],
)
async def memo_save(user_id: str, content: str, tags: str = "") -> str:
    """保存备忘录。"""
    if not content or not content.strip():
        return "备忘录内容为空，无法保存。"
    add_memo(user_id, content.strip(), tags=tags)
    return f"备忘录已保存：{content.strip()}"


@register_tool(
    name="memo_search",
    description=(
        "搜索用户的备忘录/笔记。当用户说要查笔记、找备忘录时使用。"
        "keyword 为空时返回最近的记录。"
    ),
    parameters=[
        ToolParam(
            name="user_id",
            type="string",
            description="用户ID",
        ),
        ToolParam(
            name="keyword",
            type="string",
            description="搜索关键词（留空则列出最近记录）",
            required=False,
        ),
    ],
)
async def memo_search(user_id: str, keyword: str = "") -> str:
    """搜索备忘录。"""
    results = search_memos(user_id, keyword)
    if not results:
        return "没有找到相关的备忘录。"

    items = []
    for item in results:
        dt = datetime.fromtimestamp(item["created_at"]).strftime("%m-%d %H:%M")
        items.append({"时间": dt, "内容": item["content"], "id": item.get("id")})
    return json.dumps(items, ensure_ascii=False, indent=2)
