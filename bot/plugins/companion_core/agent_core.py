"""Agent 核心：多步推理循环。

职责：
- 接收用户消息，判断是走"闲聊"还是"Agent 任务"模式。
- Agent 模式下使用 Function Calling 让 LLM 自主决定调用工具。
- 多步循环直到 LLM 给出最终文本回复或达到最大轮次。
- 保留小A人设：最终输出必须过 persona prompt。
"""

from __future__ import annotations

import json
import os
import asyncio

from nonebot import logger

from .persona import SYSTEM_PROMPT
from .mood import mood_manager, clamp
from .memory import get_chat_history, add_memory
from .db import get_all_profile, save_profile_item
from .utils.world_info import get_world_prompt
from .llm_client import get_client, load_llm_settings, rotate_key
from .llm_tags import extract_tags_and_clean
from .llm_weather import WEATHER_QA_SYSTEM
from .rag_core import search_documents, add_document
from .tool_registry import get_tools_json, execute_tool, get_tools_summary

# 延迟导入 tools 包以触发工具注册（放在模块顶层以确保启动时加载）
from . import tools as _tools_init  # noqa: F401


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


# Agent 配置
MAX_TOOL_ROUNDS = _env_int("AGENT_MAX_TOOL_ROUNDS", 5)  # 最多工具调用轮次
AGENT_MAX_TOKENS = _env_int("AGENT_MAX_TOKENS", 600)     # Agent 模式 max_tokens
CHAT_MAX_TOKENS = _env_int("XIAOA_CHAT_MAX_TOKENS", 240)
CHAT_TEMPERATURE = _env_float("XIAOA_CHAT_TEMPERATURE", 0.9)
VOICE_MAX_TOKENS = _env_int("XIAOA_VOICE_MAX_TOKENS", 180)

# 最后注入的语气覆盖 Prompt
VIBE_CHECK = "（System: 现在的语境是微信闲聊。请把回复写得短一点、松弛一点、口语化一点。不要像在写作文。不要复述规则。）"

# Agent 专用系统 prompt（追加到 persona 之后）
AGENT_SYSTEM = """你是"小a"，除了陪聊天之外，你还拥有以下工具能力。
当用户的请求需要查询实时信息、操作文件、执行命令等任务时，你可以调用工具来完成。

使用工具的规则：
1. 只在确实需要时才调用工具。纯闲聊不要调工具。
2. 可以连续多次调用不同工具来完成复杂任务。
3. 工具返回结果后，用你自己的话（小a的口吻）整理并回复用户。
4. 不要在回复里暴露"tool"、"function calling"等技术术语。
5. 如果工具执行失败，用温柔的方式告知用户。
6. user_id 参数已知时会由系统自动填充，你无需猜测。
7. 当本地工具不够用、任务复杂或需要更强外部执行能力时，优先考虑调用 openclaw_exec。
"""

VOICE_REPLY_SYSTEM = (
    "你现在会用"语音"回复用户。\n"
    "要求：\n"
    "- 只输出适合直接朗读的中文口语（像在和人聊天），句子短一点，多停顿。\n"
    "- 🚫 绝对禁止使用任何颜文字（如 QwQ、Orz、(・∀・) 等）。\n"
    "- 🚫 绝对禁止使用 Emoji 表情（如 😊、😭 等）。\n"
    "- 尽量不要用括号动作/旁白（不要出现"（……）""【……】"这类舞台指示）。\n"
    "- 少用长段落/长从句，避免项目符号/编号列表。\n"
    "- 可以适度使用"嗯/好啦/那个/唔"等语气词，但不要过量。\n"
    "- 避免输出链接；如必须提到链接，用"我发你链接"这类话术代替。\n"
)


async def _build_context_messages(
    user_id: str,
    user_text: str,
    *,
    voice_mode: bool = False,
    enable_tools: bool = True,
) -> list[dict]:
    """构建对话上下文 messages（复用原有逻辑）。"""

    # 世界感知
    world_context = await get_world_prompt(user_id, user_text=user_text, include_weather=False)

    # RAG
    rag_context_str = ""
    if len(user_text) > 2:
        try:
            rag_docs = await search_documents(
                user_text, n_results=2,
                filter_meta={"user_id": str(user_id)},
            )
            if rag_docs:
                rag_context_str = "【相关回忆/资料】：\n" + "\n".join(f"- {d}" for d in rag_docs) + "\n"
        except Exception as e:
            logger.warning(f"[RAG] Search failed: {e}")

    # 心情
    current_mood = mood_manager.get_user_mood(user_id)
    current_mood_desc = f"{mood_manager.get_mood_desc(user_id)}（心情值:{current_mood}）"

    # 历史
    history = get_chat_history(user_id) or []

    # 用户画像
    profile_data = get_all_profile(user_id) or {}
    profile_str = "\n".join(f"- {k}: {v}" for k, v in profile_data.items()) if profile_data else "目前还不了解用户的个人信息。"

    # 组装 messages
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if voice_mode:
        messages.append({"role": "system", "content": VOICE_REPLY_SYSTEM})

    # Agent 能力说明
    if enable_tools:
        messages.append({"role": "system", "content": AGENT_SYSTEM})

    # Memory Stream
    memory_stream = ""
    if rag_context_str:
        memory_stream += f"【脑海里闪过的回忆】：\n{rag_context_str}\n"
    if profile_str:
        memory_stream += f"【对他/她的印象】：\n{profile_str}\n"
    if world_context:
        memory_stream += f"【当前环境感知】：\n{world_context}\n"
    memory_stream += f"【当下的心情】：{current_mood_desc}\n"

    messages.append({
        "role": "system",
        "content": (
            f"{memory_stream}\n"
            "（注意：以上信息只是你的背景记忆，除非自然话赶话聊到，否则不要刻意在回复里背诵这些信息。）"
        ),
    })

    # 标签指令
    messages.append({
        "role": "system",
        "content": (
            "【潜意识指令】\n"
            "1. 只有在心情确实发生变化时才在末尾输出 [MOOD_CHANGE:x]。\n"
            "2. 只有当用户说了新的重要信息（喜欢什么/讨厌什么/最近发生的事）时才输出 [UPDATE_PROFILE:k=v]。\n"
            "3. 保持"微信闲聊"的状态。不要长篇大论。不要列 1. 2. 3.。"
        ),
    })

    # 情绪锁
    mood_instruction = mood_manager.get_mood_instruction(user_id)
    if mood_instruction:
        messages.append({"role": "system", "content": mood_instruction})

    # 历史对话（保持最近 10 轮）
    for msg in history[-10:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append(msg)

    # 用户当前消息
    messages.append({"role": "user", "content": user_text})

    # Vibe Check
    messages.append({"role": "system", "content": VIBE_CHECK})

    return messages


async def get_agent_reply(
    user_id: str,
    user_text: str,
    *,
    voice_mode: bool = False,
) -> str:
    """Agent 核心入口：用 Function Calling 生成回复。

    流程：
    1. 构建上下文 messages + tools JSON
    2. 调用 LLM（带 tools 参数）
    3. 如果 LLM 返回 tool_calls → 执行工具 → 把结果加回 messages → 再调 LLM
    4. 循环直到 LLM 返回纯文本或达到 MAX_TOOL_ROUNDS
    5. 解析标签、更新状态、保存记忆
    """
    try:
        _, _, model = load_llm_settings()

        # 构建上下文
        messages = await _build_context_messages(
            user_id, user_text,
            voice_mode=voice_mode,
            enable_tools=True,
        )

        tools_json = get_tools_json()

        # 心情 → 温度
        current_mood = mood_manager.get_user_mood(user_id)
        mood_instruction = mood_manager.get_mood_instruction(user_id)
        temperature = 0.5 if mood_instruction else CHAT_TEMPERATURE

        max_tokens = VOICE_MAX_TOKENS if voice_mode else AGENT_MAX_TOKENS
        raw_content = ""
        tools_used: list[str] = []

        # ===== Agent Loop =====
        for round_idx in range(MAX_TOOL_ROUNDS + 1):
            # LLM 调用（带重试）
            response = await _call_llm_with_retry(
                model=model,
                messages=messages,
                tools=tools_json if round_idx < MAX_TOOL_ROUNDS else None,  # 最后一轮不给工具，强制输出文本
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = response.choices[0]

            # 检查是否有 tool_calls
            if choice.message.tool_calls:
                # 把 assistant 的 tool_call 消息加入 messages
                messages.append(choice.message.model_dump())

                for tc in choice.message.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    # 自动注入 user_id（如果工具需要但 LLM 没传）
                    if "user_id" in _get_tool_param_names(fn_name) and "user_id" not in fn_args:
                        fn_args["user_id"] = str(user_id)

                    logger.info(f"[agent] round={round_idx} tool={fn_name} args={fn_args}")
                    result = await execute_tool(fn_name, fn_args)
                    tools_used.append(fn_name)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                # 继续循环，让 LLM 基于工具结果生成下一步
                continue
            else:
                # LLM 返回了纯文本回复
                raw_content = (choice.message.content or "").strip()
                break

        if not raw_content:
            raw_content = "唔...这个问题我处理了一会儿，但好像没生成出什么有用的回复，你可以换个说法再试试。"

        if tools_used:
            logger.info(f"[agent] completed. tools_used={tools_used}")

        # ===== 后处理：标签提取、状态更新、记忆保存 =====
        logger.opt(colors=True).info(f"<yellow>Agent原始回复(含标签)：</yellow> {raw_content}")

        clean_reply, mood_change, updates = extract_tags_and_clean(raw_content)
        logger.opt(colors=True).info(f"<yellow>Agent清洗后回复：</yellow> {clean_reply}")

        if mood_change is not None:
            mood_change = clamp(mood_change, -3, 3)
            new_total = mood_manager.update_mood(user_id, mood_change)
            logger.opt(colors=True).info(
                f"<b><green>🎭 情绪更新：</green></b> {mood_change} | "
                f"<cyan>用户 {user_id} 当前总值：</cyan> {new_total}"
            )

        if updates:
            for k, v in updates:
                save_profile_item(user_id, k, v)
                logger.opt(colors=True).info(
                    f"<b><blue>📝 记忆更新：</blue></b> 记住了 {user_id} 的 {k} = {v}"
                )

        if not clean_reply:
            clean_reply = "（小a似乎在发呆，没有说话）"

        # 保存记忆
        add_memory(user_id, "user", user_text)
        add_memory(user_id, "assistant", clean_reply)

        # RAG 存储
        if len(user_text) > 4:
            memory_text = f"User: {user_text}\nXiaoA: {clean_reply}"
            asyncio.create_task(add_document(
                memory_text,
                metadata={"user_id": str(user_id), "source": "chat_history", "type": "auto"},
            ))

        return clean_reply

    except RuntimeError as e:
        logger.error(f"❌ Agent 配置错误: {e}")
        return "唔…我这边的聊天钥匙还没配置好，你叫管理员看一下日志嘛。"
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        msg = str(e)
        if status_code == 401 or "Invalid token" in msg:
            logger.error(f"❌ Agent 鉴权失败(401): {msg}")
            return "唔…我这边的钥匙好像不对，你叫管理员检查一下嘛。"
        logger.error(f"❌ Agent 模块报错: {msg}")
        return "唔…我这会儿有点卡壳了，我们再试一次好不好？"


async def _call_llm_with_retry(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float,
    max_tokens: int,
) -> any:
    """调用 LLM，带 Key 轮换重试逻辑。"""
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            client = get_client()
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": 60.0,  # Agent 模式多轮需要更长超时
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            return await client.chat.completions.create(**kwargs)

        except Exception as e:
            msg = str(e)
            is_rate_limit = "403" in msg or "429" in msg or "RPM limit" in msg or "Credit" in msg
            if is_rate_limit and attempt < max_retries:
                logger.warning(f"[agent] Rate limit ({msg[:50]}...), rotating key ({attempt+1}/{max_retries})...")
                if rotate_key():
                    continue
            raise


def _get_tool_param_names(tool_name: str) -> set[str]:
    """获取工具的参数名列表（用于自动注入 user_id）。"""
    from .tool_registry import get_tool
    tool = get_tool(tool_name)
    if not tool:
        return set()
    return {p.name for p in tool.parameters}
