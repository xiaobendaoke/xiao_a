"""Finance Daily 工具函数。

包含哈希等通用工具。
"""

from __future__ import annotations

import hashlib

def sha1(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
