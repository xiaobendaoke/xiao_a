"""Info Agent 配置。

信息源、推送策略、分类规则等配置项。
"""

from __future__ import annotations

import os
import re


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    """解析逗号/空格分隔的列表"""
    v = (os.getenv(name) or "").strip()
    if not v:
        return list(default or [])
    return [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]


# === RSSHub 配置 ===
RSSHUB_BASE = (os.getenv("RSSHUB_BASE") or "").strip().rstrip("/")

# === 信息源配置 ===
# 科技类
INFO_FEEDS_TECH = _env_list("INFO_FEEDS_TECH", [
    "/hackernews/best",      # Hacker News 最佳
    "/sspai/matrix",         # 少数派精选
    "/36kr/newsflashes",     # 36氪快讯
    "/solidot/index",        # Solidot
])

# 财经类
INFO_FEEDS_FINANCE = _env_list("INFO_FEEDS_FINANCE", [
    "/cls/depth",            # 财联社深度
    "/wallstreetcn/news/global",  # 华尔街见闻
    "/eastmoney/report",     # 东方财富研报
])

# 热点类
INFO_FEEDS_HOT = _env_list("INFO_FEEDS_HOT", [
    "/weibo/hotmap",         # 微博热搜
    "/zhihu/hotlist",        # 知乎热榜
    "/v2ex/topics/hot",      # V2EX 热门
])

# 国际类
INFO_FEEDS_WORLD = _env_list("INFO_FEEDS_WORLD", [
    "/bbc/world",            # BBC 国际
    "/reuters/world",        # 路透社
    "/ft",                   # 金融时报
])

# 合并所有源
def get_all_feeds() -> dict[str, list[str]]:
    """返回所有 RSSHub 信息源，按分类组织"""
    return {
        "tech": INFO_FEEDS_TECH,
        "finance": INFO_FEEDS_FINANCE,
        "hot": INFO_FEEDS_HOT,
        "world": INFO_FEEDS_WORLD,
    }


# === 直接 RSS 源（不依赖 RSSHub，作为备选）===
DIRECT_RSS_FEEDS = _env_list("DIRECT_RSS_FEEDS", [
    # 科技
    "https://sspai.com/feed",               # 少数派
    "https://www.solidot.org/index.rss",    # Solidot
    "https://news.ycombinator.com/rss",     # Hacker News
    # 综合/财经
    "https://www.36kr.com/feed",            # 36氪
    "https://www.huxiu.com/rss/0.xml",      # 虎嗅
    "http://www.ftchinese.com/rss/news",    # FT中文网
])


# === 推送策略配置 ===
INFO_AGENT_ENABLED = _env_bool("INFO_AGENT_ENABLED", True)
INFO_AGENT_CHECK_INTERVAL_MINUTES = _env_int("INFO_AGENT_CHECK_INTERVAL_MINUTES", 30)
INFO_AGENT_DAILY_LIMIT = _env_int("INFO_AGENT_DAILY_LIMIT", 3)  # 每天最多推送几条（避免刷屏）
INFO_AGENT_IDLE_MINUTES_MIN = _env_int("INFO_AGENT_IDLE_MINUTES_MIN", 30)  # 用户空闲多久后才推

# === 免打扰时间 ===
INFO_AGENT_QUIET_START_HOUR = _env_int("INFO_AGENT_QUIET_START_HOUR", 23)
INFO_AGENT_QUIET_END_HOUR = _env_int("INFO_AGENT_QUIET_END_HOUR", 8)

# === 智能时段配置 ===
# 不再使用固定推送时段，改为学习用户活跃时间
# 以下为兜底配置（当用户数据不足时使用）
INFO_AGENT_FALLBACK_PUSH_HOURS = _env_list("INFO_AGENT_FALLBACK_PUSH_HOURS", ["9", "13", "19"])
# 活跃度阈值：用户在某小时的消息占比超过此值才认为是活跃时段
INFO_AGENT_ACTIVE_HOUR_THRESHOLD = 0.05  # 5%

# === 过滤规则 ===
GOSSIP_KEYWORDS = [
    "明星", "娱乐圈", "八卦", "粉丝", "演唱会", "主演",
    "恋情", "出轨", "录制", "综艺", "流量", "剧组", "绯闻", "私生饭",
]

