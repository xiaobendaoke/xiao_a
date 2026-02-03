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

_clients: list[AsyncOpenAI] = []
_current_client_index: int = 0


def load_llm_settings() -> tuple[str, str, str]:
    # 支持多 Key（逗号分隔）：sk-1,sk-2,sk-3
    raw_keys = (
        os.getenv("SILICONFLOW_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    
    # 清理注释和空格
    keys = []
    for k in raw_keys.split(","):
        k = k.split()[0].strip()
        if k:
            keys.append(k)
            
    # 如果没配 Key，返回空字符串（后续报错）
    api_key = keys[0] if keys else ""
    
    base_url = (os.getenv("SILICONFLOW_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip()
    model = (os.getenv("SILICONFLOW_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()

    # 兼容 `.env` 里带行尾注释/空格
    base_url = base_url.split()[0] if base_url else ""
    model = model.split()[0] if model else ""
    return api_key, base_url, model


def _init_clients():
    global _clients
    if _clients:
        return

    raw_keys = (
        os.getenv("SILICONFLOW_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    
    base_url = (os.getenv("SILICONFLOW_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip()
    base_url = base_url.split()[0] if base_url else ""
    
    keys = []
    for k in raw_keys.split(","):
        k = k.split()[0].strip()
        if k:
            keys.append(k)
            
    if not keys:
         raise RuntimeError("缺少 SILICONFLOW_API_KEY（或 OPENAI_API_KEY）环境变量")

    _clients = [AsyncOpenAI(api_key=k, base_url=base_url) for k in keys]
    print(f"[llm_client] Loaded {len(_clients)} keys.")


def get_client() -> AsyncOpenAI:
    global _clients, _current_client_index
    if not _clients:
        _init_clients()
    return _clients[_current_client_index]


def rotate_key() -> bool:
    """切换到下一个 API Key。如果切换成功返回 True，如果没有更多 Key 可切（转了一圈）返回 False（但这里简单实现为永远轮询）。"""
    global _clients, _current_client_index
    if not _clients:
        return False
    
    n = len(_clients)
    if n <= 1:
        return False
        
    prev = _current_client_index
    _current_client_index = (_current_client_index + 1) % n
    print(f"[llm_client] Rotating key: {prev} -> {_current_client_index} (Total: {n})")
    return True


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

