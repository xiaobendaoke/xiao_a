"""LLM 模块统一入口。

整合所有 LLM 相关功能，提供统一的导入接口。

注意：本模块仅导出 prompts 子模块。
其他功能请直接从源模块导入：
  - get_client, load_llm_settings → llm_client.py
  - get_ai_reply → llm.py
  - extract_tags_and_clean → llm_tags.py

这是为了避免循环导入问题。
"""

# === 子模块别名 ===
from . import prompts

# 导出列表
__all__ = [
    "prompts",
]
