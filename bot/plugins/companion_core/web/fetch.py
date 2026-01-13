"""网页抓取（fetch）。

职责：
- 使用 `httpx.AsyncClient` 拉取网页 HTML（`follow_redirects=True`）。
- 设置常见 UA/语言头，提高被站点接受的概率。
- 支持简单重试（`max_retries`），最终失败抛出 `RuntimeError`。

上层用途：
- 与 `web.parse.parse_readable()` 配合，将链接内容提取成可总结的正文。
"""

from __future__ import annotations
import httpx

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

async def fetch_html(url: str, timeout: float = 15.0, max_retries: int = 2) -> str:
    """
    抓取网页HTML：
    - follow_redirects=True
    - 自动重试
    """
    last_err = None
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS
    ) as client:
        for _ in range(max_retries + 1):
            try:
                r = await client.get(url, timeout=timeout)
                r.raise_for_status()
                return r.text
            except Exception as e:
                last_err = e

    raise RuntimeError(f"fetch_html failed: {last_err}")
