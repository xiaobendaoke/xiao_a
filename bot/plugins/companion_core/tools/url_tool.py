"""URL 网页总结工具。

封装现有 llm_web.py 的网页抓取与总结能力。
"""

from __future__ import annotations

from ..tool_registry import register_tool, ToolParam
from ..web.fetch import fetch_html
from ..web.parse import parse_readable


@register_tool(
    name="url_summarize",
    description=(
        "抓取并总结一个网页的主要内容。当用户分享链接并要求总结、"
        "或者需要获取某个网页的具体内容时使用。"
    ),
    parameters=[
        ToolParam(
            name="url",
            type="string",
            description="要抓取的网页 URL",
        ),
    ],
)
async def url_summarize(url: str) -> str:
    """抓取网页并返回可读内容。"""
    if not url or not url.startswith(("http://", "https://")):
        return f"无效的 URL: '{url}'"

    try:
        html = await fetch_html(url)
        if not html:
            return f"无法获取网页内容: {url}"

        parsed = parse_readable(html, url=url)
        title = parsed.get("title", "")
        text = parsed.get("text", "")

        if not text:
            return f"网页 '{title or url}' 没有提取到有效文本内容。"

        # 截断过长内容
        if len(text) > 3000:
            text = text[:2900] + "\n...(内容已截断)"

        result = f"【网页标题】{title}\n【网页正文】\n{text}"
        return result
    except Exception as e:
        return f"抓取网页失败: {e}"
