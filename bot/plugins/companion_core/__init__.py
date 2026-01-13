"""NoneBot 插件入口（companion_core）。

加载时做两件事：
- 声明插件元信息（名称/描述/用法），供 NoneBot 插件系统展示。
- 通过导入子模块触发注册：
  - `handlers`：注册私聊消息处理器；
  - `proactive`：注册主动互动定时任务；
  - `rss_push`：注册 RSS 定时推送任务。

注意：这些子模块在 import 时会产生副作用（注册 handler/scheduler、初始化 DB 等）。
"""

from nonebot.plugin import PluginMetadata
from . import handlers # 导入即注册
from . import proactive  # noqa: F401  # 注册主动互动定时任务
from . import rss_push   # noqa: F401  # 注册RSS主动分享定时任务

__plugin_meta__ = PluginMetadata(
    name="AI伴侣核心",
    description="基于Deepseek的私聊陪伴插件",
    usage="私聊即可触发",
)
