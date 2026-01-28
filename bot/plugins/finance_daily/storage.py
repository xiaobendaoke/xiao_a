"""finance_daily 的独立 SQLite 存储层（finance.db）。

特性：
- 不与 companion_core 的 `data.db` 共用，避免数据耦合；
- 保存：日任务状态、行情快照、每日指标、公告标题、LLM 结构化输出、最终日报文本；
- 接口风格：同步 sqlite3 + `asyncio.to_thread()`，避免阻塞事件循环（与 companion_core/db.py 一致）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).with_name("finance.db")


def _now_ts() -> int:
    return int(datetime.now().timestamp())


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_jobs (
              job_id TEXT PRIMARY KEY,
              market TEXT,
              trade_date TEXT,
              status TEXT,
              started_ts INTEGER DEFAULT 0,
              ended_ts INTEGER DEFAULT 0,
              error TEXT DEFAULT ''
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_jobs_market_date ON daily_jobs(market, trade_date)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS symbols_cache (
              market TEXT,
              ts_code TEXT,
              name TEXT,
              industry TEXT,
              list_date TEXT,
              is_st INTEGER DEFAULT 0,
              company_json TEXT DEFAULT '',
              basic_updated_ts INTEGER DEFAULT 0,
              company_updated_ts INTEGER DEFAULT 0,
              PRIMARY KEY (market, ts_code)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_cache_basic_updated ON symbols_cache(basic_updated_ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_cache_company_updated ON symbols_cache(company_updated_ts)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eod_quotes (
              market TEXT,
              trade_date TEXT,
              ts_code TEXT,
              open REAL, high REAL, low REAL, close REAL,
              vol REAL, amount REAL, pct_chg REAL,
              PRIMARY KEY (market, trade_date, ts_code)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_eod_quotes_market_date ON eod_quotes(market, trade_date)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_basic (
              market TEXT,
              trade_date TEXT,
              ts_code TEXT,
              turnover_rate REAL,
              volume_ratio REAL,
              total_mv REAL,
              circ_mv REAL,
              pe REAL,
              pb REAL,
              PRIMARY KEY (market, trade_date, ts_code)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_basic_market_date ON daily_basic(market, trade_date)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
              hash TEXT PRIMARY KEY,
              market TEXT,
              trade_date TEXT,
              ts_code TEXT,
              ann_date TEXT,
              pub_ts INTEGER,
              title TEXT,
              url TEXT,
              source TEXT DEFAULT ''
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_anns_market_date_code ON announcements(market, trade_date, ts_code)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_results (
              market TEXT,
              trade_date TEXT,
              ts_code TEXT,
              features_json TEXT,
              llm_json TEXT,
              llm_model TEXT,
              prompt_version TEXT,
              created_ts INTEGER DEFAULT 0,
              PRIMARY KEY (market, trade_date, ts_code)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_analysis_market_date ON analysis_results(market, trade_date)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reports (
              market TEXT,
              trade_date TEXT,
              report_text TEXT,
              report_json TEXT DEFAULT '',
              created_ts INTEGER DEFAULT 0,
              PRIMARY KEY (market, trade_date)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
              market TEXT,
              user_id INTEGER,
              enabled INTEGER DEFAULT 1,
              created_ts INTEGER DEFAULT 0,
              updated_ts INTEGER DEFAULT 0,
              PRIMARY KEY (market, user_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_market_enabled ON subscriptions(market, enabled)")
        conn.commit()

        # --- 轻量迁移：为已存在旧表补列 ---
        cur.execute("PRAGMA table_info(symbols_cache)")
        cols = {str(r[1]) for r in cur.fetchall() if r and len(r) > 1}
        if "basic_updated_ts" not in cols:
            cur.execute("ALTER TABLE symbols_cache ADD COLUMN basic_updated_ts INTEGER DEFAULT 0")
        if "company_updated_ts" not in cols:
            cur.execute("ALTER TABLE symbols_cache ADD COLUMN company_updated_ts INTEGER DEFAULT 0")
        if "updated_ts" in cols:
            # 兼容早期字段：尽量把旧值迁移到 basic_updated_ts（不强制删除列，避免破坏）
            try:
                cur.execute("UPDATE symbols_cache SET basic_updated_ts = COALESCE(basic_updated_ts, 0) + COALESCE(updated_ts, 0) WHERE basic_updated_ts=0")
            except Exception:
                pass
        conn.commit()


init_db()


async def try_start_job(market: str, trade_date: str) -> bool:
    return await try_start_job_ex(market, trade_date)


async def try_start_job_ex(
    market: str,
    trade_date: str,
    *,
    force: bool = False,
    stale_after_seconds: int = 30 * 60,
) -> bool:
    """尝试将任务置为 started（带幂等/强制重跑/卡死保护）。"""
    job_id = f"{market}|{trade_date}"
    now_ts = _now_ts()
    stale_after_seconds = max(60, int(stale_after_seconds or 0))

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            conn.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT status, started_ts FROM daily_jobs WHERE job_id=? LIMIT 1", (job_id,))
            row = cur.fetchone()
            if row:
                status = str(row[0] or "")
                started_ts = int(row[1] or 0)
                if status == "succeeded" and not force:
                    conn.rollback()
                    return False
                if status == "started" and not force:
                    # 卡死保护：started 太久了允许重跑
                    if started_ts and (now_ts - started_ts) > stale_after_seconds:
                        pass
                    else:
                        conn.rollback()
                        return False

            cur.execute(
                "INSERT OR REPLACE INTO daily_jobs(job_id,market,trade_date,status,started_ts,ended_ts,error) VALUES (?,?,?,?,?,?,?)",
                (job_id, market, trade_date, "started", now_ts, 0, ""),
            )
            conn.commit()
            return True

    return await asyncio.to_thread(_sync)


async def get_job(market: str, trade_date: str) -> dict[str, Any]:
    job_id = f"{market}|{trade_date}"

    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT job_id,market,trade_date,status,started_ts,ended_ts,error FROM daily_jobs WHERE job_id=? LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "job_id": row[0],
                "market": row[1],
                "trade_date": row[2],
                "status": row[3],
                "started_ts": int(row[4] or 0),
                "ended_ts": int(row[5] or 0),
                "error": row[6] or "",
            }

    return await asyncio.to_thread(_sync)


async def get_latest_job(market: str) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT job_id,market,trade_date,status,started_ts,ended_ts,error
                FROM daily_jobs
                WHERE market=?
                ORDER BY trade_date DESC, started_ts DESC
                LIMIT 1
                """,
                (market,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "job_id": row[0],
                "market": row[1],
                "trade_date": row[2],
                "status": row[3],
                "started_ts": int(row[4] or 0),
                "ended_ts": int(row[5] or 0),
                "error": row[6] or "",
            }

    return await asyncio.to_thread(_sync)


async def mark_job_succeeded(market: str, trade_date: str) -> None:
    job_id = f"{market}|{trade_date}"
    ended = _now_ts()

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE daily_jobs SET status='succeeded', ended_ts=?, error='' WHERE job_id=?",
                (ended, job_id),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def mark_job_failed(market: str, trade_date: str, error: str) -> None:
    job_id = f"{market}|{trade_date}"
    ended = _now_ts()

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE daily_jobs SET status='failed', ended_ts=?, error=? WHERE job_id=?",
                (ended, (error or "")[:2000], job_id),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def set_subscription(market: str, user_id: int, *, enabled: bool) -> None:
    now = _now_ts()
    m = (market or "").strip() or "CN_A"
    uid = int(user_id)
    en = 1 if enabled else 0

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO subscriptions(market,user_id,enabled,created_ts,updated_ts)
                VALUES (?,?,?,?,?)
                ON CONFLICT(market,user_id) DO UPDATE SET
                  enabled=excluded.enabled,
                  updated_ts=excluded.updated_ts
                """,
                (m, uid, en, now, now),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def is_subscription_enabled(market: str, user_id: int) -> bool:
    m = (market or "").strip() or "CN_A"
    uid = int(user_id)

    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT enabled FROM subscriptions WHERE market=? AND user_id=? LIMIT 1",
                (m, uid),
            )
            row = cur.fetchone()
            if not row:
                return False
            return int(row[0] or 0) == 1

    return await asyncio.to_thread(_sync)


async def list_enabled_subscribers(market: str) -> list[int]:
    m = (market or "").strip() or "CN_A"

    def _sync() -> list[int]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id
                FROM subscriptions
                WHERE market=? AND enabled=1
                ORDER BY created_ts ASC, user_id ASC
                """,
                (m,),
            )
            rows = cur.fetchall()
        out: list[int] = []
        for (uid,) in rows or []:
            try:
                out.append(int(uid))
            except Exception:
                continue
        return list(dict.fromkeys([u for u in out if u > 0]))

    return await asyncio.to_thread(_sync)


async def upsert_symbols_basic(
    market: str, rows: list[dict[str, Any]], *, updated_ts: Optional[int] = None
) -> None:
    ts = int(updated_ts or _now_ts())

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            data = []
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                name = str(r.get("name") or "").strip()
                industry = str(r.get("industry") or "").strip()
                list_date = str(r.get("list_date") or "").strip()
                is_st = 1 if ("st" in name.lower() or "退" in name) else 0
                data.append((market, ts_code, name, industry, list_date, is_st, ts))
            if not data:
                return
            cur.executemany(
                """
                INSERT INTO symbols_cache(market,ts_code,name,industry,list_date,is_st,basic_updated_ts)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(market,ts_code) DO UPDATE SET
                  name=excluded.name,
                  industry=excluded.industry,
                  list_date=excluded.list_date,
                  is_st=excluded.is_st,
                  basic_updated_ts=excluded.basic_updated_ts
                """,
                data,
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def get_symbol_basic(market: str, ts_code: str) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name,industry,list_date,is_st,company_json,basic_updated_ts,company_updated_ts FROM symbols_cache WHERE market=? AND ts_code=? LIMIT 1",
                (market, ts_code),
            )
            row = cur.fetchone()
            if not row:
                return {}
            company_json = row[4] or ""
            company = {}
            if company_json:
                try:
                    company = json.loads(company_json)
                except Exception:
                    company = {}
            return {
                "ts_code": ts_code,
                "name": row[0] or "",
                "industry": row[1] or "",
                "list_date": row[2] or "",
                "is_st": int(row[3] or 0),
                "company": company,
                "basic_updated_ts": int(row[5] or 0),
                "company_updated_ts": int(row[6] or 0),
            }

    return await asyncio.to_thread(_sync)


async def upsert_stock_company(market: str, ts_code: str, company: dict[str, Any]) -> None:
    js = json.dumps(company or {}, ensure_ascii=False)
    ts = _now_ts()

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO symbols_cache(market,ts_code,company_json,company_updated_ts)
                VALUES (?,?,?,?)
                ON CONFLICT(market,ts_code) DO UPDATE SET
                  company_json=excluded.company_json,
                  company_updated_ts=excluded.company_updated_ts
                """,
                (market, ts_code, js, ts),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def store_eod_quotes(market: str, trade_date: str, rows: list[dict[str, Any]]) -> None:
    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            data = []
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                data.append(
                    (
                        market,
                        trade_date,
                        ts_code,
                        r.get("open"),
                        r.get("high"),
                        r.get("low"),
                        r.get("close"),
                        r.get("vol"),
                        r.get("amount"),
                        r.get("pct_chg"),
                    )
                )
            if not data:
                return
            cur.executemany(
                """
                INSERT OR REPLACE INTO eod_quotes
                (market,trade_date,ts_code,open,high,low,close,vol,amount,pct_chg)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                data,
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def store_daily_basic(market: str, trade_date: str, rows: list[dict[str, Any]]) -> None:
    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            data = []
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                data.append(
                    (
                        market,
                        trade_date,
                        ts_code,
                        r.get("turnover_rate"),
                        r.get("volume_ratio"),
                        r.get("total_mv"),
                        r.get("circ_mv"),
                        r.get("pe"),
                        r.get("pb"),
                    )
                )
            if not data:
                return
            cur.executemany(
                """
                INSERT OR REPLACE INTO daily_basic
                (market,trade_date,ts_code,turnover_rate,volume_ratio,total_mv,circ_mv,pe,pb)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                data,
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def store_announcements(rows: list[dict[str, Any]]) -> None:
    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            data = []
            for r in rows or []:
                h = str(r.get("hash") or "").strip()
                if not h:
                    continue
                data.append(
                    (
                        h,
                        str(r.get("market") or "").strip(),
                        str(r.get("trade_date") or "").strip(),
                        str(r.get("ts_code") or "").strip(),
                        str(r.get("ann_date") or "").strip(),
                        int(r.get("pub_ts") or 0),
                        str(r.get("title") or "").strip(),
                        str(r.get("url") or "").strip(),
                        str(r.get("source") or "").strip(),
                    )
                )
            if not data:
                return
            cur.executemany(
                """
                INSERT OR IGNORE INTO announcements
                (hash,market,trade_date,ts_code,ann_date,pub_ts,title,url,source)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                data,
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def list_announcements(market: str, trade_date: str, ts_code: str, *, limit: int = 10) -> list[dict[str, Any]]:
    def _sync() -> list[dict[str, Any]]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT title,url,pub_ts,ann_date,source
                FROM announcements
                WHERE market=? AND trade_date=? AND ts_code=?
                ORDER BY pub_ts DESC
                LIMIT ?
                """,
                (market, trade_date, ts_code, int(limit)),
            )
            rows = cur.fetchall()
        out = []
        for title, url, pub_ts, ann_date, source in rows:
            out.append(
                {
                    "title": title or "",
                    "url": url or "",
                    "pub_ts": int(pub_ts or 0),
                    "ann_date": ann_date or "",
                    "source": source or "",
                }
            )
        return out

    return await asyncio.to_thread(_sync)


async def get_daily_basic_row(market: str, trade_date: str, ts_code: str) -> dict[str, Any]:
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT turnover_rate,volume_ratio,total_mv,circ_mv,pe,pb
                FROM daily_basic
                WHERE market=? AND trade_date=? AND ts_code=? LIMIT 1
                """,
                (market, trade_date, ts_code),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "turnover_rate": row[0],
                "volume_ratio": row[1],
                "total_mv": row[2],
                "circ_mv": row[3],
                "pe": row[4],
                "pb": row[5],
            }

    return await asyncio.to_thread(_sync)


async def upsert_analysis_result(
    market: str,
    trade_date: str,
    ts_code: str,
    *,
    features: dict[str, Any],
    llm_json: dict[str, Any],
    llm_model: str,
    prompt_version: str,
) -> None:
    created = _now_ts()
    fjs = json.dumps(features or {}, ensure_ascii=False)
    ljs = json.dumps(llm_json or {}, ensure_ascii=False)

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO analysis_results
                (market,trade_date,ts_code,features_json,llm_json,llm_model,prompt_version,created_ts)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (market, trade_date, ts_code, fjs, ljs, llm_model, prompt_version, created),
            )
            conn.commit()

    await asyncio.to_thread(_sync)


async def upsert_daily_report(market: str, trade_date: str, *, report_text: str, report_json: dict[str, Any]) -> None:
    created = _now_ts()
    rjs = json.dumps(report_json or {}, ensure_ascii=False)

    def _sync() -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO daily_reports
                (market,trade_date,report_text,report_json,created_ts)
                VALUES (?,?,?,?,?)
                """,
                (market, trade_date, report_text or "", rjs, created),
            )
            conn.commit()

    await asyncio.to_thread(_sync)
