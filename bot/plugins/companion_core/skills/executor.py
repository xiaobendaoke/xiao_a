"""Skills 执行器。

负责：
- 根据 skill 配置调用数据获取函数
- 组装 skill prompt（包含实时数据）
"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger

from . import get_skill, Skill, DataSource


# 数据获取函数注册表
_DATA_FUNCTIONS: dict[str, Any] = {}


def register_data_function(name: str):
    """装饰器：注册数据获取函数"""
    def decorator(func):
        _DATA_FUNCTIONS[name] = func
        return func
    return decorator


def get_data_function(name: str):
    """获取数据函数"""
    return _DATA_FUNCTIONS.get(name)


# === 注册数据函数 ===

@register_data_function("fetch_top_gainers")
async def _fetch_top_gainers(limit: int = 3) -> list[dict]:
    """获取涨幅榜"""
    try:
        from ..finance_daily.data import fetch_top_gainers
        stocks = await fetch_top_gainers(limit)
        return [
            {
                "code": s.code,
                "name": s.name,
                "pct_chg": s.pct_chg,
                "price": s.price,
                "turnover_rate": s.turnover_rate,
                "pe_ratio": s.pe_ratio,
                "amount_yi": s.amount_yi,
            }
            for s in stocks
        ]
    except Exception as e:
        logger.warning(f"[skills][executor] fetch_top_gainers failed: {e}")
        return []


@register_data_function("fetch_top_losers")
async def _fetch_top_losers(limit: int = 3) -> list[dict]:
    """获取跌幅榜"""
    try:
        from ..finance_daily.data import fetch_top_losers
        stocks = await fetch_top_losers(limit)
        return [
            {
                "code": s.code,
                "name": s.name,
                "pct_chg": s.pct_chg,
                "price": s.price,
                "turnover_rate": s.turnover_rate,
                "pe_ratio": s.pe_ratio,
                "amount_yi": s.amount_yi,
            }
            for s in stocks
        ]
    except Exception as e:
        logger.warning(f"[skills][executor] fetch_top_losers failed: {e}")
        return []


@register_data_function("fetch_recipe")
async def _fetch_recipe(keyword: str = "chicken") -> list[dict]:
    """从 TheMealDB 获取菜谱（免费 API）"""
    import os
    import httpx
    
    # 如果 keyword 是 auto，尝试从用户消息中提取食材关键词
    if keyword == "auto":
        keyword = "chicken"  # 默认
    
    base_url = os.getenv("THEMEALDB_URL", "https://www.themealdb.com/api/json/v1/1")
    url = f"{base_url}/search.php?s={keyword}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            data = resp.json()
        
        meals = data.get("meals") or []
        return [
            {
                "name": m.get("strMeal", ""),
                "category": m.get("strCategory", ""),
                "area": m.get("strArea", ""),
                "instructions": (m.get("strInstructions", "") or "")[:200] + "...",
            }
            for m in meals[:3]
        ]
    except Exception as e:
        logger.warning(f"[skills][executor] fetch_recipe failed: {e}")
        return []


async def execute_skill_data(skill_name: str) -> dict[str, Any]:
    """执行 skill 的数据采集，返回结构化数据"""
    skill = get_skill(skill_name)
    if not skill:
        return {}

    data = {}
    tasks = []
    source_names = []

    for source in skill.data_sources:
        func = get_data_function(source.function)
        if func:
            tasks.append(func(**source.args))
            source_names.append(source.name)
        else:
            logger.warning(f"[skills][executor] unknown function: {source.function}")

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(source_names, results):
            if isinstance(result, Exception):
                logger.warning(f"[skills][executor] {name} failed: {result}")
                data[name] = []
            else:
                data[name] = result

    logger.info(f"[skills][executor] fetched data for {skill_name}: {list(data.keys())}")
    return data


def format_stock_data(data: dict[str, Any]) -> str:
    """格式化股票数据为可读文本"""
    lines = []

    gainers = data.get("top_gainers", [])
    if gainers:
        lines.append("【今日涨幅榜】")
        for i, s in enumerate(gainers, 1):
            lines.append(
                f"{i}. {s['name']}({s['code']}) {s['pct_chg']:+.2f}% "
                f"现价{s['price']:.2f} 换手率{s['turnover_rate']:.1f}% "
                f"PE{s['pe_ratio']:.1f} 成交{s['amount_yi']}"
            )

    losers = data.get("top_losers", [])
    if losers:
        lines.append("\n【今日跌幅榜】")
        for i, s in enumerate(losers, 1):
            lines.append(
                f"{i}. {s['name']}({s['code']}) {s['pct_chg']:+.2f}% "
                f"现价{s['price']:.2f} 换手率{s['turnover_rate']:.1f}%"
            )

    return "\n".join(lines) if lines else "（暂无行情数据）"


def format_recipe_data(data: dict[str, Any]) -> str:
    """格式化菜谱数据为可读文本"""
    recipes = data.get("recipe", [])
    if not recipes:
        return "（暂无菜谱数据）"
    
    lines = ["【推荐菜谱】"]
    for i, r in enumerate(recipes, 1):
        name = r.get("name", "")
        category = r.get("category", "")
        area = r.get("area", "")
        lines.append(f"{i}. {name}（{area} {category}）")
    
    return "\n".join(lines)


def build_skill_prompt(skill_name: str, data: dict[str, Any]) -> str:
    """组装 skill 的 system prompt（包含实时数据）"""
    skill = get_skill(skill_name)
    if not skill:
        return ""

    # 格式化数据
    if skill_name == "financial_analysis":
        data_text = format_stock_data(data)
    elif skill_name == "life_helper":
        data_text = format_recipe_data(data)
    elif data:
        # 通用格式化
        import json
        data_text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        data_text = "（无需外部数据）"

    prompt = f"""【专业能力模块：{skill.description}】

{skill.content}

---

【实时市场数据】
{data_text}

---

【输出要求】
- 请用小a（温柔女朋友）的口吻回复
- 结合上面的实时数据进行分析
- 保持自然亲切，不要像研报/分析师
- 可以用"我帮你看了一下"、"今天行情..."这样的开头
"""
    return prompt
