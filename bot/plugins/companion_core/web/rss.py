"""RSS 拉取（feedparser 适配）。

职责：
- 拉取 RSS/Atom，并用 `feedparser` 解析。
- 将不同源的条目字段规范化成统一 dict：
  `{title, link, published, summary, feed_url, guid}`。

上层用途：
- `rss_push.py` 会基于这些条目做去重、生成分享文案并推送私聊。
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import asyncio
import os
import time

import httpx
from nonebot import logger

# 许多站点会对“默认 Python UA”做限流/拒绝；带 UA 更稳。
_UA = "Mozilla/5.0 (compatible; qqbot-stack/1.0; +https://example.invalid)"

# 简单内存缓存：减少重复拉取（聊天触发/定时任务都可能调用）
_CACHE_TTL_SECONDS = 180.0
_feed_cache: dict[str, tuple[float, list[dict]]] = {}  # feed_url -> (expires_ts, items)

def _rss_proxy() -> str | None:
    for k in (
        "RSS_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return None


async def _fetch_feed_bytes(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    for i in range(2):
        try:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
                },
            )
            break
        except Exception as e:
            if i < 1:
                await asyncio.sleep(0.3)
            else:
                logger.opt(exception=e).warning(f"[rss] fetch failed url={url!r}")
                return None

    if resp.status_code >= 400:
        logger.warning(f"[rss] fetch bad status={resp.status_code} url={url!r}")
        return None

    return resp.content or b""


def _parse_feed_bytes(feed_url: str, content: bytes, limit_each: int) -> list[dict]:
    import feedparser

    parsed = feedparser.parse(content or b"")
    if getattr(parsed, "bozo", 0):
        # bozo 不一定代表不可用（常见于编码/HTML 清洗），只做 debug 级别提示
        exc = getattr(parsed, "bozo_exception", None)
        if exc:
            logger.debug(f"[rss] parse bozo url={feed_url!r}: {exc!r}")

    items: list[dict] = []
    for e in (getattr(parsed, "entries", None) or [])[: max(0, int(limit_each))]:
        # feedparser 的 entry 同时支持属性与 dict 访问
        guid = (
            getattr(e, "id", None)
            or getattr(e, "guid", None)
            or (e.get("id") if isinstance(e, dict) else None)
            or (e.get("guid") if isinstance(e, dict) else None)
            or getattr(e, "link", None)
            or (e.get("link") if isinstance(e, dict) else "")
            or ""
        )
        title = getattr(e, "title", None) or (e.get("title") if isinstance(e, dict) else "") or ""
        link = getattr(e, "link", None) or (e.get("link") if isinstance(e, dict) else "") or ""
        published = getattr(e, "published", None) or (e.get("published") if isinstance(e, dict) else "") or ""
        summary = (
            getattr(e, "summary", None)
            or getattr(e, "description", None)
            or (e.get("summary") if isinstance(e, dict) else None)
            or (e.get("description") if isinstance(e, dict) else None)
            or ""
        )

        items.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
                "feed_url": feed_url,
                "guid": guid,
            }
        )

    return items

async def fetch_feeds(feed_urls: List[str], limit_each: int = 8) -> List[Dict[str, Any]]:
    """
    拉RSS（feedparser + httpx）：
    - 用 httpx 拉取（可控超时 + UA），避免 `feedparser.parse(url)` 在网络不可达时卡很久；
    - 再用 feedparser 解析内容。
    返回 items: [{title, link, published, summary, feed_url, guid}]
    """
    urls = [u.strip() for u in (feed_urls or []) if str(u or "").strip()]
    if not urls:
        return []

    now = time.time()
    out: list[dict] = []

    # 先把缓存命中的塞进去，剩余的再拉取
    to_fetch: list[str] = []
    for u in urls:
        cached = _feed_cache.get(u)
        if cached and now < cached[0]:
            out.extend((cached[1] or [])[:limit_each])
        else:
            to_fetch.append(u)

    if not to_fetch:
        return out

    timeout = httpx.Timeout(8.0, connect=4.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=_rss_proxy(),
        trust_env=False,
    ) as client:
        sem = asyncio.Semaphore(6)

        async def _one(u: str) -> tuple[str, list[dict]]:
            async with sem:
                content = await _fetch_feed_bytes(client, u)
            if not content:
                _feed_cache[u] = (now + 30.0, [])
                return u, []
            items = await asyncio.to_thread(_parse_feed_bytes, u, content, limit_each)
            _feed_cache[u] = (now + _CACHE_TTL_SECONDS, items)
            return u, items

        results = await asyncio.gather(*[_one(u) for u in to_fetch], return_exceptions=True)

    # 保持和输入 feed_urls 相对一致的顺序
    per_url: dict[str, list[dict]] = {}
    for r in results:
        if isinstance(r, Exception):
            logger.opt(exception=r).warning("[rss] fetch exception")
            continue
        u, items = r
        per_url[u] = items or []

    for u in urls:
        if u in per_url:
            out.extend(per_url[u][:limit_each])

    return out
