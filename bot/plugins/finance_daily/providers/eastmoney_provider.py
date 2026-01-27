"""东方财富（Eastmoney）数据源适配器（finance_daily 的 Tushare 兜底方案）。

特点：
- 不依赖 Tushare Pro 权限；
- 用公开接口拉“全市场快照”（用于 TopN 榜单 + 部分 daily_basic 指标）；
- 公告标题：使用 Eastmoney 公告列表接口（生成可打开的详情页 URL）。

注意：
- 公开接口可能随时间变动；本适配器尽量做“字段缺失可用、失败可降级”。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import httpx


_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"}


def _market_suffix(market_code: Any) -> str:
    try:
        mc = int(market_code)
    except Exception:
        mc = -1
    # f13: 0=SZ, 1=SH, 2=BJ（常见）
    if mc == 0:
        return "SZ"
    if mc == 1:
        return "SH"
    if mc == 2:
        return "BJ"
    return "SZ"


def _to_ts_code(code: str, market_code: Any) -> str:
    c = str(code or "").strip()
    if not c:
        return ""
    return f"{c}.{_market_suffix(market_code)}"


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _parse_trade_date_yyyymmdd(s: str) -> str:
    # 输入：YYYY-MM-DD
    t = (s or "").strip()
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        return t[:4] + t[5:7] + t[8:10]
    return ""


class EastmoneyProvider:
    name = "eastmoney"

    def __init__(self, *, proxy: str | None = None):
        self.proxy = proxy

    async def last_open_trade_date(self) -> str:
        """用上证指数日K拿最近交易日（避免周末/节假日误判）。"""
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": "1.000001",  # 上证指数
            "klt": "101",  # 日K
            "fqt": "1",
            "lmt": "5",
            "end": "20500101",
            "iscca": "1",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }

        async with httpx.AsyncClient(headers=_HEADERS, proxy=self.proxy, timeout=15.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json() or {}
            klines = (data.get("data") or {}).get("klines") or []
            if not klines:
                # 兜底：今天日期（上海时区由容器 TZ 决定）
                return datetime.now().strftime("%Y%m%d")
            last = str(klines[-1]).split(",")[0]
            td = _parse_trade_date_yyyymmdd(last)
            return td or datetime.now().strftime("%Y%m%d")

    async def fetch_daily(self, trade_date: str) -> list[dict[str, Any]]:
        """拉取 A股 全市场快照（字段对齐 tushare daily）。"""
        # fs: 深A/创业板/沪A/科创板（北京暂不加，避免规则差异）
        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        fields = ",".join(
            [
                "f12",  # code
                "f13",  # market
                "f14",  # name
                "f2",  # close
                "f3",  # pct
                "f5",  # vol
                "f6",  # amount（元）
                "f15",  # high
                "f16",  # low
                "f17",  # open
                "f18",  # preclose
                # daily_basic-ish
                "f8",  # turnover_rate
                "f10",  # volume_ratio（常见映射；缺失则为0）
                "f20",  # total_mv（元）
                "f21",  # circ_mv（元）
                "f9",  # pe（常见映射；缺失则为0）
                "f23",  # pb
            ]
        )

        pz = 5000
        pn = 1
        out: list[dict[str, Any]] = []

        async with httpx.AsyncClient(headers=_HEADERS, proxy=self.proxy, timeout=20.0) as client:
            while True:
                params = {
                    "pn": pn,
                    "pz": pz,
                    "po": 1,
                    "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f3",
                    "fs": fs,
                    "fields": fields,
                }
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json() or {}
                diff = (data.get("data") or {}).get("diff") or []
                if not diff:
                    break
                for it in diff:
                    code = str(it.get("f12") or "").strip()
                    mc = it.get("f13")
                    ts_code = _to_ts_code(code, mc)
                    if not ts_code:
                        continue
                    out.append(
                        {
                            "ts_code": ts_code,
                            "trade_date": trade_date,
                            "open": _safe_float(it.get("f17")),
                            "high": _safe_float(it.get("f15")),
                            "low": _safe_float(it.get("f16")),
                            "close": _safe_float(it.get("f2")),
                            "vol": _safe_float(it.get("f5")),
                            "amount": _safe_float(it.get("f6")),  # 已是元
                            "pct_chg": _safe_float(it.get("f3")),
                            "name": str(it.get("f14") or "").strip(),
                            # 附带 daily_basic 字段（方便管道直接入库）
                            "turnover_rate": _safe_float(it.get("f8")),
                            "volume_ratio": _safe_float(it.get("f10")),
                            "total_mv": _safe_float(it.get("f20")) / 10000.0,  # 转为“万元”对齐 tushare
                            "circ_mv": _safe_float(it.get("f21")) / 10000.0,
                            "pe": _safe_float(it.get("f9")),
                            "pb": _safe_float(it.get("f23")),
                        }
                    )
                if len(diff) < pz:
                    break
                pn += 1
        return out

    async def fetch_stock_basic(self) -> list[dict[str, Any]]:
        """与 tushare 对齐的占位接口：eastmoney 方案不依赖该表。"""
        return []

    async def fetch_stock_company(self, ts_code: str) -> dict[str, Any]:
        """公司画像：取东财 F10 公司概况（用于一句话画像 + 行业）。"""
        # ts_code: 000001.SZ -> code: SZ000001
        code, suffix = (ts_code or "").split(".", 1) if "." in (ts_code or "") else (ts_code or "", "SZ")
        em_code = f"{suffix.upper()}{code}"
        url = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax"
        async with httpx.AsyncClient(headers=_HEADERS, proxy=self.proxy, timeout=15.0) as client:
            r = await client.get(url, params={"code": em_code})
            r.raise_for_status()
            data = r.json() or {}
            jbzl = data.get("jbzl") or {}
            return {
                "ts_code": ts_code,
                "introduction": str(jbzl.get("gsjj") or "").strip(),
                "main_business": str(jbzl.get("zyyw") or "").strip(),
                "business_scope": str(jbzl.get("jyfw") or "").strip(),
                "website": str(jbzl.get("gswz") or "").strip(),
                "industry": str(jbzl.get("sshy") or "").strip(),
            }

    async def fetch_anns_by_symbol(self, ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """公告标题（按股票过滤）。"""
        # 接口按 stock_list=股票代码（不带交易所后缀）
        code = (ts_code or "").split(".", 1)[0]
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        headers = {"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"}

        page_index = 1
        page_size = 50
        out: list[dict[str, Any]] = []
        start = start_date or ""
        end = end_date or ""

        async with httpx.AsyncClient(headers=headers, proxy=self.proxy, timeout=20.0) as client:
            while page_index <= 4:  # TopN 用不到太深，控制成本
                params = {
                    "sr": "-1",
                    "page_size": str(page_size),
                    "page_index": str(page_index),
                    "ann_type": "A",
                    "client_source": "web",
                    "stock_list": str(code),
                    "f_node": "0",
                    "s_node": "0",
                }
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json() or {}
                items = (data.get("data") or {}).get("list") or []
                if not items:
                    break

                for it in items:
                    title = str(it.get("title") or it.get("title_ch") or "").strip()
                    art = str(it.get("art_code") or "").strip()
                    if not title or not art:
                        continue
                    display_time = str(it.get("display_time") or "").strip()
                    # display_time 形如 2026-01-27 16:34:30:708
                    pub_ts = 0
                    ann_date = ""
                    ann_time = ""
                    if display_time:
                        try:
                            ann_date = _parse_trade_date_yyyymmdd(display_time[:10])
                            ann_time = display_time[11:19] if len(display_time) >= 19 else ""
                            dt = datetime.strptime(display_time[:19], "%Y-%m-%d %H:%M:%S")
                            pub_ts = int(dt.timestamp())
                        except Exception:
                            pub_ts = 0

                    if ann_date:
                        if start and ann_date < start:
                            # 已经比 start 更早，后面会更旧：可以提前结束
                            return out
                        if end and ann_date > end:
                            # 偶发脏数据：跳过
                            continue

                    detail_url = f"https://data.eastmoney.com/notices/detail/{code}/{art}.html"
                    out.append(
                        {
                            "ts_code": ts_code,
                            "ann_date": ann_date or end_date,
                            "ann_time": ann_time,
                            "title": title,
                            "url": detail_url,
                            "pub_ts": pub_ts,
                        }
                    )

                if len(items) < page_size:
                    break
                page_index += 1
        return out
