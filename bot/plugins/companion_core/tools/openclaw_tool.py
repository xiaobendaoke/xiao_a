"""OpenClaw 外部能力工具。

说明：
- 该工具仅在 `OPENCLAW_TOOL_ENABLED=1` 时才会被导入注册。
- 用于把复杂任务委托给 OpenClaw，再把结果交还给小a整理输出。
"""

from __future__ import annotations

from nonebot import logger

from ..openclaw_bridge import run_openclaw_task
from ..tool_registry import register_tool, ToolParam


_MODE_ENUM = ["auto", "search", "analyze", "plan", "code"]


@register_tool(
    name="openclaw_exec",
    description=(
        "调用外部 OpenClaw 能力执行复杂任务（检索、分析、规划、代码相关任务）。"
        "当本地工具不足以完成任务，或需要更强的多步执行能力时使用。"
    ),
    parameters=[
        ToolParam(
            name="user_id",
            type="string",
            description="用户ID（用于会话隔离）",
        ),
        ToolParam(
            name="query",
            type="string",
            description="要交给 OpenClaw 执行的任务描述",
        ),
        ToolParam(
            name="mode",
            type="string",
            description="执行模式：auto/search/analyze/plan/code",
            required=False,
            enum=_MODE_ENUM,
        ),
        ToolParam(
            name="max_steps",
            type="integer",
            description="最大执行步数（1-20）",
            required=False,
        ),
    ],
)
async def openclaw_exec(
    user_id: str,
    query: str,
    mode: str = "auto",
    max_steps: int = 4,
) -> str:
    """调用 OpenClaw 执行任务。"""
    q = (query or "").strip()
    if not q:
        return "OpenClaw 任务为空，请补充你希望执行的内容。"

    if mode not in _MODE_ENUM:
        mode = "auto"
    try:
        max_steps = int(max_steps)
    except Exception:
        max_steps = 4
    max_steps = max(1, min(max_steps, 20))

    logger.info(f"[openclaw_tool] uid={user_id} mode={mode} steps={max_steps} q={q[:120]!r}")
    result = await run_openclaw_task(
        user_id=str(user_id),
        query=q,
        mode=mode,
        max_steps=max_steps,
    )
    result = (result or "").strip()
    if not result:
        return "OpenClaw 没有返回有效结果。"

    # 统一前缀，便于模型识别这是一段外部工具结果
    return f"【OpenClaw执行结果】\n{result}"
