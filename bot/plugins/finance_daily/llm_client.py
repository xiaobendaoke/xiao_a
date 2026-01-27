"""LLM 客户端与配置加载（finance_daily 自己维护一份，避免跨插件耦合）。

与 companion_core/llm_client.py 保持一致的行为：
- 从环境变量读取 API Key / Base URL / Model（兼容 `.env` 行尾注释）。
- 复用全局 AsyncOpenAI 客户端。
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


_load_llm_settings = load_llm_settings
_get_client = get_client

