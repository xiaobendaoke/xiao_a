"""Finance Daily 独立存储层（finance.db）。

职责：
- 订阅管理（用户订阅/取消财经日报）
- 任务状态管理（幂等控制、任务追踪）
- 行情数据存储（EOD quotes、daily_basic）
- 公告缓存（announcements）
- 股票信息缓存（symbols_basic、stock_company）
- 分析结果存储（analysis_result、daily_report）

设计：
- 使用独立的 finance.db，不与 companion_core 的 data.db 共用
- 所有异步接口内部用 asyncio.to_thread 包装同步 sqlite 操作
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# 独立数据库路径
DB_PATH = Path(__file__).parent / "finance.db"


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _now_ts() -> int:
    return int(datetime.now().timestamp())


def init_db():
    """初始化数据库表结构"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 1. 订阅表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                market TEXT,
                enabled INTEGER DEFAULT 1,
                created_ts INTEGER DEFAULT 0,
                updated_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, market)
            )
        """)
        
        # 2. 任务状态表（幂等控制）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                market TEXT,
                trade_date TEXT,
                status TEXT DEFAULT 'pending',
                started_ts INTEGER DEFAULT 0,
                finished_ts INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                PRIMARY KEY (market, trade_date)
            )
        """)
        
        # 3. 日线行情缓存
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eod_quotes (
                market TEXT,
                trade_date TEXT,
                ts_code TEXT,
                data_json TEXT,
                PRIMARY KEY (market, trade_date, ts_code)
            )
        """)
        
        # 4. 日线基本指标缓存
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_basic (
                market TEXT,
                trade_date TEXT,
                ts_code TEXT,
                data_json TEXT,
                PRIMARY KEY (market, trade_date, ts_code)
            )
        """)
        
        # 5. 股票基本信息缓存
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols_basic (
                market TEXT,
                ts_code TEXT,
                name TEXT,
                industry TEXT,
                list_date TEXT,
                data_json TEXT,
                updated_ts INTEGER DEFAULT 0,
                PRIMARY KEY (market, ts_code)
            )
        """)
        
        # 6. 公司画像缓存
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_company (
                market TEXT,
                ts_code TEXT,
                data_json TEXT,
                updated_ts INTEGER DEFAULT 0,
                PRIMARY KEY (market, ts_code)
            )
        """)
        
        # 7. 公告缓存
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                hash TEXT PRIMARY KEY,
                market TEXT,
                trade_date TEXT,
                ts_code TEXT,
                ann_date TEXT,
                pub_ts INTEGER DEFAULT 0,
                title TEXT,
                url TEXT,
                source TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ann_ts_code ON announcements(market, trade_date, ts_code)")
        
        # 8. 分析结果存储
        cursor.execute("""
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
        """)
        
        # 9. 日报存储
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                market TEXT,
                trade_date TEXT,
                report_text TEXT,
                report_json TEXT,
                created_ts INTEGER DEFAULT 0,
                PRIMARY KEY (market, trade_date)
            )
        """)
        
        conn.commit()


# 初始化数据库
init_db()


# ================================
# 订阅管理
# ================================

async def set_subscription(market: str, user_id: int, enabled: bool) -> None:
    """设置用户订阅状态"""
    uid = int(user_id)
    mkt = str(market or "").strip()
    en = 1 if enabled else 0
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO subscriptions (user_id, market, enabled, created_ts, updated_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, market) DO UPDATE SET enabled=?, updated_ts=?
                """,
                (uid, mkt, en, now, now, en, now),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def is_subscription_enabled(market: str, user_id: int) -> bool:
    """检查用户是否订阅"""
    uid = int(user_id)
    mkt = str(market or "").strip()
    
    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT enabled FROM subscriptions WHERE market=? AND user_id=?",
                (mkt, uid),
            )
            row = cur.fetchone()
            return bool(row and row[0])
    
    return await asyncio.to_thread(_sync)


async def list_enabled_subscribers(market: str) -> list[int]:
    """返回该市场的所有已订阅用户 ID"""
    mkt = str(market or "").strip()
    
    def _sync() -> list[int]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM subscriptions WHERE market=? AND enabled=1",
                (mkt,),
            )
            return [int(r[0]) for r in cur.fetchall()]
    
    return await asyncio.to_thread(_sync)


# ================================
# 任务状态管理
# ================================

async def try_start_job_ex(market: str, trade_date: str, force: bool = False) -> bool:
    """
    尝试开始任务（幂等控制）。
    返回 True 表示可以开始，False 表示任务已在运行或已完成。
    """
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    now = _now_ts()
    
    def _sync() -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            conn.execute("BEGIN IMMEDIATE")
            
            cur.execute(
                "SELECT status FROM jobs WHERE market=? AND trade_date=?",
                (mkt, td),
            )
            row = cur.fetchone()
            
            if row:
                status = str(row[0] or "")
                if status in ("running", "success") and not force:
                    conn.rollback()
                    return False
            
            # 创建或更新任务状态为 running
            cur.execute(
                """
                INSERT INTO jobs (market, trade_date, status, started_ts)
                VALUES (?, ?, 'running', ?)
                ON CONFLICT(market, trade_date) DO UPDATE SET status='running', started_ts=?, error=''
                """,
                (mkt, td, now, now),
            )
            conn.commit()
            return True
    
    return await asyncio.to_thread(_sync)


async def mark_job_succeeded(market: str, trade_date: str) -> None:
    """标记任务成功"""
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE jobs SET status='success', finished_ts=? WHERE market=? AND trade_date=?",
                (now, mkt, td),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def mark_job_failed(market: str, trade_date: str, error: str) -> None:
    """标记任务失败"""
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    err = str(error or "")[:500]
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE jobs SET status='failed', finished_ts=?, error=? WHERE market=? AND trade_date=?",
                (now, err, mkt, td),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def get_job(market: str, trade_date: str) -> dict[str, Any]:
    """获取任务状态"""
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status, started_ts, finished_ts, error FROM jobs WHERE market=? AND trade_date=?",
                (mkt, td),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "trade_date": td,
                "status": row[0],
                "started_ts": row[1],
                "finished_ts": row[2],
                "error": row[3],
            }
    
    return await asyncio.to_thread(_sync)


async def get_latest_job(market: str) -> dict[str, Any]:
    """获取最近一次任务"""
    mkt = str(market or "").strip()
    
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT trade_date, status, started_ts, finished_ts, error 
                FROM jobs WHERE market=? 
                ORDER BY trade_date DESC LIMIT 1
                """,
                (mkt,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "trade_date": row[0],
                "status": row[1],
                "started_ts": row[2],
                "finished_ts": row[3],
                "error": row[4],
            }
    
    return await asyncio.to_thread(_sync)


# ================================
# 行情数据存储
# ================================

async def store_eod_quotes(market: str, trade_date: str, rows: list[dict[str, Any]]) -> None:
    """存储日线行情"""
    import json
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                cur.execute(
                    """
                    INSERT OR REPLACE INTO eod_quotes (market, trade_date, ts_code, data_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (mkt, td, ts_code, json.dumps(r, ensure_ascii=False)),
                )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def store_daily_basic(market: str, trade_date: str, rows: list[dict[str, Any]]) -> None:
    """存储日线基本指标"""
    import json
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                cur.execute(
                    """
                    INSERT OR REPLACE INTO daily_basic (market, trade_date, ts_code, data_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (mkt, td, ts_code, json.dumps(r, ensure_ascii=False)),
                )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def get_daily_basic_row(market: str, trade_date: str, ts_code: str) -> dict[str, Any]:
    """获取单只股票的日线基本指标"""
    import json
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    code = str(ts_code or "").strip()
    
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT data_json FROM daily_basic WHERE market=? AND trade_date=? AND ts_code=?",
                (mkt, td, code),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return {}
            try:
                return json.loads(row[0])
            except Exception:
                return {}
    
    return await asyncio.to_thread(_sync)


# ================================
# 股票信息缓存
# ================================

async def upsert_symbols_basic(market: str, rows: list[dict[str, Any]]) -> None:
    """批量更新股票基本信息"""
    import json
    mkt = str(market or "").strip()
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            for r in rows or []:
                ts_code = str(r.get("ts_code") or "").strip()
                if not ts_code:
                    continue
                name = str(r.get("name") or "").strip()
                industry = str(r.get("industry") or "").strip()
                list_date = str(r.get("list_date") or "").strip()
                cur.execute(
                    """
                    INSERT INTO symbols_basic (market, ts_code, name, industry, list_date, data_json, updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market, ts_code) DO UPDATE SET 
                        name=?, industry=?, list_date=?, data_json=?, updated_ts=?
                    """,
                    (mkt, ts_code, name, industry, list_date, json.dumps(r, ensure_ascii=False), now,
                     name, industry, list_date, json.dumps(r, ensure_ascii=False), now),
                )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def get_symbol_basic(market: str, ts_code: str) -> dict[str, Any]:
    """获取单只股票的基本信息（含公司画像）"""
    import json
    mkt = str(market or "").strip()
    code = str(ts_code or "").strip()
    
    def _sync() -> dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # 基本信息
            cur.execute(
                "SELECT name, industry, list_date, data_json, updated_ts FROM symbols_basic WHERE market=? AND ts_code=?",
                (mkt, code),
            )
            row = cur.fetchone()
            if not row:
                return {}
            
            result = {
                "name": row[0] or "",
                "industry": row[1] or "",
                "list_date": row[2] or "",
            }
            if row[3]:
                try:
                    result.update(json.loads(row[3]))
                except Exception:
                    pass
            
            # 公司画像
            cur.execute(
                "SELECT data_json, updated_ts FROM stock_company WHERE market=? AND ts_code=?",
                (mkt, code),
            )
            comp_row = cur.fetchone()
            if comp_row and comp_row[0]:
                try:
                    result["company"] = json.loads(comp_row[0])
                    result["company_updated_ts"] = comp_row[1] or 0
                except Exception:
                    pass
            
            return result
    
    return await asyncio.to_thread(_sync)


async def upsert_stock_company(market: str, ts_code: str, company: dict[str, Any]) -> None:
    """更新公司画像"""
    import json
    mkt = str(market or "").strip()
    code = str(ts_code or "").strip()
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO stock_company (market, ts_code, data_json, updated_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market, ts_code) DO UPDATE SET data_json=?, updated_ts=?
                """,
                (mkt, code, json.dumps(company, ensure_ascii=False), now,
                 json.dumps(company, ensure_ascii=False), now),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)


# ================================
# 公告缓存
# ================================

async def store_announcements(rows: list[dict[str, Any]]) -> None:
    """批量存储公告"""
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            for r in rows or []:
                h = str(r.get("hash") or "").strip()
                if not h:
                    continue
                cur.execute(
                    """
                    INSERT OR IGNORE INTO announcements 
                    (hash, market, trade_date, ts_code, ann_date, pub_ts, title, url, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        h,
                        r.get("market") or "",
                        r.get("trade_date") or "",
                        r.get("ts_code") or "",
                        r.get("ann_date") or "",
                        r.get("pub_ts") or 0,
                        r.get("title") or "",
                        r.get("url") or "",
                        r.get("source") or "",
                    ),
                )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def list_announcements(
    market: str, trade_date: str, ts_code: str, limit: int = 10
) -> list[dict[str, Any]]:
    """获取某只股票的公告列表"""
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    code = str(ts_code or "").strip()
    lim = int(limit) if limit else 10
    
    def _sync() -> list[dict[str, Any]]:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ann_date, pub_ts, title, url FROM announcements
                WHERE market=? AND trade_date=? AND ts_code=?
                ORDER BY pub_ts DESC
                LIMIT ?
                """,
                (mkt, td, code, lim),
            )
            return [
                {"ann_date": r[0], "pub_ts": r[1], "title": r[2], "url": r[3]}
                for r in cur.fetchall()
            ]
    
    return await asyncio.to_thread(_sync)


# ================================
# 分析结果存储
# ================================

async def upsert_analysis_result(
    market: str,
    trade_date: str,
    ts_code: str,
    *,
    features: Optional[dict] = None,
    llm_json: Optional[dict] = None,
    llm_model: str = "",
    prompt_version: str = "",
) -> None:
    """存储分析结果"""
    import json
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    code = str(ts_code or "").strip()
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO analysis_results 
                (market, trade_date, ts_code, features_json, llm_json, llm_model, prompt_version, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, trade_date, ts_code) DO UPDATE SET
                    features_json=?, llm_json=?, llm_model=?, prompt_version=?, created_ts=?
                """,
                (
                    mkt, td, code,
                    json.dumps(features or {}, ensure_ascii=False),
                    json.dumps(llm_json or {}, ensure_ascii=False),
                    llm_model, prompt_version, now,
                    json.dumps(features or {}, ensure_ascii=False),
                    json.dumps(llm_json or {}, ensure_ascii=False),
                    llm_model, prompt_version, now,
                ),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)


async def upsert_daily_report(
    market: str, trade_date: str, report_text: str, report_json: dict[str, Any]
) -> None:
    """存储日报"""
    import json
    mkt = str(market or "").strip()
    td = str(trade_date or "").strip()
    now = _now_ts()
    
    def _sync():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO daily_reports (market, trade_date, report_text, report_json, created_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(market, trade_date) DO UPDATE SET
                    report_text=?, report_json=?, created_ts=?
                """,
                (
                    mkt, td, report_text,
                    json.dumps(report_json or {}, ensure_ascii=False), now,
                    report_text,
                    json.dumps(report_json or {}, ensure_ascii=False), now,
                ),
            )
            conn.commit()
    
    await asyncio.to_thread(_sync)
