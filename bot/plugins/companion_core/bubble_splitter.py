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
MIN_CHARS = 6    # 过短片段合并阈值

# 递归分隔符优先级（从强到弱）
SEPARATORS = [
    ("\n\n", True),      # 段落分隔
    ("\n", True),        # 单换行（优先切分）
    ("。！？!?；;", False),  # 句末标点
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
    
    # 尝试当前分隔符
    if sep_idx < len(SEPARATORS):
        sep, is_str = SEPARATORS[sep_idx]
        
        # 逻辑修正：换行符（强分隔符）总是尝试切分，不管文本多短
        # 标点符号（弱分隔符）只有在文本超长时才切分
        start_split = False
        if is_str:
            # 强分隔符（\n\n, \n）：总是切
            start_split = True
        else:
            # 弱分隔符：只有超长才切
            if len(t) > MAX_CHARS:
                start_split = True
        
        if start_split:
            if is_str:
                parts = [p.strip() for p in t.split(sep) if p.strip()]
            else:
                parts = _split_by_chars(t, sep)

            if len(parts) > 1:
                # 分割成功，继续递归（处理子块可能还需要细分的情况）
                result: list[str] = []
                for p in parts:
                    result.extend(_recursive_split(p, sep_idx))  # 保持当前 idx（例如 \n 后可能有 \n）吗？不，应该继续往下
                    # 其实 split 已经把当前 sep 消耗了，但子块里可能还有同级 sep（如果 split 是全切的话）
                    # string.split 是全切，但 _split_by_chars 也是全切。
                    # 所以子块里不会再有当前 sep 了（除非是标点保留在末尾的情况）。
                    # 稳妥起见，对子块用 sep_idx 或 sep_idx + 1？
                    # 统一用 sep_idx + 1，避免死循环。
                    # 但 wait，如果一段话有多个换行，parts已经是切好的列表。
                    # 对每个 part，我们需要检查它是不是还超长，或者有没有更低级的标点。
                    # 所以应该传 sep_idx + 1。
                
                # 还有一种情况：如果这是一个"换行符切分"，子块可能很短，但也可能很长。
                # 所以子块需要继续递归 sep_idx+1。
                
                # 重写递归逻辑：
                final_parts = []
                for p in parts:
                    final_parts.extend(_recursive_split(p, sep_idx + 1))
                return final_parts
            
        # 如果当前分隔符没切（没匹配到，或者不该切），尝试下一个
        return _recursive_split(t, sep_idx + 1)

    # 所有分隔符都试过了
    if len(t) > MAX_CHARS:
        return _hard_chunk(t, MAX_CHARS)
    return [t]


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

    # 注意：不再"再次打包"！按换行分开的片段保持独立
    # 只做数量限制
    bubbles = merged

    # 限制气泡数量（宽松上限，尽量说完）
    if len(bubbles) > MAX_BUBBLES:
        bubbles = bubbles[:MAX_BUBBLES]

    return [p for p in bubbles if p.strip()]
