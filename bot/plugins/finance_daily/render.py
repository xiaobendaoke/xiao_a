"""finance_daily 的消息渲染层（把结构化结果变成 QQ 可读文本）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _fmt_pct(x: Any) -> str:
    try:
        v = float(x)
        return f"{v:+.2f}%"
    except Exception:
        return ""


def _fmt_mv(x: Any) -> str:
    try:
        v = float(x)
        if v <= 0:
            return ""
        # tushare total_mv 单位：万元
        yi = v / 10000.0
        return f"{yi:.0f}亿"
    except Exception:
        return ""


def _short(s: Any, n: int) -> str:
    t = str(s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _first_sentences(text: Any, *, max_sentences: int = 2, max_chars: int = 120) -> str:
    """取前 N 句完整句子，避免“半句截断”。"""
    s = str(text or "").replace("\u3000", " ").strip()
    if not s:
        return ""
    s = " ".join(s.split())
    parts = []
    buf = ""
    for ch in s:
        buf += ch
        if ch in "。！？!?":
            parts.append(buf.strip())
            buf = ""
            if len(parts) >= max_sentences:
                break
        if len("".join(parts)) >= max_chars:
            break
    if not parts:
        # 没有句末标点：按字数做软截断
        return s[:max_chars].strip()
    out = "".join(parts).strip()
    return out[:max_chars].strip()


def sanitize_no_markdown(text: str) -> str:
    """把常见 Markdown 痕迹清理成 QQ 更像聊天的格式。"""
    s = str(text or "")
    if not s.strip():
        return ""
    lines = []
    for ln in s.splitlines():
        t = ln.rstrip()
        t = t.replace("**", "").replace("__", "").replace("`", "")
        # 去掉 Markdown 标题符号
        t = t.lstrip("#").strip() if t.lstrip().startswith("#") else t
        # 去掉以“- ”开头的列表符号（保留内容）
        if t.lstrip().startswith("- "):
            t = t.lstrip()[2:].strip()
        lines.append(t)
    # 连续空行收敛
    out = []
    for ln in lines:
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(ln)
    return "\n".join(out).strip()


def render_report_text(
    *,
    trade_date: str,
    summary_json: dict[str, Any],
    gainers: list[dict[str, Any]],
    losers: list[dict[str, Any]],
) -> str:
    dt = trade_date
    title = f"A股收盘复盘 {dt[:4]}-{dt[4:6]}-{dt[6:8]}"
    lines: list[str] = [title]

    theme = str((summary_json or {}).get("market_theme") or "").strip()
    if theme:
        lines.append(f"风格：{theme}")

    notes = (summary_json or {}).get("notes")
    if isinstance(notes, list) and notes:
        for n in notes[:3]:
            n = str(n or "").strip()
            if n:
                lines.append(n)

    def _list_head(items: list[dict[str, Any]]) -> str:
        parts = []
        for it in items:
            name = str(it.get("name") or it.get("ts_code") or "").strip()
            pct = _fmt_pct(it.get("pct_chg"))
            if name:
                parts.append(f"{name}{pct}")
        return "、".join(parts[:10])

    if gainers:
        lines.append(f"涨幅Top：{_list_head(gainers)}")
    if losers:
        lines.append(f"跌幅Top：{_list_head(losers)}")

    def _render_block(label: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        lines.append("")
        lines.append(label)
        def _as_title(e: Any) -> str:
            if isinstance(e, dict):
                return str(e.get("title") or "").strip()
            if isinstance(e, str):
                return e.strip()
            return ""

        for idx, it in enumerate(items, start=1):
            name = str(it.get("name") or "").strip()
            ts_code = str(it.get("ts_code") or "").strip()
            pct = _fmt_pct(it.get("pct_chg"))
            mv = _fmt_mv(((it.get("daily_basic") or {}) or {}).get("total_mv"))
            industry = str((it.get("profile") or {}).get("industry") or "").strip()
            intro = str((it.get("profile") or {}).get("one_liner") or "").strip()
            header = f"{idx}）{name}({ts_code}) {pct}"
            if mv:
                header += f" | 市值{mv}"
            if industry:
                header += f" | {industry}"
            lines.append(header)
            if intro:
                lines.append(f"画像：{_first_sentences(intro, max_sentences=2, max_chars=80)}")

            llm = it.get("analysis") or {}
            move = (llm.get("today_move_explanation") or {}) if isinstance(llm, dict) else {}
            ae = move.get("A_evidence") if isinstance(move, dict) else None
            if isinstance(ae, list) and ae:
                # 只展示前2条标题
                for e in ae[:2]:
                    t = _short(_as_title(e), 44)
                    if t:
                        lines.append(f"证据：{t}")
            else:
                # fallback：公告标题
                anns = it.get("announcements") or []
                if anns:
                    lines.append(f"公告：{_short((anns[0] or {}).get('title'), 44)}")
                else:
                    lines.append("公告：证据不足")

            wp = llm.get("watch_points_next_day") if isinstance(llm, dict) else None
            if isinstance(wp, list) and wp:
                lines.append(f"明日：{_short('；'.join([str(x) for x in wp[:3] if x]), 52)}")

    _render_block("涨幅榜", gainers)
    _render_block("跌幅榜", losers)

    wl = (summary_json or {}).get("top_watchlist")
    if isinstance(wl, list) and wl:
        lines.append("")
        lines.append("明日观察")
        for w in wl[:3]:
            code = str((w or {}).get("ts_code") or "").strip()
            reason = _short((w or {}).get("reason"), 60)
            if code:
                lines.append(f"- {code}：{reason}")

    return "\n".join([ln.rstrip() for ln in lines if ln is not None]).strip()
