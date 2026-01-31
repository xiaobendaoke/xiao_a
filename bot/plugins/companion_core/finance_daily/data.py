"""股票小白日报数据采集模块。

负责采集：
1. 涨跌榜 Top N（涨幅/跌幅）
2. 每只股票的主营业务构成
3. 换手率、市盈率等指标
4. 当日新闻/公告
5. 龙虎榜数据（可选）
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from nonebot import logger


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


@dataclass
class StockBasic:
    """股票基本信息"""
    code: str              # 6位代码
    name: str              # 股票名称
    market: str            # SH/SZ
    price: float = 0.0     # 现价
    pct_chg: float = 0.0   # 涨跌幅 %
    turnover_rate: float = 0.0  # 换手率 %
    pe_ratio: float = 0.0  # 市盈率
    amount_yi: str = ""    # 成交额（亿）
    

@dataclass
class StockDetail:
    """股票详细分析数据"""
    basic: StockBasic
    main_business: str = ""          # 主营业务描述
    main_business_breakdown: list[dict] = field(default_factory=list)  # 主营构成
    announcements: list[dict] = field(default_factory=list)  # 当日公告
    news: list[dict] = field(default_factory=list)  # 相关新闻
    dragon_tiger: dict = field(default_factory=dict)  # 龙虎榜（可选）


async def _get_json(url: str, params: dict | None = None) -> dict | list | None:
    """通用 HTTP GET 获取 JSON"""
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS, trust_env=False) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug(f"[finance_daily] fetch failed {url}: {e}")
        return None


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v not in (None, "", "-") else 0.0
    except:
        return 0.0


def _fmt_yi(amount: float) -> str:
    """格式化成交额为亿"""
    if amount <= 0:
        return ""
    yi = amount / 1e8
    if yi >= 100:
        return f"{yi:.0f}亿"
    if yi >= 10:
        return f"{yi:.1f}亿"
    return f"{yi:.2f}亿"


async def fetch_top_gainers(limit: int = 5) -> list[StockBasic]:
    """
    获取 A 股涨幅榜 Top N。
    使用 Eastmoney 涨跌幅排行接口。
    """
    # Eastmoney 涨幅排行 API
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": limit,
        "po": 1,  # 降序
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",  # 按涨跌幅排序
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # A股
        "fields": "f2,f3,f4,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18",
    }
    
    data = await _get_json(url, params)
    if not data or "data" not in data:
        return []
    
    items = (data.get("data") or {}).get("diff") or []
    results = []
    
    for item in items[:limit]:
        code = str(item.get("f12", "")).strip()
        name = str(item.get("f14", "")).strip()
        if not code or not name:
            continue
        
        # 判断市场
        if code.startswith(("60", "68")):
            market = "SH"
        elif code.startswith(("00", "30")):
            market = "SZ"
        else:
            market = "SZ"
        
        results.append(StockBasic(
            code=code,
            name=name,
            market=market,
            price=_safe_float(item.get("f2")),
            pct_chg=_safe_float(item.get("f3")),
            turnover_rate=_safe_float(item.get("f8")),
            pe_ratio=_safe_float(item.get("f9")),
            amount_yi=_fmt_yi(_safe_float(item.get("f6"))),
        ))
    
    return results


async def fetch_top_losers(limit: int = 5) -> list[StockBasic]:
    """获取 A 股跌幅榜 Top N"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": limit,
        "po": 0,  # 升序（跌幅）
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f4,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18",
    }
    
    data = await _get_json(url, params)
    if not data or "data" not in data:
        return []
    
    items = (data.get("data") or {}).get("diff") or []
    results = []
    
    for item in items[:limit]:
        code = str(item.get("f12", "")).strip()
        name = str(item.get("f14", "")).strip()
        if not code or not name:
            continue
        
        if code.startswith(("60", "68")):
            market = "SH"
        elif code.startswith(("00", "30")):
            market = "SZ"
        else:
            market = "SZ"
        
        results.append(StockBasic(
            code=code,
            name=name,
            market=market,
            price=_safe_float(item.get("f2")),
            pct_chg=_safe_float(item.get("f3")),
            turnover_rate=_safe_float(item.get("f8")),
            pe_ratio=_safe_float(item.get("f9")),
            amount_yi=_fmt_yi(_safe_float(item.get("f6"))),
        ))
    
    return results


async def fetch_main_business(code: str, market: str) -> tuple[str, list[dict]]:
    """
    获取主营业务描述和构成。
    返回：(主营业务描述, [{"name": "xxx", "ratio": 30.5}, ...])
    """
    em_code = f"{market}{code}"
    
    # 1. 公司概况
    url1 = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax"
    data1 = await _get_json(url1, params={"code": em_code})
    
    main_biz = ""
    if data1:
        jbzl = data1.get("jbzl") or {}
        main_biz = str(jbzl.get("zyyw") or jbzl.get("gsjj") or "").strip()
        # 截断过长的描述
        if len(main_biz) > 200:
            main_biz = main_biz[:197] + "..."
    
    # 2. 主营构成
    url2 = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/BusinessAnalysisAjax"
    data2 = await _get_json(url2, params={"code": em_code})
    
    breakdown = []
    if data2:
        # 尝试解析主营构成
        zygc = data2.get("zygcfx") or []
        if isinstance(zygc, list):
            for item in zygc[:5]:
                name = str(item.get("zygc") or item.get("MAINOP_TYPE") or "").strip()
                ratio = _safe_float(item.get("zygczb") or item.get("MAIN_BUSINESS_RATIO") or 0)
                if name:
                    breakdown.append({"name": name, "ratio": ratio})
    
    return main_biz, breakdown


async def fetch_announcements(code: str, limit: int = 5) -> list[dict]:
    """获取最近公告"""
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": "-1",
        "page_size": str(limit),
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, trust_env=False) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.debug(f"[finance_daily] announcements failed {code}: {e}")
        return []
    
    items = (data.get("data") or {}).get("list") or []
    results = []
    for it in items[:limit]:
        title = str(it.get("title") or "").strip()
        if title:
            results.append({
                "title": title,
                "time": str(it.get("display_time") or "")[:10],
            })
    return results


async def fetch_stock_news(code: str, limit: int = 3) -> list[dict]:
    """获取相关新闻"""
    # 使用东财新闻接口
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": "jQuery",
        "param": f'{{"uid":"","keyword":"{code}","type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{{"cmsArticleWebOld":{{"searchScope":"default","sort":"default","pageIndex":1,"pageSize":{limit},"preTag":"<em>","postTag":"</em>"}}}}}}',
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS, trust_env=False) as client:
            r = await client.get(url, params=params)
            text = r.text or ""
    except Exception as e:
        logger.debug(f"[finance_daily] news failed {code}: {e}")
        return []
    
    # 解析 JSONP
    match = re.search(r"jQuery\((.*)\)", text, re.S)
    if not match:
        return []
    
    try:
        import json
        data = json.loads(match.group(1))
        items = data.get("result", {}).get("cmsArticleWebOld", []) or []
    except:
        return []
    
    results = []
    for it in items[:limit]:
        title = str(it.get("title") or "").strip()
        title = re.sub(r"<[^>]+>", "", title)  # 去除 HTML 标签
        if title:
            results.append({
                "title": title,
                "time": str(it.get("date") or "")[:10],
            })
    return results


async def fetch_stock_detail(basic: StockBasic) -> StockDetail:
    """获取单只股票的完整详情"""
    # 并发获取所有数据
    main_biz_task = fetch_main_business(basic.code, basic.market)
    ann_task = fetch_announcements(basic.code)
    news_task = fetch_stock_news(basic.code)
    
    (main_biz, breakdown), anns, news = await asyncio.gather(
        main_biz_task, ann_task, news_task,
        return_exceptions=True,
    )
    
    # 处理异常
    if isinstance((main_biz, breakdown), Exception):
        main_biz, breakdown = "", []
    if isinstance(anns, Exception):
        anns = []
    if isinstance(news, Exception):
        news = []
    
    return StockDetail(
        basic=basic,
        main_business=main_biz if isinstance(main_biz, str) else "",
        main_business_breakdown=breakdown if isinstance(breakdown, list) else [],
        announcements=anns if isinstance(anns, list) else [],
        news=news if isinstance(news, list) else [],
    )


async def fetch_daily_report_data(top_n: int = 5) -> dict:
    """
    获取每日报告所需的全部数据。
    
    Returns:
        {
            "date": "2026-01-31",
            "gainers": [StockDetail, ...],
            "losers": [StockDetail, ...],
        }
    """
    # 获取涨跌榜
    gainers, losers = await asyncio.gather(
        fetch_top_gainers(top_n),
        fetch_top_losers(top_n),
    )
    
    # 获取每只股票的详情
    gainer_details = await asyncio.gather(*[fetch_stock_detail(s) for s in gainers])
    loser_details = await asyncio.gather(*[fetch_stock_detail(s) for s in losers])
    
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "gainers": list(gainer_details),
        "losers": list(loser_details),
    }
