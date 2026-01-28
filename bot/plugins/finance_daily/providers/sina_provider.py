"""新浪财经（Sina）数据源适配器（用于替代 Eastmoney push2 行情快照）。

背景：
- 部分网络环境下 `https://push2.eastmoney.com/...` 会频繁断连（RemoteProtocolError）；
- 但东财 F10（公司概况）与公告接口通常仍可用；
- 因此这里用 Sina 的“市场中心榜单”接口拉涨跌幅候选池，
  再复用东财接口做画像/公告证据链。

接口：
- 行情快照：`Market_Center.getHQNodeData`（支持 sort/asc，直接拿涨幅端/跌幅端）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/stock/", "Connection": "close"}


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _to_ts_code(symbol: str) -> str:
    # sina: sh688110 / sz000001 -> 688110.SH / 000001.SZ
    s = str(symbol or "").strip().lower()
    if len(s) < 3:
        return ""
    if s.startswith("sh"):
        code = s[2:]
        return f"{code}.SH" if code else ""
    if s.startswith("sz"):
        code = s[2:]
        return f"{code}.SZ" if code else ""
    return ""


async def _get_text(url: str, params: dict[str, Any], *, timeout: float = 20.0, retries: int = 3) -> str:
    last: Exception | None = None
    for i in range(max(0, int(retries)) + 1):
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=timeout, trust_env=False) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.text or ""
        except Exception as e:
            last = e
            if i >= retries:
                break
            await asyncio.sleep(0.6 * (2**i))
    raise RuntimeError(f"sina_request_failed: {last}") from last


class SinaProvider:
    name = "sina"

    def __init__(self, *, proxy: str | None = None):
        # 预留：一般不需要代理；如需可按 httpx 支持添加
        self.proxy = proxy

    async def last_open_trade_date(self) -> str:
        # 复用东财“上证指数日K”接口判断最近交易日（该域名在容器内通常可访问）
        from .eastmoney_provider import EastmoneyProvider

        return await EastmoneyProvider(proxy=None).last_open_trade_date()

    async def fetch_daily(self, trade_date: str) -> list[dict[str, Any]]:
        """拉取“涨幅端+跌幅端”候选池，字段对齐 tushare daily。"""
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

        # 取涨幅端/跌幅端各 N 页（每页 80），足够覆盖 TopN
        page_size = 80
        pages_each_side = 4
        node = "hs_a"

        out_map: dict[str, dict[str, Any]] = {}

        async def _fetch_side(*, asc: int) -> None:
            for page in range(1, pages_each_side + 1):
                params = {
                    "page": page,
                    "num": page_size,
                    "sort": "changepercent",
                    "asc": int(asc),
                    "node": node,
                    "symbol": "",
                    "_s_r_a": "init",
                }
                text = await _get_text(url, params)
                if not text.strip():
                    break
                try:
                    arr = httpx.Response(200, content=text.encode("utf-8")).json()
                except Exception:
                    break
                if not isinstance(arr, list) or not arr:
                    break

                for it in arr:
                    if not isinstance(it, dict):
                        continue
                    ts_code = _to_ts_code(it.get("symbol"))
                    if not ts_code:
                        continue
                    out_map[ts_code] = {
                        "ts_code": ts_code,
                        "trade_date": trade_date,
                        "open": _safe_float(it.get("open")),
                        "high": _safe_float(it.get("high")),
                        "low": _safe_float(it.get("low")),
                        "close": _safe_float(it.get("trade")),
                        "vol": _safe_float(it.get("volume")),
                        "amount": _safe_float(it.get("amount")),  # 元
                        "pct_chg": _safe_float(it.get("changepercent")),
                        "name": str(it.get("name") or "").strip(),
                        # daily_basic-ish
                        "turnover_rate": _safe_float(it.get("turnoverratio")),
                        "volume_ratio": 0.0,
                        # sina mktcap/nmc 单位与 tushare total_mv/circ_mv 一致（万元）
                        "total_mv": _safe_float(it.get("mktcap")),
                        "circ_mv": _safe_float(it.get("nmc")),
                        "pe": _safe_float(it.get("per")),
                        "pb": _safe_float(it.get("pb")),
                    }

        # 并行抓两侧，减少总耗时
        await asyncio.gather(_fetch_side(asc=0), _fetch_side(asc=1))
        return list(out_map.values())

    async def fetch_stock_basic(self) -> list[dict[str, Any]]:
        return []

    async def fetch_stock_company(self, ts_code: str) -> dict[str, Any]:
        # 画像/行业：复用东财 F10（该域名在容器内通常可访问）
        from .eastmoney_provider import EastmoneyProvider

        return await EastmoneyProvider(proxy=None).fetch_stock_company(ts_code)

    async def fetch_anns_by_symbol(self, ts_code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        from .eastmoney_provider import EastmoneyProvider

        return await EastmoneyProvider(proxy=None).fetch_anns_by_symbol(ts_code, start_date, end_date)
