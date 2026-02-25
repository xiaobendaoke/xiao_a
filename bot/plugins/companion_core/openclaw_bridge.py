"""OpenClaw 桥接层（方案A：小a主控，OpenClaw做能力侧车）。

设计目标：
- 不改现有 QQ / 人格 / 语音链路。
- 仅提供一个可选的“外部能力调用”接口给 Tool 使用。
- 通过环境变量控制开关，默认关闭，不影响现网行为。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from nonebot import logger


def _env(name: str, default: str = "") -> str:
    v = (os.getenv(name) or default).strip()
    return v.split()[0] if v else ""


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    if not v:
        return default
    try:
        return int(float(v))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


def is_openclaw_enabled() -> bool:
    """是否启用 OpenClaw 侧车。"""
    if not _env_bool("OPENCLAW_TOOL_ENABLED", False):
        return False
    if _env("OPENCLAW_EXEC_URL"):
        return True
    if _env("OPENCLAW_BASE_URL"):
        return True
    return False


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _env("OPENCLAW_API_KEY") or _env("OPENCLAW_GATEWAY_TOKEN")
    if api_key:
        # 兼容两种常见鉴权头（按服务端接受其一即可）
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    return headers


def _extract_text(data: Any) -> str:
    """尽量从不同响应格式里抽取文本。"""
    if data is None:
        return ""

    if isinstance(data, str):
        return data.strip()

    if isinstance(data, dict):
        # OpenAI 风格
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = (choices[0] or {}).get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
            text = (choices[0] or {}).get("text") if isinstance(choices[0], dict) else None
            if isinstance(text, str):
                return text.strip()

        # 常见业务字段
        for key in ("reply", "output", "result", "text", "content", "message"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # 兜底
        return str(data)

    return str(data)


async def run_openclaw_task(
    *,
    user_id: str,
    query: str,
    mode: str = "auto",
    max_steps: int = 4,
) -> str:
    """调用 OpenClaw 并返回文本结果。

    优先级：
    1. `OPENCLAW_EXEC_URL`：业务直连接口（推荐，最稳定）。
    2. `OPENCLAW_BASE_URL`：OpenAI 兼容接口（/v1/chat/completions）。
    """
    if not query.strip():
        return "OpenClaw 调用失败：query 为空。"

    timeout_ms = _env_int("OPENCLAW_TIMEOUT_MS", 25000)
    timeout = httpx.Timeout(timeout_ms / 1000.0)
    headers = _build_headers()

    exec_url = _env("OPENCLAW_EXEC_URL")
    base_url = _env("OPENCLAW_BASE_URL")

    if not exec_url and not base_url:
        return "OpenClaw 未配置：请设置 OPENCLAW_EXEC_URL 或 OPENCLAW_BASE_URL。"

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 模式1：直连执行接口
        if exec_url:
            payload = {
                "user_id": str(user_id),
                "query": query,
                "mode": mode,
                "max_steps": max(1, min(int(max_steps), 20)),
                "source": "xiao_a",
            }
            try:
                resp = await client.post(exec_url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = _extract_text(data)
                return text or "OpenClaw 返回为空。"
            except Exception as e:
                logger.warning(f"[openclaw] exec url failed: {e}")
                return f"OpenClaw 调用失败：{e}"

        # 模式2：OpenAI兼容接口
        # OpenClaw 文档建议 model 使用 "openclaw" 或 "openclaw:<agentId>"
        # 这里默认走 "openclaw"，并通过 header 指定 agent id。
        model = _env("OPENCLAW_MODEL", "openclaw")
        chat_path = _env("OPENCLAW_CHAT_PATH", "/v1/chat/completions")
        endpoint = base_url.rstrip("/") + (chat_path if chat_path.startswith("/") else f"/{chat_path}")
        agent_id = _env("OPENCLAW_AGENT_ID", "main")
        headers["x-openclaw-agent-id"] = agent_id
        payload = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": _env_int("OPENCLAW_MAX_TOKENS", 700),
            # OpenClaw 会在提供 OpenAI user 字段时派生稳定 session key
            "user": str(user_id),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是外部能力执行器。请直接完成任务并返回可读结果。"
                        "不要输出多余前后缀。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"user_id={user_id}\nmode={mode}\nmax_steps={max_steps}\n任务：{query}",
                },
            ],
        }

        try:
            resp = await client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = _extract_text(data)
            return text or "OpenClaw 返回为空。"
        except Exception as e:
            logger.warning(f"[openclaw] openai-compatible call failed: {e}")
            return f"OpenClaw 调用失败：{e}"
