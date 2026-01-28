"""财经日报主流程（A股收盘 TopN）。

流程（一期）：
- 交易日判断：取最近一个开市日作为 trade_date；
- 拉取日线 `daily` → 过滤 → TopN 涨/跌；
- 拉取 `daily_basic`（失败可降级）；
- 公告：按 TopN 股票拉近 N 天公告标题（作为证据链，失败可降级）；
- 公司画像：按需缓存 `stock_company`（30 天更新一次）；
- LLM：每只股票结构化 JSON 分析；再汇总出日报 JSON；
- 渲染 + 落库（finance.db）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nonebot import logger

from . import config
from .llm_finance import analyze_one_stock, summarize_daily
from .llm_finance import generate_xiao_a_finance_report
from .providers.tushare_provider import TushareProvider
from .providers.eastmoney_provider import EastmoneyProvider
from .providers.sina_provider import SinaProvider
from .storage import (
    get_daily_basic_row,
    get_symbol_basic,
    list_announcements,
    mark_job_failed,
    mark_job_succeeded,
    store_announcements,
    store_daily_basic,
    store_eod_quotes,
    try_start_job_ex,
    upsert_analysis_result,
    upsert_daily_report,
    upsert_stock_company,
    upsert_symbols_basic,
)
from .render import render_report_text
from .render import sanitize_no_markdown, _first_sentences
from .utils import sha1


def _chatify_text(text: str) -> str:
    """把 LLM 输出“更像 QQ 聊天”：短句换行、去掉(=...)这类写法。"""
    s = sanitize_no_markdown(str(text or "")).strip()
    if not s:
        return ""
    s = s.replace("（=", "（就是").replace("(=", "(就是")
    # 如果模型把整段写成一行：按句末标点做软换行
    if "\n" not in s and len(s) >= 50:
        out = []
        for ch in s:
            out.append(ch)
            if ch in "。！？!?；;":
                out.append("\n")
        s = "".join(out)
    # 连续空行收敛
    lines = [ln.rstrip() for ln in s.splitlines()]
    cleaned: list[str] = []
    for ln in lines:
        if ln.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(ln)
    return "\n".join([x for x in cleaned if x is not None]).strip()


def _is_new_listed(list_date: str, trade_date: str, *, days: int) -> bool:
    if not days:
        return False
    try:
        ld = datetime.strptime(list_date, "%Y%m%d").date()
        td = datetime.strptime(trade_date, "%Y%m%d").date()
        return (td - ld).days < int(days)
    except Exception:
        return False


def _one_liner_from_company(company: dict[str, Any]) -> str:
    for k in ("main_business", "introduction", "business_scope"):
        v = str(company.get(k) or "").strip()
        if v:
            return v.replace("\n", " ").strip()
    return ""


def _build_profile(info: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    company = (info.get("company") or {}) if isinstance(info, dict) else {}
    name = str((info.get("name") or r.get("name") or "")).strip()
    industry = str((info.get("industry") or r.get("industry") or "")).strip()
    # eastmoney stock_company 会额外带 industry 字段
    if company.get("industry") and not industry:
        industry = str(company.get("industry") or "").strip()
    intro_raw = _one_liner_from_company(company)
    intro = _first_sentences(intro_raw, max_sentences=2, max_chars=160)
    return {"name": name, "industry": industry, "intro": intro}


def _parse_ann_pub_ts(ann_date: str, ann_time: str) -> int:
    ad = (ann_date or "").strip()
    at = (ann_time or "").strip()
    if not ad:
        return 0
    if not at:
        return 0
    at = at.replace("：", ":")
    fmts = ("%Y%m%d %H:%M:%S", "%Y%m%d %H:%M")
    for f in fmts:
        try:
            dt = datetime.strptime(f"{ad} {at}", f)
            return int(dt.timestamp())
        except Exception:
            continue
    return 0


@dataclass(frozen=True)
class DailySelection:
    trade_date: str
    gainers: list[dict[str, Any]]
    losers: list[dict[str, Any]]


async def _prepare_selection(provider, trade_date: str) -> DailySelection:
    market = config.FIN_DAILY_MARKET
    # eastmoney 公开接口偶发断连：这里对“全市场快照”做一次整体重试，避免整条任务失败
    last_err: Exception | None = None
    daily_rows: list[dict[str, Any]] = []
    for i in range(3):
        try:
            daily_rows = await provider.fetch_daily(trade_date)
            last_err = None
            break
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.8 * (2**i))
    if last_err is not None:
        raise RuntimeError(f"fetch_daily_failed: {last_err}") from last_err
    if not daily_rows:
        raise RuntimeError(f"empty_daily trade_date={trade_date}")

    # 单位对齐：tushare daily.amount 是“千元”，eastmoney 是“元”
    if getattr(provider, "name", "") == "tushare":
        for r in daily_rows:
            try:
                if r.get("amount") is not None:
                    r["amount"] = float(r.get("amount") or 0.0) * 1000.0
            except Exception:
                pass
    await store_eod_quotes(market, trade_date, daily_rows)

    # daily_basic：失败可降级
    try:
        # eastmoney 的 daily 快照里已经带了部分 daily_basic 字段（turnover/pe/pb/mv）
        basic_rows = [
            {
                "ts_code": r.get("ts_code"),
                "turnover_rate": r.get("turnover_rate"),
                "volume_ratio": r.get("volume_ratio"),
                "total_mv": r.get("total_mv"),
                "circ_mv": r.get("circ_mv"),
                "pe": r.get("pe"),
                "pb": r.get("pb"),
            }
            for r in daily_rows
            if r.get("ts_code") and any(k in r for k in ("turnover_rate", "total_mv", "pb", "pe"))
        ]
        if basic_rows:
            await store_daily_basic(market, trade_date, basic_rows)
        else:
            basic_rows2 = await provider.fetch_daily_basic(trade_date)
            if basic_rows2:
                await store_daily_basic(market, trade_date, basic_rows2)
    except Exception as e:
        logger.warning(f"[finance] daily_basic fetch failed: {e}")

    # stock_basic：用于过滤新股/ST/行业等（这里一期直接每日拉；后续可改成低频刷新）
    stock_basic_rows: list[dict[str, Any]] = []
    stock_map: dict[str, dict[str, Any]] = {}
    try:
        stock_basic_rows = await provider.fetch_stock_basic()
        if stock_basic_rows:
            await upsert_symbols_basic(market, stock_basic_rows)
            for r in stock_basic_rows:
                code = str(r.get("ts_code") or "").strip()
                if code:
                    stock_map[code] = r
    except Exception as e:
        logger.warning(f"[finance] stock_basic fetch failed: {e}")

    # eastmoney 兜底：用 daily 快照里的 name 做基础过滤
    if not stock_map:
        for r in daily_rows:
            code = str(r.get("ts_code") or "").strip()
            if not code:
                continue
            stock_map[code] = {"ts_code": code, "name": r.get("name") or "", "industry": ""}
        # 顺便把 name 缓存起来，后续 get_symbol_basic 能用到
        try:
            cache_rows = [{"ts_code": c, "name": (v.get("name") or ""), "industry": ""} for c, v in stock_map.items()]
            await upsert_symbols_basic(market, cache_rows)
        except Exception:
            pass

    # 过滤 + 排序
    filtered: list[dict[str, Any]] = []
    for r in daily_rows:
        ts_code = str(r.get("ts_code") or "").strip()
        if not ts_code:
            continue
        pct = r.get("pct_chg")
        amount = float(r.get("amount") or 0.0)  # 已换算为“元”
        if amount < config.FIN_DAILY_AMOUNT_MIN:
            continue
        info = stock_map.get(ts_code) or {}
        name = str(info.get("name") or "").strip()
        if "st" in name.lower() or "退" in name:
            continue
        if _is_new_listed(
            str(info.get("list_date") or ""),
            trade_date,
            days=config.FIN_DAILY_NEW_LIST_DAYS,
        ):
            continue
        r2 = dict(r)
        r2["name"] = name
        r2["industry"] = str(info.get("industry") or "").strip()
        # pct 为空的直接跳过
        try:
            r2["pct_chg"] = float(pct)
        except Exception:
            continue
        filtered.append(r2)

    filtered.sort(key=lambda x: float(x.get("pct_chg") or 0.0), reverse=True)
    gainers = filtered[: config.FIN_DAILY_TOP_N]
    losers = (
        list(reversed(filtered[-config.FIN_DAILY_TOP_N :]))
        if len(filtered) >= config.FIN_DAILY_TOP_N
        else list(reversed(filtered))
    )
    losers.sort(key=lambda x: float(x.get("pct_chg") or 0.0))
    return DailySelection(trade_date=trade_date, gainers=gainers, losers=losers)


async def _ensure_announcements(provider, trade_date: str, ts_codes: list[str]) -> None:
    market = config.FIN_DAILY_MARKET
    if not ts_codes:
        return

    # 一期默认：按股票拉近 lookback_days（TopN 很小，调用次数可控）
    if config.FIN_DAILY_ANN_LOOKBACK_DAYS > 0:
        end = datetime.strptime(trade_date, "%Y%m%d").date()
        start = end - timedelta(days=int(config.FIN_DAILY_ANN_LOOKBACK_DAYS))
        start_s = start.strftime("%Y%m%d")
        end_s = trade_date
        all_rows: list[dict[str, Any]] = []
        for code in ts_codes:
            try:
                rows = await provider.fetch_anns_by_symbol(code, start_s, end_s)
            except Exception as e:
                logger.warning(f"[finance] anns_by_symbol failed {code}: {e}")
                rows = []
            for r in rows or []:
                title = str(r.get("title") or "").strip()
                url = str(r.get("url") or "").strip()
                ann_date = str(r.get("ann_date") or trade_date).strip()
                ann_time = str(r.get("ann_time") or "").strip()
                pub_ts = int(r.get("pub_ts") or 0) or _parse_ann_pub_ts(ann_date, ann_time)
                src = getattr(provider, "name", "src")
                h = sha1(f"{src}|{code}|{ann_date}|{ann_time}|{title}|{url}")
                all_rows.append(
                    {
                        "hash": h,
                        "market": market,
                        "trade_date": trade_date,
                        "ts_code": code,
                        "ann_date": ann_date,
                        "pub_ts": pub_ts,
                        "title": title,
                        "url": url,
                        "source": "tushare",
                    }
                )
        if all_rows:
            await store_announcements(all_rows)
        return

    # 兜底：只拉当日全量公告再过滤
    try:
        rows = await provider.fetch_anns_by_date(trade_date)
    except Exception as e:
        logger.warning(f"[finance] anns_by_date failed: {e}")
        return
    wanted = set(ts_codes)
    out: list[dict[str, Any]] = []
    for r in rows or []:
        code = str(r.get("ts_code") or "").strip()
        if code not in wanted:
            continue
        title = str(r.get("title") or "").strip()
        url = str(r.get("url") or "").strip()
        ann_date = str(r.get("ann_date") or trade_date).strip()
        ann_time = str(r.get("ann_time") or "").strip()
        pub_ts = int(r.get("pub_ts") or 0) or _parse_ann_pub_ts(ann_date, ann_time)
        src = getattr(provider, "name", "src")
        h = sha1(f"{src}|{code}|{ann_date}|{ann_time}|{title}|{url}")
        out.append(
            {
                "hash": h,
                "market": market,
                "trade_date": trade_date,
                "ts_code": code,
                "ann_date": ann_date,
                "pub_ts": pub_ts,
                "title": title,
                "url": url,
                "source": "tushare",
            }
        )
    if out:
        await store_announcements(out)


async def _enrich_company_profiles(provider: TushareProvider, trade_date: str, ts_codes: list[str]) -> None:
    market = config.FIN_DAILY_MARKET
    for code in ts_codes:
        info = await get_symbol_basic(market, code)
        company = info.get("company") or {}
        updated_ts = int(info.get("company_updated_ts") or 0)
        # 30 天更新一次
        if company and (datetime.now().timestamp() - updated_ts) < 30 * 86400:
            continue
        try:
            comp = await provider.fetch_stock_company(code)
            if comp:
                await upsert_stock_company(market, code, comp)
        except Exception as e:
            logger.warning(f"[finance] stock_company failed {code}: {e}")


def _make_provider():
    # 方向B：默认固定走 Eastmoney，避免 Tushare 权限问题导致任务无法产出。
    name = (config.FIN_DAILY_DATA_PROVIDER or "eastmoney").strip().lower()
    if name in ("tushare", "ts"):
        return TushareProvider(config.TUSHARE_TOKEN)
    if name in ("sina", "sina_finance"):
        return SinaProvider()
    return EastmoneyProvider(proxy=config.FIN_DAILY_EASTMONEY_PROXY or None)


async def run_cn_a_daily(*, force_trade_date: str | None = None, force: bool = False) -> dict[str, Any]:
    if not config.FIN_DAILY_ENABLED:
        return {"skipped": True, "reason": "disabled"}

    provider = _make_provider()
    if force_trade_date:
        trade_date = force_trade_date
    else:
        try:
            trade_date = await provider.last_open_trade_date()
        except Exception:
            # 交易日接口失败时：退化为“用今天日期尝试跑”，避免直接报错中断
            trade_date = datetime.now().strftime("%Y%m%d")

    ok = await try_start_job_ex(config.FIN_DAILY_MARKET, trade_date, force=force)
    if not ok:
        return {"skipped": True, "reason": "already_running_or_done", "trade_date": trade_date}

    try:
        try:
            sel = await _prepare_selection(provider, trade_date)
        except Exception as e:
            # Eastmoney push2 在部分环境下会频繁断连；自动切换到 Sina 行情快照，保证任务可产出
            if getattr(provider, "name", "") == "eastmoney" and (
                "fetch_daily_failed" in str(e) or "RemoteProtocolError" in str(e) or "Server disconnected" in str(e)
            ):
                logger.warning(f"[finance] eastmoney snapshot failed, fallback to sina: {e}")
                provider = SinaProvider()
                sel = await _prepare_selection(provider, trade_date)
            else:
                raise
        notes: list[str] = []
        if getattr(provider, "name", "") in ("eastmoney", "sina") and config.FIN_DAILY_NEW_LIST_DAYS > 0:
            notes.append("注：当前数据源无法稳定获取上市日期，新股过滤可能未生效。")
        ts_codes = [str(x.get("ts_code") or "").strip() for x in (sel.gainers + sel.losers) if x.get("ts_code")]
        ts_codes = list(dict.fromkeys([c for c in ts_codes if c]))

        await asyncio.gather(
            _ensure_announcements(provider, trade_date, ts_codes),
            _enrich_company_profiles(provider, trade_date, ts_codes),
        )

        items_for_llm: list[dict[str, Any]] = []
        detailed_gainers: list[dict[str, Any]] = []
        detailed_losers: list[dict[str, Any]] = []

        sem = asyncio.Semaphore(int(config.FIN_DAILY_LLM_CONCURRENCY or 1))

        async def _build_one(r: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                market = config.FIN_DAILY_MARKET
                ts_code = str(r.get("ts_code") or "").strip()
                info = await get_symbol_basic(market, ts_code)
                profile = _build_profile(info or {}, r)
                anns = await list_announcements(market, trade_date, ts_code, limit=10)
                basic = await get_daily_basic_row(market, trade_date, ts_code)

                features = {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "quote": {
                        "pct_chg": r.get("pct_chg"),
                        "open": r.get("open"),
                        "high": r.get("high"),
                        "low": r.get("low"),
                        "close": r.get("close"),
                        "vol": r.get("vol"),
                        "amount": r.get("amount"),
                    },
                    "daily_basic": basic,
                    "announcements": [{"title": a.get("title"), "url": a.get("url")} for a in (anns or [])],
                    "company_profile": profile,
                }

                llm_payload = {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "company_profile": profile,
                    "quote": features["quote"],
                    "daily_basic": basic,
                    "announcements": features["announcements"],
                }
                llm_json, model = await analyze_one_stock(llm_payload, prompt_version=config.FIN_DAILY_PROMPT_VERSION)
                await upsert_analysis_result(
                    market,
                    trade_date,
                    ts_code,
                    features=features,
                    llm_json=llm_json,
                    llm_model=model,
                    prompt_version=config.FIN_DAILY_PROMPT_VERSION,
                )
                out = dict(r)
                out["profile"] = profile
                out["daily_basic"] = basic
                out["announcements"] = anns
                out["analysis"] = llm_json
                items_for_llm.append(
                    {"ts_code": ts_code, "analysis": llm_json, "profile": profile, "pct_chg": r.get("pct_chg")}
                )
                return out

        detailed_gainers = list(await asyncio.gather(*[_build_one(r) for r in sel.gainers]))
        detailed_losers = list(await asyncio.gather(*[_build_one(r) for r in sel.losers]))

        summary_json, _ = await summarize_daily(items_for_llm, prompt_version=config.FIN_DAILY_PROMPT_VERSION)
        if notes:
            summary_json = dict(summary_json or {})
            summary_json["notes"] = notes
        # === 最终输出：交给小a“聊天式转译”为多段 QQ 气泡 ===
        x_payload = {
            "trade_date": trade_date,
            "market": config.FIN_DAILY_MARKET,
            "theme": (summary_json or {}).get("market_theme"),
            "notes": (summary_json or {}).get("notes") or [],
            "top_watchlist": (summary_json or {}).get("top_watchlist") or [],
            "gainers": [
                {
                    "ts_code": x.get("ts_code"),
                    "name": (x.get("profile") or {}).get("name"),
                    "pct_chg": x.get("pct_chg"),
                    "industry": (x.get("profile") or {}).get("industry"),
                    "intro": (x.get("profile") or {}).get("intro"),
                    "daily_basic": x.get("daily_basic") or {},
                    "announcements": [{"title": a.get("title"), "url": a.get("url")} for a in (x.get("announcements") or [])[:3]],
                    "analysis": x.get("analysis") or {},
                    "amount": (x.get("amount") or (x.get("quote") or {}).get("amount")),
                }
                for x in (detailed_gainers or [])
            ],
            "losers": [
                {
                    "ts_code": x.get("ts_code"),
                    "name": (x.get("profile") or {}).get("name"),
                    "pct_chg": x.get("pct_chg"),
                    "industry": (x.get("profile") or {}).get("industry"),
                    "intro": (x.get("profile") or {}).get("intro"),
                    "daily_basic": x.get("daily_basic") or {},
                    "announcements": [{"title": a.get("title"), "url": a.get("url")} for a in (x.get("announcements") or [])[:3]],
                    "analysis": x.get("analysis") or {},
                    "amount": (x.get("amount") or (x.get("quote") or {}).get("amount")),
                }
                for x in (detailed_losers or [])
            ],
        }
        x_data = {}
        x_model = ""
        if config.FIN_DAILY_OUTPUT_MODE == "xiao_a":
            x_data, x_model = await generate_xiao_a_finance_report(x_payload, prompt_version=config.FIN_DAILY_PROMPT_VERSION)
        parts: list[str] = []
        if isinstance(x_data, dict) and config.FIN_DAILY_CHAT_PER_STOCK:
            overview = _chatify_text(str(x_data.get("overview") or ""))
            if overview and config.FIN_DAILY_OVERVIEW_ENABLED:
                parts.append(overview)

            def _collect(kind: str) -> list[dict[str, Any]]:
                v = x_data.get(kind)
                return v if isinstance(v, list) else []

            for kind in ("gainers", "losers"):
                for it in _collect(kind):
                    t = _chatify_text(str((it or {}).get("text") or ""))
                    if not t:
                        continue
                    # 过长就硬截（LLM 通常会控制长度，这里兜底）
                    if len(t) > config.FIN_DAILY_ITEM_MAX_CHARS:
                        t = t[: config.FIN_DAILY_ITEM_MAX_CHARS - 1] + "…"
                    parts.append(t)

            # 兜底：如果 LLM 漏了涨/跌列表，就用 fallback 补齐，保证“TopN涨+TopN跌”都能发出来
            expected = 0
            if config.FIN_DAILY_OVERVIEW_ENABLED:
                expected += 1
            expected += len(detailed_gainers) + len(detailed_losers)
            if len(parts) < expected:
                existing = set(parts)
                fb = []
                if config.FIN_DAILY_OVERVIEW_ENABLED:
                    fb.append(sanitize_no_markdown(render_report_text(trade_date=trade_date, summary_json=summary_json, gainers=detailed_gainers, losers=detailed_losers).splitlines()[0]))
                # 每只股票补一条
                for x in detailed_gainers + detailed_losers:
                    name = str((x.get("profile") or {}).get("name") or x.get("name") or "").strip()
                    code = str(x.get("ts_code") or "").strip()
                    try:
                        pct = float(x.get("pct_chg") or 0.0)
                    except Exception:
                        pct = 0.0
                    intro = (x.get("profile") or {}).get("intro") or ""
                    ann = (x.get("announcements") or [])
                    ann_title = (ann[0].get("title") if ann else "") if isinstance(ann, list) else ""
                    tag = "【涨】" if pct >= 0 else "【跌】"
                    if ann_title:
                        reason_line = f"今天可能和“{ann_title}”有关。"
                    else:
                        reason_line = "今天标题证据不足，更像情绪/资金走动。"
                    msg = (
                        f"{tag}{name}({code}) {pct:+.2f}%\n"
                        f"{intro or '这家公司做的业务我这边资料还不太全。'}\n"
                        f"{reason_line}\n"
                        "明天就先看板块热度能不能接住，再留意有没有新公告/新消息。"
                    )
                    msg = _chatify_text(msg)
                    if len(msg) > config.FIN_DAILY_ITEM_MAX_CHARS:
                        msg = msg[: config.FIN_DAILY_ITEM_MAX_CHARS - 1] + "…"
                    fb.append(msg)
                # 用 fb 覆盖（保证数量齐）
                parts = fb[:expected]
        elif isinstance(x_data, dict):
            # 兼容旧 schema
            p = x_data.get("parts")
            if isinstance(p, list):
                parts = [sanitize_no_markdown(str(s)) for s in p if str(s).strip()]

        if parts:
            report_text = "\n\n".join(parts).strip()
            # 存最终“给用户看的文本”
            report_json = dict(summary_json or {})
            report_json["provider_used"] = getattr(provider, "name", "")
            report_json["final_model"] = x_model
            report_json["final_parts"] = parts
        else:
            # fallback：用渲染器拼一份“非markdown”的文本
            report_text = sanitize_no_markdown(
                render_report_text(trade_date=trade_date, summary_json=summary_json, gainers=detailed_gainers, losers=detailed_losers)
            )
            report_json = dict(summary_json or {})
            report_json["provider_used"] = getattr(provider, "name", "")
            report_json["final_error"] = x_data.get("error") if isinstance(x_data, dict) else "unknown"

        await upsert_daily_report(config.FIN_DAILY_MARKET, trade_date, report_text=report_text, report_json=report_json)
        await mark_job_succeeded(config.FIN_DAILY_MARKET, trade_date)
        return {
            "skipped": False,
            "trade_date": trade_date,
            "report_text": report_text,
            "summary_json": summary_json,
            "report_parts": parts,
        }

    except Exception as e:
        await mark_job_failed(config.FIN_DAILY_MARKET, trade_date, str(e))
        raise
