# bot/plugins/companion_core/bubble_splitter.py

from __future__ import annotations
import re

# 只要单句超过这个长度，就尝试找标点切分
# 设为 1，意味着“只要有标点，咱们就尽量切开”，让气泡更短更碎
SPLIT_THRESHOLD = 50 

def bubble_parts(text: str) -> list[str]:
    """
    智能气泡分段：
    1. 显式换行符 (\n) 是最强拆分信号。
    2. 标点符号 (。！？) 是次级拆分信号。
    3. 空格 ( ) 在某些情况下也作为拆分信号（针对无标点流）。
    """
    s = str(text or "").strip()
    if not s:
        return []

    # 1. 保护代码块（不切分代码）
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
    """核心切分逻辑"""
    # 1. 先按物理换行符切分 (Explicit Newline)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    final_bubbles = []
    
    for line in lines:
        # 2. 如果这行本身就不长，或者没有“句尾标点”，那就直接作为一条
        # (但在无标点风格下，我们要看长度)
        if len(line) < 20: 
            final_bubbles.append(line)
            continue
            
        # 3. 尝试按标点符号炸开 (。！？!?)
        # 这种正则保留分隔符： 'a。b' -> ['a', '。', 'b']
        parts = re.split(r'([。！？!?])', line)
        
        buffer = ""
        current_chunk_bubbles = []
        
        for p in parts:
            if not p: continue
            
            # 如果是标点，附在上一句末尾并结束当前气泡
            if p in "。！？!?":
                buffer += p
                if buffer.strip():
                    current_chunk_bubbles.append(buffer.strip())
                buffer = ""
            else:
                # 是正文
                # 如果 buffer 已经有东西了（说明上一段被强制切断了但没标点？不，逻辑通常是 buffer+标点->push）
                # 这里处理: text + text (无标点中间连接?? re.split 不会产生这个)
                # re.split 结果通常是 [text, sep, text, sep...]
                buffer += p
        
        # 处理末尾残留
        if buffer.strip():
            current_chunk_bubbles.append(buffer.strip())
            
        # 4. 如果切分虽然完成了，但有些句子还是太长（比如全是逗号的），再尝试按空格 切分
        # 或者 刚才根本就没切开（即 current_chunk_bubbles 只有一个元素 == line）
        
        # 为了避免太碎，我们这里只对“长文本”做二次切分
        for bubble in current_chunk_bubbles:
            final_bubbles.extend(_try_split_by_space_or_comma(bubble))
            
    return final_bubbles


def _try_split_by_space_or_comma(text: str) -> list[str]:
    """只有文本过长 (>=30) 时，才尝试用空格/逗号救急"""
    if len(text) < 30:
        return [text]
        
    # 尝试按空格切分（针对无标点流： "医生开的药是消炎的 记得多休息"）
    # 只有当空格前后都是中文时，才视作“换气分隔符”
    # 暂时简单点：直接 split spaces
    
    sub_parts = []
    # 如果包含空格，且片段较长
    if " " in text:
        # 简单的按空格分
        raw_spaces = text.split(" ")
        buf = ""
        for seg in raw_spaces:
            if not seg.strip(): continue
            # 累积 buffer，直到长度适中
            if len(buf) + len(seg) < 30:
                buf += (" " + seg) if buf else seg
            else:
                if buf: sub_parts.append(buf)
                buf = seg
        if buf: sub_parts.append(buf)
    else:
        # 实在没辙，看有没有逗号
        # 只有在非常长 (>60) 的情况下才切逗号
        if len(text) > 60:
             # ... 简单起见，暂不切逗号，保留长句完整性，或者可以切 "，"
             # 现在的需求是 微信风格，可以切
             parts = re.split(r'([，,])', text)
             buf = ""
             for p in parts:
                 buf += p
                 if len(buf) > 30 and p in "，,":
                     sub_parts.append(buf.strip())
                     buf = ""
             if buf: sub_parts.append(buf.strip())
        else:
            sub_parts = [text]
            
    # 最后的兜底：如果没切出个所以然（sub_parts为空），返回原样
    return sub_parts if sub_parts else [text]
