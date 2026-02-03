"""股票小白日报 LLM 分析器 (v2)。

负责调用 LLM 生成符合"女友八卦"风格的市场分析。
核心逻辑：一次性给 LLM 多个股票，让它自己挑重点讲。
"""

from __future__ import annotations

import json
from typing import Any

from nonebot import logger

from ..llm_client import get_client, load_llm_settings
from .data import StockDetail
from .prompts import STOCK_DAILY_REPORT_V3_SYSTEM, MARKET_OVERVIEW_V3_SYSTEM


def _build_stock_context(detail: StockDetail) -> str:
    """构建单只股票上下文供 LLM 分析"""
    basic = detail.basic
    
    lines = [
        f"【股票】{basic.name}({basic.code})",
        f"涨跌幅：{basic.pct_chg:+.2f}%",
        f"换手率：{basic.turnover_rate:.2f}%",
        f"市盈率：{basic.pe_ratio:.1f}" if basic.pe_ratio > 0 else "市盈率：亏损",
        "",
        f"【主营业务】",
        detail.main_business or "（无数据）",
    ]
    
    if detail.main_business_breakdown:
        lines.append("")
        lines.append("【主营构成】")
        for item in detail.main_business_breakdown[:3]:
            lines.append(f"- {item['name']}: {item['ratio']:.1f}%")
    
    if detail.announcements:
        lines.append("")
        lines.append("【今日公告】")
        for ann in detail.announcements[:3]:
            lines.append(f"- {ann['title']}")
    
    if detail.news:
        lines.append("")
        lines.append("【相关新闻】")
        for n in detail.news[:3]:
            lines.append(f"- {n['title']}")
    
    return "\n".join([l for l in lines if l is not None])


async def generate_market_overview(gainers: list[StockDetail], losers: list[StockDetail]) -> str:
    """生成市场总览 (开场白)"""
    client = get_client()
    _, _, model = load_llm_settings()
    
    # 简要构建涨跌榜摘要，不用完整 detail
    lines = ["【今日涨幅榜 Top 5】"]
    for i, g in enumerate(gainers[:5], 1):
        lines.append(f"{i}. {g.basic.name} {g.basic.pct_chg:+.2f}% ({g.basic.market})")
    
    lines.append("")
    lines.append("【今日跌幅榜 Top 5】")
    for i, l in enumerate(losers[:5], 1):
        lines.append(f"{i}. {l.basic.name} {l.basic.pct_chg:+.2f}% ({l.basic.market})")
    
    context = "\n".join(lines)
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MARKET_OVERVIEW_V3_SYSTEM},
                {"role": "user", "content": context},
            ],
            temperature=0.7,
            max_tokens=300,
        )
        content = (response.choices[0].message.content or "").strip()
        return content or "今天市场感觉有点乱，咱们还是看看个股吧～"
    except Exception as e:
        logger.warning(f"[finance_daily] overview failed: {e}")
        return "今天市场感觉有点乱，咱们还是看看个股吧～"


async def generate_daily_report(data: dict) -> list[str]:
    """
    生成完整的每日报告。
    
    Args:
        data: fetch_daily_report_data() 的返回值
        
    Returns:
        消息列表: [开场白, 个股1, 个股2, ...]
    """
    client = get_client()
    _, _, model = load_llm_settings()
    
    gainers = data.get("gainers", [])
    losers = data.get("losers", [])
    
    messages = []
    
    # 1. 市场总览 (Opening)
    overview = await generate_market_overview(gainers, losers)
    if overview:
        messages.append(overview)
    
    # 2. 个股分析 (Stock Bubbles)
    # 策略：将 Top 5 Gainers + Top 3 Losers 打包给 LLM，让它挑 2-3 个讲。
    
    candidates = []
    # 加个标题区分
    if gainers:
        candidates.append("=== 涨幅榜前列 ===")
        for g in gainers[:5]:
            candidates.append(_build_stock_context(g))
            candidates.append("---") # 内部简单分隔
            
    if losers:
        candidates.append("=== 跌幅榜前列 ===")
        for l in losers[:3]:
            candidates.append(_build_stock_context(l))
            candidates.append("---")
            
    full_context = "\n".join(candidates)
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": STOCK_DAILY_REPORT_V3_SYSTEM},
                {"role": "user", "content": f"今天的候选股票数据：\n\n{full_context}"},
            ],
            temperature=0.75, # 稍微调高点，增加灵气
            max_tokens=1000,
        )
        content = (response.choices[0].message.content or "").strip()
        
        # V3 提示词要求“不要用横线分隔”，且输出为“纯文本”。
        # 所以我们不再做 split("---")，而是直接把整个文案作为一个大消息。
        # 后续的 bubble_splitter 会负责把它切成微信小气泡。
        if content:
            # 简单清理 markdown 格式
            content = content.replace("```json", "").replace("```", "").strip()
            messages.append(content)

                
    except Exception as e:
        logger.error(f"[finance_daily] generate stocks failed: {e}")
        messages.append("哎呀，今天数据有点太多，我 CPU 烧了... 晚点再聊股票吧🥺")
        
    return messages
