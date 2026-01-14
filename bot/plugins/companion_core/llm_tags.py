"""LLM 输出标签解析（仅负责“从回复里抽取系统标签”）。

支持两类标签（兼容独立成行/贴在句尾）：
- `[MOOD_CHANGE:x]`
- `[UPDATE_PROFILE:键=值]`（可多条）
"""

from __future__ import annotations

import re

MOOD_TAG_RE = re.compile(r"\[MOOD_CHANGE[:：]\s*(-?\d+)\s*\]", re.IGNORECASE)
PROFILE_TAG_RE = re.compile(
    r"\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]",
    re.IGNORECASE,
)


def extract_tags_and_clean(raw: str) -> tuple[str, int | None, list[tuple[str, str]]]:
    """返回：(clean_text, mood_change(or None), profile_updates[list[(k,v)]]).

 - mood_change：取最后一个出现的值
 - profile_updates：支持多条
 - clean_text：移除标签（无论是否独立成行）
 """

    mood_values: list[int] = []
    for m in MOOD_TAG_RE.finditer(raw or ""):
        try:
            mood_values.append(int(m.group(1)))
        except Exception:
            continue

    updates: list[tuple[str, str]] = []
    for p in PROFILE_TAG_RE.finditer(raw or ""):
        k, v = p.group(1).strip(), p.group(2).strip()
        if k and v:
            updates.append((k, v))

    cleaned = MOOD_TAG_RE.sub("", raw or "")
    cleaned = PROFILE_TAG_RE.sub("", cleaned)

    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in cleaned.splitlines()]
    clean_text = "\n".join(lines).strip()
    mood_change = mood_values[-1] if mood_values else None
    return clean_text, mood_change, updates

