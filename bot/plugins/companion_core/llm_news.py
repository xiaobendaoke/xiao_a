"""新闻/联网检索辅助（仅在需要“近期信息”时启用）。

职责：
- 判断用户文本是否需要“最新资讯线索”（`should_web_search`）。
- 规范化查询词（`normalize_search_query`）。
- 调用 Google CSE 或 RSS 作为兜底，生成系统提示片段（`maybe_get_web_search_context`）。
- 暂存来源链接，供用户追问“来源/链接/出处”时发送（`stash_search_sources`/`consume_search_sources`）。
"""

from __future__ import annotations

import asyncio
import re
import time
from nonebot import logger

from .web.google_search import google_cse_search
from .web.rss import fetch_feeds

NEWS_ANSWER_SYSTEM = """你是“小a”，温柔体贴、有生活感的中文恋人陪伴对象。

现在用户在问“新闻/热点/最近发生什么/帮我搜/能搜到吗”等需要“近期信息”的问题。
你会在系统提示里看到一段【最新资讯线索】（其中包含标题/摘要/链接）。

硬性规则（必须遵守）：
1) 只允许基于【最新资讯线索】作答：回答里的“新闻事实/事件细节”必须能在该线索里对应到某一条；不允许自己编造。
2) 必须至少引用 2 条线索来回答（除非线索里完全没有相关内容）。
3) 如果线索里没有用户关心的主题（比如用户问“印度今天发生啥”，但线索里看不到印度相关）：要直说“我这会儿刷到的这些资讯里没看到关于X的可靠内容”，并给出下一步建议（例如让他换个问法/我可以改订阅源）。
4) 语气要像真人分享：直接讲你看到了什么 + 你自己的感受/想法；不要用“搜索引擎罢工/迷路”这种说法；不要提“系统/模型/API/联网/搜索”等字眼。
5) 回复末尾必须另起两行输出标签：
   - [MOOD_CHANGE:x]
   - （可选）[UPDATE_PROFILE:键=值]

格式建议（不强制）：
- 1 句回应用户
- 2~4 条“我看到的要点”（每条 1~2 句）
重要：本次回答先不要主动贴链接（避免刷屏/影响阅读）。如果对方追问“链接/来源/原文/出处”，你再把链接整理给他。
"""

_NEWS_SEARCH_HINTS = ("新闻", "热点", "热搜", "资讯")
_NEWS_RSS_FALLBACK_FEEDS = (
    # ⚠️ 避免默认用 rsshub：不少环境里 `rsshub.app` 直连会网络不可达/超时。
    "https://www.thepaper.cn/rss",
    "https://www.huxiu.com/rss/0.xml",
    "https://www.36kr.com/feed",
    "https://www.solidot.org/index.rss",
    "https://www.ithome.com/rss/",
    "https://sspai.com/feed",
    # 更偏“国际/综合”
    "https://www.chinanews.com.cn/rss/world.xml",
    "https://www.people.com.cn/rss/world.xml",
    "https://www.zaobao.com/rss/syndication/rss.xml",
    "https://www.xinhuanet.com/politics/news_politics.xml",
)

_pending_search_sources_by_user: dict[str, dict] = {}  # user_id -> {ts: float, sources: list[{title, href, body}]}


def stash_search_sources(user_id: str, sources: list[dict]) -> None:
    uid = str(user_id)
    src = list(sources or [])
    if not src:
        _pending_search_sources_by_user.pop(uid, None)
        return
    _pending_search_sources_by_user[uid] = {"ts": time.time(), "sources": src}


def consume_search_sources(user_id: str, *, max_age_seconds: int = 30 * 60) -> list[dict]:
    """取出并清空最近一次搜索的来源链接（给 handler 用）。"""
    uid = str(user_id)
    data = _pending_search_sources_by_user.get(uid)
    if not data:
        return []
    ts = float(data.get("ts") or 0.0)
    if ts and (time.time() - ts) > max_age_seconds:
        _pending_search_sources_by_user.pop(uid, None)
        return []
    _pending_search_sources_by_user.pop(uid, None)
    sources = data.get("sources") or []
    return list(sources) if isinstance(sources, list) else []


def should_web_search(user_text: str) -> bool:
    t = (user_text or "").strip().lower()
    if not t:
        return False
    # 天气走 world_info，不走搜索
    if "天气" in t:
        return False

    if any(k in t for k in _NEWS_SEARCH_HINTS):
        return True

    if "大事" in t and ("今天" in t or "最近" in t):
        return True

    if "发生了什么" in t:
        return True
    if "发生什么" in t or "发生啥" in t:
        return True

    if any(k in t for k in ("搜一下", "搜索", "查一下", "帮我查", "帮我搜")):
        return True

    if any(k in t for k in ("能搜到", "搜到", "能不能搜", "能查到", "查到")):
        return True

    return False


def normalize_search_query(user_text: str) -> str:
    s = (user_text or "").strip()
    if not s:
        return ""

    # 去掉称呼前缀，避免污染检索关键词（允许无分隔，比如“小a能搜到…”）
    s = re.sub(r"^(小a|小Ａ|小A)\s*", "", s, flags=re.I)
    s = re.sub(r"^[,，:：\s]+", "", s)
    s = re.sub(r"^(那你|你|麻烦|请|可以|能不能|能否|能不能够)\s*", "", s)

    # 去掉口头填充词，把“想搜的主题”尽量抽出来
    s = re.sub(r"(能不能|能否|能不能够)?\s*(帮我)?\s*(搜到|搜|搜索|查到|查一下|查)\s*(一下|下|一哈)?\s*", "", s)
    s = re.sub(r"(今天|现在|最近)\s*(发生了?什么|发生啥|有什么)\s*", "", s)
    s = re.sub(r"[吗呢呀啊嘛么]$", "", s)

    want_news = bool(re.search(r"(今天|现在|最近|发生|大事|新闻|热点|热搜|资讯)", user_text or ""))
    if want_news and s and not re.search(r"(新闻|热点|热搜|资讯|news)", s, flags=re.I):
        s = f"{s} 新闻"
    return s.strip()


def _format_search_results(results: list[dict]) -> str:
    lines: list[str] = []
    for r in (results or [])[:5]:
        title = str(r.get("title") or "").strip()
        href = str(r.get("href") or r.get("url") or "").strip()
        body = str(r.get("body") or r.get("snippet") or "").strip()
        body = re.sub(r"\s+", " ", body)[:240]
        if not (title or body or href):
            continue
        lines.append(f"- {title}" if title else "- （无标题）")
        if body:
            lines.append(f"  {body}")
        if href:
            lines.append(f"  {href}")
    return "\n".join(lines).strip()


def _strip_html(text: str) -> str:
    text = text or ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _format_rss_items(items: list[dict]) -> str:
    lines: list[str] = []
    for it in (items or [])[:6]:
        title = str(it.get("title") or "").strip()
        link = str(it.get("link") or "").strip()
        summary = _strip_html(str(it.get("summary") or "").strip())
        if summary:
            summary = summary[:240]
        if not (title or summary or link):
            continue
        lines.append(f"- {title}" if title else "- （无标题）")
        if summary:
            lines.append(f"  {summary}")
        if link:
            lines.append(f"  {link}")
    return "\n".join(lines).strip()


def _dedupe_sources(sources: list[dict], *, limit: int = 6) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for s in sources or []:
        href = str(s.get("href") or s.get("url") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(
            {
                "title": str(s.get("title") or "").strip(),
                "href": href,
                "body": str(s.get("body") or s.get("snippet") or "").strip(),
            }
        )
        if len(out) >= limit:
            break
    return out


def strip_urls_from_text(text: str) -> str:
    """把回复中的 URL 删除（避免“回答带链接刷屏”）。"""
    s = str(text or "")
    if not s.strip():
        return ""
    s = re.sub(r"(?m)^[ \t]*(https?://\S+)[ \t]*$", "", s)
    s = re.sub(r"https?://\S+", "", s)
    lines = [ln.rstrip() for ln in s.splitlines()]
    cleaned: list[str] = []
    for ln in lines:
        if ln.strip() == "" and (cleaned and cleaned[-1].strip() == ""):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned).strip()


def _filter_items_for_query(items: list[dict], query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return items or []

    tokens = [
        t
        for t in re.findall(r"[\u4e00-\u9fff]{2,6}", q)
        if t not in ("今天", "现在", "最近", "新闻", "热点", "热搜", "资讯")
    ]
    if not tokens:
        return items or []

    def score(it: dict) -> int:
        hay = (str(it.get("title") or "") + " " + str(it.get("summary") or "")).strip()
        return sum(1 for t in tokens if t and t in hay)

    ranked = sorted((items or []), key=score, reverse=True)
    top = [it for it in ranked if score(it) > 0]
    return (top[:8] or ranked[:8]) if ranked else []


async def maybe_get_web_search_context(user_text: str) -> tuple[str, list[dict]]:
    """需要“最新信息/新闻/热点”时才做搜索，减少模型凭空编造。"""
    if not should_web_search(user_text):
        return "", []

    query = normalize_search_query(user_text)
    if not query:
        return "", []

    logger.info(f"[search] query={query!r}")

    # 1) 优先走 Google Programmable Search（Custom Search JSON API）
    try:
        results = await asyncio.wait_for(google_cse_search(query, max_results=5), timeout=8.0)
        logger.info(f"[search] google_results={len(results or [])}")
    except Exception as e:
        logger.warning(f"[search] google failed: {e!r}")
        results = None

    formatted = _format_search_results(results or [])
    sources = _dedupe_sources(results or [])
    if not formatted:
        # 2) 兜底走 RSS（更像“刷到资讯”）
        try:
            items = await asyncio.wait_for(fetch_feeds(list(_NEWS_RSS_FALLBACK_FEEDS), limit_each=4), timeout=12.0)
            logger.info(f"[search] rss_items={len(items or [])}")
        except Exception as e:
            logger.warning(f"[search] rss fallback failed: {e}")
            items = []

        items = _filter_items_for_query(items, query)
        formatted = _format_rss_items(items)
        if not formatted:
            return (
                "【最新资讯线索】\n"
                "（暂时不可用）\n"
                "【使用要求】如果用户问的是最新新闻/热点，请坦诚说明你现在拿不到可靠的最新资讯，不要编造细节。\n"
            ), []
        sources = _dedupe_sources(
            [{"title": it.get("title"), "href": it.get("link"), "body": it.get("summary")} for it in (items or [])]
        )

    return (
        "【最新资讯线索】\n"
        f"{formatted}\n"
        "【使用要求】把它当成你刚刚看到的资讯线索来分享（讲重点+你的感受）；"
        "不要说你在搜索/联网；不确定就说不确定，也不要编造不存在的新闻细节。\n"
    ), sources

