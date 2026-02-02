"""LLM 客户端与配置加载（仅负责“怎么连上模型”）。

本模块只做三件事：
- 从环境变量读取 API Key / Base URL / Model（兼容 `.env` 行尾注释）。
- 复用全局 `AsyncOpenAI` 客户端（避免重复建连）。
- 提供向后兼容别名：`_load_llm_settings` / `_get_client`。
"""

from __future__ import annotations

import os
from openai import AsyncOpenAI

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

_client: AsyncOpenAI | None = None


def load_llm_settings() -> tuple[str, str, str]:
    api_key = (
        os.getenv("SILICONFLOW_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    base_url = (os.getenv("SILICONFLOW_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip()
    model = (os.getenv("SILICONFLOW_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()

    # 兼容 `.env` 里带行尾注释/空格：`KEY=xxx  # comment`
    api_key = api_key.split()[0] if api_key else ""
    base_url = base_url.split()[0] if base_url else ""
    model = model.split()[0] if model else ""
    return api_key, base_url, model


def get_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client

    api_key, base_url, _ = load_llm_settings()
    if not api_key:
        raise RuntimeError("缺少 SILICONFLOW_API_KEY（或 OPENAI_API_KEY）环境变量")

    _client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _client


# === Backward compatible aliases ===
_load_llm_settings = load_llm_settings
_get_client = get_client


# === Embedding Support ===
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"

def load_embedding_model() -> str:
    """获取 Embedding 模型名称（优先环境变量 EMBEDDING_MODEL）"""
    return (os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip()

async def get_text_embedding(text: str) -> list[float] | None:
    """调用 API 获取文本向量"""
    if not text or not text.strip():
        return None
    
    try:
        client = get_client()
        model = load_embedding_model()
        # 兼容处理：有些文本太长需要截断，但目前简单处理
        resp = await client.embeddings.create(
            input=text.replace("\n", " "),
            model=model
        )
        return resp.data[0].embedding
    except Exception as e:
        # 避免在这里打太多日志，外层处理
        print(f"[Embedding Error] {e}")
        return None

