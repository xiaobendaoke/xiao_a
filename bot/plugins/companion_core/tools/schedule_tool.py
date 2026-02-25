"""日程提醒工具。

封装现有 scheduler_custom.py 的日程管理能力。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..tool_registry import register_tool, ToolParam
from ..db import save_schedule, get_pending_schedules


@register_tool(
    name="schedule_set",
    description=(
        "设置一个定时提醒/闹钟。当用户说要提醒他做什么事、设闹钟时使用。"
        "需要提供提醒时间（相对或绝对）和提醒内容。"
    ),
    parameters=[
        ToolParam(
            name="user_id",
            type="string",
            description="用户ID",
        ),
        ToolParam(
            name="minutes_later",
            type="integer",
            description="从现在起多少分钟后提醒（优先使用此参数）",
            required=False,
        ),
        ToolParam(
            name="target_time",
            type="string",
            description="目标时间，格式 HH:MM（24小时制）。如果时间已过默认明天。",
            required=False,
        ),
        ToolParam(
            name="content",
            type="string",
            description="提醒内容，如'喝水'、'开会'、'取快递'",
        ),
    ],
)
async def schedule_set(
    user_id: str,
    content: str,
    minutes_later: int | None = None,
    target_time: str | None = None,
) -> str:
    """设置定时提醒。"""
    now = datetime.now()

    if minutes_later is not None and minutes_later > 0:
        trigger_dt = now + timedelta(minutes=minutes_later)
    elif target_time:
        try:
            parts = target_time.split(":")
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            trigger_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if trigger_dt <= now:
                trigger_dt += timedelta(days=1)
        except Exception:
            return f"时间格式不正确：'{target_time}'，请使用 HH:MM 格式。"
    else:
        return "请提供提醒时间（minutes_later 或 target_time）。"

    save_schedule(user_id, int(trigger_dt.timestamp()), content)
    fmt = trigger_dt.strftime("%m-%d %H:%M") if trigger_dt.date() != now.date() else trigger_dt.strftime("%H:%M")
    return f"提醒已设置：{fmt} - {content}"
