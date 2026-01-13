"""网页解析（parse）。

提供两类能力：
- `extract_urls()`：用简单正则从用户消息中提取 `http(s)://` 链接。
- `parse_readable()`：把 HTML 转为可读正文：
  1) 首选 `trafilatura` 提取正文与标题；
  2) 失败则用 `readability-lxml + BeautifulSoup`；
  3) 再失败用正则粗暴去标签兜底。

控制：
- 对正文做长度截断（默认 12000 字符），避免喂给 LLM 过长导致成本/延迟上升。
"""

from __future__ import annotations
from typing import Dict, Any
import re

def extract_urls(text: str) -> list[str]:
    """从消息中简单提取 URL"""
    url_re = re.compile(r"(https?://[^\s]+)")
    return url_re.findall(text or "")

def parse_readable(html: str, url: str = "") -> Dict[str, Any]:
    """
    解析网页正文：
    返回: {"title": str, "text": str}
    """
    html = html or ""
    title = ""
    text = ""

    # 1) trafilatura（效果最好）
    try:
        import trafilatura
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)

        meta = trafilatura.metadata.extract_metadata(html)
        if meta and meta.title:
            title = meta.title.strip()

        if extracted:
            text = extracted.strip()
    except Exception:
        pass

    # 2) readability-lxml（备用）
    if not text:
        try:
            from readability import Document
            doc = Document(html)
            title = title or (doc.short_title() or "").strip()
            content_html = doc.summary()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content_html, "html.parser")
            text = soup.get_text("\n").strip()
        except Exception:
            pass

    # 3) fallback：直接去标签
    if not text:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    # 截断避免太长（喂给模型太贵）
    if len(text) > 12000:
        text = text[:12000] + "…"

    return {"title": title, "text": text}
