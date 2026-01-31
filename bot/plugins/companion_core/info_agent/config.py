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
    """返回所有信息源，按分类组织"""
    return {
        "tech": INFO_FEEDS_TECH,
        "finance": INFO_FEEDS_FINANCE,
        "hot": INFO_FEEDS_HOT,
        "world": INFO_FEEDS_WORLD,
    }


# === 推送策略配置 ===
INFO_AGENT_ENABLED = _env_bool("INFO_AGENT_ENABLED", True)
INFO_AGENT_CHECK_INTERVAL_MINUTES = _env_int("INFO_AGENT_CHECK_INTERVAL_MINUTES", 30)
INFO_AGENT_DAILY_LIMIT = _env_int("INFO_AGENT_DAILY_LIMIT", 5)  # 每天最多推送几条
INFO_AGENT_IDLE_MINUTES_MIN = _env_int("INFO_AGENT_IDLE_MINUTES_MIN", 30)  # 用户空闲多久后才推

# === 免打扰时间 ===
INFO_AGENT_QUIET_START_HOUR = _env_int("INFO_AGENT_QUIET_START_HOUR", 23)
INFO_AGENT_QUIET_END_HOUR = _env_int("INFO_AGENT_QUIET_END_HOUR", 8)

# === 推送时段（优先在这些时段推送）===
INFO_AGENT_PUSH_HOURS = _env_list("INFO_AGENT_PUSH_HOURS", ["8", "12", "18"])

# === 过滤规则 ===
GOSSIP_KEYWORDS = [
    "明星", "娱乐圈", "八卦", "粉丝", "演唱会", "主演",
    "恋情", "出轨", "录制", "综艺", "流量", "剧组", "绯闻", "私生饭",
]
