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

from __future__ import annotations

import time
import asyncio
import re
import os
from typing import Any

from nonebot import on_message, on_notice, get_bot, logger
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.rule import Rule, to_me
from .llm import get_ai_reply, consume_search_sources
from .db import touch_active, save_profile_item, log_user_active_hour
from .utils.world_info import get_time_description, get_time_period
from .utils.typing_speed import typing_delay_seconds
from .llm_vision import extract_images_and_text, generate_image_reply
from .memory import add_memory
from .mood import mood_manager
from .stock import parse_stock_id, build_stock_context

# ✅ 新增：URL总结相关
from .llm_web import should_summarize_url, generate_url_summary, generate_url_confirm
from .web.parse import extract_urls, parse_readable
from .web.fetch import fetch_html
from .db import web_cache_get, web_cache_set
from .llm_stock import generate_stock_chat_text
from .voice.asr import transcribe_audio_file
from .voice.io import fetch_record_from_event
from .voice.tts import synthesize_record_base64
from .memo import try_handle_memo
from .scheduler_custom import try_handle_schedule


RATE_LIMIT_SECONDS = 1.2
SOURCE_MAX_AGE_SECONDS = 30 * 60
PENDING_URL_TTL_SECONDS = 10 * 60
WEB_CACHE_TTL_HOURS = 12
TYPING_MAX_WAIT_SECONDS = 60.0
PENDING_IMAGE_TTL_SECONDS = 60.0

# 避免重复触发（简单锁）
last_user_call_time: dict[int, float] = {}

# URL 确认后的“待总结链接”（内存态，进程重启会丢失）
pending_url_by_user: dict[int, dict[str, Any]] = {}

# 用户输入状态：用于“对方正在输入”时延迟回复
_typing_events: dict[str, asyncio.Event] = {}

# 图片待处理：允许“先发图，再发问题”
pending_image_by_user: dict[int, dict[str, Any]] = {}
pending_image_task_by_user: dict[int, asyncio.Task] = {}


def _get_typing_event(user_id: str | int) -> asyncio.Event:
    uid = str(user_id)
    ev = _typing_events.get(uid)
    if ev is None:
        ev = asyncio.Event()
        ev.set()
        _typing_events[uid] = ev
    return ev


typing_notice = on_notice(priority=2, block=False)


@typing_notice.handle()
async def handle_typing_notice(event):
    """监听输入状态（对方正在输入），用于延迟回复避免打扰。"""
    if getattr(event, "notice_type", "") != "notify":
        return
    if getattr(event, "sub_type", "") != "input_status":
        return

    uid = getattr(event, "user_id", None)
    if uid is None:
        return
    status_text = str(getattr(event, "status_text", "") or "")
    event_type = getattr(event, "event_type", None)

    ev = _get_typing_event(uid)
    if event_type == 1 or "正在输入" in status_text:
        ev.clear()
    elif event_type == 2 or status_text == "":
        ev.set()


async def _wait_if_user_typing(user_id: int) -> None:
    """若检测到对方正在输入，则等待输入结束或超时再继续。"""
    ev = _get_typing_event(user_id)
    if ev.is_set():
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout=TYPING_MAX_WAIT_SECONDS)
    except asyncio.TimeoutError:
        ev.set()


def is_private(event: PrivateMessageEvent) -> bool:
    """NoneBot Rule：仅允许私聊事件进入主 handler。"""
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


def _looks_like_summary_request(text: str) -> bool:
    """判断用户是否在表达“请帮我总结（上一条链接）”。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    triggers = ("帮我总结", "帮我整理", "给我总结", "总结一下", "总结下", "总结下吧", "请总结", "总结")
    return any(x in t for x in triggers)


def _looks_like_source_request(text: str) -> bool:
    """判断用户是否在追问“来源/链接/原文/出处”。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    triggers = (
        "链接", "来源", "出处", "原文", "参考", "参考资料",
        "发我链接", "把链接", "给我链接", "给下链接", "发下链接",
        "发我原文", "给我原文", "原地址", "哪里看到的", "哪看到的", "哪来的",
    )
    return any(x in t for x in triggers)


def _looks_like_time_request(text: str) -> bool:
    """判断用户是否在询问当前时间。"""
    t = (text or "").strip()
    if not t:
        return False
    triggers = ("几点", "几点了", "现在几点", "现在是几点", "现在几点呀", "现在几点呢", "现在什么时间")
    return any(x in t for x in triggers)


def _env_flag(name: str) -> bool:
    v = (os.getenv(name) or "").strip()
    v = v.split()[0] if v else ""
    return v.lower() in ("1", "true", "yes", "y", "on")


def _looks_like_voice_reply_request(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _env_flag("VOICE_REPLY_ON_TEXT"):
        return True
    keywords = (os.getenv("VOICE_REPLY_KEYWORDS") or "").strip()
    if keywords:
        ks = [k.strip() for k in re.split(r"[,，\\s]+", keywords) if k.strip()]
        return any(k in t for k in ks)
    # 默认仅在用户明显要求语音时触发
    triggers = ("发语音", "用语音", "语音回复", "语音回我", "发个语音")
    return any(x in t for x in triggers)


def _bubble_parts(text: str) -> list[str]:
    """把要发送的文本拆成“气泡段落”，模拟真人分段发送。"""
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


def _maybe_learn_city_from_user_text(user_id: int, user_input: str) -> None:
    """从用户发言里尝试“记住所在地城市”，用于后续天气查询与早晨提醒。"""
    t = (user_input or "").strip()
    if not t:
        return

    m = re.match(r"^(?:我\\s*)?(?:现在\\s*)?(?:人在|在)\\s*([\\u4e00-\\u9fff]{2,10})(?:市)?[。.!！]?$", t)
    if not m:
        m = re.match(r"^(?:我\\s*)?在\\s*([\\u4e00-\\u9fff]{2,10})(?:市)?[。.!！]?$", t)
    if not m:
        return

    city = (m.group(1) or "").strip()
    if city in ("这里", "那边", "这边", "家", "公司", "学校", "宿舍", "单位", "附近", "本地", "当地"):
        return
    if "天气" in t:
        # “北京天气”这种更像提问，不当作“我在北京”的陈述来记
        return

    try:
        save_profile_item(str(user_id), "所在城市", city)
        logger.info(f"[chat] learned city uid={user_id} city={city!r}")
    except Exception as e:
        logger.warning(f"[chat] save city failed uid={user_id}: {e}")


async def _send_bubbles_and_finish(text: str, *, user_id: int | None = None) -> None:
    """按气泡分段发送，并在最后一段 `finish()` 结束当前会话。"""
    parts = _bubble_parts(text)
    if not parts:
        msg = "唔…我刚刚走神了一下，你再说一遍嘛。"
        await asyncio.sleep(typing_delay_seconds(msg, user_id=user_id))
        await chat_handler.finish(msg)

    for p in parts[:-1]:
        if user_id is not None:
            await _wait_if_user_typing(user_id)
        await asyncio.sleep(typing_delay_seconds(p, user_id=user_id))
        await chat_handler.send(p)
    last = parts[-1]
    if user_id is not None:
        await _wait_if_user_typing(user_id)
    await asyncio.sleep(typing_delay_seconds(last, user_id=user_id))
    await chat_handler.finish(last)


def _format_sources_message(sources: list[dict]) -> str:
    """把搜索来源列表整理成用户可读的多行文本（用于“要链接/出处”场景）。"""
    lines = ["好～我把刚刚那几条的来源链接整理给你："]
    for s in (sources or [])[:6]:
        title = str(s.get("title") or "").strip()
        href = str(s.get("href") or "").strip()
        if not href:
            continue
        if title:
            lines.append(f"- {title}")
            lines.append(f"  {href}")
        else:
            lines.append(f"- {href}")
    return "\n".join(lines).strip()


def _cancel_pending_image_task(user_id: int) -> None:
    task = pending_image_task_by_user.pop(user_id, None)
    if task and not task.done():
        task.cancel()


async def _send_private_bubbles(user_id: int, text: str) -> None:
    """在后台任务里私聊发送（不依赖 matcher 上下文）。"""
    bot = get_bot()
    parts = _bubble_parts(text)
    if not parts:
        return
    if len(parts) > 4:
        parts = parts[:3] + [" ".join(parts[3:])]

    for p in parts:
        await _wait_if_user_typing(user_id)
        await asyncio.sleep(typing_delay_seconds(p, user_id=user_id))
        await bot.send_private_msg(user_id=user_id, message=p)


async def _image_idle_reply_task(user_id: int, urls: list[str], ts: float) -> None:
    """若用户发图后 60s 未追问，则自由回复一次。"""
    try:
        await asyncio.sleep(PENDING_IMAGE_TTL_SECONDS)
        pending = pending_image_by_user.get(user_id)
        if not pending or float(pending.get("ts", 0)) != float(ts):
            return

        reply = await generate_image_reply(str(user_id), list(urls), "")
        add_memory(str(user_id), "user", "（发送了一张图片）")
        add_memory(str(user_id), "assistant", reply)
        pending_image_by_user.pop(user_id, None)
        await _send_private_bubbles(user_id, reply)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.error(f"[vision] idle reply failed uid={user_id}: {e}")

async def _handle_time_request_if_any(user_id: int, user_input: str) -> None:
    """若用户询问当前时间，直接基于系统时间回复并结束会话。"""
    if not _looks_like_time_request(user_input):
        return
    now_desc = get_time_description()
    period = get_time_period()
    await _send_bubbles_and_finish(f"现在是 {now_desc}。\n大概是{period}啦。", user_id=user_id)


async def _handle_stock_query_if_any(user_id: int, user_input: str) -> None:
    """私聊命令：`查股 688110` / `股票 688110`。"""
    t = (user_input or "").strip()
    if not t:
        return
    if not (t.startswith("查股") or t.startswith("股票")):
        return

    sid = parse_stock_id(t)
    if sid is None:
        await _send_bubbles_and_finish("你发我一个 6 位股票代码就行啦。\n比如：查股 688110", user_id=user_id)

    ctx = await build_stock_context(sid)
    quote = ctx.get("quote") or {}
    profile = ctx.get("profile") or {}
    anns = ctx.get("announcements") or []

    # 兜底：行情失败时不走 LLM，直接提示
    if isinstance(quote, dict) and quote.get("error"):
        await _send_bubbles_and_finish("我刚刚去查了一下。\n但是行情接口这会儿没给到数据。\n你等我一会儿再查一次好不好？", user_id=user_id)

    name = str((quote.get("name") or profile.get("name") or sid.code) or "").strip()
    try:
        pct = float(quote.get("pct_chg") or 0.0)
    except Exception:
        pct = 0.0
    amount_yi = str(quote.get("amount_yi") or "").strip()

    ann_titles = []
    for a in anns[:3] if isinstance(anns, list) else []:
        title = str((a or {}).get("title") or "").strip()
        if title:
            ann_titles.append(title)

    llm_payload = {
        "user_id": str(user_id),
        "stock": {"name": name, "code": sid.code, "ts_code": sid.ts_code},
        "quote": {"pct_chg": pct, "amount_yi": amount_yi, "price": quote.get("price")},
        "company_profile": {
            "industry": str(profile.get("industry") or "").strip(),
            "intro": str(profile.get("main_business") or profile.get("intro") or "").strip(),
        },
        "announcements": [{"title": t} for t in ann_titles],
    }

    text = (await generate_stock_chat_text(llm_payload)).strip()
    if not text:
        # fallback：保持聊天短句
        first = f"【查股】{name}({sid.code}) {pct:+.2f}%"
        if ann_titles:
            reason = f"今天可能和“{ann_titles[0]}”有关。"
        else:
            reason = "今天标题证据不足，更像情绪/资金走动。"
        hot = f"成交挺热的（成交额大概 {amount_yi}）。" if amount_yi else "今天交易还挺热的。"
        intro = str(profile.get("main_business") or profile.get("intro") or "").strip()
        if intro:
            intro = " ".join(intro.replace("\u3000", " ").split())
            if len(intro) > 70:
                intro = intro[:69] + "…"
        else:
            intro = "这家公司做的业务我这边资料还不太全。"
        text = "\n".join([first, intro, reason, hot, "明天就先看板块热度能不能接住，再留意有没有新公告。"])

    add_memory(str(user_id), "user", user_input)
    add_memory(str(user_id), "assistant", text)
    await _send_bubbles_and_finish(text, user_id=user_id)


async def _handle_source_request_if_any(user_id: int, user_input: str) -> None:
    """若用户在追问来源，尝试发送上一轮新闻检索的来源链接（如果存在则结束会话）。"""
    if not _looks_like_source_request(user_input):
        return
    sources = consume_search_sources(str(user_id), max_age_seconds=SOURCE_MAX_AGE_SECONDS)
    if not sources:
        return
    await _send_bubbles_and_finish(_format_sources_message(sources), user_id=user_id)


async def _handle_image_request_if_any(user_id: int, message: Message, user_input: str, now: float) -> bool:
    """处理图片理解：支持“先发图后发问”，返回是否已处理/已延后。"""
    image_urls, user_text = extract_images_and_text(message)

    # 1) 本次消息带图
    if image_urls:
        _cancel_pending_image_task(user_id)
        if user_text:
            reply = await generate_image_reply(str(user_id), image_urls, user_text)
            user_mem = user_text.strip() if user_text else "（发送了一张图片）"
            add_memory(str(user_id), "user", user_mem)
            add_memory(str(user_id), "assistant", reply)
            await _send_bubbles_and_finish(reply, user_id=user_id)
            return True

        # 只有图片：先缓存，等用户下一条文字追问
        pending_image_by_user[user_id] = {"urls": image_urls, "ts": now}
        pending_image_task_by_user[user_id] = asyncio.create_task(
            _image_idle_reply_task(user_id, list(image_urls), float(now))
        )
        logger.info(f"[vision] cached image uid={user_id} count={len(image_urls)}")
        return True

    # 2) 本次消息没带图，但可能是“图后追问”
    pending = pending_image_by_user.get(user_id)
    if pending and (now - float(pending.get("ts", 0))) < PENDING_IMAGE_TTL_SECONDS:
        cached_urls = pending.get("urls") or []
        if cached_urls and user_input:
            _cancel_pending_image_task(user_id)
            pending_image_by_user.pop(user_id, None)
            reply = await generate_image_reply(str(user_id), list(cached_urls), user_input)
            add_memory(str(user_id), "user", user_input)
            add_memory(str(user_id), "assistant", reply)
            await _send_bubbles_and_finish(reply, user_id=user_id)
            return True

    # 超时清理
    if pending and (now - float(pending.get("ts", 0))) >= PENDING_IMAGE_TTL_SECONDS:
        _cancel_pending_image_task(user_id)
        pending_image_by_user.pop(user_id, None)

    return False


def _check_and_update_rate_limit(user_id: int, now: float) -> bool:
    """简单限流：同一用户两次触发间隔过短则丢弃本次消息。"""
    last_time = last_user_call_time.get(user_id, 0.0)
    if now - last_time < RATE_LIMIT_SECONDS:
        logger.debug(f"[chat] rate-limited uid={user_id}")
        return False
    last_user_call_time[user_id] = now
    return True


async def _get_url_readable_content(url: str) -> tuple[str, str]:
    """获取 URL 的（标题、正文），优先读 SQLite 缓存，未命中则抓取并缓存。"""
    cached = web_cache_get(url)
    if cached:
        return cached["title"], cached["content"]

    html = await fetch_html(url)
    parsed = parse_readable(html, url=url)
    title, content = parsed.get("title", ""), parsed.get("text", "")
    web_cache_set(url, title, content, ttl_hours=WEB_CACHE_TTL_HOURS)
    return title, content


async def _handle_summary_followup_if_any(user_id: int, user_input: str, now: float) -> None:
    """处理“总结/帮我总结”的跟进：对上一条 ASK 的链接做总结（成功则结束会话）。"""
    urls = extract_urls(user_input)
    if urls:
        return
    if not _looks_like_summary_request(user_input):
        return

    pending = pending_url_by_user.get(user_id)
    if not pending:
        return
    if (now - float(pending.get("ts", 0))) >= PENDING_URL_TTL_SECONDS:
        return

    url = str(pending.get("url") or "").strip()
    if not url:
        return

    logger.info(f"[chat] url-followup uid={user_id} url={url}")
    pending_url_by_user.pop(user_id, None)

    title, content = await _get_url_readable_content(url)
    msg = await generate_url_summary(str(user_id), url, title, content)
    text = (msg.get("text") or "").strip()
    if text:
        await _send_bubbles_and_finish(text, user_id=user_id)


async def _handle_url_auto_if_any(user_id: int, user_input: str, now: float) -> None:
    """处理“消息里带 URL”的场景：LLM 决定 ASK/SUMMARIZE/IGNORE，必要时结束会话。"""
    urls = extract_urls(user_input)
    if not urls:
        return

    decision = await should_summarize_url(user_input)
    action = str(decision.get("action", "ASK")).upper()
    url = urls[0]
    logger.info(f"[chat] url-detect uid={user_id} action={action} url={url}")

    if action == "IGNORE":
        return  # 继续走普通聊天

    if action == "ASK":
        pending_url_by_user[user_id] = {"url": url, "ts": now}
        msg = await generate_url_confirm(str(user_id), user_input, url)
        text = (msg.get("text") or "").strip()
        if not text:
            text = (
                "我看到你发了个链接～\n"
                "你是想让我帮你整理重点，还是想问里面某个点呀？\n"
                "想要我总结的话回我一句“总结”就行。"
            )
        await _send_bubbles_and_finish(text, user_id=user_id)

    # SUMMARIZE（或其它异常值，按总结处理）
    pending_url_by_user.pop(user_id, None)
    title, content = await _get_url_readable_content(url)
    msg = await generate_url_summary(str(user_id), url, title, content)
    text = (msg.get("text") or "").strip()
    if text:
        await _send_bubbles_and_finish(text, user_id=user_id)


group_hint = on_message(rule=to_me(), priority=9, block=False)


@group_hint.handle()
async def handle_group_hint(event):
    """群聊被 @ 时的引导：提示用户转到私聊，避免误以为机器人失效。"""
    if not hasattr(event, "group_id"):
        return
    msg = "我现在主要在私聊里陪你聊～你私聊我一句就好。"
    await asyncio.sleep(typing_delay_seconds(msg, user_id=getattr(event, "user_id", None)))
    await group_hint.finish(msg)


@chat_handler.handle()
async def handle_private_chat(event: PrivateMessageEvent):
    """私聊主入口：按优先级依次处理来源追问、限流、链接总结与普通聊天。"""
    try:
        user_id = event.user_id
        message = event.get_message()
        bot = get_bot()

        # 0) 语音消息：QQ 语音 → ASR → LLM → TTS → QQ 语音
        record_seg = next((seg for seg in message if getattr(seg, "type", "") == "record"), None)
        if record_seg is not None:
            touch_active(str(user_id))
            log_user_active_hour(str(user_id))  # 记录活跃小时
            now = time.time()
            if not _check_and_update_rate_limit(user_id, now):
                return
            await _wait_if_user_typing(user_id)

            try:
                audio_path = await fetch_record_from_event(bot, record_seg)
                asr_text = (await transcribe_audio_file(audio_path)).strip()
                if not asr_text:
                    msg = "我刚刚没听清…你可以再说一遍吗？"
                    await asyncio.sleep(typing_delay_seconds(msg, user_id=user_id))
                    await chat_handler.finish(msg)

                logger.info(f"[voice] uid={user_id} asr={asr_text[:200]!r}")
                reply_text = await get_ai_reply(str(user_id), asr_text, voice_mode=True)
                try:
                    mood = mood_manager.get_user_mood(str(user_id))
                    record_b64 = await synthesize_record_base64(reply_text, mood=mood)
                    await chat_handler.finish(MessageSegment.record(file=record_b64))
                except FinishedException:
                    # finish() 会通过 FinishedException 中断 handler，这里属于正常流程
                    raise
                except Exception as e:
                    logger.exception(f"[voice] tts failed uid={user_id}: {e}")
                    extra = ""
                    if "QWEN_TTS_VOICE" in str(e):
                        extra = (
                            "\n\n（我这边还没配置语音音色，所以只能先发文字。\n"
                            "请在 bot/.env 里设置：QWEN_TTS_VOICE=你复刻出来的 output.voice，然后重启 nonebot。）"
                        )
                    await _send_bubbles_and_finish((reply_text or "").strip() + extra, user_id=user_id)
            except FinishedException:
                raise
            except Exception as e:
                logger.exception(f"[voice] failed uid={user_id}: {e}")
                msg = "语音处理失败了…你发文字我也可以聊，或者稍后再试试。"
                await asyncio.sleep(typing_delay_seconds(msg, user_id=user_id))
                await chat_handler.finish(msg)

        user_input = str(message).strip()
        if not user_input:
            return

        logger.info(f"[chat] recv uid={user_id} text={user_input[:200]!r}")

        # ✅ 一进来就记录活跃
        touch_active(str(user_id))
        log_user_active_hour(str(user_id))  # 记录活跃小时（用于学习用户习惯）

        # ✅ 尝试记住用户所在地（用户回答城市时不依赖 LLM 标签）
        _maybe_learn_city_from_user_text(user_id, user_input)

        # 1) “要链接/出处/来源”跟进：发送上一轮搜索的来源链接
        await _handle_source_request_if_any(user_id, user_input)

        # 2) 简单限流：防止刷屏
        now = time.time()
        if not _check_and_update_rate_limit(user_id, now):
            return

        # 3) 输入中检测：等待对方输入结束，避免打扰
        await _wait_if_user_typing(user_id)

        # 4) 图片理解：优先处理图片（或缓存等待下一条文字）
        handled = await _handle_image_request_if_any(user_id, message, user_input, now)
        if handled:
            return

        # 5) 时间询问：直接返回系统时间（避免模型乱编）
        await _handle_time_request_if_any(user_id, user_input)

        # 6) “总结”跟进：对上一条 ASK 的链接做总结
        await _handle_summary_followup_if_any(user_id, user_input, now)

        # 6.5) 日程提醒
        schedule_reply = await try_handle_schedule(str(user_id), user_input)
        if schedule_reply:
            await _send_bubbles_and_finish(schedule_reply, user_id=user_id)

        # 7) URL 自动处理：LLM 判断是否要总结/确认
        await _handle_url_auto_if_any(user_id, user_input, now)

        # 7.2) 智能备忘录
        memo_reply = await try_handle_memo(str(user_id), user_input)
        if memo_reply:
            await _send_bubbles_and_finish(memo_reply, user_id=user_id)

        # 7.5) 股票查询（私聊命令）
        await _handle_stock_query_if_any(user_id, user_input)

        # 8) 默认走普通聊天逻辑
        voice_wanted = _looks_like_voice_reply_request(user_input)
        reply = await get_ai_reply(str(user_id), user_input, voice_mode=voice_wanted)
        if voice_wanted:
            try:
                mood = mood_manager.get_user_mood(str(user_id))
                record_b64 = await synthesize_record_base64(reply, mood=mood)
                await chat_handler.finish(MessageSegment.record(file=record_b64))
            except FinishedException:
                raise
            except Exception as e:
                logger.exception(f"[voice] tts failed(uid={user_id}) on text: {e}")
                await _send_bubbles_and_finish(reply, user_id=user_id)
        else:
            await _send_bubbles_and_finish(reply, user_id=user_id)

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(e)
        msg = "我这边刚刚出错了…你再发一次我试试，好不好？"
        # 这里可能没有 user_id（极少数异常），兜底传 None
        await asyncio.sleep(typing_delay_seconds(msg, user_id=locals().get("user_id", None)))
        await chat_handler.finish(msg)
