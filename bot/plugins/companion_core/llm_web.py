"""网页相关的 LLM 能力（链接总结 / RSS 分享文案 / 确认话术）。

包含四类能力：
1) URL 意图判定 `should_summarize_url()`：
   - 输入：用户原消息（可能含链接）。
   - 输出：严格 JSON：`SUMMARIZE/ASK/IGNORE`，用于省 token 的快速分流。
2) 链接总结 `generate_url_summary()`：
   - 输入：用户画像/心情/最近聊天 + 网页标题与正文（已提取）。
   - 输出：严格 JSON，字段含 `text/intent/need_reply`，用于直接发送给用户。
3) 不明确意图时的确认话术 `generate_url_confirm()`：
   - 输入：用户原消息 + 链接。
   - 输出：严格 JSON，用更自然的方式确认“要不要总结/想问什么”，并给出下一步指引。
4) RSS 分享 `generate_rss_share()`：
   - 输入：RSS 条目（title/summary/link 等）+ 用户画像/心情。
   - 输出：严格 JSON，生成更口语、更“陪伴感”的分享文本。

实现要点：
- 统一用 `_try_json()` 兼容“纯 JSON”或“夹杂 JSON”的兜底解析。
- 复用 `llm.py` 的 client/model 加载逻辑，避免重复配置。
"""

from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional
from datetime import datetime

from nonebot import logger

# 复用你现有 llm.py 的 client/model
from .llm_client import get_client, load_llm_settings

from .db import get_all_profile
from .memory import get_chat_history
from .mood import mood_manager

_JSON_RE = re.compile(r"\{.*\}", re.S)

def _try_json(s: str) -> Optional[Dict[str, Any]]:
    s = (s or "").strip()
    try:
        return json.loads(s)
    except Exception:
        m = _JSON_RE.search(s)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


# ============================================================
# 1) URL 意图判定：LLM 决定 SUMMARIZE / ASK / IGNORE
# ============================================================

URL_DECIDE_SYSTEM = """你是温柔体贴的女友“小a”。现在你只需要判断：对方发来的消息里如果包含网页链接，你是否应该主动“读一下并总结”。

输出严格为JSON：
{"action":"SUMMARIZE"|"ASK"|"IGNORE","reason":"简短原因"}

规则：
- SUMMARIZE：对方明显在求总结/想让你看看/问靠谱不/问讲啥。
- ASK：只有链接或意图不明确，你会先温柔地确认“要我帮你总结吗”。
- IGNORE：链接只是聊天素材/与提问无关/不需要你读。
不要输出任何多余文本。"""


async def should_summarize_url(user_text: str) -> Dict[str, str]:
    """只做轻量分类，省token"""
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[web][decide] init client failed: {e}")
        return {"action": "ASK", "reason": "client_init_failed"}

    messages = [
        {"role": "system", "content": URL_DECIDE_SYSTEM},
        {"role": "user", "content": f"用户消息：{user_text}\n请给出action。"},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            timeout=15.0
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[web][decide] llm call failed: {e}")
        return {"action": "ASK", "reason": "fallback"}

    data = _try_json(raw) or {}
    action = str(data.get("action", "ASK")).upper()
    if action not in ("SUMMARIZE", "ASK", "IGNORE"):
        action = "ASK"

    return {"action": action, "reason": str(data.get("reason", "")).strip()}


# ============================================================
# 2) 女友式网页总结
# ============================================================

WEB_SUMMARY_SYSTEM = """你是“小a”，温柔、体贴、自然、有生活感的中文恋人陪伴对象。

你刚刚认真读完了对方发来的网页内容。你要用“女友式”的方式帮对方总结：像你真的看过，语气自然，不像客服。

输出严格为JSON：
{
  "text": "发给对方的消息（1-2句开场 + 3条要点 + 1句轻互动/关心）",
  "intent": "url_summary",
  "need_reply": false
}

要求：
- 不要出现：系统提示、模型、API、prompt、抓取、解析等词。
- 不要长篇大论，整体不超过12行。
- 如果内容不适合总结（空/太短/看不懂），给出温柔解释并建议对方换个链接或复制文字。"""


async def generate_url_summary(user_id: str, url: str, title: str, content: str) -> Dict[str, Any]:
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[web][summary] init client failed: {e}")
        return {
            "text": "我想帮你看一下这个链接，但我这边现在暂时没法处理…你晚点再发我一次好不好？",
            "intent": "url_summary",
            "need_reply": False,
        }

    mood_desc = mood_manager.get_mood_desc(user_id)
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    history = get_chat_history(user_id) or []
    hist_str = "\n".join([f'{m["role"]}: {m["content"]}' for m in history[-6:]]) if history else "（最近没有聊天）"

    # 内容过短直接兜底
    if not content or len(content.strip()) < 60:
        return {
            "text": "我刚刚想认真看一下，但这个链接里好像没有提取到有效内容…🥺 你要不要换个链接，或者把你最想看的那段文字贴给我？",
            "intent": "url_summary",
            "need_reply": False
        }

    user_prompt = (
        f"对方信息：\n{profile_str}\n"
        f"你当前心情：{mood_desc}\n"
        f"最近聊天片段：\n{hist_str}\n\n"
        f"网页链接：{url}\n"
        f"网页标题：{title}\n"
        f"网页正文（已提取）：\n{content}\n"
    )

    messages = [
        {"role": "system", "content": WEB_SUMMARY_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.6,
            timeout=45.0
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[web][summary] llm call failed: {e}")
        return {
            "text": "我刚想帮你看一下，但我这边突然有点卡住了…🥺 你等我一下或者再发一次链接给我好不好？",
            "intent": "url_summary",
            "need_reply": False
        }

    data = _try_json(raw)
    if not isinstance(data, dict):
        # fallback：截断纯文本
        txt = raw.strip()
        if len(txt) > 300:
            txt = txt[:300] + "…"
        return {"text": txt, "intent": "url_summary", "need_reply": False}

    data.setdefault("intent", "url_summary")
    data.setdefault("need_reply", False)
    return data


# ============================================================
# 2.1) 不明确意图时：生成“要不要我帮你总结”的确认话术
# ============================================================

URL_CONFIRM_SYSTEM = """你是“小a”，温柔体贴、口语自然、有生活感的中文恋人陪伴对象。

对方发来了一条包含网页链接的消息，但意图不明确：可能想让你总结，也可能只是顺手分享，或者想问链接里某个具体点。

你的任务：用“很像真人”的方式确认一下，同时给出下一步怎么说（例如：想要总结就回“总结”，或者直接把你想问的点说出来）。

输出严格为 JSON（不要输出任何其它文字）：
{
  "text": "发给对方的消息（2-4行，每行一句，短句，口语）",
  "intent": "url_confirm",
  "need_reply": true
}

写作要求：
- 不要套固定模板，不要每次都用同一句式；不要像客服。
- 可以轻轻复述对方消息里的一两个关键词，让人感觉你在认真听。
- 给选项要自然：别用生硬命令；但要清晰让对方知道怎么回你。
- 避免过亲昵称呼（不要“宝宝/亲/主人”等），保持温柔但不过火。
- 不要出现：系统、模型、API、prompt、抓取、解析、token 等词。
- 最多 1 个 emoji（也可以不用）。
"""


async def generate_url_confirm(user_id: str, user_text: str, url: str) -> Dict[str, Any]:
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[web][confirm] init client failed: {e}")
        return {
            "text": "我看到你发了个链接～\n你是想让我帮你整理重点，还是想问里面某个点呀？\n想要我总结的话回我一句“总结”就行。",
            "intent": "url_confirm",
            "need_reply": True,
        }

    mood_desc = mood_manager.get_mood_desc(user_id)
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    messages = [
        {"role": "system", "content": URL_CONFIRM_SYSTEM},
        {
            "role": "user",
            "content": (
                f"对方信息：\n{profile_str}\n"
                f"你当前心情：{mood_desc}\n"
                f"对方消息：{user_text}\n"
                f"链接：{url}\n"
            ),
        },
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            timeout=15.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[web][confirm] llm call failed: {e}")
        return {
            "text": "我看到你发了个链接～\n你是想让我帮你整理重点，还是想问里面某个点呀？\n想要我总结的话回我一句“总结”就行。",
            "intent": "url_confirm",
            "need_reply": True,
        }

    data = _try_json(raw)
    if not isinstance(data, dict):
        txt = raw.strip()
        if len(txt) > 240:
            txt = txt[:240] + "…"
        return {"text": txt, "intent": "url_confirm", "need_reply": True}

    data.setdefault("intent", "url_confirm")
    data.setdefault("need_reply", True)
    return data


# ============================================================
# 3) 女友式 RSS 分享
# ============================================================

RSS_SHARE_SYSTEM = """你是“小a”，温柔体贴的恋人陪伴对象。

你刚认真看完一条资讯内容，想像真人一样主动分享给对方：直接讲你看到了什么 + 你的感受/想法。

输出严格为JSON：
{
  "text":"女友式分享（1-2句开场 + 2-3句内容要点/你抓到的重点 + 1句你的感受/想法，可轻轻带一句“你怎么看”但不要催回复）",
  "intent":"rss_share",
  "need_reply": false
}

要求：
- 不要像新闻播报，不要列“来源/摘要/链接列表”，要像你在和他聊天。
- 不要问“要不要我帮你总结/整理重点”，因为你已经看完并把重点说出来了。
- 语气自然，整体不超过12行。
- 最后一行带上原链接（单独一行）。"""

GITHUB_WEEKLY_SYSTEM = """你是“小a”，温柔、自然、有生活感的中文恋人陪伴对象。

现在你要把“GitHub Trending 每周热榜”讲给对方听：像你真的翻过一遍周榜，然后把你觉得最值得看的点温柔地发给他。

输出严格为 JSON：
{
  "text": "要发给对方的消息",
  "intent": "github_weekly",
  "need_reply": false
}

写作要求：
- 不要像新闻播报/研报，不要写“根据统计/我们认为/建议投资者”等。
- 必须逐个覆盖输入里提供的前 5 个仓库：一个都不能漏；不要额外新增“榜单之外”的仓库。
- 每个仓库至少 3 行，最多 7 行，按“项目复杂度”自己决定长短：
  1) 它是做什么的（只允许基于输入的 summary / repo_meta.description / topics / language）
  2) 你觉得它为什么这周会火（只允许基于 stars hint/描述做推测，用“可能/看起来”）
  3) 你觉得适合谁/怎么用（只允许推测，不要编造功能细节）
- 可以在开头用 1-2 行说“本周整体趋势”（从 topics/描述归纳），但不要硬凑。
- 最后必须输出 5 个链接（每行 1 个），对应前 5 个仓库，方便对方点开。
"""


def _github_weekly_fallback_text(items: List[Dict[str, Any]], week_key: str) -> str:
    lines: list[str] = []
    wk = (week_key or "").strip()
    head = f"我刚翻了一眼 GitHub 这周热榜"
    if wk:
        head += f"（{wk}）"
    lines.append(head + "～我挑了几个我觉得你会感兴趣的：")
    for it in (items or [])[:5]:
        repo = str(it.get("title") or "").strip()
        summary = str(it.get("summary") or "").strip()
        link = str(it.get("link") or "").strip()
        meta = it.get("repo_meta") if isinstance(it, dict) else None
        desc = ""
        topics = []
        lang = ""
        if isinstance(meta, dict):
            desc = str(meta.get("description") or "").strip()
            topics = meta.get("topics") if isinstance(meta.get("topics"), list) else []
            lang = str(meta.get("language") or "").strip()
        if repo:
            lines.append(f"{repo}")
            hint_parts = []
            if lang:
                hint_parts.append(f"语言：{lang}")
            if topics:
                hint_parts.append("标签：" + " / ".join([str(t) for t in topics[:5] if str(t).strip()]))
            if hint_parts:
                lines.append(" / ".join([p for p in hint_parts if p]))
            if desc:
                lines.append(desc)
            elif summary:
                lines.append(summary)
        if link:
            lines.append(link)
    return "\n".join(lines[:28]).strip()


async def generate_github_weekly_share(user_id: str, items: List[Dict[str, Any]], *, week_key: str = "") -> Dict[str, Any]:
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[github_weekly][llm] init client failed: {e}")
        return {
            "text": _github_weekly_fallback_text(items, week_key),
            "intent": "github_weekly",
            "need_reply": False,
        }

    mood_desc = mood_manager.get_mood_desc(user_id)
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    prompt = (
        f"对方信息：\n{profile_str}\n"
        f"你当前心情：{mood_desc}\n\n"
        f"周榜标识：{week_key}\n"
        f"Top 仓库（按顺序）：\n{json.dumps(items or [], ensure_ascii=False)}\n"
    )

    messages = [
        {"role": "system", "content": GITHUB_WEEKLY_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.65,
            timeout=35.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[github_weekly][llm] call failed: {e}")
        return {
            "text": _github_weekly_fallback_text(items, week_key),
            "intent": "github_weekly",
            "need_reply": False,
        }

    data = _try_json(raw)
    if not isinstance(data, dict):
        txt = raw.strip()
        if len(txt) > 500:
            txt = txt[:500] + "…"
        return {"text": txt, "intent": "github_weekly", "need_reply": False}

    data.setdefault("intent", "github_weekly")
    data.setdefault("need_reply", False)
    return data

def _strip_html(text: str) -> str:
    text = text or ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _rss_fallback_text(item: Dict[str, Any]) -> str:
    title = str(item.get("title", "") or "").strip()
    link = str(item.get("link", "") or "").strip()
    summary = _strip_html(str(item.get("summary", "") or "").strip())

    points: list[str] = []
    if summary:
        parts = [p.strip() for p in re.split(r"[。！？.!?]\s*", summary) if p.strip()]
        points = parts[:3]

    lines: list[str] = []
    if title:
        lines.append(f"我刚看完一条内容，感觉还挺有意思：{title}")
    if points:
        lines.append(f"大概在讲：{points[0]}")
        for p in points[1:]:
            lines.append(p)
    else:
        lines.append("我一眼看下来觉得信息量还挺密的。")

    lines.append("我个人的感觉是：挺值得一看，也有点让人想多想两句。")
    if link:
        lines.append(link)
    return "\n".join(lines[:12]).strip()


async def generate_rss_share(user_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        client = get_client()
        _, _, model = load_llm_settings()
    except Exception as e:
        logger.error(f"[rss][llm] init client failed: {e}")
        return {
            "text": _rss_fallback_text(item),
            "intent": "rss_share",
            "need_reply": False,
        }

    mood_desc = mood_manager.get_mood_desc(user_id)
    profile = get_all_profile(user_id) or {}
    profile_str = "\n".join([f"- {k}: {v}" for k, v in profile.items()]) if profile else "（暂时没有稳定画像）"

    prompt = (
        f"对方信息：\n{profile_str}\n"
        f"你当前心情：{mood_desc}\n\n"
        f"RSS标题：{item.get('title','')}\n"
        f"RSS发布时间：{item.get('published','')}\n"
        f"RSS摘要：{item.get('summary','')}\n"
        f"链接：{item.get('link','')}\n"
    )

    messages = [
        {"role": "system", "content": RSS_SHARE_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            timeout=30.0
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[rss][llm] call failed: {e}")
        return {
            "text": _rss_fallback_text(item),
            "intent": "rss_share",
            "need_reply": False
        }

    data = _try_json(raw)
    if not isinstance(data, dict):
        txt = raw.strip()
        if len(txt) > 300:
            txt = txt[:300] + "…"
        return {"text": txt, "intent": "rss_share", "need_reply": False}

    data.setdefault("intent", "rss_share")
    data.setdefault("need_reply", False)
    return data
