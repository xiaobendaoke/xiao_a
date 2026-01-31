"""Info Agent 数据模型。

定义信息条目的统一数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import hashlib


def _hash_url(url: str) -> str:
    """生成 URL 的短哈希作为唯一 ID"""
    return hashlib.sha1((url or "").encode()).hexdigest()[:16]


@dataclass
class InfoItem:
    """信息条目的统一数据结构"""
    
    # 基础信息
    id: str                         # 唯一 ID（URL hash）
    source: str                     # 来源（rsshub/github/finance）
    category: str                   # 分类（tech/finance/hot/world）
    title: str
    summary: str
    url: str
    published: datetime
    
    # 评分与标签
    score: float = 0.0              # 综合打分 0-100
    tags: list[str] = field(default_factory=list)
    
    # 原始数据
    raw: dict[str, Any] = field(default_factory=dict)
    
    # 推送状态
    pushed_to: set[str] = field(default_factory=set)  # 已推送给哪些用户
    
    @classmethod
    def from_rss(cls, item: dict, category: str = "tech") -> "InfoItem":
        """从 RSS 条目创建"""
        url = str(item.get("link") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or item.get("description") or "").strip()
        
        # 解析发布时间
        pub_str = item.get("published") or item.get("pubDate") or ""
        try:
            if isinstance(pub_str, datetime):
                published = pub_str
            elif pub_str:
                # 尝试多种格式
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S"):
                    try:
                        published = datetime.strptime(pub_str[:19], fmt)
                        break
                    except Exception:
                        continue
                else:
                    published = datetime.now()
            else:
                published = datetime.now()
        except Exception:
            published = datetime.now()
        
        return cls(
            id=_hash_url(url),
            source="rsshub",
            category=category,
            title=title,
            summary=summary[:500] if summary else "",
            url=url,
            published=published,
            raw=dict(item),
        )
    
    @classmethod
    def from_github(cls, item: dict) -> "InfoItem":
        """从 GitHub Trending 条目创建"""
        url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        
        return cls(
            id=_hash_url(url),
            source="github",
            category="tech",
            title=title,
            summary=summary,
            url=url,
            published=datetime.now(),
            tags=["github", "trending"],
            raw=dict(item),
        )
    
    def is_pushed_to(self, user_id: str) -> bool:
        """检查是否已推送给指定用户"""
        return str(user_id) in self.pushed_to
    
    def mark_pushed(self, user_id: str) -> None:
        """标记已推送给指定用户"""
        self.pushed_to.add(str(user_id))
    
    def to_context(self) -> str:
        """生成供 LLM 使用的上下文描述"""
        lines = [
            f"【{self.category.upper()}】{self.title}",
        ]
        if self.summary:
            lines.append(self.summary[:200])
        if self.url:
            lines.append(f"链接：{self.url}")
        return "\n".join(lines)
