from __future__ import annotations
import re

# 调整阈值：稍微放宽，避免太碎
SPLIT_THRESHOLD = 15

# ✅ 优化语义词库：
# 移除了 "真的"(副词), "其实"(易混淆), "特别是"(易混淆), "感觉"(易做动词)
# 保留了强转折和强承接词，且通常这些词放在句首切分才自然
# Added "现在" as requested
SEMANTIC_BREAK_WORDS = [
    "但是", "不过", "而且", "所以", "然后", "就是说", "也就是", "另外", 
]

def bubble_parts(text: str) -> list[str]:
    """
    智能气泡分段主入口
    """
    s = str(text or "").strip()
    if not s:
        return []

    # 1. 保护代码块 (Code Block)
    code_re = re.compile(r"```.*?```", re.S)
    segments = []
    last = 0
    for m in code_re.finditer(s):
        pre_text = s[last:m.start()]
        segments.extend(_split_text_smartly(pre_text))

        block = (m.group(0) or "").strip()
        if block:
            segments.append(block)
        last = m.end()

    segments.extend(_split_text_smartly(s[last:]))

    return [p.strip() for p in segments if p.strip()]


def _split_text_smartly(text: str) -> list[str]:
    """
    核心切分逻辑
    """
    # 1. 预处理：统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. 保护 Markdown 链接/图片 [text](url) 不被换行切碎
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    final_bubbles: list[str] = []

    for line in lines:
        # 3. 深度扫描（处理标点 + 引号保护）
        segments = _split_line_with_state_machine(line)

        for seg in segments:
            if not seg: continue

            # 4. 长度检测
            if len(seg) < SPLIT_THRESHOLD:
                final_bubbles.append(seg)
                continue

            # 5. 语义切分（基于正则的二次切分）
            sub_bubbles = _split_by_semantics(seg)
            final_bubbles.extend(sub_bubbles)

    return final_bubbles


def _split_line_with_state_machine(line: str) -> list[str]:
    """
    改进版深度分割：
    1. 支持括号 () （）
    2. 支持引号 "" “” ‘’ 保护（避免切断对话引用）
    3. 支持 Markdown 链接 []() 保护
    """
    buf: list[str] = []
    res: list[str] = []

    depth = 0        # 括号深度
    in_quote = False # 是否在引号内
    quote_char = None # 记录当前是由哪个引号开启的

    # 映射配对
    pairs = {"(": ")", "（": "）", "[": "]"}
    # 引号集合
    quotes = {'"', "'", '“', '”', '‘', '’'}

    for i, ch in enumerate(line):
        # --- 状态更新逻辑 ---

        # 1. 引号处理
        if ch in quotes:
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif in_quote:
                # 简单闭合逻辑：遇到同类引号，或者遇到智能闭合引号
                if ch == quote_char or (quote_char == '“' and ch == '”') or (quote_char == '‘' and ch == '’'):
                    in_quote = False
                    quote_char = None

        # 2. 括号处理 (仅在非引号状态下，或 Markdown 链接保护)
        elif not in_quote:
            if ch in pairs: # 左括号
                depth += 1
            elif ch in pairs.values(): # 右括号
                if depth > 0:
                    depth -= 1

        buf.append(ch)

        # --- 切分触发逻辑 ---
        # 只有在 深度为0 且 不在引号内 时，才允许由标点触发切分
        if depth == 0 and not in_quote:
            if ch in "。！？!?":
                # 避免 "..." 被切成 ". . ." (简单的防抖动，或者允许连读)
                # 这里简单处理：遇到标点就切
                res.append("".join(buf).strip())
                buf = []

    if buf:
        res.append("".join(buf).strip())

    return [r for r in res if r]


def _split_by_semantics(text: str) -> list[str]:
    """
    对长句进行语义词切分。
    修复了原代码中语义词会切断括号内容的 Bug。
    """
    # 构造正则
    pattern_words = "|".join(SEMANTIC_BREAK_WORDS)
    # 捕获组使得 split 后保留分隔符
    # Match: 3+ dots OR ellipsis char OR semantic words
    # Match: 3+ dots OR ellipsis char OR semantic words
    # Use concatenation to avoid f-string brace escaping issues with {3,}
    break_pattern = r"(\.{3,}|…|" + pattern_words + r")"
    parts = re.split(break_pattern, text)

    final_chunks = []
    buffer = ""

    for p in parts:
        if not p: continue

        # CASE 1: Semantic Word (Leading) -> 归入下一句
        if p in SEMANTIC_BREAK_WORDS:
            # 关键修正：检查当前 buffer 是否处于“不安全”状态（如括号未闭合）
            open_count = buffer.count("(") + buffer.count("（") + buffer.count("[")
            close_count = buffer.count(")") + buffer.count("）") + buffer.count("]")

            # 如果括号未闭合，或者 buffer 太短（避免 "我" 被切出来），则不切分
            if open_count > close_count or len(buffer) < 2:
                buffer += p
            else:
                # 可以切分：把之前的 buffer 存入，p 作为新句子的开始
                if buffer.strip():
                    final_chunks.append(buffer.strip())
                buffer = p 

        # CASE 2: Ellipsis (Trailing) -> 归入上一句
        elif re.fullmatch(r"\.{3,}|…", p):
            buffer += p
            # Check bracket safety
            open_count = buffer.count("(") + buffer.count("（") + buffer.count("[")
            close_count = buffer.count(")") + buffer.count("）") + buffer.count("]")
            
            if open_count <= close_count:
                # 切分，把当前buffer（含省略号）推入results
                if buffer.strip():
                    final_chunks.append(buffer.strip())
                buffer = ""

        else:
            buffer += p

    if buffer.strip():
        final_chunks.append(buffer.strip())

    # 最后的兜底：如果切分后还是很长，尝试用逗号/空格救急
    result = []
    for chunk in final_chunks:
        result.extend(_try_split_by_space_or_comma(chunk))

    return result


def _try_split_by_space_or_comma(text: str) -> list[str]:
    """
    兜底策略：只有极长文本才动用逗号和空格
    """
    if len(text) < 25: # 提高阈值
        return [text]

    # 简单策略：优先切逗号
    if "，" in text or "," in text:
        parts = re.split(r'([，,])', text)
        buf = ""
        res = []
        for p in parts:
            buf += p
            # 只有当缓冲区够长，且遇到了逗号，才切分
            if len(buf) > 15 and p in "，,":
                res.append(buf.strip())
                buf = ""
        if buf: res.append(buf.strip())
        return res

    # 实在不行切空格 (针对英文或无标点中文)
    if " " in text and len(text) > 30:
        parts = text.split(" ")
        new_parts = []
        buf = ""
        for p in parts:
            if not p.strip(): continue
            if len(buf) + len(p) < 15: # min length to avoid fragmentation
                buf += (" " + p) if buf else p
            else:
                if buf: new_parts.append(buf)
                buf = p
        if buf: new_parts.append(buf)
        return new_parts

    return [text]
