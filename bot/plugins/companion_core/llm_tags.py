"""LLM 输出标签解析（仅负责"从回复里抽取系统标签"）。

支持两类标签（兼容独立成行/贴在句尾）：
- `[MOOD_CHANGE:x]`
- `[UPDATE_PROFILE:键=值]`（可多条）

同时清理不应出现在用户可见回复中的内容：
- `[表情：xxx]` / `[动作：xxx]` 等方括号标记
- `（xxx）` 圆括号旁白/动作描述
"""

from __future__ import annotations

import re

MOOD_TAG_RE = re.compile(r"\[MOOD_CHANGE[:：]\s*(-?\d+)\s*\]", re.IGNORECASE)
PROFILE_TAG_RE = re.compile(
    r"\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]",
    re.IGNORECASE,
)

# 清理：[表情：xxx] / [动作：xxx] / [突然弹你脑门] 等所有方括号标记
BRACKET_TAG_RE = re.compile(r"\[[^\]]+\]", re.IGNORECASE)

# 清理：（旁白/动作描述）—— 中文圆括号包裹的短文本（通常是舞台指示）
# 限制长度避免误删正常括号内容
PAREN_ASIDE_RE = re.compile(r"（[^）]{1,20}）")


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

    # 移除系统标签
    cleaned = MOOD_TAG_RE.sub("", raw or "")
    cleaned = PROFILE_TAG_RE.sub("", cleaned)
    
    # 移除表情/动作等方括号标记
    cleaned = BRACKET_TAG_RE.sub("", cleaned)
    
    # 移除圆括号旁白（舞台指示）
    cleaned = PAREN_ASIDE_RE.sub("", cleaned)

    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in cleaned.splitlines()]
    clean_text = "\n".join(lines).strip()
    mood_change = mood_values[-1] if mood_values else None
    return clean_text, mood_change, updates
