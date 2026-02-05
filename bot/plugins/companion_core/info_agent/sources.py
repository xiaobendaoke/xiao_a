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
    """[DEPRECATED] RSSHub 已移除，返回空。"""
    return []


def _strip_html(s: str) -> str:
    """去除 HTML 标签"""
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_rss_items(xml_text: str, category: str) -> list[InfoItem]:
    """健壮的 RSS/Atom 解析（优先使用标准 XML 解析，尽量避免正则解析）"""
    items: list[InfoItem] = []
    if not xml_text:
        return items

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception:
        # 解析失败，走原有的保守实现路径（返回空以避免抛错）
        return items

    def _strip(s: str | None) -> str:
        return _strip_html(str(s)) if s is not None else ""

    def _parse_date_str(dt_str: str | None) -> datetime | None:
        if not dt_str:
            return None
        if isinstance(dt_str, datetime):
            return dt_str
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z",  # RFC 1123
                    "%a, %d %b %Y %H:%M:%S",     # RFC 2822 without TZ
                    "%Y-%m-%dT%H:%M:%S%z",        # ISO with TZ
                    "%Y-%m-%dT%H:%M:%S",           # ISO
                    "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(dt_str[:19], fmt)
            except Exception:
                continue
        return None

    # Try RSS/RSS2: <rss> -> <channel> -> <item>
    channel = root.find("channel")  # type: ignore
    if channel is not None:
        for item_el in channel.findall("item"):
            title = _strip(item_el.findtext("title"))
            link = (item_el.findtext("link") or "").strip()
            # 常见的摘要字段
            summary = _strip(item_el.findtext("description"))
            if not summary:
                summary = _strip(item_el.findtext("summary"))
            if not summary:
                # content:encoded 常见于 RSSHub 的扩展
                summary = _strip(item_el.findtext("content:encoded"))
            pub = item_el.findtext("pubDate") or item_el.findtext("published") or item_el.findtext("updated")
            dt = _parse_date_str(pub)

            if not title and not link:
                continue
            info = {"title": title, "link": link, "summary": summary, "published": dt or pub}
            items.append(InfoItem.from_rss(info, category=category))
        if items:
            return items

    # Try Atom: <feed> -> <entry>
    for entry_el in root.findall("{http://www.w3.org/2005/Atom}entry"):
        t = entry_el.findtext("{http://www.w3.org/2005/Atom}title")
        title = _strip(t)
        link = None
        for l in entry_el.findall("{http://www.w3.org/2005/Atom}link"):
            href = l.attrib.get("href")
            rel = (l.attrib.get("rel") or "alternate").lower()
            if href and rel in ("alternate", ""):
                link = href
                break
        if not link:
            # 兼容：某些实现把链接放在其他字段
            link = entry_el.findtext("{http://www.w3.org/2005/Atom}link")
        summary = entry_el.findtext("{http://www.w3.org/2005/Atom}summary")
        if summary is None:
            summary = entry_el.findtext("{http://www.w3.org/2005/Atom}content")
        summary = _strip(summary)
        pub = entry_el.findtext("{http://www.w3.org/2005/Atom}updated") or entry_el.findtext("{http://www.w3.org/2005/Atom}published")
        dt = _parse_date_str(pub)

        if not title and not link:
            continue
        info = {"title": title or "", "link": link or "", "summary": summary or "", "published": dt or pub}
        items.append(InfoItem.from_rss(info, category=category))

    return items


async def fetch_all_rsshub_feeds() -> list[InfoItem]:
    """[DEPRECATED] RSSHub 已移除，返回空。"""
    return []


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


async def _fetch_direct_rss(url: str) -> list[InfoItem]:
    """从直接 RSS 源拉取"""
    timeout = httpx.Timeout(10.0, connect=5.0)
    
    # 根据 URL 推断分类
    category = "tech"  # 默认
    if any(kw in url.lower() for kw in ["finance", "ft", "wallstreet", "36kr", "huxiu"]):
        category = "finance"
    elif any(kw in url.lower() for kw in ["bbc", "reuters", "nytimes"]):
        category = "world"
    
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml,application/xml,*/*"})
            if resp.status_code >= 400:
                logger.warning(f"[info_agent] direct rss failed {url}: status={resp.status_code}")
                return []
            
            text = resp.text or ""
            items = _parse_rss_items(text, category)
            logger.debug(f"[info_agent] fetched {len(items)} items from {url}")
            return items
    except Exception as e:
        logger.warning(f"[info_agent] direct rss failed {url}: {e}")
        return []


async def fetch_direct_rss_feeds() -> list[InfoItem]:
    """并发拉取所有直接 RSS 源（作为 RSSHub 的备选）"""
    feeds = config.DIRECT_RSS_FEEDS
    if not feeds:
        return []
    
    tasks = [_fetch_direct_rss(url) for url in feeds]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    items: list[InfoItem] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[info_agent] direct rss exception: {r}")
            continue
        items.extend(r or [])
    
    logger.info(f"[info_agent] fetched total {len(items)} items from direct RSS")
    return items


async def fetch_all_sources() -> list[InfoItem]:
    """拉取所有信息源"""
    results = await asyncio.gather(
        fetch_all_rsshub_feeds(),
        fetch_direct_rss_feeds(),  # 添加直接 RSS 源
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
