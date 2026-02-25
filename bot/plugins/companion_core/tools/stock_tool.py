"""股票查询工具。

封装现有 stock.py 的行情查询能力。
"""

from __future__ import annotations

import json

from ..tool_registry import register_tool, ToolParam
from ..stock import parse_stock_id, build_stock_context


@register_tool(
    name="stock_query",
    description=(
        "查询A股股票的实时行情、涨跌幅、成交额和最新公告。"
        "需要提供6位数字的股票代码（如 688110、000001）。"
    ),
    parameters=[
        ToolParam(
            name="stock_code",
            type="string",
            description="6位数字的A股代码，例如'688110'、'000001'、'600519'",
        ),
    ],
)
async def stock_query(stock_code: str) -> str:
    """查询股票行情并返回结构化数据。"""
    # 用 parse_stock_id 兼容多种输入
    sid = parse_stock_id(f"查股 {stock_code}")
    if sid is None:
        return f"无法识别股票代码 '{stock_code}'，请提供有效的6位A股代码。"

    ctx = await build_stock_context(sid)
    quote = ctx.get("quote") or {}
    profile = ctx.get("profile") or {}
    anns = ctx.get("announcements") or []

    if isinstance(quote, dict) and quote.get("error"):
        return f"行情接口暂时不可用：{quote.get('error')}"

    name = str((quote.get("name") or profile.get("name") or stock_code)).strip()
    try:
        pct = float(quote.get("pct_chg") or 0.0)
    except Exception:
        pct = 0.0

    result = {
        "股票名称": name,
        "股票代码": stock_code,
        "涨跌幅": f"{pct:+.2f}%",
        "当前价格": quote.get("price", "N/A"),
        "成交额(亿)": quote.get("amount_yi", "N/A"),
        "行业": str(profile.get("industry") or "N/A").strip(),
        "主营业务": str(profile.get("main_business") or "N/A").strip()[:100],
        "最新公告": [str(a.get("title", "")).strip() for a in (anns[:3] if isinstance(anns, list) else []) if a.get("title")],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
