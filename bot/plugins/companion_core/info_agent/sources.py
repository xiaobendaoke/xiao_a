"""Info Agent 数据源采集。

从多个源拉取信息：RSSHub、GitHub Trending 等。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from html import unescape

import httpx
from nonebot import logger

from . import config
from .models import InfoItem


_UA = "Mozilla/5.0 (compatible; xiao-a-bot/1.0)"


async def _fetch_rsshub_feed(route: str, category: str) -> list[InfoItem]:
    """从 RSSHub 拉取单个 feed"""
    if not config.RSSHUB_BASE:
        return []
    
    url = f"{config.RSSHUB_BASE}{route}"
    timeout = httpx.Timeout(10.0, connect=5.0)
    
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml,application/xml,*/*"})
            if resp.status_code >= 400:
                logger.warning(f"[info_agent] rsshub feed failed {route}: status={resp.status_code}")
                return []
            
            text = resp.text or ""
            items = _parse_rss_items(text, category)
            logger.debug(f"[info_agent] fetched {len(items)} items from {route}")
            return items
    except Exception as e:
        logger.warning(f"[info_agent] rsshub feed failed {route}: {e}")
        return []


def _strip_html(s: str) -> str:
    """去除 HTML 标签"""
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_rss_items(xml_text: str, category: str) -> list[InfoItem]:
    """简单解析 RSS XML（不依赖 feedparser）"""
    items: list[InfoItem] = []
    
    # 提取所有 <item> 或 <entry> 块
    for block in re.split(r"<(?:item|entry)\b", xml_text, flags=re.I)[1:]:
        block = block.split("</item>")[0].split("</entry>")[0]
        
        # 提取字段
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", block, flags=re.S | re.I)
        if m:
            title = _strip_html(m.group(1))
        
        link = ""
        m = re.search(r"<link[^>]*href=[\"']([^\"']+)[\"']", block, flags=re.I)
        if m:
            link = m.group(1).strip()
        if not link:
            m = re.search(r"<link[^>]*>(.*?)</link>", block, flags=re.S | re.I)
            if m:
                link = _strip_html(m.group(1))
        
        summary = ""
        for tag in ("description", "summary", "content"):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, flags=re.S | re.I)
            if m:
                summary = _strip_html(m.group(1))[:500]
                break
        
        pub_date = ""
        for tag in ("pubDate", "published", "updated"):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, flags=re.S | re.I)
            if m:
                pub_date = m.group(1).strip()
                break
        
        if not title and not link:
            continue
        
        item = InfoItem.from_rss({
            "title": title,
            "link": link,
            "summary": summary,
            "published": pub_date,
        }, category=category)
        items.append(item)
    
    return items


async def fetch_all_rsshub_feeds() -> list[InfoItem]:
    """并发拉取所有 RSSHub 源"""
    if not config.RSSHUB_BASE:
        logger.info("[info_agent] RSSHUB_BASE not configured, skipping")
        return []
    
    all_feeds = config.get_all_feeds()
    tasks = []
    for category, routes in all_feeds.items():
        for route in routes:
            tasks.append(_fetch_rsshub_feed(route, category))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    items: list[InfoItem] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[info_agent] feed exception: {r}")
            continue
        items.extend(r or [])
    
    logger.info(f"[info_agent] fetched total {len(items)} items from RSSHub")
    return items


async def fetch_github_trending() -> list[InfoItem]:
    """拉取 GitHub Trending"""
    # 复用现有的 trending 模块
    try:
        from ..web.trending import fetch_github_trending as _fetch
        raw_items = await _fetch(limit=10, since="daily")
        return [InfoItem.from_github(it) for it in (raw_items or [])]
    except Exception as e:
        logger.warning(f"[info_agent] github trending failed: {e}")
        return []


async def fetch_all_sources() -> list[InfoItem]:
    """拉取所有信息源"""
    results = await asyncio.gather(
        fetch_all_rsshub_feeds(),
        fetch_github_trending(),
        return_exceptions=True,
    )
    
    items: list[InfoItem] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[info_agent] source exception: {r}")
            continue
        items.extend(r or [])
    
    return items
