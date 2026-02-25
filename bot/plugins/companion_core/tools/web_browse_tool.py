"""浏览器自动化工具。

使用 Playwright 进行网页自动化操作：
- 获取网页文本内容
- 截图
- 表单填写

依赖：playwright（需要 pip install playwright && playwright install chromium）
如果 Playwright 未安装，工具会降级到 HTTP 抓取。
"""

from __future__ import annotations

import asyncio
import os

from nonebot import logger
from ..tool_registry import register_tool, ToolParam


# 是否启用 Playwright（需要安装依赖）
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.info("[web_browse] playwright not installed, using HTTP fallback")


async def _browse_with_playwright(url: str, action: str = "text") -> str:
    """使用 Playwright 浏览网页。"""
    if not _PLAYWRIGHT_AVAILABLE:
        return await _browse_with_http(url)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # 设置合理超时
            page.set_default_timeout(15000)

            await page.goto(url, wait_until="domcontentloaded")
            # 等页面稳定
            await asyncio.sleep(1)

            if action == "text":
                # 获取页面主要文本
                content = await page.evaluate("""
                    () => {
                        // 移除 script/style
                        document.querySelectorAll('script, style, nav, footer, header').forEach(el => el.remove());
                        return document.body.innerText || '';
                    }
                """)
                title = await page.title()
                await browser.close()

                if not content:
                    return f"页面 '{title}' 没有提取到文本内容。"
                if len(content) > 3000:
                    content = content[:2900] + "\n...(内容已截断)"
                return f"【{title}】\n{content}"

            elif action == "screenshot":
                # 截图（返回描述，不返回二进制）
                title = await page.title()
                await browser.close()
                return f"已访问页面：{title} ({url})"

            await browser.close()
            return "未知的操作类型。"

    except Exception as e:
        logger.warning(f"[web_browse] playwright failed: {e}, falling back to HTTP")
        return await _browse_with_http(url)


async def _browse_with_http(url: str) -> str:
    """降级：使用 HTTP 抓取（无 JS 执行）。"""
    try:
        from ..web.fetch import fetch_html
        from ..web.parse import parse_readable

        html = await fetch_html(url)
        if not html:
            return f"无法访问: {url}"

        parsed = parse_readable(html, url=url)
        title = parsed.get("title", "")
        text = parsed.get("text", "")

        if not text:
            return f"页面 '{title or url}' 没有提取到内容。"
        if len(text) > 3000:
            text = text[:2900] + "\n...(内容已截断)"
        return f"【{title}】\n{text}"

    except Exception as e:
        return f"HTTP 抓取失败: {e}"


@register_tool(
    name="web_browse",
    description=(
        "使用浏览器访问网页并获取内容。支持 JavaScript 渲染的动态页面。"
        "当 url_summarize 无法获取内容（如 SPA 应用）时使用此工具。"
    ),
    parameters=[
        ToolParam(
            name="url",
            type="string",
            description="要访问的网页 URL",
        ),
        ToolParam(
            name="action",
            type="string",
            description="操作类型：'text'(获取文本) 或 'screenshot'(截图描述)",
            required=False,
            enum=["text", "screenshot"],
        ),
    ],
)
async def web_browse(url: str, action: str = "text") -> str:
    """浏览器访问网页。"""
    if not url or not url.startswith(("http://", "https://")):
        return f"无效的 URL: '{url}'"
    return await _browse_with_playwright(url, action)
