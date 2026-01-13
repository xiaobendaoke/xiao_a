"""RSS 定时推送任务（apscheduler）。

流程：
- 按 cron 定时触发（早/中/晚各一次），并在免打扰时段跳过。
- 拉取 `DEFAULT_FEEDS` 的 RSS 条目（`web.rss.fetch_feeds()`）。
- 选择推送目标：默认取 `proactive_enabled=1` 的用户（见 `db.get_rss_user_targets()`）。
- 对每个用户：
  - 先做推送冷却（`db.claim_rss_slot()`，默认 2 小时一次）；
  - 在候选条目中挑一个“没推过的”（`db.rss_seen/rss_mark_seen`）；
  - 调用 `llm_web.generate_rss_share()` 生成“女友式分享”；
  - 通过 OneBot 私聊接口发送。

可配置项：
- `DEFAULT_FEEDS`：订阅源列表（可按需增删）。
- `QUIET_START/QUIET_END`：免打扰时间窗口。
"""

from __future__ import annotations
import os
import re
from datetime import datetime, time
from nonebot import get_bots, logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .web.rss import fetch_feeds
from .web.utils import sha1
from .db import rss_seen, rss_mark_seen, get_rss_user_targets, claim_rss_slot
from .llm_web import generate_rss_share

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


def in_quiet_hours(now: datetime) -> bool:
    t = now.time()
    return (t >= QUIET_START) or (t < QUIET_END)


def pick_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


async def _push_once(tag: str):
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

    # 2) 获取推送目标用户
    # 默认：所有开启 proactive 的用户（你也可以改成只推给某一个 user_id）
    targets = await get_rss_user_targets()
    if not targets:
        logger.info("[rss] skip: no target users")
        return

    # 3) 对每个用户挑一条没见过的
    for uid in targets:
        try:
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
            await bot.call_api("send_private_msg", user_id=int(uid), message=text)
            logger.info(f"[rss] sent uid={uid} title={chosen.get('title','')!r}")

        except Exception as e:
            logger.exception(f"[rss] push failed uid={uid}: {e}")


# ✅ 上午一条
@scheduler.scheduled_job(
    "cron",
    hour=9,
    minute=30,
    id="rss_morning",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60
)
async def rss_morning():
    await _push_once("morning")


# ✅ 下午一条
@scheduler.scheduled_job(
    "cron",
    hour=15,
    minute=30,
    id="rss_afternoon",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60
)
async def rss_afternoon():
    await _push_once("afternoon")


# ✅ 晚上一条
@scheduler.scheduled_job(
    "cron",
    hour=20,
    minute=44,
    id="rss_evening",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60
)
async def rss_evening():
    await _push_once("evening")
