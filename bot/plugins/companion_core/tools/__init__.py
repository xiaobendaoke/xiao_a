"""Tools 包：将小A现有能力封装为 LLM 可调用的工具。

导入本包会自动注册所有工具到 tool_registry。
"""

from __future__ import annotations
import os

# 导入所有工具模块，触发 @register_tool 装饰器
from . import (
    search_web_tool,
    weather_tool,
    stock_tool,
    memo_tool,
    schedule_tool,
    url_tool,
    file_ops_tool,
    shell_tool,
    web_browse_tool,
)


def _env_flag(name: str) -> bool:
    v = (os.getenv(name) or "").strip()
    v = v.split()[0] if v else ""
    return v.lower() in ("1", "true", "yes", "y", "on")


# OpenClaw 工具默认关闭，避免影响现有 agent 行为；按需打开：
# OPENCLAW_TOOL_ENABLED=1
if _env_flag("OPENCLAW_TOOL_ENABLED"):
    from . import openclaw_tool  # noqa: F401
