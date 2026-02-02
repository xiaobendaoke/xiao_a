"""LLM 对话入口（私聊回复生成）。

为避免“功能交叉堆在一个文件里”，本模块只做编排：
- 组装对话上下文（persona / world_info / mood / profile / history）。
- 调用聊天补全接口生成回复文本。
- 解析标签并落库（mood/profile/chat_history）。

具体能力拆分到独立模块：
- `llm_client.py`：加载配置 + 复用 AsyncOpenAI 客户端
- `llm_news.py`：新闻/热点检索线索 + 来源链接暂存
- `llm_tags.py`：MOOD/PROFILE 标签抽取与清洗
- `skills/`：动态能力加载系统（金融分析等专业模块）
"""

from __future__ import annotations

import os

from nonebot import logger
from .persona import SYSTEM_PROMPT
from .mood import mood_manager, clamp
from .memory import get_chat_history, add_memory
from .db import get_all_profile, save_profile_item
from .utils.world_info import get_world_prompt
from .llm_client import get_client, load_llm_settings
from .llm_news import (
    NEWS_ANSWER_SYSTEM,
    consume_search_sources,
    maybe_get_web_search_context,
    should_web_search,
    stash_search_sources,
    strip_urls_from_text,
)
from .rag_core import search_documents, add_document
from .llm_tags import extract_tags_and_clean
from .llm_weather import WEATHER_QA_SYSTEM
from .skills.router import route_skill
from .skills.executor import execute_skill_data, build_skill_prompt

# 兼容旧引用（llm_web/llm_proactive 可能还没改时）
_get_client = get_client
_load_llm_settings = load_llm_settings


def _env_int(name: str, default: int) -> int:
    v = (os.getenv(name) or "").strip()
    if not v:
        return default
    try:
        return int(float(v))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = (os.getenv(name) or "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


# 默认聊天偏短、偏稳；可用环境变量覆盖
CHAT_MAX_TOKENS = _env_int("XIAOA_CHAT_MAX_TOKENS", 240)
CHAT_MAX_TOKENS_SKILL = _env_int("XIAOA_CHAT_MAX_TOKENS_SKILL", 420)
CHAT_TEMPERATURE = _env_float("XIAOA_CHAT_TEMPERATURE", 0.6)
VOICE_MAX_TOKENS = _env_int("XIAOA_VOICE_MAX_TOKENS", 180)


def _is_weather_query(user_text: str) -> bool:
    t = (user_text or "").strip()
    if not t:
        return False
    triggers = ("天气", "温度", "下雨", "降雨", "雨吗", "要带伞", "穿什么", "冷不冷", "热不热", "气温")
    return any(x in t for x in triggers)


VOICE_REPLY_SYSTEM = (
    "你现在会用“语音”回复用户。\n"
    "要求：\n"
    "- 只输出适合直接朗读的中文口语（像在和人聊天），句子短一点，多停顿。\n"
    "- 尽量不要用括号动作/旁白（不要出现“（……）”“【……】”这类舞台指示）。\n"
    "- 少用长段落/长从句，避免项目符号/编号列表。\n"
    "- 可以适度使用“嗯/好啦/那个/唔”等语气词，但不要过量。\n"
    "- 避免输出链接；如必须提到链接，用“我发你链接”这类话术代替。\n"
)


async def get_ai_reply(user_id: str, user_text: str, *, voice_mode: bool = False):
    try:
        client = get_client()
        _, _, model = load_llm_settings()

        # ✅ Skills 路由：判断是否需要专业能力模块
        skill_name = await route_skill(user_text)
        skill_prompt = None
        if skill_name:
            logger.info(f"[skills] 激活专业模块: {skill_name}")
            skill_data = await execute_skill_data(skill_name)
            skill_prompt = build_skill_prompt(skill_name, skill_data)

        include_weather = _is_weather_query(user_text)
        world_context = await get_world_prompt(user_id, user_text=user_text, include_weather=include_weather)
        web_search_context, web_sources = await maybe_get_web_search_context(user_text)
        
        # 提前判断是否为新闻查询（用于后续跳过 RAG）
        is_news_query = should_web_search(user_text) and bool(web_search_context)
        
        # ✅ RAG 检索：查找相关长期记忆
        # 新闻类查询跳过 RAG（避免旧新闻数据干扰实时信息）
        rag_context_str = ""
        # 仅当文本有一定长度时才检索，避免“嗯/啊”之类的短语触发无效搜索
        if len(user_text) > 2 and not is_news_query:
            try:
                # 检索属于该用户的相关记忆
                rag_docs = await search_documents(user_text, n_results=2, filter_meta={"user_id": str(user_id)})
                if rag_docs:
                    rag_context_str = "【相关回忆/资料】：\n" + "\n".join([f"- {d}" for d in rag_docs]) + "\n"
                    logger.info(f"[RAG] Hit {len(rag_docs)} docs")
            except Exception as e:
                logger.warning(f"[RAG] Search failed: {e}")

        current_mood = mood_manager.get_user_mood(user_id)
        current_mood_desc = f"{mood_manager.get_mood_desc(user_id)}（心情值:{current_mood}）"

        history = get_chat_history(user_id) or []

        profile_data = get_all_profile(user_id) or {}
        if profile_data:
            # ✅ 更自然：一行一个字段，别“xx是yy”堆一串
            profile_str = "\n".join([f"- {k}: {v}" for k, v in profile_data.items()])
        else:
            profile_str = "目前还不了解用户的个人信息。"

        # is_news_query 已在前面判断

        context_prefix = (world_context or "").rstrip() + "\n"
        if web_search_context:
            context_prefix += web_search_context.rstrip() + "\n"
        # 加入 RAG 上下文
        if rag_context_str:
            context_prefix += rag_context_str.rstrip() + "\n"

        # ✅ system 拆成两条：persona & 动态上下文
        # “新闻/搜索”类提问用更强约束，强制基于【最新资讯线索】作答，避免模型嘴甜乱编。
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if voice_mode:
            messages.append({"role": "system", "content": VOICE_REPLY_SYSTEM})
        if is_news_query:
            messages.append({"role": "system", "content": NEWS_ANSWER_SYSTEM})
        if include_weather:
            messages.append({"role": "system", "content": WEATHER_QA_SYSTEM})
        # ✅ 注入 skill 专业能力 prompt
        if skill_prompt:
            messages.append({"role": "system", "content": skill_prompt})

        messages.append(
            {
                "role": "system",
                "content": (
                    f"{context_prefix}"
                    f"【当前心情】：{current_mood_desc}\n"
                    f"【你记得的用户信息】：\n{profile_str}\n"
                    f"【画像使用规则】：只有当用户这句话确实用得上时才引用其中某一条；不要把画像当清单复述；"
                    f"不要无中生有提旧事（例如比赛/作品/简历等），除非用户主动提到或明确求助。\n"
                    f"【记忆指令】：当用户明确提供长期稳定信息时，回复末尾另起一行输出 "
                    f"[UPDATE_PROFILE:键=值]（可多条）。每次回复末尾另起一行输出 [MOOD_CHANGE:x]。\n"
                    f"【格式要求】：以上标签必须单独占一行，且放在消息最后，不要和正文写在同一行。\n"
                    f"【天气规则】：只有当用户问到天气/穿衣/带伞/冷不冷/热不热时，才引用【现实环境感知】里的天气字段；"
                    f"如果天气可用性=不可用或未提供天气字段，就说拿不到可靠天气信息，别编造。\n"
                    f"【跑题约束】：只围绕用户当前这句话回应；不要突然开启新话题（例如改简历/找工作计划/项目复盘等）。\n"
                    f"【长度约束】：正文尽量 1-6 行、短句；禁止编号列表（1. 2. 3.）和长段落。\n"
                    f"【现实感知要求】：现实环境感知里给了“时间/时段”，不要把白天说成凌晨/深夜。"
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
            temperature=CHAT_TEMPERATURE,
            max_tokens=(VOICE_MAX_TOKENS if voice_mode else (CHAT_MAX_TOKENS_SKILL if skill_prompt else CHAT_MAX_TOKENS)),
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

        # 新闻/搜索模式：不主动贴链接，把来源留给用户追问时再发
        if is_news_query:
            if web_sources:
                stash_search_sources(str(user_id), web_sources)
            clean_reply = strip_urls_from_text(clean_reply)
            if not clean_reply:
                clean_reply = "我刚刚翻了翻，先给你讲讲我看到的重点～"

        add_memory(user_id, "user", user_text)
        add_memory(user_id, "assistant", clean_reply)
        
        # ✅ RAG 存储：自动记住这次对话（User + AI）
        # 异步存储，不阻塞回复。仅存储有意义长度的内容。
        if len(user_text) > 4:
            import asyncio
            # 格式：Q: ... \n A: ...
            memory_text = f"User: {user_text}\nXiaoA: {clean_reply}"
            asyncio.create_task(add_document(
                memory_text, 
                metadata={"user_id": str(user_id), "source": "chat_history", "type": "auto"}
            ))

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


SYSTEM_REPLY_PROMPT = """你是“小a”，温柔、自然、有生活感的中文陪伴对象。
现在由于系统功能触发（如闹钟、备忘录反馈、错误提示等），系统产生了一个意图。
请你用“小a”的口吻，把这个意图转化为对用户说的话。

【系统意图】：
{instruction}

【用户画像】：
{profile_str}

要求：
1. 保持人设：温柔、可爱，像女朋友/好朋友。如果画像里有称呼（如“哥哥”），请使用它。
2. 只要转化意图即可，不要添加无关的闲聊。
3. 如果是提醒/闹钟，要显得贴心。
4. 如果是错误提示，要显得委屈或安抚用户。
5. 语气要自然，可以使用“～”、“嘛”等语气词，但不要过分卖萌。
6. 直接输出转化后的回复文本，不要带 JSON 或标签。
"""

async def get_system_reply(user_id: str, instruction: str) -> str:
    """把系统指令转化为小a口吻的回复（用于闹钟、备忘录等后台消息）。"""
    try:
        client = get_client()
        _, _, model = load_llm_settings()
        
        # 获取用户画像，以便正确称呼
        profile_data = get_all_profile(user_id) or {}
        if profile_data:
            profile_str = "\n".join([f"- {k}: {v}" for k, v in profile_data.items()])
        else:
            profile_str = "无"

        prompt = SYSTEM_REPLY_PROMPT.format(
            instruction=instruction,
            profile_str=profile_str
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请生成一条回复。"}
        ]
        
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7, # 稍微高一点，让语气更自然
            max_tokens=150,
            timeout=20.0
        )
        
        reply = (response.choices[0].message.content or "").strip()
        # 清理可能产生的引号
        if reply.startswith('"') and reply.endswith('"'):
            reply = reply[1:-1]
        
        return reply

    except Exception as e:
        logger.error(f"[system_reply] failed: {e}")
        # 降级：直接返回指令原义，但稍微包装一下
        return f"（小a：{instruction}）"
