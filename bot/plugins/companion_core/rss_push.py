"""RSS 主动分享（基于“用户空闲时间”的触发）。

流程：
- 按 interval 周期检查（默认 6 分钟一次），在免打扰时段跳过。
- 拉取 `DEFAULT_FEEDS` 的 RSS 条目（`web.rss.fetch_feeds()`）。
- 选择推送目标：读取用户最近活跃时间（`db.get_idle_user_states()`）。
- 只有当“用户空闲超过随机阈值”才触发：
  - 每个用户每天生成一个随机阈值（`RSS_IDLE_MINUTES_MIN~MAX`）；
  - 超过该阈值后，且通过 `claim_rss_slot()` 冷却判定，则进行推送。
- 推送内容：
  - 在候选条目中挑一个“没推过的”（`db.rss_seen/rss_mark_seen`）；
  - 调用 `llm_web.generate_rss_share()` 生成口语化分享；
  - 通过 OneBot 私聊接口发送。

可配置项：
- `DEFAULT_FEEDS`：订阅源列表（可按需增删）。
- `QUIET_START/QUIET_END`：免打扰时间窗口。
- `RSS_IDLE_ENABLED`：是否启用“空闲触发”模式（默认 1）。
- `RSS_IDLE_CHECK_INTERVAL_MINUTES`：检查间隔（默认 6）。
- `RSS_IDLE_MINUTES_MIN`/`RSS_IDLE_MINUTES_MAX`：空闲阈值范围（分钟）。
"""

from __future__ import annotations
import asyncio
import os
import re
import random
from datetime import date, datetime, time
from nonebot import get_bots, logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .web.rss import fetch_feeds
from .web.utils import sha1
from .db import rss_seen, rss_mark_seen, get_idle_user_states, claim_rss_slot
from .llm_web import generate_rss_share
from .utils.typing_speed import typing_delay_seconds

# ✅ 你可以在这里配置 RSS 源
DEFAULT_FEEDS = [
    # ⚠️ 默认不再依赖 rsshub：不少环境里 `rsshub.app` 直连会网络不可达/超时，导致一直“拉不到条目”。
    # 这些源在多数国内网络可直连，且内容适合“像人一样分享”。
    "https://www.thepaper.cn/rss",         # 澎湃新闻（综合）
    "https://www.huxiu.com/rss/0.xml",     # 虎嗅（商业/科技）
    "https://www.36kr.com/feed",           # 36氪（创业/科技）
    "https://www.ithome.com/rss/",         # IT之家（科技）
    "https://sspai.com/feed",              # 少数派（效率/生活方式）
    "https://www.solidot.org/index.rss",   # Solidot（科技资讯）
]

# 可选：通过环境变量覆盖（逗号/空格/换行分隔）
_env_feeds = (os.getenv("RSS_FEEDS") or "").strip()
if _env_feeds:
    DEFAULT_FEEDS = [u for u in re.split(r"[\s,]+", _env_feeds) if u]

# 兼容旧变量名（如果你在别处引用过）
RSS_FEEDS = DEFAULT_FEEDS

# ✅ 免打扰时间
QUIET_START = time(23, 0)
QUIET_END = time(8, 0)


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


RSS_IDLE_ENABLED = _env_int("RSS_IDLE_ENABLED", 1) == 1
RSS_IDLE_CHECK_INTERVAL_MINUTES = _env_int("RSS_IDLE_CHECK_INTERVAL_MINUTES", 6)
RSS_IDLE_MINUTES_MIN = _env_int("RSS_IDLE_MINUTES_MIN", 60)
RSS_IDLE_MINUTES_MAX = _env_int("RSS_IDLE_MINUTES_MAX", 180)


def in_quiet_hours(now: datetime) -> bool:
    t = now.time()
    return (t >= QUIET_START) or (t < QUIET_END)


def pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


def _pick_idle_threshold_minutes(user_id: str, today: date) -> int:
    """为每个用户每天生成一个固定随机阈值（分钟），避免频繁抖动。"""
    lo = min(RSS_IDLE_MINUTES_MIN, RSS_IDLE_MINUTES_MAX)
    hi = max(RSS_IDLE_MINUTES_MIN, RSS_IDLE_MINUTES_MAX)
    seed = f"rss|{user_id}|{today.isoformat()}"
    rng = random.Random(seed)
    return rng.randint(int(lo), int(hi))


async def _push_when_idle(tag: str):
    now = datetime.now()
    logger.info(f"[rss] tick tag={tag} now={now.isoformat(sep=' ', timespec='seconds')}")
    if in_quiet_hours(now):
        logger.info("[rss] skip: quiet hours")
        return

    bot = pick_bot()
    if bot is None:
        logger.info("[rss] skip: no connected bot")
        return

    # 1) 拉 RSS
    try:
        items = await fetch_feeds(DEFAULT_FEEDS, limit_each=10)
    except Exception as e:
        logger.error(f"[rss] fetch feeds failed: {e}")
        return

    if not items:
        logger.warning("[rss] skip: empty feed items")
        return

    # 2) 获取推送目标用户（带 last_active_ts）
    targets = await get_idle_user_states()
    if not targets:
        logger.info("[rss] skip: no target users")
        return

    # 3) 对每个用户挑一条没见过的（满足“空闲时间随机阈值”）
    today = now.date()
    now_ts = int(now.timestamp())
    for uid, last_active_ts in targets:
        try:
            idle_minutes = max(0, int((now_ts - int(last_active_ts or 0)) // 60))
            threshold = _pick_idle_threshold_minutes(str(uid), today)
            if idle_minutes < threshold:
                continue

            # ✅ RSS 冷却：2小时内最多推一次（避免刷屏）
            claimed = await claim_rss_slot(uid, now, cooldown_minutes=120)
            if not claimed:
                continue

            chosen = None
            for it in items:
                guid = it.get("guid") or it.get("link") or (it.get("title", "") + it.get("published", ""))
                h = sha1(f"{it.get('feed_url','')}|{guid}")

                if not await rss_seen(h):
                    chosen = it
                    await rss_mark_seen(
                        guid_hash=h,
                        feed_url=it.get("feed_url", ""),
                        title=it.get("title", ""),
                        link=it.get("link", ""),
                    )
                    break

            if not chosen:
                continue

            # 4) 女友式生成分享
            msg = await generate_rss_share(uid, chosen)
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            # 5) 私聊发送
            await asyncio.sleep(typing_delay_seconds(text, user_id=uid))
            await bot.call_api("send_private_msg", user_id=int(uid), message=text)
            logger.info(f"[rss] sent uid={uid} title={chosen.get('title','')!r}")

        except Exception as e:
            logger.exception(f"[rss] push failed uid={uid}: {e}")


@scheduler.scheduled_job(
    "interval",
    minutes=RSS_IDLE_CHECK_INTERVAL_MINUTES,
    id="rss_idle",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
)
async def rss_idle_job():
    if not RSS_IDLE_ENABLED:
        return
    await _push_when_idle("idle")
