"""NoneBot 插件入口（finance_daily）。

目标：
- A股收盘后生成“TopN涨幅/跌幅”简报；
- 证据链以“公告标题”为主，量价/估值为辅；
- 使用独立 SQLite（finance.db），不与 companion_core 的 data.db 共用。
"""

from nonebot.plugin import PluginMetadata

from . import daily_job  # noqa: F401  # 导入即注册 scheduler
from . import commands   # noqa: F401  # 注册手动触发入口

__plugin_meta__ = PluginMetadata(
    name="财经日报",
    description="A股收盘TopN涨跌+公告催化+结构化分析",
    usage="配置环境变量后自动定时推送",
)
