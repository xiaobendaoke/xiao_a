"""日程提醒助手。

功能：
1. 解析“提醒我...”指令。
2. 后台任务定期检查到期的提醒并推送。
"""
from __future__ import annotations

import re
import asyncio
from datetime import datetime, timedelta
from nonebot import get_bot, logger, require
from .db import save_schedule, get_pending_schedules, update_schedule_status
from .memory import add_memory as add_chat_memory

# 引入 apscheduler
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


def _parse_time(text: str) -> datetime | None:
    """简单的时间解析（正则）。"""
    now = datetime.now()
    t = text.strip()

    # 1. 相对时间：10分钟后、半小时后、2小时后
    m = re.match(r"^(\d+)\s*(分钟|分|min)后?$", t)
    if m:
        return now + timedelta(minutes=int(m.group(1)))
    
    if t in ("半小时", "半小时后"):
        return now + timedelta(minutes=30)
    
    m = re.match(r"^(\d+)\s*(小时|时|hour|h)后?$", t)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    if t in ("明天", "明天早上"):
        # 默认明早 8 点
        return now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)

    # 2. 绝对时间：明天8点、晚上9点、8:30
    # 简单支持：明天X点，X点X分
    m = re.match(r"^(?:明天|明早)\s*(\d{1,2})(?:[:点])?(\d{2})?$", t)
    if m:
        h = int(m.group(1))
        min_ = int(m.group(2) or 0)
        target = now.replace(hour=h, minute=min_, second=0, microsecond=0) + timedelta(days=1)
        return target
    
    # 今天的 X点
    m = re.match(r"^(?:今天|晚上|早上|上午|下午)?\s*(\d{1,2})(?:[:点])(\d{2})$", t)
    if m:
        h = int(m.group(1))
        min_ = int(m.group(2))
        if "晚上" in t or "下午" in t:
            if h < 12: h += 12
        target = now.replace(hour=h, minute=min_, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1) # 过去了就默认明天
        return target
        
    return None

async def try_handle_schedule(user_id: str, user_input: str) -> str | None:
    """尝试处理提醒指令。"""
    text = (user_input or "").strip()
    if not text:
        return None

    # 模式：(提醒|叫)我 [时间] [干啥]
    # 例：提醒我 10分钟后 喝水
    # 例：10分钟后 叫我 喝水
    # 例：提醒我 喝水
    
    if not (text.startswith("提醒我") or "叫我" in text or "提醒" in text):
        return None

    # 提取核心部分
    # 简单处理：移除开头触发词
    content = text
    for p in ("提醒我", "麻烦提醒我", "帮我定个闹钟"):
        if content.startswith(p):
            content = content[len(p):].strip()
            break
    
    # 尝试解析时间
    # 策略：分割字符串，看哪部分像时间
    parts = re.split(r"\s+", content)
    trigger_dt = None
    remind_content = ""

    # 尝试匹配第一段或第二段作为时间
    for i, part in enumerate(parts):
        dt = _parse_time(part)
        if dt:
            trigger_dt = dt
            # 剩下的就是内容
            remind_content = " ".join(parts[:i] + parts[i+1:])
            break
    
    # 如果没按空格分，尝试正则提取
    if not trigger_dt:
        # 正则提取时间词
        # 10分钟后
        m = re.search(r"(\d+(?:分钟|分|min|小时|hour|h)后)", content)
        if m:
            time_str = m.group(1)
            trigger_dt = _parse_time(time_str)
            remind_content = content.replace(time_str, "").strip()
    
    if not trigger_dt:
        return None # 没识别出时间，还是让 LLM 处理或者当普通聊天

    # 清理内容中的“叫我”
    remind_content = remind_content.replace("叫我", "").strip()
    if not remind_content:
        remind_content = "（到点啦）"

    # 保存
    save_schedule(user_id, int(trigger_dt.timestamp()), remind_content)
    
    fmt_time = trigger_dt.strftime("%H:%M")
    diff = trigger_dt - datetime.now()
    if diff.total_seconds() > 86400:
        fmt_time = trigger_dt.strftime("%m-%d %H:%M")
    elif trigger_dt.date() != datetime.now().date():
        fmt_time = "明天 " + fmt_time
        
    reply = f"好的，我会在 {fmt_time} 提醒你：{remind_content}"
    add_chat_memory(user_id, "user", text)
    add_chat_memory(user_id, "assistant", reply)
    return reply


# === 后台检查任务 ===

@scheduler.scheduled_job("interval", seconds=60, id="check_schedules")
async def check_schedules():
    """每分钟检查一次到期提醒"""
    now_ts = int(datetime.now().timestamp())
    pendings = get_pending_schedules(now_ts)
    
    if not pendings:
        return

    bot = get_bot()
    for task in pendings:
        tid = task["id"]
        uid = task["user_id"]
        content = task["content"]
        
        try:
            msg = f"⏰ 叮叮！时间到啦～\n{content}"
            # 只有内容不为空才发，或者发默认提示
            await bot.send_private_msg(user_id=int(uid), message=msg)
            update_schedule_status(tid, "done")
            logger.info(f"[schedule] triggered id={tid} uid={uid}")
        except Exception as e:
            logger.error(f"[schedule] failed id={tid} uid={uid}: {e}")
