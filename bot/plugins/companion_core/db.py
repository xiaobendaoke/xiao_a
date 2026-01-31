"""SQLite 存储层（本插件的本地状态/缓存）。

维护的主要数据：
- `user_mood`：用户心情值（用于调整语气/主动互动策略）。
- `chat_history`：聊天记录（用于上下文与回忆）。
- `user_profile`：用户画像/备忘录（长期稳定信息）。
- `user_state`：用户活跃/主动互动的冷却、每日次数、锁等状态。
- `web_cache`：网页抓取后的标题/正文缓存（URL 总结用）。
- `rss_seen`：RSS 去重（避免重复推送同一条）。

关键点：
- `init_db()` 在模块导入时执行（创建表/索引），确保首次运行可用。
- 部分接口用 `asyncio.to_thread()` 包装同步 sqlite 操作，以避免阻塞事件循环。
- `lock_until_ts` 用于多实例/并发场景的简易互斥。
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from .web.utils import sha1 as _sha1

DB_PATH = Path(__file__).parent / "data.db"

# 方案 A：24 小时滚动窗口。超过 24h 未与小A对话 → 不再收到任何“主动推送”（不影响用户来触发的回复）
ACTIVE_WITHIN_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class ProactiveCandidate:
    user_id: int
    nickname: Optional[str]
    last_active_at: datetime
    last_user_text: Optional[str]


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def init_db():
    """初始化数据库表结构"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 1. 情绪持久化表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_mood (
                user_id TEXT PRIMARY KEY,
                mood_value INTEGER DEFAULT 0
            )
        """)
        # 2. 聊天记忆持久化表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                role TEXT,
                content TEXT,
                time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 3. ✅ 新增：用户画像表 (备忘录)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)

        # 4. ✅ 主动互动状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                user_id TEXT PRIMARY KEY,
                last_active_ts INTEGER DEFAULT 0,
                last_proactive_ts INTEGER DEFAULT 0,
                proactive_date TEXT DEFAULT '',
                proactive_count_today INTEGER DEFAULT 0,
                proactive_enabled INTEGER DEFAULT 1,
                cooldown_until_ts INTEGER DEFAULT 0,
                lock_until_ts INTEGER DEFAULT 0
            )
        """)

        # 5) ✅ 网页缓存（URL解析总结用）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS web_cache (
                url_hash TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                content TEXT,
                fetched_ts INTEGER DEFAULT 0,
                expires_ts INTEGER DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_web_cache_expires ON web_cache(expires_ts)")

        # 6) ✅ RSS去重（避免重复推送）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rss_seen (
                guid_hash TEXT PRIMARY KEY,
                feed_url TEXT,
                title TEXT,
                link TEXT,
                seen_ts INTEGER DEFAULT 0
            )
        """)

        # 7) ✅ 天气早报去重（避免一天重复推送）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weather_daily_push (
                user_id TEXT,
                day TEXT,
                pushed_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
        """)

        # 8) ✅ GitHub 周榜去重（避免一周重复推送）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS github_weekly_push (
                user_id TEXT,
                week TEXT,
                pushed_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, week)
            )
        """)

        # 9) ✅ 用户活跃小时统计（学习用户习惯，决定推送时段）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_active_hours (
                user_id TEXT,
                hour INTEGER,
                count INTEGER DEFAULT 0,
                last_updated_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, hour)
            )
        """)

        # 10) ✅ 用户洞察（从聊天记录提取的兴趣/偏好/习惯）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_insights (
                user_id TEXT,
                insight_type TEXT,
                content TEXT,
                confidence REAL DEFAULT 0.5,
                created_ts INTEGER DEFAULT 0,
                last_updated_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, insight_type, content)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_insights_type ON user_insights(user_id, insight_type)")


        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_state_last_active ON user_state(last_active_ts)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_state_cooldown ON user_state(cooldown_until_ts)")
        conn.commit()

# 初始化
init_db()

# --- 原有函数保持不变 ---
def get_mood(user_id: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT mood_value FROM user_mood WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else 0

def save_mood(user_id: str, value: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_mood (user_id, mood_value) VALUES (?, ?)", (user_id, value))
        conn.commit()

def save_chat(user_id: str, role: str, content: str):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        conn.commit()

def load_chats(user_id: str, limit: int = 10):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        rows = cursor.fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

# --- ✅ 新增：备忘录操作函数 ---
def save_profile_item(user_id: str, key: str, value: str):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_profile (user_id, key, value) VALUES (?, ?, ?)", (user_id, key, value))
        conn.commit()

def get_all_profile(user_id: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM user_profile WHERE user_id = ?", (user_id,))
        return {row[0]: row[1] for row in cursor.fetchall()}


def touch_active(user_id: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now()
    now_ts = _ts(now)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO user_state (user_id, last_active_ts) VALUES (?, ?)",
            (user_id, now_ts),
        )
        cur.execute("UPDATE user_state SET last_active_ts=? WHERE user_id=?", (now_ts, user_id))
        conn.commit()


def _get_profile_nickname(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    cur = conn.cursor()
    for k in ("称呼", "昵称", "名字", "name"):
        cur.execute("SELECT value FROM user_profile WHERE user_id=? AND key=? LIMIT 1", (user_id, k))
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    return None


def _get_last_user_text(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM chat_history WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    return (row[0].strip() if row and row[0] else None)


async def get_proactive_candidates(now: datetime, idle_before: datetime, limit: int = 20) -> list[ProactiveCandidate]:
    now_ts = _ts(now)
    idle_ts = _ts(idle_before)
    active_after_ts = max(0, int(now_ts) - int(ACTIVE_WITHIN_SECONDS))

    def _sync() -> list[ProactiveCandidate]:
        out: list[ProactiveCandidate] = []
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, last_active_ts
                FROM user_state
                WHERE proactive_enabled=1
                  AND last_active_ts > 0
                  AND last_active_ts <= ?
                  AND last_active_ts >= ?
                  AND cooldown_until_ts <= ?
                  AND lock_until_ts <= ?
                ORDER BY last_active_ts ASC
                LIMIT ?
                """,
                (idle_ts, active_after_ts, now_ts, now_ts, limit),
            )
            rows = cur.fetchall()
            for uid_text, last_active_ts in rows:
                try:
                    uid_int = int(uid_text)
                except Exception:
                    continue
                out.append(
                    ProactiveCandidate(
                        user_id=uid_int,
                        nickname=_get_profile_nickname(conn, uid_text),
                        last_active_at=datetime.fromtimestamp(int(last_active_ts)),
                        last_user_text=_get_last_user_text(conn, uid_text),
                    )
                )
        return out

    return await asyncio.to_thread(_sync)


async def claim_proactive_slot(
    user_id: int,
    now: datetime,
    today: date,
    max_per_day: int,
    lock_seconds: int = 300,
) -> bool:
    uid = str(user_id)
    now_ts = _ts(now)
    today_str = today.isoformat()
    lock_until = now_ts + int(lock_seconds)

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            conn.execute("BEGIN IMMEDIATE")

            cur.execute(
                "SELECT proactive_enabled, proactive_date, proactive_count_today, cooldown_until_ts, lock_until_ts "
                "FROM user_state WHERE user_id=?",
                (uid,),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT OR IGNORE INTO user_state (user_id, last_active_ts) VALUES (?, ?)",
                    (uid, now_ts),
                )
                cur.execute(
                    "SELECT proactive_enabled, proactive_date, proactive_count_today, cooldown_until_ts, lock_until_ts "
                    "FROM user_state WHERE user_id=?",
                    (uid,),
                )
                row = cur.fetchone()

            enabled, p_date, cnt, cooldown_until_ts, lock_until_ts = row
            if int(enabled) != 1:
                conn.rollback()
                return False

            if (p_date or "") != today_str:
                cnt = 0

            if int(cnt) >= int(max_per_day):
                conn.rollback()
                return False

            if int(cooldown_until_ts) > now_ts:
                conn.rollback()
                return False

            if int(lock_until_ts) > now_ts:
                conn.rollback()
                return False

            cur.execute("UPDATE user_state SET lock_until_ts=? WHERE user_id=?", (lock_until, uid))
            conn.commit()
            return True

    return await asyncio.to_thread(_sync)


async def mark_proactive_sent(user_id: int, now: datetime, cooldown_minutes: int) -> None:
    uid = str(user_id)
    now_ts = _ts(now)
    today_str = now.date().isoformat()
    cooldown_until = now_ts + int(cooldown_minutes) * 60

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT proactive_date, proactive_count_today FROM user_state WHERE user_id=?", (uid,))
            row = cur.fetchone()
            if row and row[0] == today_str:
                new_cnt = int(row[1]) + 1
            else:
                new_cnt = 1

            cur.execute(
                """
                UPDATE user_state
                SET last_proactive_ts=?,
                    proactive_date=?,
                    proactive_count_today=?,
                    cooldown_until_ts=?,
                    lock_until_ts=0
                WHERE user_id=?
                """,
                (now_ts, today_str, new_cnt, cooldown_until, uid),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def mark_proactive_failed(user_id: int, now: datetime, cooldown_seconds: int = 600) -> None:
    uid = str(user_id)
    now_ts = _ts(now)
    new_cd = now_ts + int(cooldown_seconds)

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT cooldown_until_ts FROM user_state WHERE user_id=?", (uid,))
            row = cur.fetchone()
            old_cd = int(row[0]) if row else 0
            cur.execute(
                "UPDATE user_state SET lock_until_ts=0, cooldown_until_ts=? WHERE user_id=?",
                (max(old_cd, new_cd), uid),
            )
            conn.commit()

    await asyncio.to_thread(_sync)

# ================================
# ✅ 网页缓存（URL总结）
# ================================
from .web.utils import sha1 as _sha1

def web_cache_get(url: str):
    """读取网页缓存，若过期返回 None"""
    h = _sha1(url)
    now_ts = int(datetime.now().timestamp())

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT title, content, expires_ts FROM web_cache WHERE url_hash=?", (h,))
        row = cur.fetchone()
        if not row:
            return None

        title, content, exp = row
        if int(exp or 0) < now_ts:
            return None

        return {"title": title or "", "content": content or ""}

def web_cache_set(url: str, title: str, content: str, ttl_hours: int = 12):
    """写入网页缓存"""
    h = _sha1(url)
    now_ts = int(datetime.now().timestamp())
    exp = now_ts + ttl_hours * 3600

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO web_cache (url_hash, url, title, content, fetched_ts, expires_ts) VALUES (?,?,?,?,?,?)",
            (h, url, title, content, now_ts, exp)
        )
        conn.commit()


# ================================
# ✅ RSS 去重 / 推送目标 / 冷却
# ================================
async def rss_seen(guid_hash: str) -> bool:
    """检查该RSS条目是否已推送过"""
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM rss_seen WHERE guid_hash=? LIMIT 1", (guid_hash,))
            return cur.fetchone() is not None
    return await asyncio.to_thread(_sync)

async def rss_mark_seen(guid_hash: str, feed_url: str, title: str, link: str):
    """标记该RSS条目已推送"""
    now_ts = int(datetime.now().timestamp())

    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO rss_seen (guid_hash, feed_url, title, link, seen_ts) VALUES (?,?,?,?,?)",
                (guid_hash, feed_url, title, link, now_ts)
            )
            conn.commit()
    await asyncio.to_thread(_sync)

async def get_rss_user_targets():
    """
    默认：推送给所有开启 proactive_enabled 的用户
    如果你只想推送给你自己，可以直接 return ["你的QQ号"]
    """
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM user_state WHERE proactive_enabled=1")
            return [r[0] for r in cur.fetchall()]
    return await asyncio.to_thread(_sync)


async def get_idle_user_states(limit: int = 200) -> list[tuple[str, int]]:
    """获取处于“可主动互动”的用户与其最后活跃时间戳。"""
    limit = int(limit) if limit else 200
    now_ts = _ts(datetime.now())
    active_after_ts = max(0, int(now_ts) - int(ACTIVE_WITHIN_SECONDS))

    def _sync() -> list[tuple[str, int]]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, last_active_ts
                FROM user_state
                WHERE proactive_enabled=1
                  AND last_active_ts > 0
                  AND last_active_ts >= ?
                ORDER BY last_active_ts ASC
                LIMIT ?
                """,
                (active_after_ts, limit),
            )
            return [(str(uid), int(ts or 0)) for uid, ts in cur.fetchall()]

    return await asyncio.to_thread(_sync)

async def claim_rss_slot(user_id: str, now: datetime, cooldown_minutes: int = 120) -> bool:
    """
    RSS推送冷却：
    - 每个用户 cooldown_minutes 内最多推送一次
    - 用 lock_until_ts 防止多实例重复推送
    """
    uid = str(user_id)
    now_ts = int(now.timestamp())
    cooldown_until = now_ts + cooldown_minutes * 60

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            conn.execute("BEGIN IMMEDIATE")

            cur.execute("SELECT cooldown_until_ts, lock_until_ts FROM user_state WHERE user_id=?", (uid,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False

            cd, lock_ts = int(row[0] or 0), int(row[1] or 0)
            if lock_ts > now_ts:
                conn.rollback()
                return False
            if cd > now_ts:
                conn.rollback()
                return False

            # 占用锁 5 分钟
            cur.execute("UPDATE user_state SET lock_until_ts=? WHERE user_id=?", (now_ts + 300, uid))
            conn.commit()
            return True

    ok = await asyncio.to_thread(_sync)
    if not ok:
        return False

    # 写冷却并释放锁（这里简单处理：先写冷却，防止发送失败重复；你也可改成发送成功后写）
    def _sync2():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE user_state SET cooldown_until_ts=?, lock_until_ts=0 WHERE user_id=?",
                (cooldown_until, uid)
            )
            conn.commit()

    await asyncio.to_thread(_sync2)
    return True


# ================================
# ✅ 天气提醒：目标用户 / 当日去重
# ================================
_CITY_KEYS = ("所在城市", "所在地", "城市", "位置", "当前城市", "常住地", "家乡")


async def get_weather_user_targets() -> list[tuple[str, str]]:
    """
    返回需要天气提醒的用户列表：[(user_id, city), ...]

    规则：
    - 默认只给 `user_state.proactive_enabled=1` 的用户推送；
    - 用户画像里存在“城市/所在地/所在城市...”任一字段才视为可推送。
    """
    now_ts = _ts(datetime.now())
    active_after_ts = max(0, int(now_ts) - int(ACTIVE_WITHIN_SECONDS))

    def _sync() -> list[tuple[str, str]]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT us.user_id, up.key, up.value
                FROM user_state us
                JOIN user_profile up ON up.user_id = us.user_id
                WHERE us.proactive_enabled=1
                  AND us.last_active_ts > 0
                  AND us.last_active_ts >= ?
                  AND up.key IN ({",".join(["?"] * len(_CITY_KEYS))})
                """,
                (active_after_ts, *_CITY_KEYS),
            )
            rows = cur.fetchall()

        # 按 key 优先级给每个 user_id 选一个 city
        priority = {k: i for i, k in enumerate(_CITY_KEYS)}
        best: dict[str, tuple[int, str]] = {}
        for uid, k, v in rows:
            uid = str(uid)
            city = str(v or "").strip()
            if not city:
                continue
            rank = priority.get(str(k), 999)
            prev = best.get(uid)
            if prev is None or rank < prev[0]:
                best[uid] = (rank, city)

        return [(uid, city) for uid, (_, city) in best.items()]

    return await asyncio.to_thread(_sync)


async def weather_pushed_today(user_id: str, day: date) -> bool:
    """检查某用户今天是否已推送过天气早报。"""
    uid = str(user_id)
    day_str = day.isoformat()

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM weather_daily_push WHERE user_id=? AND day=? LIMIT 1",
                (uid, day_str),
            )
            return cur.fetchone() is not None

    return await asyncio.to_thread(_sync)


async def github_weekly_pushed(user_id: str, week: str) -> bool:
    """检查某用户本周是否已推送过 GitHub 周榜。week 建议用 ISO week，如 '2026-W05'。"""
    uid = str(user_id)
    wk = str(week or "").strip()
    if not wk:
        return False

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM github_weekly_push WHERE user_id=? AND week=? LIMIT 1",
                (uid, wk),
            )
            return cur.fetchone() is not None

    return await asyncio.to_thread(_sync)


async def github_weekly_mark_pushed(user_id: str, week: str) -> None:
    """标记某用户本周已推送 GitHub 周榜。"""
    uid = str(user_id)
    wk = str(week or "").strip()
    if not wk:
        return
    now_ts = int(datetime.now().timestamp())

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO github_weekly_push (user_id, week, pushed_ts) VALUES (?,?,?)",
                (uid, wk, now_ts),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def filter_active_user_ids(user_ids: list[int], now: Optional[datetime] = None) -> list[int]:
    """按“24小时内活跃”过滤 user_id（保持输入顺序）。"""
    ids: list[int] = []
    for x in user_ids or []:
        try:
            v = int(x)
        except Exception:
            continue
        if v > 0:
            ids.append(v)

    if not ids:
        return []

    now_ts = _ts(now or datetime.now())
    active_after_ts = max(0, int(now_ts) - int(ACTIVE_WITHIN_SECONDS))
    uid_texts = [str(i) for i in ids]

    def _sync() -> set[int]:
        placeholders = ",".join(["?"] * len(uid_texts))
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT user_id
                FROM user_state
                WHERE last_active_ts > 0
                  AND last_active_ts >= ?
                  AND user_id IN ({placeholders})
                """,
                (active_after_ts, *uid_texts),
            )
            rows = cur.fetchall()
        out: set[int] = set()
        for (uid,) in rows or []:
            try:
                out.add(int(uid))
            except Exception:
                continue
        return out

    active = await asyncio.to_thread(_sync)
    return [i for i in ids if i in active]


async def weather_mark_pushed(user_id: str, day: date) -> None:
    """标记某用户今日已推送天气早报。"""
    uid = str(user_id)
    day_str = day.isoformat()
    now_ts = int(datetime.now().timestamp())

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO weather_daily_push (user_id, day, pushed_ts) VALUES (?,?,?)",
                (uid, day_str, now_ts),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


# ================================
# ✅ 用户活跃小时统计（学习用户习惯）
# ================================

def log_user_active_hour(user_id: str, hour: int | None = None) -> None:
    """
    记录用户在某个小时活跃（每次用户发消息时调用）。
    hour 默认取当前小时。
    """
    uid = str(user_id)
    if hour is None:
        hour = datetime.now().hour
    hour = int(hour) % 24
    now_ts = int(datetime.now().timestamp())
    
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_active_hours (user_id, hour, count, last_updated_ts)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, hour) DO UPDATE SET
                count = count + 1,
                last_updated_ts = excluded.last_updated_ts
            """,
            (uid, hour, now_ts),
        )
        conn.commit()


async def get_user_active_hours(user_id: str) -> dict[int, int]:
    """
    获取用户各小时的活跃计数：{hour: count}。
    返回的 hour 是 0-23 的整数。
    """
    uid = str(user_id)
    
    def _sync() -> dict[int, int]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT hour, count FROM user_active_hours WHERE user_id=?",
                (uid,),
            )
            return {int(h): int(c) for h, c in cur.fetchall()}
    
    return await asyncio.to_thread(_sync)


def get_user_active_hours_sync(user_id: str) -> dict[int, int]:
    """同步版本的 get_user_active_hours。"""
    uid = str(user_id)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT hour, count FROM user_active_hours WHERE user_id=?",
            (uid,),
        )
        return {int(h): int(c) for h, c in cur.fetchall()}


# ================================
# ✅ 用户洞察（从聊天记录提取的兴趣/偏好/习惯）
# ================================

def save_user_insight(
    user_id: str,
    insight_type: str,
    content: str,
    confidence: float = 0.5,
) -> None:
    """
    保存用户洞察。
    
    Args:
        user_id: 用户 ID
        insight_type: 洞察类型（interest/preference/habit/topic）
        content: 洞察内容
        confidence: 置信度 0-1
    """
    uid = str(user_id)
    itype = str(insight_type or "").strip()
    cont = str(content or "").strip()
    if not itype or not cont:
        return
    
    now_ts = int(datetime.now().timestamp())
    confidence = max(0.0, min(1.0, float(confidence)))
    
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_insights (user_id, insight_type, content, confidence, created_ts, last_updated_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, insight_type, content) DO UPDATE SET
                confidence = MAX(confidence, excluded.confidence),
                last_updated_ts = excluded.last_updated_ts
            """,
            (uid, itype, cont, confidence, now_ts, now_ts),
        )
        conn.commit()


def get_user_insights(user_id: str, insight_type: str | None = None) -> list[dict]:
    """
    获取用户洞察。
    
    Args:
        user_id: 用户 ID
        insight_type: 可选，筛选特定类型（interest/preference/habit/topic）
        
    Returns:
        洞察列表 [{"type": "...", "content": "...", "confidence": 0.8}, ...]
    """
    uid = str(user_id)
    
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if insight_type:
            cur.execute(
                """
                SELECT insight_type, content, confidence
                FROM user_insights
                WHERE user_id=? AND insight_type=?
                ORDER BY confidence DESC, last_updated_ts DESC
                """,
                (uid, str(insight_type)),
            )
        else:
            cur.execute(
                """
                SELECT insight_type, content, confidence
                FROM user_insights
                WHERE user_id=?
                ORDER BY confidence DESC, last_updated_ts DESC
                """,
                (uid,),
            )
        
        return [
            {"type": str(t), "content": str(c), "confidence": float(conf)}
            for t, c, conf in cur.fetchall()
        ]


def get_user_insights_summary(user_id: str) -> str:
    """
    获取用户洞察的文本摘要（用于构建 LLM 上下文）。
    """
    insights = get_user_insights(user_id)
    if not insights:
        return ""
    
    # 按类型分组
    by_type: dict[str, list[str]] = {}
    type_labels = {
        "interest": "兴趣",
        "preference": "偏好",
        "habit": "习惯",
        "topic": "关注话题",
    }
    
    for ins in insights[:20]:  # 最多取 20 条
        t = ins["type"]
        label = type_labels.get(t, t)
        if label not in by_type:
            by_type[label] = []
        by_type[label].append(ins["content"])
    
    lines = []
    for label, items in by_type.items():
        lines.append(f"- {label}：{', '.join(items[:5])}")
    
    return "\n".join(lines)


