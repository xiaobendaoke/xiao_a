"""finance_daily 手动触发入口（仅私聊）。

为什么需要：
- cron 任务如果在机器人启动后“错过了当天触发点”，会等到下一天才跑；
- 手动命令用于排障/验收：立即跑一次并回发结果。

触发词（私聊）：
- `开启财经日报`：写入订阅表 enabled=1，回复“已开启”
- `关闭财经日报`：enabled=0，回复“已关闭”
- `财经日报状态`：回复当前是否开启 + 每天几点推送
- `财经日报` / `财经复盘`：只回发给当前对话用户（不影响订阅）
- `财经日报 强制`：忽略当天幂等，强制重跑一次并回发（不影响订阅）
- `财经状态`：排障用，查看最近一次任务状态与配置
可选：`财经日报/财经复盘/财经日报 强制` 后面跟日期 `YYYYMMDD`，例如：`财经日报 20260127`
"""

from __future__ import annotations

import asyncio
import re

from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.rule import Rule

from ..utils.typing_speed import typing_delay_seconds
from ..db import touch_active

from . import config
from .pipeline import run_cn_a_daily
from .daily_job import pick_bot, send_private_messages
from .storage import (
    get_job,
    get_latest_job,
    is_subscription_enabled,
    list_enabled_subscribers,
    set_subscription,
)
from ..llm_core import get_system_reply


def _parse_manual_trigger(text: str) -> tuple[str | None, str | None, bool]:
    """返回 (mode, trade_date, force)。mode in ('subscribe_on','subscribe_off','sub_status','run','debug_status')。"""
    t = (text or "").strip()
    if not t:
        return None, None, False

    mode = None
    force = False

    if t.startswith("开启财经日报"):
        return "subscribe_on", None, False
    if t.startswith("关闭财经日报"):
        return "subscribe_off", None, False
    if t.startswith("财经日报状态"):
        return "sub_status", None, False
    if t.startswith("财经状态"):
        mode = "debug_status"
        rest = t[len("财经状态") :].strip()
    elif t.startswith("财经日报") or t.startswith("财经复盘"):
        mode = "run"
        rest = re.sub(r"^(财经日报|财经复盘)", "", t, count=1).strip()
    else:
        return None, None, False

    if "强制" in rest:
        force = True
        rest = rest.replace("强制", " ").strip()

    m = re.search(r"\b(\d{8})\b", rest)
    return mode, (m.group(1) if m else None), force


def _manual_rule(event: PrivateMessageEvent) -> bool:
    if not isinstance(event, PrivateMessageEvent):
        return False
    text = str(event.get_message() or "").strip()
    mode, _, _ = _parse_manual_trigger(text)
    return bool(mode)


manual_trigger = on_message(rule=Rule(_manual_rule), priority=4, block=True)


@manual_trigger.handle()
async def handle_manual_trigger(event: PrivateMessageEvent):
    text = str(event.get_message() or "").strip()
    mode, trade_date, force = _parse_manual_trigger(text)
    if not mode:
        return

    bot = pick_bot()
    if bot is None:
        msg = await get_system_reply(str(event.user_id), "告诉用户我这边还没连上QQ，等连接好了再试。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=event.user_id))
        await manual_trigger.finish(msg)

    uid = int(event.user_id)
    # finance_daily 会 block companion_core 的私聊 handler；这里补一份活跃记录，确保“24小时未对话停推送”行为正确。
    try:
        touch_active(str(uid))
    except Exception:
        pass

    if mode == "subscribe_on":
        await set_subscription(config.FIN_DAILY_MARKET, uid, enabled=True)
        msg = await get_system_reply(str(uid), "告诉用户财经日报已经开启，每天定时推送。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    if mode == "subscribe_off":
        await set_subscription(config.FIN_DAILY_MARKET, uid, enabled=False)
        msg = await get_system_reply(str(uid), "告诉用户财经日报已经关闭，不会再推送。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    if mode == "sub_status":
        enabled = await is_subscription_enabled(config.FIN_DAILY_MARKET, uid)
        hh = int(config.FIN_DAILY_RUN_HOUR)
        mm = int(config.FIN_DAILY_RUN_MINUTE)
        st = "已开启" if enabled else "已关闭"
        instruction = f"告诉用户财经日报当前状态是：{st}，推送时间是每天 {hh:02d}:{mm:02d}"
        msg = await get_system_reply(str(uid), instruction)
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    if mode == "debug_status":
        latest = await get_latest_job(config.FIN_DAILY_MARKET)
        subs = await list_enabled_subscribers(config.FIN_DAILY_MARKET)
        lines = ["财经状态："]
        lines.append(f"- 数据源：{config.FIN_DAILY_DATA_PROVIDER}")
        lines.append(f"- 定时：{config.FIN_DAILY_RUN_HOUR:02d}:{config.FIN_DAILY_RUN_MINUTE:02d}")
        lines.append(f"- TopN：{config.FIN_DAILY_TOP_N}")
        lines.append(f"- 订阅人数：{len(subs)}")
        if latest:
            lines.append(
                f"- 最近任务：{latest.get('trade_date')} {latest.get('status')} err={str(latest.get('error') or '')[:80]}"
            )
        else:
            lines.append("- 最近任务：暂无记录")
        raw_info = "\n".join(lines)
        instruction = f"用户想查看财经日报的调试状态，信息如下，请整理后告诉用户：\n{raw_info}"
        msg = await get_system_reply(str(uid), instruction)
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    # 先给一个“正在处理”的反馈，避免用户以为没反应（LLM+网络可能要几分钟）
    try:
        warm = await get_system_reply(str(uid), "告诉用户财经日报开始生成了，需要等两三分钟。")
        await asyncio.sleep(typing_delay_seconds(warm, user_id=uid))
        await bot.call_api(
            "send_private_msg",
            user_id=uid,
            message=warm,
        )
    except Exception:
        pass

    try:
        res = await run_cn_a_daily(force_trade_date=trade_date, force=force)
    except Exception as e:
        logger.exception(f"[finance] manual run failed: {e}")
        msg = await get_system_reply(str(uid), f"财经日报跑失败了，错误信息：{e}，请告诉用户出错了。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    if res.get("skipped"):
        td = res.get("trade_date") or ""
        job = await get_job(config.FIN_DAILY_MARKET, str(td)) if td else {}
        extra = ""
        if job:
            extra = f"（status={job.get('status')} err={str(job.get('error') or '')[:80]}）"
        instruction = f"财经日报这次没有运行，原因是：{res.get('reason') or 'skipped'}（日期={td}）{extra}，请告诉用户。"
        msg = await get_system_reply(str(uid), instruction)
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    parts = res.get("report_parts")
    if isinstance(parts, list) and parts:
        messages = [str(p) for p in parts if str(p).strip()]
    else:
        report = str(res.get("report_text") or "").strip()
        messages = [report] if report else []

    if not messages:
        msg = await get_system_reply(str(uid), "财经日报跑完了但内容是空的，请用户查看日志是否有报错。")
        await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
        await manual_trigger.finish(msg)

    await send_private_messages(bot, uid, messages, interval=0.6)
    msg = await get_system_reply(str(uid), "告诉用户本次财经日报已经发完了。")
    await asyncio.sleep(typing_delay_seconds(msg, user_id=uid))
    await manual_trigger.finish(msg)
