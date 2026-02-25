"""文件操作工具（沙箱模式）。

安全策略：
- 只允许在指定的工作目录下操作（默认 /app/workspace）
- 禁止访问系统目录
- 读取文件有大小限制
"""

from __future__ import annotations

import os
from pathlib import Path

from nonebot import logger
from ..tool_registry import register_tool, ToolParam


# 沙箱工作目录（通过环境变量可配置）
_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", "/app/workspace")).resolve()
# 最大读取文件大小 (字节)
_MAX_READ_SIZE = 100_000  # 100KB


def _safe_path(path: str) -> Path | None:
    """验证路径是否在沙箱目录内。"""
    try:
        resolved = (_WORKSPACE / path).resolve()
        if not str(resolved).startswith(str(_WORKSPACE)):
            return None  # 路径逃逸
        return resolved
    except Exception:
        return None


@register_tool(
    name="file_read",
    description=(
        "读取工作目录下的文件内容。路径相对于工作目录（/app/workspace）。"
        "用于查看用户文件、配置文件、日志等。"
    ),
    parameters=[
        ToolParam(
            name="path",
            type="string",
            description="相对于工作目录的文件路径，如 'notes.txt'、'config/app.yml'",
        ),
    ],
)
async def file_read(path: str) -> str:
    """读取文件。"""
    safe = _safe_path(path)
    if safe is None:
        return f"[安全限制] 路径 '{path}' 不允许访问。只能操作工作目录内的文件。"

    if not safe.exists():
        return f"文件不存在: {path}"

    if not safe.is_file():
        return f"'{path}' 不是一个文件。"

    size = safe.stat().st_size
    if size > _MAX_READ_SIZE:
        return f"文件太大（{size} 字节），超过了 {_MAX_READ_SIZE} 字节的限制。"

    try:
        content = safe.read_text(encoding="utf-8", errors="replace")
        return f"文件 '{path}' 的内容（{len(content)} 字符）：\n{content}"
    except Exception as e:
        return f"读取文件失败: {e}"


@register_tool(
    name="file_write",
    description=(
        "写入文件到工作目录。路径相对于工作目录（/app/workspace）。"
        "会自动创建不存在的父目录。已有文件会被覆盖。"
    ),
    parameters=[
        ToolParam(
            name="path",
            type="string",
            description="相对于工作目录的文件路径",
        ),
        ToolParam(
            name="content",
            type="string",
            description="要写入的文件内容",
        ),
    ],
)
async def file_write(path: str, content: str) -> str:
    """写入文件。"""
    safe = _safe_path(path)
    if safe is None:
        return f"[安全限制] 路径 '{path}' 不允许访问。"

    try:
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
        logger.info(f"[file_ops] wrote {len(content)} chars to {path}")
        return f"文件已写入: {path}（{len(content)} 字符）"
    except Exception as e:
        return f"写入文件失败: {e}"


@register_tool(
    name="file_list",
    description=(
        "列出工作目录下某个路径的文件和文件夹。"
        "路径相对于工作目录（/app/workspace）。"
    ),
    parameters=[
        ToolParam(
            name="path",
            type="string",
            description="相对于工作目录的目录路径，留空则列出根目录",
            required=False,
        ),
    ],
)
async def file_list(path: str = ".") -> str:
    """列出目录内容。"""
    safe = _safe_path(path)
    if safe is None:
        return f"[安全限制] 路径 '{path}' 不允许访问。"

    if not safe.exists():
        return f"目录不存在: {path}"

    if not safe.is_dir():
        return f"'{path}' 不是一个目录。"

    try:
        items = []
        for child in sorted(safe.iterdir()):
            rel = child.relative_to(_WORKSPACE)
            if child.is_dir():
                items.append(f"📁 {rel}/")
            else:
                size = child.stat().st_size
                items.append(f"📄 {rel} ({size} bytes)")

        if not items:
            return f"目录 '{path}' 是空的。"

        return f"目录 '{path}' 内容：\n" + "\n".join(items[:50])
    except Exception as e:
        return f"列出目录失败: {e}"
