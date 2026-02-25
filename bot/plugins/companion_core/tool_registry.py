"""Tool 注册中心。

职责：
- 定义 Tool 的注册接口与数据结构。
- 提供 OpenAI Function Calling 兼容的 JSON Schema 输出。
- 统一调度 Tool 执行并返回结果。

所有工具通过 `@register_tool` 装饰器注册。
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from nonebot import logger


@dataclass
class ToolParam:
    """工具参数定义"""
    name: str
    type: str  # "string" | "number" | "integer" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    enum: list[str] | None = None


@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    parameters: list[ToolParam] = field(default_factory=list)
    handler: Callable[..., Awaitable[str]] | None = None  # async (kwargs) -> str

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI Function Calling 的 JSON Schema 格式"""
        properties = {}
        required = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


# 全局工具注册表
_tools: dict[str, Tool] = {}


def register_tool(
    name: str,
    description: str,
    parameters: list[ToolParam] | None = None,
):
    """装饰器：注册一个 Tool。

    用法：
        @register_tool("search_web", "联网搜索", [ToolParam(...)])
        async def search_web(query: str) -> str:
            ...
    """
    def decorator(fn: Callable[..., Awaitable[str]]):
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters or [],
            handler=fn,
        )
        _tools[name] = tool
        logger.info(f"[tool_registry] registered: {name}")
        return fn
    return decorator


def get_all_tools() -> list[Tool]:
    """获取所有已注册工具"""
    return list(_tools.values())


def get_tools_json() -> list[dict]:
    """获取所有工具的 OpenAI Function Calling JSON Schema"""
    return [t.to_openai_schema() for t in _tools.values()]


def get_tool(name: str) -> Tool | None:
    """按名称获取工具"""
    return _tools.get(name)


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """执行指定工具并返回结果字符串。

    Args:
        name: 工具名称
        arguments: 工具参数（由 LLM 生成）

    Returns:
        工具执行结果（字符串），失败时返回错误描述
    """
    tool = _tools.get(name)
    if not tool:
        return f"[错误] 工具 '{name}' 不存在。可用工具: {', '.join(_tools.keys())}"

    if not tool.handler:
        return f"[错误] 工具 '{name}' 没有注册处理函数。"

    try:
        result = await tool.handler(**arguments)
        # 截断过长结果（避免 token 爆炸）
        if len(result) > 4000:
            result = result[:3900] + "\n...(结果已截断)"
        return result
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[tool_registry] execute '{name}' failed: {e}\n{tb}")
        return f"[工具执行失败] {name}: {e}"


def get_tools_summary() -> str:
    """生成工具摘要（调试用）"""
    if not _tools:
        return "（暂无注册工具）"
    lines = []
    for t in _tools.values():
        params = ", ".join(p.name for p in t.parameters)
        lines.append(f"- {t.name}({params}): {t.description}")
    return "\n".join(lines)
