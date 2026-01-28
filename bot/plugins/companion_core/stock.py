"""股票查询工具（companion_core）。

目的：
- 支持私聊命令 `查股 <代码>` / `股票 <代码>`；
- 行情：使用 Sina `hq.sinajs.cn`（容器内可访问，且对单票查询稳定）；
- 画像/公告：使用 Eastmoney 的 F10/公告接口（同 finance_daily 一致）。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx


_SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
_EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/", "Connection": "close"}


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _short(s: Any, n: int) -> str:
    t = str(s or "").strip().replace("\u3000", " ")
    t = " ".join(t.split())
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _fmt_yi(amount_yuan: float) -> str:
    try:
        v = float(amount_yuan or 0.0)
    except Exception:
        return ""
    if v <= 0:
        return ""
    yi = v / 1e8
    if yi >= 100:
        return f"{yi:.0f}亿"
    if yi >= 10:
        return f"{yi:.1f}亿"
    return f"{yi:.2f}亿"


@dataclass(frozen=True)
class StockId:
    code: str  # 6 digits
    market: str  # SH/SZ/BJ

    @property
    def ts_code(self) -> str:
        return f"{self.code}.{self.market}"

    @property
    def sina_symbol(self) -> str:
        m = self.market.lower()
        return f"{m}{self.code}"

    @property
    def em_code(self) -> str:
        return f"{self.market}{self.code}"


def parse_stock_id(text: str) -> StockId | None:
    t = (text or "").strip()
    if not t:
        return None

    # 688110.SH / 000001.SZ
    m = re.search(r"\b(\d{6})\.(SH|SZ|BJ)\b", t, re.I)
    if m:
        return StockId(code=m.group(1), market=m.group(2).upper())

    # sh688110 / sz000001 / bj920471
    m = re.search(r"\b(sh|sz|bj)(\d{6})\b", t, re.I)
    if m:
        return StockId(code=m.group(2), market=m.group(1).upper())

    # bare 6 digits: guess market
    m = re.search(r"\b(\d{6})\b", t)
    if not m:
        return None
    code = m.group(1)
    if code.startswith(("60", "68", "90")):
        market = "SH"
    elif code.startswith(("00", "30")):
        market = "SZ"
    elif code.startswith(("83", "87", "43", "82", "92")):
        market = "BJ"
    else:
        # 兜底：按 SZ 处理（不会影响“查不到就提示”）
        market = "SZ"
    return StockId(code=code, market=market)


async def _get_text(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> str:
    async with httpx.AsyncClient(timeout=20.0, headers=headers or {}, trust_env=False) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.text or ""


async def fetch_sina_quote(stock: StockId) -> dict[str, Any]:
    """单票行情：Sina hq.sinajs.cn。"""
    url = f"https://hq.sinajs.cn/list={stock.sina_symbol}"
    raw = await _get_text(url, headers=_SINA_HEADERS)
    # var hq_str_sh688110="name,open,preclose,price,high,low,...,date,time,...";
    if "hq_str_" not in raw or "=\"" not in raw:
        return {"error": "empty_quote"}
    try:
        payload = raw.split("=\"", 1)[1].rsplit("\"", 1)[0]
    except Exception:
        return {"error": "bad_quote"}
    parts = payload.split(",")
    if len(parts) < 10:
        return {"error": "bad_quote"}
    name = parts[0].strip()
    open_p = _safe_float(parts[1])
    preclose = _safe_float(parts[2])
    price = _safe_float(parts[3])
    high = _safe_float(parts[4])
    low = _safe_float(parts[5])
    vol = _safe_float(parts[8])
    amount = _safe_float(parts[9])
    pct = ((price - preclose) / preclose * 100.0) if preclose > 0 else 0.0
    # 末尾通常有日期/时间
    d = parts[30].strip() if len(parts) > 30 else ""
    tm = parts[31].strip() if len(parts) > 31 else ""
    return {
        "name": name,
        "open": open_p,
        "preclose": preclose,
        "price": price,
        "high": high,
        "low": low,
        "vol": vol,
        "amount": amount,
        "amount_yi": _fmt_yi(amount),
        "pct_chg": pct,
        "date": d,
        "time": tm,
    }


async def fetch_eastmoney_company_profile(stock: StockId) -> dict[str, Any]:
    """公司概况：Eastmoney F10 CompanySurveyAjax。"""
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax"
    txt = await _get_text(url, params={"code": stock.em_code}, headers=_EM_HEADERS)
    try:
        data = httpx.Response(200, content=txt.encode("utf-8")).json()
    except Exception:
        return {}
    jbzl = (data or {}).get("jbzl") or {}
    return {
        "name": str(jbzl.get("gsmc") or jbzl.get("agjc") or "").strip(),
        "industry": str(jbzl.get("sshy") or "").strip(),
        "intro": _short(jbzl.get("gsjj") or "", 140),
        "main_business": _short(jbzl.get("zyyw") or "", 140),
    }


def _parse_trade_date_yyyymmdd(s: str) -> str:
    t = (s or "").strip()
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        return t[:4] + t[5:7] + t[8:10]
    return ""


async def fetch_eastmoney_announcements(stock: StockId, *, lookback_days: int = 7, limit: int = 6) -> list[dict[str, Any]]:
    """公告标题：Eastmoney 公告列表（近 lookback_days）。"""
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/", "Connection": "close"}
    page_index = 1
    page_size = 30
    out: list[dict[str, Any]] = []
    end = datetime.now().date()
    start = end - timedelta(days=max(1, int(lookback_days)))
    start_s = start.strftime("%Y%m%d")

    async with httpx.AsyncClient(timeout=20.0, headers=headers, trust_env=False) as client:
        while page_index <= 3 and len(out) < limit:
            params = {
                "sr": "-1",
                "page_size": str(page_size),
                "page_index": str(page_index),
                "ann_type": "A",
                "client_source": "web",
                "stock_list": stock.code,
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
                display_time = str(it.get("display_time") or "").strip()
                if not title or not art:
                    continue
                ann_date = _parse_trade_date_yyyymmdd(display_time[:10]) if display_time else ""
                if ann_date and ann_date < start_s:
                    return out
                detail_url = f"https://data.eastmoney.com/notices/detail/{stock.code}/{art}.html"
                out.append({"title": title, "url": detail_url, "ann_date": ann_date})
                if len(out) >= limit:
                    break
            page_index += 1
            await asyncio.sleep(0.15)
    return out


async def build_stock_context(stock: StockId) -> dict[str, Any]:
    """汇总股票查询所需的结构化上下文。"""
    quote, profile, anns = await asyncio.gather(
        fetch_sina_quote(stock),
        fetch_eastmoney_company_profile(stock),
        fetch_eastmoney_announcements(stock),
    )
    return {"stock": {"ts_code": stock.ts_code, "code": stock.code, "market": stock.market}, "quote": quote, "profile": profile, "announcements": anns}

