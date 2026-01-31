"""推送模块统一入口。

整合所有定时推送功能，提供统一的管理入口。

模块结构：
- weather: 天气早晨提醒（原 weather_push.py）
- github: GitHub 周榜推送（原 github_weekly_push.py）
- info_agent: 智能信息推送（原 info_agent/）

使用方法：
    from .push import weather_morning, github_weekly_job
"""

# === 从现有模块导入，保持向后兼容 ===

# 天气推送
from ..weather_push import (
    weather_morning,
    WEATHER_PUSH_ENABLED,
    WEATHER_PUSH_HOUR,
    WEATHER_PUSH_MINUTE,
)

# GitHub 周榜
from ..github_weekly_push import (
    github_weekly_job,
    GITHUB_WEEKLY_ENABLED,
    GITHUB_WEEKLY_USER_ID,
)

# Info Agent
from ..info_agent.scheduler import (
    info_agent_job,
)

from ..info_agent import config as info_agent_config

__all__ = [
    # 天气
    "weather_morning",
    "WEATHER_PUSH_ENABLED",
    "WEATHER_PUSH_HOUR",
    "WEATHER_PUSH_MINUTE",
    # GitHub
    "github_weekly_job",
    "GITHUB_WEEKLY_ENABLED",
    "GITHUB_WEEKLY_USER_ID",
    # Info Agent
    "info_agent_job",
    "info_agent_config",
]
