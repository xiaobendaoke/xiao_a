"""联网搜索工具。

封装现有 llm_news.py 的搜索能力。
"""

from __future__ import annotations

from ..tool_registry import register_tool, ToolParam
from ..llm_news import (
    normalize_search_query,
    maybe_get_web_search_context,
    stash_search_sources,
)


@register_tool(
    name="search_web",
    description=(
        "联网搜索最新信息。当用户询问新闻、热点、实时价格、最近发生的事件等"
        "需要最新数据才能回答的问题时使用。不要用于常识性知识。"
    ),
    parameters=[
        ToolParam(
            name="query",
            type="string",
            description="搜索关键词，例如'今天的黄金价格'、'最近的科技新闻'",
        ),
    ],
)
async def search_web(query: str) -> str:
    """执行联网搜索并返回结果。"""
    normalized = normalize_search_query(query)
    if not normalized:
        normalized = query

    context, sources = await maybe_get_web_search_context(normalized)
    if not context or "暂时不可用" in context:
        return "搜索未找到相关结果，请稍后再试。"

    # 暂存来源供后续追问（用默认 user_id，实际调用时会被 agent 覆盖）
    # 来源追问在 agent_core 层面处理
    return context
