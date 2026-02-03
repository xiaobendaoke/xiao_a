
def sanitize_no_markdown(text: str) -> str:
    return text.replace("**", "").replace("#", "").strip()

def _first_sentences(text: str, max_sentences: int=2, max_chars: int=100) -> str:
    if not text:
        return ""
    parts = text.split("。")
    res = []
    current_len = 0
    for p in parts:
        if len(res) >= max_sentences:
            break
        if current_len >= max_chars:
            break
        s = p.strip()
        if s:
            res.append(s)
            current_len += len(s)
    return "。".join(res) + ("。" if res else "")

def render_report_text(trade_date, summary_json, gainers, losers) -> str:
    return f"A股收盘复盘 {trade_date}"
