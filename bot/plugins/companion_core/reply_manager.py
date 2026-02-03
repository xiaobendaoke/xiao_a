"""消息回复管理器。

职责：
1. 统一管理“正在输入”状态（Typing Event）。
2. 提供统一的发送接口，包含气泡分割（Bubble Split）和发送节奏控制（Send Rhythm）。
3. 支持 matcher.finish() 场景和纯后台 send_private_msg 场景。
"""
import asyncio
from typing import Any

from nonebot import logger, get_bot
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import MessageSegment

from .bubble_splitter import bubble_parts as _bubble_parts
from .llm_bubbles import parse_bubble_tag, strip_bubble_tag
from .send_rhythm import bubble_delay_seconds, typing_delay_seconds as _calc_typing_delay

# === State Management ===
TYPING_MAX_WAIT_SECONDS = 60.0
_typing_events: dict[str, asyncio.Event] = {}

def get_typing_event(user_id: str | int) -> asyncio.Event:
    uid = str(user_id)
    ev = _typing_events.get(uid)
    if ev is None:
        ev = asyncio.Event()
        ev.set()
        _typing_events[uid] = ev
    return ev

async def wait_if_user_typing(user_id: int | str) -> None:
    """若检测到对方正在输入，则等待输入结束或超时再继续。"""
    ev = get_typing_event(user_id)
    if ev.is_set():
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout=TYPING_MAX_WAIT_SECONDS)
    except asyncio.TimeoutError:
        ev.set()

def update_typing_status(user_id: str | int, is_typing: bool) -> None:
    """更新用户的输入状态。"""
    ev = get_typing_event(user_id)
    if is_typing:
        ev.clear()
    else:
        ev.set()

def is_user_typing(user_id: str | int) -> bool:
    """非阻塞检查：用户当前是否正在输入。"""
    uid = str(user_id)
    ev = _typing_events.get(uid)
    if ev is None:
        return False
    return not ev.is_set()

# === Splitting Logic ===

def _split_text_to_bubbles(text: str) -> list[str]:
    """统一的气泡分割逻辑：优先尝试 LLM 标签，失败则回退到算法分割。"""
    # 尝试从 LLM 输出解析气泡标签
    parts = parse_bubble_tag(text)
    if parts:
        return parts
    
    # 回退到自动分割（先移除可能残留的标签）
    clean_text = strip_bubble_tag(text)
    return _bubble_parts(clean_text)

# === Sending Interface ===

async def send_bubbles_and_finish(matcher: Matcher, text: str, *, user_id: int | None = None) -> None:
    """按气泡分段发送，并在最后一段 finish()。"""
    parts = _split_text_to_bubbles(text)
    
    if not parts:
        # 异常兜底
        msg = "唔…我刚刚走神了一下，你再说一遍嘛。"
        if user_id:
            await asyncio.sleep(_calc_typing_delay(msg, user_id=user_id))
        await matcher.finish(msg)

    total = len(parts)
    # 前 N-1 条
    for i, p in enumerate(parts[:-1]):
        if user_id is not None:
            await wait_if_user_typing(user_id)
        
        delay = bubble_delay_seconds(p, user_id=user_id, bubble_index=i, total_bubbles=total)
        await asyncio.sleep(delay)
        await matcher.send(p)
    
    # 最后一条
    last = parts[-1]
    last_idx = total - 1
    if user_id is not None:
        await wait_if_user_typing(user_id)
    
    delay = bubble_delay_seconds(last, user_id=user_id, bubble_index=last_idx, total_bubbles=total)
    await asyncio.sleep(delay)
    await matcher.finish(last)


async def send_private_bubbles(user_id: int | str, text: str) -> None:
    """后台任务发送（使用 bot.send_private_msg），带节奏控制。"""
    bot = get_bot()
    uid_int = int(user_id)
    
    parts = _split_text_to_bubbles(text)
    if not parts:
        return

    total = len(parts)
    for i, p in enumerate(parts):
        await wait_if_user_typing(user_id)
        
        delay = bubble_delay_seconds(p, user_id=user_id, bubble_index=i, total_bubbles=total)
        await asyncio.sleep(delay)
        await bot.send_private_msg(user_id=uid_int, message=p)

async def reply_with_error(matcher: Matcher, error_msg: str, user_id: int | None = None) -> None:
    """统一的错误回复辅助及其延迟模拟。"""
    if user_id:
        await asyncio.sleep(_calc_typing_delay(error_msg, user_id=user_id))
    await matcher.finish(error_msg)
