"""股票小白日报 Skill。

核心功能：三层解读法
1. 身份大起底 - 把主营业务翻译成菜市场大妈都懂的话
2. 剧情还原 - 把新闻变成故事
3. 情绪与风险温度计 - 用生活化类比解释指标
4. 每日一词 - 顺便科普金融术语

使用方式：
- 定时推送：每个交易日 15:35 自动推送
- 手动触发：私聊发送 "股票日报" / "今日股市" / "涨跌榜"

配置项（.env）：
- FINANCE_DAILY_ENABLED=1         # 是否启用
- FINANCE_DAILY_HOUR=15           # 推送小时
- FINANCE_DAILY_MINUTE=35         # 推送分钟
- FINANCE_DAILY_USER_ID=123456    # 接收用户 QQ
"""

from __future__ import annotations

# 导入子模块以注册定时任务和命令
from . import scheduler  # noqa: F401

# 显式导出供外部使用
from .data import (
    fetch_daily_report_data,
    fetch_top_gainers,
    fetch_top_losers,
    StockBasic,
    StockDetail,
)
from .analyzer import (
    analyze_single_stock,
    generate_daily_report,
    generate_market_overview,
)
from .scheduler import (
    run_daily_report,
    FINANCE_DAILY_ENABLED,
    FINANCE_DAILY_HOUR,
    FINANCE_DAILY_MINUTE,
    FINANCE_DAILY_USER_ID,
)

__all__ = [
    # 数据
    "fetch_daily_report_data",
    "fetch_top_gainers",
    "fetch_top_losers",
    "StockBasic",
    "StockDetail",
    # 分析
    "analyze_single_stock",
    "generate_daily_report",
    "generate_market_overview",
    # 调度
    "run_daily_report",
    "FINANCE_DAILY_ENABLED",
    "FINANCE_DAILY_HOUR",
    "FINANCE_DAILY_MINUTE",
    "FINANCE_DAILY_USER_ID",
]
