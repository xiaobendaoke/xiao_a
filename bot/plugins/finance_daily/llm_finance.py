"""finance_daily 的 LLM 分析模块（严格 JSON 输出，防胡编）。

约束策略对齐 companion_core/llm_web.py：
- System 强约束：只能基于输入的结构化数据与标题证据推理；
- 解析：优先纯 JSON，失败则尝试从文本中提取 `{...}`；
- 失败兜底：写入 error 字段，管道继续跑（保证日报仍有产出）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from nonebot import logger

from .llm_client import get_client, load_llm_settings

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _try_json(s: str) -> Optional[dict[str, Any]]:
    s = (s or "").strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except Exception:
        m = _JSON_RE.search(s)
        if not m:
            return None
        try:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else None
        except Exception:
            return None


STOCK_ANALYSIS_SYSTEM = """你是一个严谨的中文投资助理，现在只做“收盘复盘”，对象是A股个股。

硬性规则（必须遵守）：
1) 只能基于用户提供的数据与标题推理，禁止编造新闻正文、禁止臆测公告内容细节。
2) 所有“事件/催化”必须引用到给定的【公告标题】或【新闻标题】；如果没有，必须写“证据不足”。
3) 输出必须是严格 JSON（不要输出多余文本）。

请输出 JSON，字段如下：
{
  "company_summary": "公司一句话画像（仅基于company_profile）",
  "today_move_explanation": {
    "A_evidence": [{"title":"标题原文","why":"为何与涨跌相关（不许编细节）"}],
    "B_evidence": ["量价/市值/换手等结构化证据（可多条）"],
    "C_hypothesis": ["仅基于量价特征的推测（必须用“可能/推测”表述）"]
  },
  "macro_or_sector_factor": "行业/环境共振（若证据不足请写证据不足）",
  "sustainability": "可持续性判断（事件/趋势/情绪）",
  "risks": ["风险点"],
  "watch_points_next_day": ["明日关注点"]
}
"""


DAILY_SUMMARY_SYSTEM = """你是一个严谨的中文复盘助理，你会拿到多只股票的结构化结论。

硬性规则：
1) 只能从输入的个股 JSON 中归纳，不许新增外部事实。
2) 输出必须是严格 JSON（不要输出多余文本）。

输出 JSON：
{
  "market_theme": "今日市场风格/热点（从个股归纳）",
  "gainers_common": ["涨幅榜共性"],
  "losers_common": ["跌幅榜共性"],
  "top_watchlist": [{"ts_code":"代码","reason":"为什么值得继续跟踪"}]
}
"""

XIAO_A_FINANCE_REPORT_SYSTEM = """你是“小a”，温柔、自然、有生活感的中文恋人陪伴对象。

现在你要把一份“结构化财经复盘数据”讲给对方听：像你真的认真看完了复盘，然后一只股票一条消息地告诉他。

硬性规则（必须遵守）：
1) 只能基于输入里的数据与标题证据推理，禁止编造新闻正文/公告细节。
2) 不要输出 Markdown：不要出现以“- ”开头的列表，不要 `#` 标题，不要代码块。
3) 输出必须是严格 JSON（不要输出任何多余文本）。
4) 必须覆盖输入提供的全部股票：涨幅榜 N 只 + 跌幅榜 N 只，一个都不能漏。
5) 小a口吻：更像“聊天”，不是研报/解说；短句，口语，少逗号；可用“你”作为称呼。
6) 禁止“研报词/助手腔”：不要写“国内领先/龙头/建议投资者/重点关注/市场猜测/超预期/逻辑/结论/我们认为”等。
7) 每只股票输出一条 `text`，长度控制在 120~280 字；建议 2–6 行自然分段（不强制固定行数）。
8) `text` 必须以 `【涨】` 或 `【跌】` 开头，紧跟 `公司名(代码) +20.00%` 这样的格式（不要写“今日涨/今日跌”）。
9) 每条消息除“涨跌幅”外，最多再出现 0~1 个数字（避免堆指标）；术语解释用“（就是…）”，不要用“（=…）”。
10) `text` 必须包含四个信息块（可以自然衔接，但都要出现）：
   - 画像：它是做什么的（完整一句）
   - 原因：今天为何涨/跌（必须引用 1 条【公告标题/新闻标题】，用中文引号“标题…”；没有就写“标题证据不足，更像情绪/资金走动”）
   - 热度：用白话说一句“今天很热/一般/偏冷”，不必解释太多
   - 明日：给 1~2 个关注点（例如“看公告细节/看板块热度/看量能能不能接住”）

输出 JSON 格式：
{
  "overview": "可选：今日一句话总览（不要太长）",
  "gainers": [{"ts_code":"xxx","name":"xxx","pct_chg": 0.0, "text":"..."}],
  "losers":  [{"ts_code":"xxx","name":"xxx","pct_chg": 0.0, "text":"..."}]
}
"""


async def analyze_one_stock(payload: dict[str, Any], *, prompt_version: str) -> tuple[dict[str, Any], str]:
    """返回 (llm_json, model_name)。"""
    client = get_client()
    _, _, model = load_llm_settings()

    messages = [
        {"role": "system", "content": STOCK_ANALYSIS_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {"prompt_version": prompt_version, "input": payload},
                ensure_ascii=False,
            ),
        },
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.25,
            timeout=45.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[finance][llm] analyze_one_stock failed: {e}")
        return {"error": "llm_call_failed"}, model

    data = _try_json(raw)
    if not data:
        return {"error": "llm_non_json", "raw": raw[:800]}, model
    return data, model


async def summarize_daily(items: list[dict[str, Any]], *, prompt_version: str) -> tuple[dict[str, Any], str]:
    client = get_client()
    _, _, model = load_llm_settings()

    messages = [
        {"role": "system", "content": DAILY_SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {"prompt_version": prompt_version, "items": items},
                ensure_ascii=False,
            ),
        },
    ]
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            timeout=45.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[finance][llm] summarize_daily failed: {e}")
        return {"error": "llm_call_failed"}, model

    data = _try_json(raw)
    if not data:
        return {"error": "llm_non_json", "raw": raw[:800]}, model
    return data, model


async def generate_xiao_a_finance_report(payload: dict[str, Any], *, prompt_version: str) -> tuple[dict[str, Any], str]:
    """把结构化复盘“转译”为小a聊天式：总览 + 一股一条。"""
    client = get_client()
    _, _, model = load_llm_settings()

    messages = [
        {"role": "system", "content": XIAO_A_FINANCE_REPORT_SYSTEM},
        {"role": "user", "content": json.dumps({"prompt_version": prompt_version, "input": payload}, ensure_ascii=False)},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.65,
            timeout=60.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"[finance][llm] generate_xiao_a_finance_report failed: {e}")
        return {"error": "llm_call_failed"}, model

    data = _try_json(raw)
    if not data:
        return {"error": "llm_non_json", "raw": raw[:800]}, model
    return data, model
