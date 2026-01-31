"""Info Agent 模块 - 智能信息推送系统。

整合多个高质量信息源（RSSHub、GitHub Trending、财经数据），
通过 LLM 智能决策向用户推送有价值的信息。

功能：
- 信息采集：从多个源拉取信息
- 信息融合：去重、分类、打分
- 智能推送：LLM 决定推什么、怎么推
- 用户追问：主动搜索相关信息
"""

from nonebot.plugin import PluginMetadata

from . import scheduler  # noqa: F401  # 导入即注册定时任务

__plugin_meta__ = PluginMetadata(
    name="Info Agent",
    description="智能信息推送系统 - 整合高质量信息源",
    usage="自动推送，或发送「信息推送」手动触发",
)
