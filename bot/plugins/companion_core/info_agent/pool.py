"""Info Agent 信息池。

管理所有信息条目：去重、分类、打分、缓存。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

from nonebot import logger

from . import config
from .models import InfoItem


# 内存缓存
_pool: dict[str, InfoItem] = {}  # id -> InfoItem
_pool_updated_ts: float = 0.0
_POOL_TTL_SECONDS = 30 * 60  # 30 分钟


def _is_gossip(item: InfoItem) -> bool:
    """检查是否是八卦/娱乐内容"""
    text = f"{item.title} {item.summary}".lower()
    return any(kw.lower() in text for kw in config.GOSSIP_KEYWORDS if kw)


def _calculate_score(item: InfoItem) -> float:
    """计算信息条目的综合得分"""
    score = 50.0
    
    # 时效性加分（越新越高）
    age_hours = (datetime.now() - item.published).total_seconds() / 3600
    if age_hours < 1:
        score += 20
    elif age_hours < 3:
        score += 15
    elif age_hours < 6:
        score += 10
    elif age_hours < 12:
        score += 5
    elif age_hours > 48:
        score -= 10
    
    # 分类加分
    category_bonus = {
        "world": 10,    # 国际新闻优先
        "finance": 8,   # 财经其次
        "tech": 5,      # 科技
        "hot": 3,       # 热点
    }
    score += category_bonus.get(item.category, 0)
    
    # 标题长度（太短可能是标题党）
    if len(item.title) < 10:
        score -= 5
    elif len(item.title) > 50:
        score += 3
    
    # 有摘要加分
    if item.summary:
        score += 5
    
    # GitHub 项目加分
    if item.source == "github":
        score += 5
    
    return max(0, min(100, score))


def add_items(items: list[InfoItem]) -> int:
    """添加信息条目到池中，返回新增数量"""
    global _pool_updated_ts
    
    added = 0
    for item in items or []:
        # 过滤八卦
        if _is_gossip(item):
            continue
        
        # 去重
        if item.id in _pool:
            continue
        
        # 计算得分
        item.score = _calculate_score(item)
        
        _pool[item.id] = item
        added += 1
    
    _pool_updated_ts = time.time()
    logger.info(f"[info_agent] pool added {added} items, total {len(_pool)}")
    return added


def get_pool() -> list[InfoItem]:
    """获取当前信息池（按得分降序）"""
    return sorted(_pool.values(), key=lambda x: x.score, reverse=True)


def get_pool_for_user(user_id: str, *, limit: int = 20) -> list[InfoItem]:
    """获取用户可推送的信息（排除已推送的）"""
    uid = str(user_id)
    items = [it for it in _pool.values() if not it.is_pushed_to(uid)]
    items.sort(key=lambda x: x.score, reverse=True)
    return items[:limit]


def mark_pushed(item_id: str, user_id: str) -> None:
    """标记已推送"""
    if item_id in _pool:
        _pool[item_id].mark_pushed(str(user_id))


def get_item(item_id: str) -> Optional[InfoItem]:
    """获取单个条目"""
    return _pool.get(item_id)


def is_pool_stale() -> bool:
    """检查信息池是否过期需要刷新"""
    if not _pool:
        return True
    return (time.time() - _pool_updated_ts) > _POOL_TTL_SECONDS


def clear_old_items(max_age_hours: int = 48) -> int:
    """清理过期条目"""
    global _pool
    
    now = datetime.now()
    old_ids = [
        item_id for item_id, item in _pool.items()
        if (now - item.published).total_seconds() > max_age_hours * 3600
    ]
    
    for item_id in old_ids:
        _pool.pop(item_id, None)
    
    if old_ids:
        logger.info(f"[info_agent] cleared {len(old_ids)} old items")
    
    return len(old_ids)


def get_pool_stats() -> dict:
    """获取信息池统计"""
    categories = {}
    for item in _pool.values():
        categories[item.category] = categories.get(item.category, 0) + 1
    
    return {
        "total": len(_pool),
        "updated_ts": _pool_updated_ts,
        "categories": categories,
    }
