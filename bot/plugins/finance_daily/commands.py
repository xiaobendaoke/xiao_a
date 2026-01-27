"""finance_daily 手动触发入口（仅私聊）。

为什么需要：
- cron 任务如果在机器人启动后“错过了当天触发点”，会等到下一天才跑；
- 手动命令用于排障/验收：立即跑一次并回发结果。

触发词（私聊）：
- `财经日报` / `财经复盘`：只回发给当前对话用户（不广播）
- `财经日报广播`：按配置广播给所有好友（或 targets 私聊列表）
- `财经日报 强制` / `财经日报广播 强制`：忽略当天幂等，强制重跑一次
- `财经状态`：查看最近一次任务状态与配置
可选：后面跟日期 `YYYYMMDD`，例如：`财经日报 20260127`
"""

from __future__ import annotations

import re
import asyncio

from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from nonebot.rule import Rule

from . import config
from .pipeline import run_cn_a_daily
from .daily_job import _send_to_targets, _split_qq_messages, pick_bot
from .storage import get_job, get_latest_job


def _parse_manual_trigger(text: str) -> tuple[str | None, str | None, bool]:
    """返回 (mode, trade_date, force)。mode in ('self','broadcast','status')。"""
    t = (text or "").strip()
    if not t:
        return None, None, False

    mode = None
    force = False
    if t.startswith("财经日报广播"):
        mode = "broadcast"
        rest = t[len("财经日报广播") :].strip()
    elif t.startswith("财经日报") or t.startswith("财经复盘"):
        mode = "self"
        rest = re.sub(r"^(财经日报|财经复盘)", "", t, count=1).strip()
    elif t.startswith("财经状态"):
        mode = "status"
        rest = t[len("财经状态") :].strip()
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
        await manual_trigger.finish("我这边还没连上 QQ（bot 未连接），等连接好了再试一次。")

    if mode == "status":
        latest = await get_latest_job(config.FIN_DAILY_MARKET)
        lines = ["财经状态："]
        lines.append(f"- 数据源：{config.FIN_DAILY_DATA_PROVIDER}")
        lines.append(f"- 定时：{config.FIN_DAILY_RUN_HOUR:02d}:{config.FIN_DAILY_RUN_MINUTE:02d}")
        lines.append(f"- TopN：{config.FIN_DAILY_TOP_N}")
        lines.append(f"- 广播好友：{1 if config.FIN_DAILY_BROADCAST_ALL_FRIENDS else 0}")
        if latest:
            lines.append(
                f"- 最近任务：{latest.get('trade_date')} {latest.get('status')} err={str(latest.get('error') or '')[:80]}"
            )
        else:
            lines.append("- 最近任务：暂无记录")
        await manual_trigger.finish("\n".join(lines))

    # 先给一个“正在处理”的反馈，避免用户以为没反应（LLM+网络可能要几分钟）
    try:
        await bot.call_api(
            "send_private_msg",
            user_id=int(event.user_id),
            message="好～我开始跑财经日报了，可能要两三分钟，你等我一下哈。",
        )
    except Exception:
        pass

    try:
        res = await run_cn_a_daily(force_trade_date=trade_date, force=force)
    except Exception as e:
        logger.exception(f"[finance] manual run failed: {e}")
        await manual_trigger.finish(f"财经日报跑失败了：{e}")

    if res.get("skipped"):
        td = res.get("trade_date") or ""
        job = await get_job(config.FIN_DAILY_MARKET, str(td)) if td else {}
        extra = ""
        if job:
            extra = f"（status={job.get('status')} err={str(job.get('error') or '')[:80]}）"
        await manual_trigger.finish(f"这次没跑：{res.get('reason') or 'skipped'}（trade_date={td}）{extra}")

    report = str(res.get("report_text") or "").strip()
    parts = res.get("report_parts")
    if isinstance(parts, list) and parts:
        report = "\n\n".join([str(p) for p in parts if str(p).strip()]).strip()
    if not report:
        await manual_trigger.finish("跑完了，但生成的内容是空的……你看下 nonebot 日志里有没有报错。")

    if mode == "broadcast":
        await _send_to_targets(report)
        await manual_trigger.finish("好～我已经按配置广播出去啦（仅私聊）。")

    # self：只回发给触发的人，避免一上来就群发/全好友刷屏
    parts = _split_qq_messages(report)
    uid = int(event.user_id)
    for p in parts:
        await bot.call_api("send_private_msg", user_id=uid, message=p)
        await asyncio.sleep(0.6)
    await manual_trigger.finish("（以上是本次财经日报测试结果）")
