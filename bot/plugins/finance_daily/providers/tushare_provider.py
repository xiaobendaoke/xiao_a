"""Tushare Pro 数据源适配器（async 封装）。

说明：
- tushare 官方 SDK 主要是同步接口，本适配器用 `asyncio.to_thread()` 包一层；
- 一期只用到：交易日历、日线、每日指标、公告、公司画像、股票基础信息。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import tushare as ts


@dataclass(frozen=True)
class TushareConfig:
    token: str


_pro = None


def _get_pro(token: str):
    global _pro
    if _pro is not None:
        return _pro
    ts.set_token(token)
    _pro = ts.pro_api(token)
    return _pro


def _to_records(df) -> list[dict[str, Any]]:
    if df is None:
        return []
    try:
        return df.to_dict(orient="records")
    except Exception:
        return []


class TushareProvider:
    def __init__(self, token: str):
        token = (token or "").strip().split()[0]
        if not token:
            raise RuntimeError("缺少 TUSHARE_TOKEN 环境变量")
        self.token = token

    async def last_open_trade_date(self, *, end: Optional[date] = None, lookback_days: int = 40) -> str:
        """返回最近一个开市日 YYYYMMDD（按 SSE 交易日历）。"""
        end = end or datetime.now().date()
        start = end - timedelta(days=int(lookback_days))
        pro = _get_pro(self.token)

        # 1) 优先：trade_cal（更省调用），但部分账号/积分可能无权限
        def _sync_trade_cal() -> str:
            df = pro.trade_cal(
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="cal_date,is_open",
            )
            if df is None or df.empty:
                return ""
            df = df[df["is_open"] == 1]
            if df.empty:
                return ""
            return str(df["cal_date"].iloc[-1])

        try:
            cal = await asyncio.to_thread(_sync_trade_cal)
            if cal:
                return cal
        except Exception:
            # 降级：用 daily 反查最近一个有数据的交易日（通常周末/节假日只多 1~3 次调用）
            pass

        # 2) 降级：从 end 往前扫，找到 daily 不为空的日期
        def _sync_daily_scan() -> str:
            for i in range(max(1, int(lookback_days))):
                d = end - timedelta(days=i)
                td = d.strftime("%Y%m%d")
                df = pro.daily(trade_date=td, fields="ts_code")
                if df is not None and not df.empty:
                    return td
            return end.strftime("%Y%m%d")

        return await asyncio.to_thread(_sync_daily_scan)

    async def fetch_daily(self, trade_date: str) -> list[dict[str, Any]]:
        pro = _get_pro(self.token)

        def _sync():
            df = pro.daily(
                trade_date=str(trade_date),
                fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
            )
            return _to_records(df)

        return await asyncio.to_thread(_sync)

    async def fetch_daily_basic(self, trade_date: str) -> list[dict[str, Any]]:
        pro = _get_pro(self.token)

        def _sync():
            df = pro.daily_basic(
                trade_date=str(trade_date),
                fields="ts_code,trade_date,turnover_rate,volume_ratio,total_mv,circ_mv,pe,pb",
            )
            return _to_records(df)

        return await asyncio.to_thread(_sync)

    async def fetch_stock_basic(self) -> list[dict[str, Any]]:
        pro = _get_pro(self.token)

        def _sync():
            df = pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,industry,list_date",
            )
            return _to_records(df)

        return await asyncio.to_thread(_sync)

    async def fetch_stock_company(self, ts_code: str) -> dict[str, Any]:
        pro = _get_pro(self.token)

        def _sync() -> dict[str, Any]:
            df = pro.stock_company(
                ts_code=str(ts_code),
                fields="ts_code,introduction,main_business,business_scope,website,employees,setup_date,province,city",
            )
            recs = _to_records(df)
            return recs[0] if recs else {}

        return await asyncio.to_thread(_sync)

    async def fetch_anns_by_date(self, ann_date: str) -> list[dict[str, Any]]:
        pro = _get_pro(self.token)

        def _sync():
            df = pro.anns_d(
                ann_date=str(ann_date),
                fields="ts_code,ann_date,ann_time,title,url",
            )
            return _to_records(df)

        return await asyncio.to_thread(_sync)

    async def fetch_anns_by_symbol(self, ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """按股票拉公告（用于小范围 lookback，TopN 很小的时候更省事）。"""
        pro = _get_pro(self.token)

        def _sync():
            df = pro.anns_d(
                ts_code=str(ts_code),
                start_date=str(start_date),
                end_date=str(end_date),
                fields="ts_code,ann_date,ann_time,title,url",
            )
            return _to_records(df)

        return await asyncio.to_thread(_sync)
