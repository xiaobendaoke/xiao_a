"""Google Programmable Search（Custom Search JSON API）检索。

职责：
- 通过 Google CSE JSON API 拉取搜索结果（标题/链接/摘要）。
- 提供给上层 `llm.py` 作为“最新资讯线索”，减少模型编造。

配置（环境变量）：
- `GOOGLE_CSE_API_KEY`：Google Cloud API Key（启用 Custom Search API）。
- `GOOGLE_CSE_CX`：Programmable Search Engine ID（也叫 cx）。
可选：
- `GOOGLE_CSE_GL`：国家/地区（如 `cn`/`us`），影响结果地域倾向。
- `GOOGLE_CSE_HL`：界面语言（如 `zh-CN`），影响结果语言倾向。
- `GOOGLE_CSE_PROXY`：代理地址（如 `http://host.docker.internal:7890`），用于容器无法直连外网的场景。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import httpx

_API_URL = "https://www.googleapis.com/customsearch/v1"
_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # query -> (expires_ts, results)
_disabled_until_ts: float = 0.0


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _settings() -> tuple[str, str, str, str]:
    api_key = _env("GOOGLE_CSE_API_KEY")
    cx = _env("GOOGLE_CSE_CX")
    gl = _env("GOOGLE_CSE_GL")
    hl = _env("GOOGLE_CSE_HL")
    api_key = api_key.split()[0] if api_key else ""
    cx = cx.split()[0] if cx else ""
    gl = gl.split()[0] if gl else ""
    hl = hl.split()[0] if hl else ""
    return api_key, cx, gl, hl


async def google_cse_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    返回: [{title, href, body}]
    - max_results: 1~10
    """
    q = (query or "").strip()
    if not q:
        return []

    now = time.time()
    global _disabled_until_ts
    if now < _disabled_until_ts:
        return []

    api_key, cx, gl, hl = _settings()
    if not api_key or not cx:
        # 短暂熔断，避免每条消息都刷 warning（上层会记录异常一次即可）
        _disabled_until_ts = max(_disabled_until_ts, now + 300.0)
        raise RuntimeError("missing GOOGLE_CSE_API_KEY/GOOGLE_CSE_CX")

    cached = _cache.get(q)
    if cached and now < cached[0]:
        return cached[1] or []

    num = max(1, min(int(max_results or 5), 10))
    params: dict[str, str] = {
        "key": api_key,
        "cx": cx,
        "q": q,
        "num": str(num),
        # 限制返回字段，降低体积/延迟
        "fields": "items(title,link,snippet),searchInformation(totalResults)",
    }
    if gl:
        params["gl"] = gl
    if hl:
        params["hl"] = hl

    timeout = httpx.Timeout(6.0, connect=2.0)
    try:
        proxy = _env("GOOGLE_CSE_PROXY")
        # 只给 Google 这条链路单独挂代理，避免影响 pip/其它外部请求
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            proxy=(proxy or None),
            trust_env=False,
        ) as client:
            resp = await client.get(_API_URL, params=params)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        # Google 在部分网络环境可能完全不可达（超时/被阻断）；熔断一段时间避免拖慢每次回复
        _disabled_until_ts = max(_disabled_until_ts, now + 1800.0)
        raise e

    # 4xx/5xx 直接抛给上层（由上层记录日志并走 RSS 兜底）
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    items = data.get("items") or []

    out: list[dict[str, Any]] = []
    for it in items[:num]:
        title = str(it.get("title") or "").strip()
        link = str(it.get("link") or "").strip()
        snippet = str(it.get("snippet") or "").strip()
        if not (title or link or snippet):
            continue
        out.append({"title": title, "href": link, "body": snippet})

    _cache[q] = (now + _CACHE_TTL_SECONDS, out)
    return out
