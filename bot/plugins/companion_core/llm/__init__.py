"""LLM 模块统一入口。

整合所有 LLM 相关功能，提供统一的导入接口。

使用方法：
    from .llm import get_client, load_llm_settings, get_ai_reply
    # 或
    from .llm import client, prompts

模块结构：
- 客户端: get_client, load_llm_settings
- 回复生成: get_ai_reply
- 标签解析: extract_tags_and_clean
- prompts: 各类场景 prompt 模块
"""

# === 从现有模块导入，保持向后兼容 ===

# 客户端
from ..llm_client import (
    get_client,
    load_llm_settings,
)

# 回复生成
from ..llm import (
    get_ai_reply,
)

# 标签解析
from ..llm_tags import (
    extract_tags_and_clean,
)

# === 子模块别名 ===
from . import prompts

# 导出列表
__all__ = [
    # 客户端
    "get_client",
    "load_llm_settings",
    # 回复生成
    "get_ai_reply",
    # 标签解析
    "extract_tags_and_clean",
    # 子模块
    "prompts",
]
