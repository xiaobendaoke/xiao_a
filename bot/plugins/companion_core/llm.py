"""LLM 对话核心（私聊回复生成）。

职责：
- 读取环境变量加载模型配置（API Key / Base URL / Model）。
- 复用全局 `AsyncOpenAI` 客户端（避免每次创建连接开销）。
- 组装对话上下文：
  - 人设系统提示词（`persona.SYSTEM_PROMPT`）；
  - 现实环境感知（时间/天气等，`utils.world_info.get_world_prompt()`）；
  - 用户当前心情描述（`mood_manager`）；
  - 用户画像/备忘录（`db.get_all_profile()`）；
  - 最近聊天历史（`memory.get_chat_history()`）。
- 调用聊天补全接口生成回复文本。
- 解析并清洗系统标签：
  - `[MOOD_CHANGE:x]`：更新心情值（写入 SQLite）。
  - `[UPDATE_PROFILE:键=值]`：更新用户画像（写入 SQLite）。
- 追加本轮消息到聊天记忆表（持久化）。

注意：
- 本模块会在缺少 API Key 时抛 `RuntimeError`，上层会转为友好提示。
"""

import os
import re
import asyncio
from openai import AsyncOpenAI
from nonebot import logger
from .persona import SYSTEM_PROMPT
from .mood import mood_manager
from .memory import get_chat_history, add_memory
from .db import get_all_profile, save_profile_item
from .utils.world_info import get_world_prompt
from .web.google_search import google_cse_search
from .web.rss import fetch_feeds

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

_client: AsyncOpenAI | None = None


def _load_llm_settings() -> tuple[str, str, str]:
    api_key = (
        os.getenv("SILICONFLOW_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.getenv("SILICONFLOW_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or DEFAULT_BASE_URL
    ).strip()
    model = (os.getenv("SILICONFLOW_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()

    # 兼容 .env 写法里带行尾注释/空格：`KEY=xxx  # comment`
    api_key = api_key.split()[0] if api_key else ""
    base_url = base_url.split()[0] if base_url else ""
    model = model.split()[0] if model else ""
    return api_key, base_url, model


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client

    api_key, base_url, _ = _load_llm_settings()
    if not api_key:
        raise RuntimeError("缺少 SILICONFLOW_API_KEY（或 OPENAI_API_KEY）环境变量")

    _client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _client

# —— 标签解析：兼容“独立一行”或“贴在句尾”两种输出 ——
MOOD_TAG_RE = re.compile(r"\[MOOD_CHANGE[:：]\s*(-?\d+)\s*\]", re.IGNORECASE)
PROFILE_TAG_RE = re.compile(
    r"\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]",
    re.IGNORECASE,
)

_NEWS_SEARCH_HINTS = ("新闻", "热点", "热搜", "资讯")
_NEWS_RSS_FALLBACK_FEEDS = (
    # ⚠️ 这里避免默认用 rsshub：不少环境里 `rsshub.app` 直连会网络不可达/超时，导致“搜索兜底”永远为空。
    # 这些源在多数国内网络可直连，适合做“最新资讯线索”。
    "https://www.thepaper.cn/rss",
    "https://www.huxiu.com/rss/0.xml",
    "https://www.36kr.com/feed",
    "https://www.solidot.org/index.rss",
    "https://www.ithome.com/rss/",
    "https://sspai.com/feed",
    # 更偏“国际/综合”，对“印度/美国/日本今天发生啥”这类问题更有机会命中
    "https://www.chinanews.com.cn/rss/world.xml",
    "https://www.people.com.cn/rss/world.xml",
    "https://www.zaobao.com/rss/syndication/rss.xml",
    "https://www.xinhuanet.com/politics/news_politics.xml",
)


def _should_web_search(user_text: str) -> bool:
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

    # 更口语的触发
    if any(k in t for k in ("能搜到", "搜到", "能不能搜", "能查到", "查到")):
        return True

    return False


def _normalize_search_query(user_text: str) -> str:
    s = (user_text or "").strip()
    if not s:
        return ""

    # 去掉称呼前缀，避免污染检索关键词（允许无分隔，比如“小a能搜到…”）
    s = re.sub(r"^(小a|小Ａ|小A)\\s*", "", s, flags=re.I)
    s = re.sub(r"^[,，:：\\s]+", "", s)

    # 去掉口头填充词，把“想搜的主题”尽量抽出来
    s = re.sub(r"(能不能|能否|能不能够)?(帮我)?(搜到|搜|搜索|查到|查一下|查)\\s*", "", s)
    s = re.sub(r"(今天|现在|最近)\\s*(发生了?什么|发生啥|有什么)\\s*", "", s)
    s = re.sub(r"[吗呢呀啊嘛么]$", "", s)

    if s and not re.search(r"(新闻|热点|热搜|资讯|news)", s, flags=re.I):
        # 给检索一点“新闻语境”，否则容易变成闲聊句子
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

def _filter_items_for_query(items: list[dict], query: str) -> list[dict]:
    """
    简单关键词过滤：让“问印度/美国/某人名”的时候尽量喂到更相关的 RSS 线索。
    """
    q = (query or "").strip()
    if not q:
        return items or []

    # 取 2~6 字的中文片段作为关键词（非常轻量的启发式）
    tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,6}", q) if t not in ("今天", "现在", "最近", "新闻", "热点", "热搜", "资讯")]
    if not tokens:
        return items or []

    def score(it: dict) -> int:
        hay = (str(it.get("title") or "") + " " + str(it.get("summary") or "")).strip()
        return sum(1 for t in tokens if t and t in hay)

    ranked = sorted((items or []), key=score, reverse=True)
    top = [it for it in ranked if score(it) > 0]
    return (top[:8] or ranked[:8]) if ranked else []


async def _maybe_get_web_search_context(user_text: str) -> str:
    """
    需要“最新信息/新闻/热点”时才做搜索，减少模型凭空编造。
    """
    if not _should_web_search(user_text):
        return ""

    query = _normalize_search_query(user_text)
    if not query:
        return ""

    logger.info(f"[search] query={query!r}")

    # 1) 优先走 Google Programmable Search（Custom Search JSON API）
    try:
        results = await asyncio.wait_for(google_cse_search(query, max_results=5), timeout=8.0)
        logger.info(f"[search] google_results={len(results or [])}")
    except Exception as e:
        logger.warning(f"[search] google failed: {e!r}")
        results = None

    formatted = _format_search_results(results or [])
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
            )

    return (
        "【最新资讯线索】\n"
        f"{formatted}\n"
        "【使用要求】把它当成你刚刚看到的资讯线索来分享（讲重点+你的感受）；"
        "不要说你在搜索/联网；不确定就说不确定，也不要编造不存在的新闻细节。\n"
    )


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
- 最后 1 行或 2 行贴上对应的链接（独立成行）
"""

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def extract_tags_and_clean(raw: str):
    """
    返回：(clean_text, mood_change(or None), profile_updates[list[(k,v)]])
    - mood_change 取最后一个出现的值
    - profile_updates 支持多条
    - clean_text 会移除标签（无论是否独立成行）
    """
    mood_values = []
    for m in MOOD_TAG_RE.finditer(raw):
        try:
            mood_values.append(int(m.group(1)))
        except Exception:
            continue

    updates: list[tuple[str, str]] = []
    for p in PROFILE_TAG_RE.finditer(raw):
        k, v = p.group(1).strip(), p.group(2).strip()
        if k and v:
            updates.append((k, v))

    cleaned = MOOD_TAG_RE.sub("", raw)
    cleaned = PROFILE_TAG_RE.sub("", cleaned)

    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in cleaned.splitlines()]
    clean_text = "\n".join(lines).strip()
    mood_change = mood_values[-1] if mood_values else None
    return clean_text, mood_change, updates


async def get_ai_reply(user_id: str, user_text: str):
    try:
        client = _get_client()
        _, _, model = _load_llm_settings()

        world_context = await get_world_prompt(user_id)
        web_search_context = await _maybe_get_web_search_context(user_text)
        current_mood = mood_manager.get_user_mood(user_id)
        current_mood_desc = f"{mood_manager.get_mood_desc(user_id)}（心情值:{current_mood}）"

        history = get_chat_history(user_id) or []

        profile_data = get_all_profile(user_id) or {}
        if profile_data:
            # ✅ 更自然：一行一个字段，别“xx是yy”堆一串
            profile_str = "\n".join([f"- {k}: {v}" for k, v in profile_data.items()])
        else:
            profile_str = "目前还不了解用户的个人信息。"

        is_news_query = _should_web_search(user_text) and bool(web_search_context)

        context_prefix = (world_context or "").rstrip() + "\n"
        if web_search_context:
            context_prefix += web_search_context.rstrip() + "\n"

        # ✅ system 拆成两条：persona & 动态上下文
        # “新闻/搜索”类提问用更强约束，强制基于【最新资讯线索】作答，避免模型嘴甜乱编。
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if is_news_query:
            messages.append({"role": "system", "content": NEWS_ANSWER_SYSTEM})

        messages.append(
            {
                "role": "system",
                "content": (
                    f"{context_prefix}"
                    f"【当前心情】：{current_mood_desc}\n"
                    f"【你记得的用户信息】：\n{profile_str}\n"
                    f"【记忆指令】：当用户明确提供长期稳定信息时，回复末尾另起一行输出 "
                    f"[UPDATE_PROFILE:键=值]（可多条）。每次回复末尾另起一行输出 [MOOD_CHANGE:x]。\n"
                    f"【格式要求】：以上标签必须单独占一行，且放在消息最后，不要和正文写在同一行。\n"
                    f"【现实感知要求】：如果现实环境感知里“天气可用性=不可用”，请坦诚说明拿不到实时天气，不要猜。"
                ),
            }
        )

        # 新闻模式下尽量减少历史干扰（否则容易“顺着聊天走偏”忽略线索）
        hist_keep = 4 if is_news_query else 10
        for msg in history[-hist_keep:]:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                messages.append(msg)

        messages.append({"role": "user", "content": user_text})

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.65,          # ✅ 稍微降一点，更少“演”
            # frequency_penalty=0.2,   # 如果你的网关支持，可打开：减少复读/口癖
            timeout=30.0
        )

        raw_content = (response.choices[0].message.content or "").strip()
        logger.opt(colors=True).info(f"<yellow>小a原始回复(含标签)：</yellow> {raw_content}")

        clean_reply, mood_change, updates = extract_tags_and_clean(raw_content)
        logger.opt(colors=True).info(f"<yellow>小a清洗后回复：</yellow> {clean_reply}")

        # ✅ mood：取最后一个，并做范围约束（按你想要的范围改这里）
        if mood_change is not None:
            # 如果你决定用 -3~3（更稳），就用这行：
            mood_change = clamp(mood_change, -3, 3)

            new_total = mood_manager.update_mood(user_id, mood_change)
            logger.opt(colors=True).info(
                f"<b><green>🎭 情绪更新：</green></b> {mood_change} | "
                f"<cyan>用户 {user_id} 当前总值：</cyan> {new_total}"
            )

        # ✅ profile：支持多条更新
        if updates:
            for k, v in updates:
                save_profile_item(user_id, k, v)
                logger.opt(colors=True).info(
                    f"<b><blue>📝 记忆更新：</blue></b> 记住了 {user_id} 的 {k} = {v}"
                )

        if not clean_reply:
            clean_reply = "唔…我刚才走神了一下，你再说一遍嘛。"

        add_memory(user_id, "user", user_text)
        add_memory(user_id, "assistant", clean_reply)

        return clean_reply

    except RuntimeError as e:
        # 一般是缺少 API Key / 配置
        logger.error(f"❌ LLM 配置错误: {e}")
        return "唔…我这边的聊天钥匙还没配置好（SILICONFLOW_API_KEY），你叫管理员看一下日志/环境变量嘛。"
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        msg = str(e)
        if status_code == 401 or "Invalid token" in msg:
            logger.error(f"❌ LLM 鉴权失败(401): {msg}")
            return "唔…我这边的钥匙好像不对（401），你叫管理员检查一下 SILICONFLOW_API_KEY 是否填错了嘛。"
        logger.error(f"❌ LLM 模块报错: {msg}")
        return "唔…我这会儿有点卡壳了，我们再试一次好不好？"
