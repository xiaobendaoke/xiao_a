"""私聊消息处理入口（NoneBot handler）。

职责：
- 仅处理私聊消息（`PrivateMessageEvent`），避免群聊误触发。
- 记录用户活跃时间（用于主动互动与 RSS 推送的节流/候选筛选）。
- 基础限流：用内存字典做 1.2s 级别的防刷屏。
- URL 自动处理：
  - 从用户文本提取 URL；
  - 由 LLM 轻量判定 `SUMMARIZE/ASK/IGNORE`；
  - 需要总结时：抓取网页 HTML → 提取可读正文 → 走总结提示词；
  - 结果缓存到 SQLite（降低重复请求与 token 消耗）。
- 默认聊天路径：调用 `llm.get_ai_reply()` 获取回复并结束会话。
"""

import time
import asyncio
import re
import random
from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.exception import FinishedException
from nonebot.rule import Rule, to_me
from .llm import get_ai_reply, consume_search_sources
from .db import touch_active

# ✅ 新增：URL总结相关
from .llm_web import should_summarize_url, generate_url_summary, generate_url_confirm
from .web.parse import extract_urls, parse_readable
from .web.fetch import fetch_html
from .db import web_cache_get, web_cache_set


# 仅响应私聊消息
def is_private(event: PrivateMessageEvent):
    return isinstance(event, PrivateMessageEvent)


_probe_count = 0
event_probe = on_message(priority=1, block=False)


@event_probe.handle()
async def probe_any_message(event):
    """启动阶段排障用：记录少量收到的消息事件类型，帮助判断是私聊还是群聊/是否有事件进来。"""
    global _probe_count
    if _probe_count >= 30:
        return
    _probe_count += 1
    try:
        ev_name = getattr(event, "get_event_name", lambda: type(event).__name__)()
        uid = getattr(event, "user_id", None)
        gid = getattr(event, "group_id", None)
        msg = getattr(event, "get_message", lambda: "")()
        logger.info(f"[probe] name={ev_name} uid={uid} gid={gid} msg={str(msg)[:120]!r}")
    except Exception as e:
        logger.warning(f"[probe] failed: {e}")


chat_handler = on_message(rule=Rule(is_private), priority=5, block=True)

# 避免重复触发（简单锁）
last_user_call_time = {}

# URL 确认后的“待总结链接”（内存态，进程重启会丢失）
pending_url_by_user = {}


def _looks_like_summary_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    triggers = ("帮我总结", "帮我整理", "给我总结", "总结一下", "总结下", "总结下吧", "请总结", "总结")
    return any(x in t for x in triggers)

def _looks_like_source_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    triggers = (
        "链接", "来源", "出处", "原文", "参考", "参考资料",
        "发我链接", "把链接", "给我链接", "给下链接", "发下链接",
        "发我原文", "给我原文", "原地址", "哪里看到的", "哪看到的", "哪来的",
    )
    return any(x in t for x in triggers)


def _bubble_parts(text: str) -> list[str]:
    s = str(text or "").strip()
    if not s:
        return []

    parts = [p.strip() for p in s.splitlines() if p.strip()]
    if len(parts) <= 1 and len(s) >= 18:
        # 单行但偏长：按句末标点拆成“短句气泡”
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*", s) if p.strip()] or parts
        if len(parts) <= 1 and len(s) >= 36:
            parts = [p.strip() for p in re.split(r"(?<=[，,、])\s*", s) if p.strip()] or parts

    if len(parts) > 4:
        parts = parts[:3] + [" ".join(parts[3:])]
    return parts


def _bubble_pause_seconds(text: str) -> float:
    n = len((text or "").strip())
    # 让消息“更像人”：短句也留一点停顿，长句稍微更久
    base = 0.75 + min(1.0, n / 30) * 0.75
    jitter = random.uniform(0.05, 0.25)
    return min(2.2, base + jitter)


async def _send_bubbles_and_finish(text: str) -> None:
    parts = _bubble_parts(text)
    if not parts:
        await chat_handler.finish("唔…我刚刚走神了一下，你再说一遍嘛。")

    for p in parts[:-1]:
        await chat_handler.send(p)
        await asyncio.sleep(_bubble_pause_seconds(p))
    await chat_handler.finish(parts[-1])


group_hint = on_message(rule=to_me(), priority=9, block=False)


@group_hint.handle()
async def handle_group_hint(event):
    # 如果是群聊里 @ 到我，给一个明确引导，避免用户以为“没回复”
    if not hasattr(event, "group_id"):
        return
    await group_hint.finish("我现在主要在私聊里陪你聊～你私聊我一句就好。")


@chat_handler.handle()
async def handle_private_chat(event: PrivateMessageEvent):
    try:
        user_id = event.user_id
        user_input = str(event.get_message()).strip()
        if not user_input:
            return

        logger.info(f"[chat] recv uid={user_id} text={user_input[:200]!r}")

        # ✅ 一进来就记录活跃
        touch_active(str(user_id))

        # ===============================
        # ✅ “要链接/出处/来源”跟进：发送上一轮搜索的来源链接
        # ===============================
        if _looks_like_source_request(user_input):
            sources = consume_search_sources(str(user_id), max_age_seconds=30 * 60)
            if sources:
                lines = ["好～我把刚刚那几条的来源链接整理给你："]
                for s in sources[:6]:
                    title = str(s.get("title") or "").strip()
                    href = str(s.get("href") or "").strip()
                    if not href:
                        continue
                    if title:
                        lines.append(f"- {title}")
                        lines.append(f"  {href}")
                    else:
                        lines.append(f"- {href}")
                await _send_bubbles_and_finish("\n".join(lines).strip())

        # ✅ 简单限流：防止刷屏
        now = time.time()
        last_time = last_user_call_time.get(user_id, 0)
        if now - last_time < 1.2:
            logger.debug(f"[chat] rate-limited uid={user_id}")
            return
        last_user_call_time[user_id] = now

        # ===============================
        # ✅ “总结/帮我总结”跟进：对上一条 ASK 的链接做总结
        # ===============================
        urls = extract_urls(user_input)
        if not urls and _looks_like_summary_request(user_input):
            pending = pending_url_by_user.get(user_id)
            if pending and (now - float(pending.get("ts", 0))) < 10 * 60:
                url = str(pending.get("url") or "").strip()
                if url:
                    logger.info(f"[chat] url-followup uid={user_id} url={url}")
                    pending_url_by_user.pop(user_id, None)
                    cached = web_cache_get(url)
                    if cached:
                        title, content = cached["title"], cached["content"]
                    else:
                        html = await fetch_html(url)
                        parsed = parse_readable(html, url=url)
                        title, content = parsed.get("title", ""), parsed.get("text", "")
                        web_cache_set(url, title, content, ttl_hours=12)

                    msg = await generate_url_summary(str(user_id), url, title, content)
                    text = (msg.get("text") or "").strip()
                    if text:
                        await _send_bubbles_and_finish(text)

        # ===============================
        # ✅ URL 自动处理：LLM判断是否需要总结
        # ===============================
        urls = urls or extract_urls(user_input)
        if urls:
            decision = await should_summarize_url(user_input)
            action = decision.get("action", "ASK")
            url = urls[0]
            logger.info(f"[chat] url-detect uid={user_id} action={action} url={url}")

            if action == "IGNORE":
                pass  # 继续走普通聊天

            elif action == "ASK":
                pending_url_by_user[user_id] = {"url": url, "ts": now}
                msg = await generate_url_confirm(str(user_id), user_input, url)
                text = (msg.get("text") or "").strip()
                if not text:
                    text = (
                        "我看到你发了个链接～\n"
                        "你是想让我帮你整理重点，还是想问里面某个点呀？\n"
                        "想要我总结的话回我一句“总结”就行。"
                    )
                await _send_bubbles_and_finish(text)

            else:  # SUMMARIZE
                pending_url_by_user.pop(user_id, None)
                cached = web_cache_get(url)
                if cached:
                    title, content = cached["title"], cached["content"]
                else:
                    html = await fetch_html(url)
                    parsed = parse_readable(html, url=url)
                    title, content = parsed.get("title", ""), parsed.get("text", "")
                    web_cache_set(url, title, content, ttl_hours=12)

                msg = await generate_url_summary(str(user_id), url, title, content)
                text = (msg.get("text") or "").strip()
                if text:
                    await _send_bubbles_and_finish(text)

        # ===============================
        # ✅ 默认走原有聊天逻辑
        # ===============================
        reply = await get_ai_reply(str(user_id), user_input)
        await _send_bubbles_and_finish(reply)

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(e)
        await chat_handler.finish("我这边刚刚出错了…你再发一次我试试，好不好？")
