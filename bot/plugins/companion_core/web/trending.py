"""Trending sources (HTML -> pseudo RSS items).

用于补足“高热度/趋势”但没有稳定 RSS 的站点：
- GitHub Trending（抓取 `https://github.com/trending`）
- V2EX 热门（抓取 `https://www.v2ex.com/?tab=hot`）

输出字段对齐 `web.rss.fetch_feeds()` 的 item 结构：
  {title, link, published, summary, feed_url, guid}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import unescape
import asyncio
import os
import re
import time
from typing import Optional

import httpx
from nonebot import logger

_UA = "Mozilla/5.0 (compatible; qqbot-stack/1.0; +https://example.invalid)"


@dataclass(frozen=True)
class _CacheEntry:
    expires_ts: float
    items: list[dict]


_cache: dict[str, _CacheEntry] = {}

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


def _cache_get(key: str) -> Optional[list[dict]]:
    now = time.time()
    ent = _cache.get(key)
    if not ent:
        return None
    if now >= ent.expires_ts:
        _cache.pop(key, None)
        return None
    return ent.items


def _cache_set(key: str, items: list[dict], ttl_seconds: float) -> None:
    _cache[key] = _CacheEntry(expires_ts=time.time() + float(ttl_seconds), items=items)


def _now_published() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


async def fetch_github_trending(limit: int = 8, since: str = "daily", language: str = "") -> list[dict]:
    since = (since or "daily").strip().lower()
    if since not in {"daily", "weekly", "monthly"}:
        since = "daily"

    lang_q = (language or "").strip()
    url = "https://github.com/trending"
    params = {"since": since}
    if lang_q:
        params["l"] = lang_q

    cache_key = f"github_trending|{since}|{lang_q}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[: max(0, int(limit))]

    timeout = httpx.Timeout(8.0, connect=4.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=_rss_proxy(), trust_env=False) as client:
            for i in range(2):
                try:
                    resp = await client.get(url, params=params, headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"})
                    break
                except Exception:
                    if i < 1:
                        await asyncio.sleep(0.3)
                        continue
                    raise
    except Exception as e:
        logger.opt(exception=e).warning("[rss] github trending fetch failed")
        _cache_set(cache_key, [], ttl_seconds=120.0)
        return []

    if resp.status_code >= 400:
        logger.warning(f"[rss] github trending bad status={resp.status_code}")
        _cache_set(cache_key, [], ttl_seconds=120.0)
        return []

    html = resp.text or ""
    feed_url = str(resp.url)
    published = _now_published()

    items: list[dict] = []
    for block in re.split(r"<article\b", html)[1:]:
        if len(items) >= max(0, int(limit)):
            break

        # repo path: /owner/repo
        m = re.search(r'href="(/[^"\s]+/[^"\s]+)"', block)
        if not m:
            continue
        repo_path = m.group(1).strip()
        if not repo_path.startswith("/"):
            continue

        repo = repo_path.strip("/").split("?", 1)[0].split("#", 1)[0]
        link = f"https://github.com/{repo}"

        # description: first <p ...>...</p> after repo title
        desc = ""
        m_desc = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.S)
        if m_desc:
            desc = re.sub(r"<[^>]+>", " ", m_desc.group(1))
            desc = unescape(desc)
            desc = re.sub(r"\\s+", " ", desc).strip()

        # starred today/week: contains "stars today" / "stars this week"
        stars_hint = ""
        m_stars = re.search(r"([0-9,]+)\\s+stars\\s+(today|this\\s+week)", block, flags=re.I)
        if m_stars:
            stars_hint = f"{m_stars.group(1)} stars {m_stars.group(2)}"

        summary = desc
        if stars_hint:
            summary = f"{summary} ({stars_hint})".strip() if summary else stars_hint

        items.append(
            {
                "title": repo,
                "link": link,
                "published": published,
                "summary": summary,
                "feed_url": feed_url,
                "guid": link,
            }
        )

    _cache_set(cache_key, items, ttl_seconds=20 * 60.0)
    return items[: max(0, int(limit))]


async def fetch_github_repo_meta(repo: str, *, timeout_seconds: float = 12.0) -> dict:
    """抓取仓库首页的轻量信息（避免 LLM 只能靠 trending 一句话）。

    返回字段（尽量稳定/可空）：
    - description: str
    - topics: list[str]
    - language: str
    """
    r = (repo or "").strip().strip("/")
    if not r or "/" not in r:
        return {}

    cache_key = f"github_repo|{r}"
    cached = _cache_get(cache_key)
    if cached is not None:
        # _cache 的 value 类型是 list[dict]，这里用单元素 list 存放 dict，复用缓存容器
        return dict((cached[0] if cached else {}) or {})

    url = f"https://github.com/{r}"
    timeout = httpx.Timeout(float(timeout_seconds), connect=min(6.0, float(timeout_seconds)))

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=_rss_proxy(), trust_env=False) as client:
            resp = await client.get(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"})
    except Exception as e:
        logger.opt(exception=e).warning("[github_repo] fetch failed")
        _cache_set(cache_key, [{}], ttl_seconds=10 * 60.0)
        return {}

    if resp.status_code >= 400:
        logger.warning(f"[github_repo] bad status={resp.status_code}")
        _cache_set(cache_key, [{}], ttl_seconds=10 * 60.0)
        return {}

    html = resp.text or ""
    lower = html.lower()
    # 偶发会命中登录/风控页：不要硬解析，避免给 LLM 喂垃圾
    if "sign in" in lower and "join github" in lower and "repository" not in lower:
        _cache_set(cache_key, [{}], ttl_seconds=10 * 60.0)
        return {}

    # description：优先 meta name=description
    desc = ""
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, flags=re.I)
    if m:
        desc = _strip_tags(m.group(1))

    # language：常见为 itemprop="programmingLanguage"
    lang = ""
    m = re.search(r'itemprop="programmingLanguage"[^>]*>\s*([^<]+)\s*<', html, flags=re.I)
    if m:
        lang = _strip_tags(m.group(1))

    # topics：常见为 topic-tag 链接文本
    topics: list[str] = []
    for tm in re.finditer(r'topic-tag[^>]*>\s*([^<]+)\s*<', html, flags=re.I):
        t = _strip_tags(tm.group(1))
        if t:
            topics.append(t)
        if len(topics) >= 8:
            break

    out = {"description": desc, "topics": list(dict.fromkeys(topics)), "language": lang}
    _cache_set(cache_key, [out], ttl_seconds=6 * 60 * 60)
    return out


async def fetch_v2ex_hot(limit: int = 10) -> list[dict]:
    url = "https://www.v2ex.com/?tab=hot"
    cache_key = "v2ex_hot"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[: max(0, int(limit))]

    timeout = httpx.Timeout(8.0, connect=4.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=_rss_proxy(), trust_env=False) as client:
            for i in range(2):
                try:
                    resp = await client.get(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"})
                    break
                except Exception:
                    if i < 1:
                        await asyncio.sleep(0.3)
                        continue
                    raise
    except Exception as e:
        logger.opt(exception=e).warning("[rss] v2ex hot fetch failed")
        _cache_set(cache_key, [], ttl_seconds=120.0)
        return []

    if resp.status_code >= 400:
        logger.warning(f"[rss] v2ex hot bad status={resp.status_code}")
        _cache_set(cache_key, [], ttl_seconds=120.0)
        return []

    html = resp.text or ""
    feed_url = str(resp.url)
    published = _now_published()

    items: list[dict] = []
    # <span class="item_title"><a href="/t/123">title</a>
    for m in re.finditer(r'<span\\s+class="item_title"[^>]*>\\s*<a\\s+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S):
        if len(items) >= max(0, int(limit)):
            break
        href = m.group(1).strip()
        title = unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        title = re.sub(r"\\s+", " ", title).strip()
        if not href:
            continue
        if href.startswith("/"):
            link = f"https://www.v2ex.com{href}"
        else:
            link = href

        items.append(
            {
                "title": title,
                "link": link,
                "published": published,
                "summary": "",
                "feed_url": feed_url,
                "guid": link,
            }
        )

    _cache_set(cache_key, items, ttl_seconds=15 * 60.0)
    return items[: max(0, int(limit))]
