"""LLM 气泡解析 - 尝试从 LLM 输出中解析语义级气泡边界。

设计目标：
- 如果 LLM 返回 [BUBBLES:["句1","句2"]] 标签，直接使用语义级分割
- 如果没有标签或解析失败，回退到 bubble_splitter.bubble_parts() 兜底

默认禁用，通过环境变量 XIAOA_BUBBLE_JSON=true 启用。
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from nonebot import logger


def _env_bool(name: str, default: bool = False) -> bool:
    """读取环境变量布尔值。"""
    v = (os.getenv(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# 是否启用 LLM 气泡 JSON 解析
BUBBLE_JSON_ENABLED = _env_bool("XIAOA_BUBBLE_JSON", False)

# 气泡 JSON 标签的正则
BUBBLE_TAG_RE = re.compile(r"\[BUBBLES:\s*(\[.*?\])\s*\]", re.S)


def parse_bubble_tag(text: str) -> Optional[list[str]]:
    """从文本中解析 [BUBBLES:["a","b"]] 标签。

    Returns:
        解析成功返回字符串列表，否则返回 None
    """
    if not BUBBLE_JSON_ENABLED:
        return None

    m = BUBBLE_TAG_RE.search(text)
    if not m:
        return None

    json_str = m.group(1)
    try:
        arr = json.loads(json_str)
        if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
            # 过滤空字符串
            bubbles = [s.strip() for s in arr if s.strip()]
            if bubbles:
                logger.debug(f"[bubble] parsed {len(bubbles)} bubbles from LLM tag")
                return bubbles
    except json.JSONDecodeError as e:
        logger.warning(f"[bubble] failed to parse BUBBLES tag: {e}")

    return None


def strip_bubble_tag(text: str) -> str:
    """从文本中移除 [BUBBLES:...] 标签。"""
    return BUBBLE_TAG_RE.sub("", text).strip()


# 气泡 JSON 的 prompt 指令（添加到 system prompt）
BUBBLE_JSON_SYSTEM = """【气泡输出格式】：如果你想分多条消息发送，可以在回复末尾另起一行输出 [BUBBLES:["句1","句2","句3"]]（JSON 数组格式，每条 30~80 字）。不输出也可以，系统会自动分段。"""
