"""股票小白日报 LLM 分析器。

负责调用 LLM 生成三层解读内容。
"""

from __future__ import annotations

import json
import re
from typing import Any

from nonebot import logger

from ..llm_client import get_client, load_llm_settings
from .data import StockDetail
from .prompts import STOCK_TEACHER_SYSTEM, XIAOA_STYLE_SYSTEM, MARKET_OVERVIEW_SYSTEM


_JSON_RE = re.compile(r"\{[\s\S]*\}", re.S)


def _try_parse_json(text: str) -> dict | None:
    """尝试从文本中解析 JSON"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except:
        pass
    
    match = _JSON_RE.search(text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    return None


def _build_stock_context(detail: StockDetail) -> str:
    """构建股票上下文供 LLM 分析"""
    basic = detail.basic
    
    lines = [
        f"【股票】{basic.name}({basic.code})",
        f"涨跌幅：{basic.pct_chg:+.2f}%",
        f"换手率：{basic.turnover_rate:.2f}%",
        f"市盈率：{basic.pe_ratio:.1f}" if basic.pe_ratio > 0 else "市盈率：亏损",
        f"成交额：{basic.amount_yi}" if basic.amount_yi else "",
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


async def analyze_single_stock(detail: StockDetail) -> dict:
    """
    对单只股票进行三层解读分析。
    
    Returns:
        {
            "stock": "科创源(300731)",
            "pct_chg": "+19.99%",
            "what_it_does": {...},
            "drama": {...},
            "temperature": {...},
            "daily_word": {...},
        }
    """
    client = get_client()
    _, _, model = load_llm_settings()
    
    context = _build_stock_context(detail)
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": STOCK_TEACHER_SYSTEM},
                {"role": "user", "content": context},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        raw = (response.choices[0].message.content or "").strip()
        result = _try_parse_json(raw) or {}
    except Exception as e:
        logger.error(f"[finance_daily] analyze failed {detail.basic.code}: {e}")
        result = {}
    
    # 添加基本信息
    result["stock"] = f"{detail.basic.name}({detail.basic.code})"
    result["pct_chg"] = f"{detail.basic.pct_chg:+.2f}%"
    result["turnover_rate"] = f"{detail.basic.turnover_rate:.2f}%"
    
    return result


async def generate_xiaoa_message(analysis: dict, rank: int, is_gainer: bool = True) -> str:
    """
    将分析结果转换为小a口吻的消息。
    
    Args:
        analysis: 分析结果
        rank: 排名（1-5）
        is_gainer: 是否是涨幅榜
    """
    client = get_client()
    _, _, model = load_llm_settings()
    
    # 构建待转换的内容（不使用 emoji）
    rank_label = ["第一", "第二", "第三", "第四", "第五"][rank - 1] if 1 <= rank <= 5 else ""
    direction = "涨" if is_gainer else "跌"
    
    content_parts = [
        f"【{direction}幅榜{rank_label}】{analysis.get('stock', '')} {analysis.get('pct_chg', '')}",
        "",
    ]
    
    # 添加各部分内容
    what = analysis.get("what_it_does", {})
    if what:
        content_parts.append(f"它是干嘛的：{what.get('content', '')}")
        content_parts.append("")
    
    drama = analysis.get("drama", {})
    if drama:
        drama_name = drama.get("drama_name", "")
        content_parts.append(f"今天的剧情：{drama_name}")
        content_parts.append(drama.get("content", ""))
        content_parts.append("")
    
    temp = analysis.get("temperature", {})
    if temp:
        level = temp.get("level", "")
        content_parts.append(f"温度：{level}")
        content_parts.append(temp.get("content", ""))
        content_parts.append("")
    
    word = analysis.get("daily_word", {})
    if word:
        term = word.get("term", "")
        exp = word.get("explanation", "")
        content_parts.append(f"每日一词：{term}")
        content_parts.append(exp)
    
    raw_content = "\n".join(content_parts)
    
    # 用小a口吻润色
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": XIAOA_STYLE_SYSTEM},
                {"role": "user", "content": raw_content},
            ],
            temperature=0.6,
            max_tokens=400,
        )
        styled = (response.choices[0].message.content or "").strip()
        return styled if styled else raw_content
    except Exception as e:
        logger.warning(f"[finance_daily] style failed: {e}")
        return raw_content


async def generate_market_overview(gainers: list[StockDetail], losers: list[StockDetail]) -> str:
    """生成市场总览"""
    client = get_client()
    _, _, model = load_llm_settings()
    
    # 构建上下文
    lines = ["【今日涨幅榜】"]
    for i, g in enumerate(gainers[:5], 1):
        lines.append(f"{i}. {g.basic.name}({g.basic.code}) {g.basic.pct_chg:+.2f}%")
    
    lines.append("")
    lines.append("【今日跌幅榜】")
    for i, l in enumerate(losers[:5], 1):
        lines.append(f"{i}. {l.basic.name}({l.basic.code}) {l.basic.pct_chg:+.2f}%")
    
    context = "\n".join(lines)
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MARKET_OVERVIEW_SYSTEM},
                {"role": "user", "content": context},
            ],
            temperature=0.6,
            max_tokens=200,
        )
        raw = (response.choices[0].message.content or "").strip()
        result = _try_parse_json(raw) or {}
        
        summary = result.get("summary", "")
        vibe = result.get("vibe", "")
        
        if summary and vibe:
            return f"{summary}\n\n{vibe}"
        return summary or "今天市场有点复杂，我整理一下再告诉你～"
    except Exception as e:
        logger.warning(f"[finance_daily] overview failed: {e}")
        return "今天市场有点复杂，我整理一下再告诉你～"


async def generate_daily_report(data: dict) -> list[str]:
    """
    生成完整的每日报告（多条消息）。
    
    Args:
        data: fetch_daily_report_data() 的返回值
        
    Returns:
        消息列表，每条消息对应一只股票或总览
    """
    messages = []
    
    gainers = data.get("gainers", [])
    losers = data.get("losers", [])
    
    # 1. 市场总览
    overview = await generate_market_overview(gainers, losers)
    messages.append(overview)
    
    # 2. 涨幅榜 Top 5（只分析前3个，避免刷屏）
    for i, detail in enumerate(gainers[:3], 1):
        analysis = await analyze_single_stock(detail)
        msg = await generate_xiaoa_message(analysis, i, is_gainer=True)
        messages.append(msg)
    
    # 3. 可选：跌幅榜前 1-2 个
    if losers:
        analysis = await analyze_single_stock(losers[0])
        msg = await generate_xiaoa_message(analysis, 1, is_gainer=False)
        messages.append(msg)
    
    return messages
