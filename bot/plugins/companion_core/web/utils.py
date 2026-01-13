"""Web 相关小工具。

当前提供：
- `sha1(s)`：对字符串做 SHA1 哈希（用于 URL/RSS 去重与缓存键）。
"""

from __future__ import annotations
import hashlib

def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()
