"""气泡分割模块 - 把回复文本拆成"气泡段落"，模拟真人分段发送。

设计目标：
- 单条气泡约 90 字（更像真人聊天节奏）
- 过短片段自动合并（min_chars=15，避免碎碎碎）
- 递归分隔符优先级：段落 → 句号 → 逗号 → 硬切
- 总气泡数上限 12 条（尽量说完，不强制截断）
"""

from __future__ import annotations

import re

MAX_BUBBLES = 12
MAX_CHARS = 90   # 单条气泡目标长度
MIN_CHARS = 15   # 过短片段合并阈值

# 递归分隔符优先级（从强到弱）
SEPARATORS = [
    ("\n\n", True),      # 段落分隔（保留换行）
    ("\n", True),        # 单换行
    ("。！？!?；;", False),  # 句末标点（按字符集切）
    ("，,、：:", False),    # 弱断句标点
]


def _split_by_chars(t: str, chars: str) -> list[str]:
    """按字符集切分（标点保留在前一段末尾）。"""
    buf = ""
    out: list[str] = []
    for ch in t:
        buf += ch
        if ch in chars:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _hard_chunk(t: str, n: int) -> list[str]:
    """硬切：最后的兜底手段。"""
    t = (t or "").strip()
    if not t:
        return []
    return [t[i:i + n].strip() for i in range(0, len(t), n) if t[i:i + n].strip()]


def _recursive_split(t: str, sep_idx: int = 0) -> list[str]:
    """递归分隔符分割：优先用强分隔符，不够再用弱分隔符。"""
    t = (t or "").strip()
    if not t:
        return []
    if len(t) <= MAX_CHARS:
        return [t]

    # 尝试当前优先级的分隔符
    if sep_idx < len(SEPARATORS):
        sep, is_str = SEPARATORS[sep_idx]
        if is_str:
            # 按字符串分割（如 \n\n）
            parts = [p.strip() for p in t.split(sep) if p.strip()]
        else:
            # 按字符集分割（如标点）
            parts = _split_by_chars(t, sep)

        if len(parts) > 1:
            # 分割成功，对每个子块递归
            result: list[str] = []
            for p in parts:
                result.extend(_recursive_split(p, sep_idx))
            return result
        else:
            # 当前分隔符没效果，尝试下一个
            return _recursive_split(t, sep_idx + 1)
    else:
        # 所有分隔符都试过了，硬切
        return _hard_chunk(t, MAX_CHARS)


def bubble_parts(text: str) -> list[str]:
    """把要发送的文本拆成"气泡段落"，模拟真人分段发送。

    返回一个字符串列表，每个元素代表一条消息气泡。
    """
    s = str(text or "").strip()
    if not s:
        return []

    # 保护代码块：避免把 ``` ``` 内部拆碎
    code_re = re.compile(r"```.*?```", re.S)
    candidates: list[str] = []
    last = 0
    for m in code_re.finditer(s):
        before = s[last:m.start()]
        candidates.extend(_recursive_split(before))
        block = (m.group(0) or "").strip()
        if block:
            candidates.append(block)
        last = m.end()
    candidates.extend(_recursive_split(s[last:]))

    # 合并过短片段（避免碎碎碎）
    merged: list[str] = []
    for piece in [c for c in candidates if c.strip()]:
        if not merged:
            merged.append(piece)
            continue
        last_piece = merged[-1]
        # 如果当前片段太短，且合并后不超限，就合并
        if len(piece) < MIN_CHARS and len(last_piece) + len(piece) + 1 <= MAX_CHARS:
            merged[-1] = f"{last_piece}\n{piece}"
        elif len(last_piece) < MIN_CHARS and len(last_piece) + len(piece) + 1 <= MAX_CHARS:
            merged[-1] = f"{last_piece}\n{piece}"
        else:
            merged.append(piece)

    # 再次打包：确保单条不超限
    bubbles: list[str] = []
    current = ""
    for piece in merged:
        if not current:
            current = piece
            continue
        joiner = "\n" if ("```" in current or "```" in piece) else "\n"
        if len(current) + len(joiner) + len(piece) <= MAX_CHARS:
            current = f"{current}{joiner}{piece}"
        else:
            bubbles.append(current.strip())
            current = piece
    if current.strip():
        bubbles.append(current.strip())

    # 限制气泡数量（宽松上限，尽量说完）
    if len(bubbles) > MAX_BUBBLES:
        bubbles = bubbles[:MAX_BUBBLES]

    return [p for p in bubbles if p.strip()]
